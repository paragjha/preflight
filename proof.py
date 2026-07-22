"""End-to-end pipeline runner + honest metrics vs. the answer key.

Usage:
    ANTHROPIC_API_KEY=... python proof.py            # normal run
    ANTHROPIC_API_KEY=... python proof.py --learn    # + demonstrate the memory loop

Rebuilds the demo batch, runs schema then semantic, then compares the
pipeline's output against demo_batch.answer_key.json — computed by
this script, never by eyeball.
"""

import io
import json
import os
import subprocess
import sys

# Force UTF-8 on Windows consoles so em-dashes etc. don't get mojibake'd.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from ingest import ingest
from validate_schema import check_schema
from semantic import check_semantic
import memory

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, "demo_batch.xlsx")
ANSWER_KEY = os.path.join(HERE, "demo_batch.answer_key.json")


def _ensure_demo_batch():
    if not os.path.exists(XLSX):
        subprocess.check_call([sys.executable, os.path.join(HERE, "generator.py")])


def _run_pipeline(corrections):
    rows = ingest(XLSX)
    counters: dict = {}
    for row in rows:
        check_schema(row, region="AE", counters=counters)
    sem_flags = check_semantic(rows, corrections)
    for row in rows:
        row["flags"] = list(row["flags"]) + sem_flags.get(row["row_id"], [])
    return rows


def _score(rows):
    with open(ANSWER_KEY) as f:
        ak = json.load(f)

    clean_ids     = [f"r{i:03d}" for i in range(1, 31)]
    broken_ids    = [f"r{i:03d}" for i in range(31, 49)]
    semantic_ids  = [f"r{i:03d}" for i in range(49, 61)]
    rows_by_id    = {r["row_id"]: r for r in rows}

    _SEMANTIC_CODES = {"TITLE_BRAND_MISMATCH", "TITLE_SIZE_MISMATCH",
                       "UNIT_IMPLAUSIBLE", "DESC_CONTRADICTION"}

    def sem_flags(rid):
        return [f for f in rows_by_id[rid]["flags"] if f.get("layer") == "semantic"
                and f.get("code") in _SEMANTIC_CODES]

    def schema_reject_flags(rid):
        return [f for f in rows_by_id[rid]["flags"] if f.get("layer") == "schema"
                and f.get("severity") == "reject" and not f.get("auto_fixed")]

    def trap_caught(rid):
        # A trap row counts as caught by EITHER a semantic flag OR by a
        # deterministic cross-row check that matches the answer-key code.
        # r060 is now caught by DUPLICATE_GTIN (schema, cross-row); the
        # rest are caught semantically.
        expected = set(ak.get(rid, []))
        actual_semantic = {f["code"] for f in sem_flags(rid)}
        actual_schema   = {f["code"] for f in schema_reject_flags(rid)}
        return bool(expected & (actual_semantic | actual_schema))

    schema_caught = sum(1 for rid in broken_ids if schema_reject_flags(rid))
    semantic_caught = sum(1 for rid in semantic_ids if trap_caught(rid))
    false_positives = sum(1 for rid in clean_ids if sem_flags(rid))

    return {
        "schema_caught": schema_caught,
        "schema_total": len(broken_ids),
        "semantic_caught": semantic_caught,
        "semantic_total": len(semantic_ids),
        "false_positives_on_clean": false_positives,
        "clean_total": len(clean_ids),
        "answer_key": ak,
        "rows_by_id": rows_by_id,
    }


def _print_report(score, label=""):
    print(f"\n=== Pipeline result {label} ".ljust(70, "="))
    print(f"Schema layer:  caught {score['schema_caught']}/{score['schema_total']} "
          f"schema-broken rows")
    print(f"Semantic layer: caught {score['semantic_caught']}/{score['semantic_total']} "
          f"semantic-trap rows")
    print(f"False positives on {score['clean_total']} clean rows: "
          f"{score['false_positives_on_clean']}")

    print("\nPer-trap detail:")
    ak = score["answer_key"]
    for rid in [f"r{i:03d}" for i in range(49, 61)]:
        expected = ak[rid]
        row = score["rows_by_id"][rid]
        sem_codes = [f["code"] for f in row["flags"] if f.get("layer") == "semantic"]
        cross_codes = [f["code"] for f in row["flags"]
                       if f.get("layer") == "schema" and f.get("severity") == "reject"
                       and not f.get("auto_fixed")
                       and f["code"] in {"DUPLICATE_GTIN", "DUPLICATE_SKU"}]
        actual = sem_codes + cross_codes
        hit = "hit " if (set(expected) & set(actual)) else "MISS"
        reasons = "; ".join(
            f["reason"] for f in row["flags"]
            if (f.get("layer") == "semantic")
               or (f.get("layer") == "schema" and f["code"] in cross_codes)
        )
        print(f"  {rid} {hit}  expected={expected}  actual={actual}"
              + (f"  — {reasons}" if reasons else ""))

    fps = [(rid, [f for f in score["rows_by_id"][rid]["flags"] if f.get("layer") == "semantic"])
           for rid in [f"r{i:03d}" for i in range(1, 31)]]
    fps = [(rid, flags) for rid, flags in fps if flags]
    if fps:
        print("\nFalse positives on clean rows:")
        for rid, flags in fps:
            for f in flags:
                print(f"  {rid} {f['code']} conf={f['confidence']:.2f} — {f['reason']}")


def main():
    _ensure_demo_batch()
    demo_learn = "--learn" in sys.argv

    if demo_learn:
        # Fresh state for the learning demo
        memory.clear(memory.DEFAULT_TENANT)

    print("Loaded corrections:", len(memory.load(memory.DEFAULT_TENANT)))
    rows = _run_pipeline(memory.load(memory.DEFAULT_TENANT))
    score = _score(rows)
    _print_report(score, label="(initial)")

    dod_ok = (score["semantic_caught"] >= 9 and score["false_positives_on_clean"] <= 2)
    print(f"\nDoD: semantic >= 9/12 AND FP <= 2 -> {'PASS' if dod_ok else 'FAIL'}")

    if not demo_learn:
        return

    # Learning-loop demo: mark a semantic flag as "false positive" per a human
    # reviewer, re-run, confirm the model suppresses the same-shape flag.
    # Prefer a real FP if the model produced one; otherwise use a caught trap
    # to demonstrate the mechanism (the memory loop is what we're proving here,
    # not that we can invent FPs on demand).
    clean_ids = [f"r{i:03d}" for i in range(1, 31)]
    trap_ids  = [f"r{i:03d}" for i in range(49, 61)]

    def _first_sem_flag(rid_list):
        for rid in rid_list:
            flags = [f for f in score["rows_by_id"][rid]["flags"]
                     if f.get("layer") == "semantic"
                     and f.get("code") != "OTHER"]
            if flags:
                return score["rows_by_id"][rid], flags[0]
        return None, None

    fp_row, fp_flag = _first_sem_flag(clean_ids)
    used_synthetic = False
    if fp_row is None:
        fp_row, fp_flag = _first_sem_flag(trap_ids)
        used_synthetic = True

    if fp_row is None:
        print("\n(no semantic flags to feed the learning loop)")
        return

    tag = "TRAP re-labelled as FP (demo of mechanism)" if used_synthetic else "real FP"
    print(f"\nHuman review: marking {fp_row['row_id']}'s {fp_flag['code']} flag "
          f"as a false positive ({tag})...")
    memory.append({
        "fields": fp_row["fields"],
        "human_verdict": "false_positive",
        "note": "reviewer confirmed this row is fine as-is",
        "flag_code": fp_flag["code"],
    })

    print("Re-running semantic layer with memory in place...")
    rows2 = _run_pipeline(memory.load(memory.DEFAULT_TENANT))
    score2 = _score(rows2)
    _print_report(score2, label="(after learning)")

    still = [f for f in score2["rows_by_id"][fp_row["row_id"]]["flags"]
             if f.get("layer") == "semantic" and f.get("code") == fp_flag["code"]]
    print(f"\nWas {fp_row['row_id']}'s {fp_flag['code']} flag suppressed after learning? "
          f"{'YES' if not still else 'NO'}")


if __name__ == "__main__":
    main()
