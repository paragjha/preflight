"""The single LLM touchpoint.

check_semantic(rows, corrections) -> dict[row_id, list[Flag]]
    Runs four semantic checks on rows that survived the schema layer:
        1. TITLE_BRAND_MISMATCH — brand appears in title (brand_policy=forbid)
        2. TITLE_SIZE_MISMATCH — title cites a size the size fields contradict
        3. UNIT_IMPLAUSIBLE     — title cites a unit implausible for the category
        4. DESC_CONTRADICTION   — description contradicts other attributes

    Batches up to 20 rows per API call. Skips rows whose schema layer
    already produced a hard reject. Recent corrections are folded into
    the prompt as few-shot examples — the learning loop is "corrections
    in the prompt", no fine-tuning, no embeddings.

    On a parse failure, retries once with a stricter instruction. If the
    retry also fails, every row in that batch gets an OTHER/warn flag
    noting the semantic layer errored, and processing continues.
"""

import json
import os
import re
import sys
from typing import Any

import httpx

from schema_def import CATEGORIES

# Google Gemini via its OpenAI-compatible endpoint. Uses the rolling
# `-latest` alias so new keys route to whatever the currently-live free-tier
# Flash model is (pinned model IDs like gemini-2.5-flash are 404 for keys
# minted after Google's routing change). Request/response shape is stable.
MODEL_ID = "gemini-flash-latest"
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
API_KEY_ENV = "GEMINI_API_KEY"
BATCH_SIZE = 20
MAX_FEW_SHOT = 10
MAX_TOKENS = 8192
TIMEOUT_SECONDS = 120.0


def _load_dotenv_once():
    """Populate os.environ from the nearest .env file if the key isn't already set.

    Searches: <preflight>/.env, then <preflight>/../.env. Handles simple
    KEY=VALUE lines, strips surrounding quotes, ignores blanks and comments.
    We avoid the python-dotenv dependency to stay within the spec's stack.
    """
    if os.environ.get(API_KEY_ENV):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, ".env"), os.path.join(here, "..", ".env")):
        candidate = os.path.abspath(candidate)
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
        except Exception:
            pass


_load_dotenv_once()

ALLOWED_CODES = {
    "TITLE_BRAND_MISMATCH",
    "TITLE_SIZE_MISMATCH",
    "UNIT_IMPLAUSIBLE",
    "DESC_CONTRADICTION",
}


def _has_schema_reject(row: dict) -> bool:
    return any(
        f.get("layer") == "schema"
        and f.get("severity") == "reject"
        and not f.get("auto_fixed")
        for f in row.get("flags", [])
    )


def _row_for_prompt(row: dict) -> dict:
    f = row["fields"]
    return {
        "row_id": row["row_id"],
        "category":    f.get("category", ""),
        "brand":       f.get("brand", ""),
        "title":       f.get("title", ""),
        "size":        f.get("size", ""),
        "size_unit":   f.get("size_unit", ""),
        "description": f.get("description", ""),
    }


def _category_reference() -> str:
    lines = []
    for cat, meta in CATEGORIES.items():
        units = meta["size_units"]
        if not units:
            lines.append(f"- {cat}: (no size fields)")
        else:
            lines.append(f"- {cat}: {', '.join(units)}")
    return "\n".join(lines)


def _few_shot_block(corrections: list[dict]) -> str:
    if not corrections:
        return "(no prior human reviews)"
    recent = corrections[-MAX_FEW_SHOT:]
    lines = []
    for c in recent:
        fields = c.get("fields", {})
        summary_parts = []
        for k in ("category", "brand", "title", "size", "size_unit", "description"):
            if k in fields and fields[k] != "":
                v = fields[k]
                if isinstance(v, str) and len(v) > 80:
                    v = v[:77] + "..."
                summary_parts.append(f"{k}={v!r}")
        summary = ", ".join(summary_parts)
        verdict = c.get("human_verdict", "?")
        note = c.get("note", "")
        code = c.get("flag_code", "?")
        line = f"- ({summary}) — was flagged {code}; human said: {verdict}"
        if note:
            line += f" ({note})"
        lines.append(line)
    return "\n".join(lines)


def _build_prompt(rows_for_prompt: list[dict], corrections: list[dict]) -> str:
    return (
        "You are a marketplace catalog validator. Perform SEMANTIC checks only "
        "(deterministic schema checks have already passed on these rows).\n\n"
        "PERFORM EXACTLY THESE FOUR CHECKS ON EACH ROW:\n\n"
        "1. TITLE_BRAND_MISMATCH — the row's brand appears (in any casing) as a word "
        "inside the title. This marketplace uses brand_policy=forbid: the brand is "
        "stored in its own field and concatenated at display time, so putting the "
        "brand in the title creates duplication.\n\n"
        "2. TITLE_SIZE_MISMATCH — the title mentions a numeric size/quantity that "
        "contradicts the row's size + size_unit fields. Example: title says "
        "\"200 ml\" but size=500 and size_unit=ml.\n\n"
        "3. UNIT_IMPLAUSIBLE — a unit of measure mentioned in the title is implausible "
        "for the row's category. Example: \"500 ml\" appearing in a Fashion > Apparel "
        "title, or \"kg\" in a Beauty > Fragrance title.\n\n"
        "4. DESC_CONTRADICTION — the description contradicts other attributes. "
        "Examples: title says \"Cotton T-Shirt\" but description says \"100% polyester\"; "
        "title implies a single item but description says \"Pack of 3\".\n\n"
        "CATEGORY UNIT REFERENCE (units plausible per category):\n"
        f"{_category_reference()}\n\n"
        "LEARNING FROM HUMAN REVIEWS (most recent, up to 10):\n"
        f"{_few_shot_block(corrections)}\n\n"
        "If a past reviewer marked a similar-looking flag as \"false_positive\", "
        "DO NOT emit the same class of flag for lookalike rows.\n\n"
        "ROWS TO CHECK (JSON):\n"
        f"{json.dumps(rows_for_prompt, indent=2, ensure_ascii=False)}\n\n"
        "OUTPUT FORMAT:\n"
        "Return ONLY a JSON array. Each element is one flag:\n"
        "  {\"row_id\": \"...\", \"code\": \"...\", \"field\": \"...\", "
        "\"reason\": \"one plain-English sentence\", \"confidence\": 0.0-1.0}\n"
        "- code must be one of: TITLE_BRAND_MISMATCH, TITLE_SIZE_MISMATCH, "
        "UNIT_IMPLAUSIBLE, DESC_CONTRADICTION\n"
        "- field: the primary field the issue is about (e.g., \"title\", "
        "\"description\", \"size_unit\")\n"
        "- confidence: your calibrated certainty. 0.75+ = high confidence; "
        "0.4-0.75 = review-worthy but uncertain; below 0.4 do NOT emit the flag.\n"
        "- Emit at most one flag per row per check code.\n"
        "- Rows with no issues: omit from the output entirely.\n"
        "- If no rows have issues, return [].\n\n"
        "Output the JSON array and NOTHING ELSE — no markdown fences, no prose, "
        "no keys wrapping the array."
    )


_RATE_RETRY_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)


def _call_llm(prompt: str) -> str:
    """Single request with one automatic 429-backoff retry.

    Free tiers throttle by RPM; the API returns 429 with a hint like
    "Please retry in 27.1s". We honour that hint (capped at 60s) once,
    then bubble any further error to the caller.
    """
    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(f"{API_KEY_ENV} not set")

    def _post():
        return httpx.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_ID,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=TIMEOUT_SECONDS,
        )

    resp = _post()
    if resp.status_code == 429:
        wait = 15.0
        m = _RATE_RETRY_RE.search(resp.text)
        if m:
            try:
                wait = min(60.0, float(m.group(1)) + 1.0)
            except ValueError:
                pass
        print(f"[semantic] 429 rate-limited; sleeping {wait:.1f}s before retry",
              file=sys.stderr)
        import time
        time.sleep(wait)
        resp = _post()

    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _extract_json_array(text: str) -> Any:
    """Return the first JSON array in `text`, tolerating fences and stray prose.

    Order: (1) strip ``` fences, (2) try full-string parse, (3) walk the
    string tracking brackets and string boundaries to isolate the first
    balanced [...] block, then parse that.
    """
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass

    lo = t.find("[")
    if lo == -1:
        raise ValueError("no '[' in response")
    depth = 0
    in_str = False
    escape = False
    for i in range(lo, len(t)):
        c = t[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(t[lo:i + 1])
    raise ValueError("no balanced ']' found")


def _make_semantic_flag(code: str, field: str, reason: str, confidence: float) -> dict:
    try:
        c = float(confidence)
    except Exception:
        c = 0.5
    return {
        "layer": "semantic",
        "code": code,
        "field": field,
        "severity": "warn",
        "auto_fixed": False,
        "fix": None,
        "reason": reason,
        "confidence": max(0.0, min(1.0, c)),
    }


def _describe_error(exc: Exception) -> str:
    """Compress an exception into a single-line, log-friendly reason.

    HTTP errors keep the status code and the first slice of the response body
    (that's where Gemini and OpenAI-compat endpoints stash the quota/limit
    text). Everything else falls back to `TypeName: message`.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip().replace("\n", " ")
        return f"HTTP {exc.response.status_code}: {body[:200]}"
    msg = str(exc).strip().replace("\n", " ")
    return f"{type(exc).__name__}: {msg[:200]}"


def _batch_check(rows: list[dict],
                 corrections: list[dict]) -> tuple[dict[str, list[dict]], str | None]:
    """Run one batch. Returns (row_flags, error_detail_or_None).

    error_detail is None if the batch (or its retry) produced parseable output.
    If both the initial call and the retry fail, every row in the batch gets
    an OTHER/warn flag whose reason INCLUDES the underlying error string, and
    that same string is returned so the caller can attach it to the batch.
    """
    if not rows:
        return {}, None
    prompt = _build_prompt([_row_for_prompt(r) for r in rows], corrections)

    parsed = None
    first_err = None
    try:
        text = _call_llm(prompt)
        parsed = _extract_json_array(text)
    except Exception as e:
        first_err = _describe_error(e)
        print(f"[semantic] first attempt failed ({first_err}); retrying",
              file=sys.stderr)

    retry_err = None
    if parsed is None:
        try:
            retry_prompt = (
                prompt
                + "\n\nCRITICAL: Return ONLY a JSON array. Nothing before or after."
            )
            text = _call_llm(retry_prompt)
            parsed = _extract_json_array(text)
        except Exception as e:
            retry_err = _describe_error(e)
            print(f"[semantic] retry failed too ({retry_err}); "
                  "marking batch as semantic_error", file=sys.stderr)

    if parsed is None:
        # Prefer the retry error if it's different, otherwise the first.
        detail = retry_err or first_err or "unknown"
        reason = f"semantic layer failed (batch_status=semantic_error): {detail}"
        return (
            {r["row_id"]: [_make_semantic_flag("OTHER", "", reason, 0.0)] for r in rows},
            detail,
        )

    out: dict[str, list[dict]] = {r["row_id"]: [] for r in rows}
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            rid = item.get("row_id")
            code = item.get("code")
            if rid not in out or code not in ALLOWED_CODES:
                continue
            out[rid].append(_make_semantic_flag(
                code=code,
                field=str(item.get("field", "")),
                reason=str(item.get("reason", "")),
                confidence=item.get("confidence", 0.5),
            ))
    return out, None


def check_semantic(rows: list[dict], corrections: list[dict]) -> dict:
    """Run semantic checks on a list of Row dicts.

    Returns a dict:
        {
          "row_flags":       {row_id: [Flag, ...]},
          "status":          "ok" | "partial" | "failed",
          "error_detail":    <last error string, or None>,
          "batches_run":     <int>,
          "batches_failed":  <int>,
        }
    Rows with a schema-layer reject are skipped (empty flag list).
    """
    row_flags: dict[str, list[dict]] = {r["row_id"]: [] for r in rows}
    eligible = [r for r in rows if not _has_schema_reject(r)]
    batches_run = 0
    batches_failed = 0
    last_error: str | None = None

    for i in range(0, len(eligible), BATCH_SIZE):
        batch = eligible[i:i + BATCH_SIZE]
        batches_run += 1
        batch_flags, err = _batch_check(batch, corrections)
        if err is not None:
            batches_failed += 1
            last_error = err
        for rid, flags in batch_flags.items():
            row_flags[rid] = flags

    if batches_run == 0 or batches_failed == 0:
        status = "ok"
    elif batches_failed < batches_run:
        status = "partial"
    else:
        status = "failed"

    return {
        "row_flags": row_flags,
        "status": status,
        "error_detail": last_error,
        "batches_run": batches_run,
        "batches_failed": batches_failed,
    }
