"""Semantic engine selector.

`run_semantic(rows, corrections)` picks the engine from `SEMANTIC_ENGINE`:

    rules   deterministic, no network, no key (DEFAULT)
    llm     the original Gemini path in semantic.py
    both    run both and merge (rules preferred on a row+code clash)

The architecture is the argument: PreFlight shipped on an LLM, then measured
what the model was actually worth and replaced it with rules where the rules
hold. `both` is how that measurement is run. `semantic.py` is untouched and
imported lazily, only when an LLM engine is selected, so the default path has
no outbound call and needs no key.
"""

import os

from rules import check_rules

ENGINE_ENV = "SEMANTIC_ENGINE"
DEFAULT_ENGINE = "rules"
VALID_ENGINES = {"rules", "llm", "both"}


def _load_dotenv_once():
    """Minimal .env reader so SEMANTIC_ENGINE (and a key, for the llm path) can
    live in .env. Does not override an already-set variable. ~stdlib only."""
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
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass


_load_dotenv_once()


def current_engine() -> str:
    eng = (os.environ.get(ENGINE_ENV) or DEFAULT_ENGINE).strip().lower()
    return eng if eng in VALID_ENGINES else DEFAULT_ENGINE


def _merge(rules_res: dict, llm_res: dict) -> dict:
    """Union both engines' flags per row, deduped on code (rules preferred).
    Status follows the LLM so its failures still surface."""
    row_flags: dict[str, list[dict]] = {}
    all_ids = set(rules_res["row_flags"]) | set(llm_res["row_flags"])
    for rid in all_ids:
        seen = set()
        merged = []
        for f in rules_res["row_flags"].get(rid, []) + llm_res["row_flags"].get(rid, []):
            if f["code"] in seen:
                continue
            seen.add(f["code"])
            merged.append(f)
        row_flags[rid] = merged
    return {
        "row_flags": row_flags,
        "status": llm_res["status"],
        "error_detail": llm_res["error_detail"],
        "batches_run": rules_res["batches_run"] + llm_res["batches_run"],
        "batches_failed": llm_res["batches_failed"],
    }


def run_semantic(rows, corrections) -> dict:
    """Run the selected engine. Always returns the check_semantic shape plus
    an `engine` key."""
    eng = current_engine()
    if eng == "rules":
        res = check_rules(rows, corrections)
    elif eng == "llm":
        import semantic  # lazy: only imported when an LLM engine is selected
        res = semantic.check_semantic(rows, corrections)
    else:  # both
        import semantic
        rules_res = check_rules(rows, corrections)
        llm_res = semantic.check_semantic(rows, corrections)
        res = _merge(rules_res, llm_res)
    res["engine"] = eng
    return res
