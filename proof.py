"""End-to-end pipeline runner + honest metrics vs. the answer key.

Usage:
    python proof.py                       # demo batch, default engine (rules, no key)
    python proof.py --batch holdout       # the committed holdout batch
    python proof.py --batch both          # both, scored separately
    python proof.py --learn               # + demonstrate the correction loop
    SEMANTIC_ENGINE=llm python proof.py   # score the LLM path instead (needs a key)

Runs ingest -> schema (incl. cross-row) -> the selected semantic engine, then
compares the pipeline's output against the batch's answer key. Buckets are
derived from the answer key itself, never hardcoded, so the same scorer works
for the demo and the holdout. The answer key is for auditing only; the pipeline
never reads it.
"""

import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import engine
import memory
from ingest import ingest
from validate_schema import check_schema_batch

HERE = os.path.dirname(os.path.abspath(__file__))

SEMANTIC_CODES = {"TITLE_BRAND_MISMATCH", "TITLE_SIZE_MISMATCH",
                  "UNIT_IMPLAUSIBLE", "DESC_CONTRADICTION"}

BATCHES = {
    "demo":    ("demo_batch.xlsx",    "demo_batch.answer_key.json"),
    "holdout": ("holdout_batch.xlsx", "holdout_batch.answer_key.json"),
}


def _ensure_files(xlsx):
    """Regenerate the batches if missing (demo_* are gitignored)."""
    if not os.path.exists(os.path.join(HERE, xlsx)):
        subprocess.check_call([sys.executable, os.path.join(HERE, "generator.py")])
        subprocess.check_call([sys.executable, os.path.join(HERE, "generator.py"), "--holdout"])


def _run_pipeline(xlsx, corrections):
    rows = ingest(os.path.join(HERE, xlsx))
    check_schema_batch(rows, region="AE")
    result = engine.run_semantic(rows, corrections)
    for row in rows:
        row["flags"] = list(row["flags"]) + result["row_flags"].get(row["row_id"], [])
    return rows, result


def _score(rows, ak):
    by = {r["row_id"]: r for r in rows}

    def sem_codes(rid):
        return {f["code"] for f in by[rid]["flags"]
                if f.get("layer") == "semantic" and f.get("code") in SEMANTIC_CODES}

    def schema_reject_codes(rid):
        return {f["code"] for f in by[rid]["flags"]
                if f.get("layer") == "schema"
                and f.get("severity") == "reject" and not f.get("auto_fixed")}

    clean = [rid for rid, c in ak.items() if not c]
    sem_traps = [rid for rid, c in ak.items() if set(c) & SEMANTIC_CODES]
    schema_traps = [rid for rid, c in ak.items() if c and not set(c) & SEMANTIC_CODES]

    sem_caught, sem_miss = 0, []
    for rid in sem_traps:
        exp = set(ak[rid]) & SEMANTIC_CODES
        if exp & sem_codes(rid):
            sem_caught += 1
        else:
            sem_miss.append((rid, ak[rid]))

    schema_caught = sum(1 for rid in schema_traps
                        if set(ak[rid]) & schema_reject_codes(rid))

    fps = [(rid, sorted(sem_codes(rid))) for rid in clean if sem_codes(rid)]

    return {
        "clean": clean, "sem_traps": sem_traps, "schema_traps": schema_traps,
        "sem_caught": sem_caught, "sem_miss": sem_miss,
        "schema_caught": schema_caught,
        "fps": fps, "by": by, "ak": ak,
    }


def _report(name, s, result):
    print(f"\n=== {name} ({result.get('engine')} engine) "
          .ljust(66, "="))
    print(f"Schema layer:   caught {s['schema_caught']}/{len(s['schema_traps'])} "
          f"schema-broken rows")
    print(f"Semantic layer: caught {s['sem_caught']}/{len(s['sem_traps'])} semantic traps")
    print(f"False positives on {len(s['clean'])} clean rows: {len(s['fps'])}")
    for rid, codes in s["sem_miss"]:
        reasons = "; ".join(f["reason"] for f in s["by"][rid]["flags"]
                            if f.get("layer") == "semantic") or "(no flag)"
        print(f"   MISS {rid} expected {codes} — {reasons}")
    for rid, codes in s["fps"]:
        reasons = "; ".join(f["reason"] for f in s["by"][rid]["flags"]
                            if f.get("layer") == "semantic")
        print(f"   FP   {rid} {codes} — {reasons}")


def _run_one(key):
    xlsx, akname = BATCHES[key]
    _ensure_files(xlsx)
    ak = json.load(open(os.path.join(HERE, akname)))
    rows, result = _run_pipeline(xlsx, memory.load(memory.DEFAULT_TENANT))
    s = _score(rows, ak)
    _report(key.upper(), s, result)
    return s, result


def _learn_demo():
    """Mark one semantic flag as a false positive, re-run, confirm it's gone.

    With the rules engine this proves the exception loop; the suppression is
    targeted (same brand / same term pair), not generalised like the LLM path.
    """
    xlsx, akname = BATCHES["demo"]
    _ensure_files(xlsx)
    ak = json.load(open(os.path.join(HERE, akname)))
    memory.clear(memory.DEFAULT_TENANT)

    rows, result = _run_pipeline(xlsx, memory.load(memory.DEFAULT_TENANT))
    s = _score(rows, ak)
    _report("DEMO (initial)", s, result)

    target = None
    for rid in s["sem_traps"]:
        flags = [f for f in s["by"][rid]["flags"]
                 if f.get("layer") == "semantic" and f.get("code") in SEMANTIC_CODES]
        if flags:
            target = (rid, flags[0])
            break
    if not target:
        print("\n(no semantic flag to feed the learning loop)")
        return
    rid, flag = target
    print(f"\nHuman review: marking {rid}'s {flag['code']} as a false positive...")
    memory.append({
        "fields": s["by"][rid]["fields"],
        "human_verdict": "false_positive",
        "note": "reviewer confirmed this row is fine",
        "flag_code": flag["code"],
    })

    rows2, result2 = _run_pipeline(xlsx, memory.load(memory.DEFAULT_TENANT))
    s2 = _score(rows2, ak)
    still = {f["code"] for f in s2["by"][rid]["flags"] if f.get("layer") == "semantic"}
    print(f"Re-ran. {rid}'s {flag['code']} suppressed after learning? "
          f"{'YES' if flag['code'] not in still else 'NO'}")
    memory.clear(memory.DEFAULT_TENANT)


def main():
    if "--learn" in sys.argv:
        _learn_demo()
        return
    which = "demo"
    if "--batch" in sys.argv:
        which = sys.argv[sys.argv.index("--batch") + 1]
    keys = ["demo", "holdout"] if which == "both" else [which]
    for k in keys:
        _run_one(k)


if __name__ == "__main__":
    main()
