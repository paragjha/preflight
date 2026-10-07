"""Row verdicts + the pitch numbers.

Verdict rules (spec §2):
    verdict_schema:
        - "reject" if any unfixed severity=reject flag exists
        - "fixed"  if only auto_fixed flags exist (no unfixed rejects)
        - "pass"   otherwise (no schema flags, or only unfixed warns)
        # Unfixed warns fall under "pass" here — spec is silent on the
        # combination and reject-severity is what stops the upload.
    verdict_semantic:
        - "flag"   if any semantic flag with confidence >= 0.75
        - "review" if any semantic flag with confidence in [0.4, 0.75)
                   -> the escalation queue
        - "pass"   otherwise

Metrics (spec §3, /api/batches/{id}/metrics):
    total_rows, would_reject_on_upload, caught_preflight, auto_fixed,
    silent_errors_found (schema-clean rows with semantic flags),
    escalated_for_review, clean, human_touched_rows
"""

_SEMANTIC_CODES = {
    "TITLE_BRAND_MISMATCH",
    "TITLE_SIZE_MISMATCH",
    "UNIT_IMPLAUSIBLE",
    "DESC_CONTRADICTION",
}

CONFIDENCE_FLAG_MIN = 0.75
CONFIDENCE_REVIEW_MIN = 0.4


def _has_unfixed_reject(row: dict) -> bool:
    return any(
        f.get("layer") == "schema"
        and f.get("severity") == "reject"
        and not f.get("auto_fixed")
        for f in row.get("flags", [])
    )


def _has_autofix(row: dict) -> bool:
    return any(
        f.get("layer") == "schema" and f.get("auto_fixed")
        for f in row.get("flags", [])
    )


def _semantic_flags(row: dict) -> list[dict]:
    return [
        f for f in row.get("flags", [])
        if f.get("layer") == "semantic" and f.get("code") in _SEMANTIC_CODES
    ]


def _schema_verdict(row: dict) -> str:
    if _has_unfixed_reject(row):
        return "reject"
    if _has_autofix(row):
        return "fixed"
    return "pass"


def _semantic_verdict(row: dict) -> str:
    sf = _semantic_flags(row)
    if not sf:
        return "pass"
    if any(float(f.get("confidence", 0)) >= CONFIDENCE_FLAG_MIN for f in sf):
        return "flag"
    if any(float(f.get("confidence", 0)) >= CONFIDENCE_REVIEW_MIN for f in sf):
        return "review"
    return "pass"


def compute_verdicts(rows: list[dict]) -> None:
    for row in rows:
        row["verdict_schema"] = _schema_verdict(row)
        row["verdict_semantic"] = _semantic_verdict(row)


def compute_metrics(batch_or_rows) -> dict:
    """Compute the pitch numbers.

    Accepts a batch dict (preferred: carries semantic_status) or a bare
    list[Row] (legacy path, assumes semantic ran cleanly). When the semantic
    layer errored on any batch, silent_errors_found / clean are set to None
    so the UI can render '—' — a failed check must not read like a passed
    check.
    """
    if isinstance(batch_or_rows, list):
        rows = batch_or_rows
        semantic_status = "ok"
        error_detail = None
        batches_failed = 0
    else:
        rows = batch_or_rows.get("rows", [])
        semantic_status = batch_or_rows.get("semantic_status", "ok")
        error_detail = batch_or_rows.get("semantic_error_detail")
        batches_failed = batch_or_rows.get("semantic_batches_failed", 0)

    compute_verdicts(rows)
    total = len(rows)
    would_reject = sum(1 for r in rows if _has_unfixed_reject(r))
    auto_fixed = sum(1 for r in rows if _has_autofix(r))
    escalated = sum(1 for r in rows if r["verdict_semantic"] == "review")

    if semantic_status == "ok":
        silent = sum(
            1 for r in rows
            if r["verdict_schema"] in {"pass", "fixed"} and _semantic_flags(r)
        )
        clean = sum(
            1 for r in rows
            if r["verdict_schema"] in {"pass", "fixed"}
            and r["verdict_semantic"] == "pass"
        )
    else:
        # Any semantic batch failure poisons these two counts — a rejected
        # row we couldn't classify semantically is not the same as clean.
        silent = None
        clean = None

    touched_ids = {
        r["row_id"] for r in rows
        if r["verdict_semantic"] == "review" or r.get("reviews")
    }
    already_in_catalog = sum(
        1 for r in rows
        if (r.get("catalog") or {}).get("status") == "found"
    )
    return {
        "total_rows": total,
        "would_reject_on_upload": would_reject,
        "caught_preflight": would_reject,
        "auto_fixed": auto_fixed,
        "silent_errors_found": silent,
        "escalated_for_review": escalated,
        "clean": clean,
        "human_touched_rows": len(touched_ids),
        "already_in_catalog": already_in_catalog,
        "semantic_status": semantic_status,
        "semantic_error_detail": error_detail,
        "semantic_batches_failed": batches_failed,
    }
