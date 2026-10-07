"""FastAPI app + routes. Thin — logic lives in modules.

Routes (all JSON unless stated):
    GET  /                                          serves static/index.html
    POST /api/batches                               multipart xlsx + form region -> pipeline
    GET  /api/batches                               list summaries
    GET  /api/batches/{batch_id}                    full batch JSON
    GET  /api/batches/{batch_id}/metrics            the pitch numbers
    POST /api/batches/{batch_id}/rows/{row_id}/review  record human verdict on a flag
    POST /api/batches/{batch_id}/rerun              re-run semantic layer with current memory
"""

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import catalog
import engine  # importing runs its own _load_dotenv_once; semantic.py loaded lazily
import errors as error_sheet
import memory
import template
from export import build_export_xlsx
from ingest import ingest as ingest_xlsx
from metrics import compute_metrics, compute_verdicts
from schema_def import REGIONS
from validate_schema import check_schema_batch

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
BATCHES_DIR = os.path.join(DATA_DIR, "batches")
STATIC_DIR = os.path.join(HERE, "static")

# Public-demo mode: block the data-upload routes so a deployed instance only
# runs the bundled synthetic batch. Real partner data never touches it.
DEMO_ONLY = os.environ.get("PREFLIGHT_DEMO_ONLY", "").strip() in {"1", "true", "yes"}
# Whether the drawer should try to load listing images (your browser fetches
# them from the partner URL). Off = zero outbound traffic.
SHOW_IMAGES = os.environ.get("PREFLIGHT_SHOW_IMAGES", "1").strip() not in {"0", "false", "no"}

os.makedirs(BATCHES_DIR, exist_ok=True)

app = FastAPI(title="PreFlight", version="1.0")


def _require_not_demo():
    if DEMO_ONLY:
        raise HTTPException(403, "this is the public demo — uploads are disabled. "
                                 "Use 'Load demo batch'.")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _batch_path(batch_id: str) -> str:
    return os.path.join(BATCHES_DIR, f"{batch_id}.json")


def _load_batch(batch_id: str) -> dict:
    path = _batch_path(batch_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"batch {batch_id!r} not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_batch(batch: dict) -> None:
    with open(_batch_path(batch["batch_id"]), "w", encoding="utf-8") as f:
        json.dump(batch, f, indent=2, ensure_ascii=False)


def _apply_semantic(batch: dict) -> None:
    """Drop any existing semantic flags, re-run the semantic layer, attach meta.

    Stamps `batch["semantic_status"]`, `semantic_error_detail`,
    `semantic_batches_run`, `semantic_batches_failed` so metrics.compute_metrics
    can distinguish a truly clean run from one where LLM calls failed.

    Corrections are loaded from the BATCH's tenant so learning stays scoped
    per customer (multi-tenant memory, v2).
    """
    rows = batch["rows"]
    tenant = batch.get("tenant", memory.DEFAULT_TENANT)
    for row in rows:
        row["flags"] = [f for f in row["flags"] if f.get("layer") != "semantic"]
    result = engine.run_semantic(rows, memory.load(tenant))
    for row in rows:
        row["flags"] = list(row["flags"]) + result["row_flags"].get(row["row_id"], [])
    batch["semantic_status"] = result["status"]
    batch["semantic_error_detail"] = result["error_detail"]
    batch["semantic_batches_run"] = result["batches_run"]
    batch["semantic_batches_failed"] = result["batches_failed"]
    batch["semantic_engine"] = result.get("engine", engine.current_engine())
    _apply_catalog(batch)


def _apply_catalog(batch: dict) -> None:
    """Resolve each row's GTIN against the catalog resolver. Attaches an
    ALREADY_IN_CATALOG flag + row['catalog'] finding where a GTIN already
    exists, so a duplicate listing is caught before a new SKU is minted."""
    rows = batch["rows"]
    for row in rows:
        row["flags"] = [f for f in row["flags"] if f.get("layer") != "catalog"]
    findings = catalog.check_catalog(rows)
    for row in rows:
        finding = findings.get(row["row_id"], {"status": "new", "zsku": None})
        row["catalog"] = finding
        if finding.get("status") == "found":
            row["flags"] = list(row["flags"]) + [catalog.make_flag(finding)]
    batch["catalog_resolver"] = catalog.current_resolver()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    index = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index):
        return {"detail": "frontend not built yet (Phase 5)"}
    return FileResponse(index)


@app.get("/api/config")
def get_config():
    """Front-end feature flags, so the UI reflects the running instance."""
    return {
        "engine": engine.current_engine(),
        "demo_only": DEMO_ONLY,
        "show_images": SHOW_IMAGES,
        "catalog_resolver": catalog.current_resolver(),
    }


@app.post("/api/batches")
async def upload_batch(
    file: UploadFile = File(...),
    region: str = Form(...),
    tenant: str = Form(memory.DEFAULT_TENANT),
):
    _require_not_demo()
    if region not in REGIONS:
        raise HTTPException(400, f"region must be one of {REGIONS}")

    tmp_path = os.path.join(DATA_DIR, f"_upload_{uuid.uuid4().hex}.xlsx")
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(await file.read())
        rows = ingest_xlsx(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    check_schema_batch(rows, region=region, rulepack=template.load_rulepack(tenant))
    batch = {
        "batch_id": f"b{uuid.uuid4().hex[:12]}",
        "created_at": _now_iso(),
        "source_filename": file.filename,
        "region": region,
        "tenant": tenant,
        "rows": rows,
    }
    _apply_semantic(batch)
    compute_verdicts(rows)
    _save_batch(batch)
    return batch


@app.get("/api/batches")
def list_batches():
    out = []
    for fname in sorted(os.listdir(BATCHES_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(BATCHES_DIR, fname), "r", encoding="utf-8") as f:
                b = json.load(f)
        except Exception:
            continue
        out.append({
            "batch_id": b.get("batch_id"),
            "created_at": b.get("created_at"),
            "source_filename": b.get("source_filename"),
            "region": b.get("region"),
            "row_count": len(b.get("rows", [])),
        })
    return out


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    return _load_batch(batch_id)


@app.get("/api/batches/{batch_id}/metrics")
def get_metrics(batch_id: str):
    batch = _load_batch(batch_id)
    m = compute_metrics(batch)
    _save_batch(batch)  # persist any verdict changes computed on the fly
    return m


@app.post("/api/batches/demo")
def upload_demo_batch(region: str = "AE", tenant: str = memory.DEFAULT_TENANT):
    """One-click demo upload: reads the bundled demo_batch.xlsx from disk.

    Same pipeline as POST /api/batches, just skips the multipart file dance
    so the recording flow doesn't fumble a file picker on camera.
    """
    if region not in REGIONS:
        raise HTTPException(400, f"region must be one of {REGIONS}")
    demo_path = os.path.join(HERE, "demo_batch.xlsx")
    if not os.path.exists(demo_path):
        raise HTTPException(
            500,
            "demo_batch.xlsx not present — run `python generator.py` from the "
            "preflight/ directory to create it.",
        )
    rows = ingest_xlsx(demo_path)
    check_schema_batch(rows, region=region, rulepack=template.load_rulepack(tenant))
    batch = {
        "batch_id": f"b{uuid.uuid4().hex[:12]}",
        "created_at": _now_iso(),
        "source_filename": "demo_batch.xlsx",
        "region": region,
        "tenant": tenant,
        "rows": rows,
    }
    _apply_semantic(batch)
    compute_verdicts(rows)
    _save_batch(batch)
    return batch


@app.get("/api/batches/{batch_id}/export")
def export_batch(batch_id: str):
    batch = _load_batch(batch_id)
    compute_verdicts(batch["rows"])
    stem = batch.get("source_filename") or "batch"
    if stem.lower().endswith(".xlsx"):
        stem = stem[:-5]
    out_name = f"{stem}_preflight.xlsx"
    return Response(
        content=build_export_xlsx(batch),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


@app.post("/api/batches/{batch_id}/rows/{row_id}/review")
def review_row(batch_id: str, row_id: str, body: dict):
    verdict = body.get("human_verdict")
    if verdict not in {"confirmed_error", "false_positive"}:
        raise HTTPException(
            400, "human_verdict must be 'confirmed_error' or 'false_positive'"
        )
    note = str(body.get("note", ""))
    explicit_code = body.get("flag_code", "") or ""

    batch = _load_batch(batch_id)
    row = next((r for r in batch["rows"] if r["row_id"] == row_id), None)
    if row is None:
        raise HTTPException(404, f"row {row_id!r} not in batch {batch_id!r}")

    flag_code = explicit_code
    if not flag_code:
        sem = [f for f in row["flags"] if f.get("layer") == "semantic"]
        if sem:
            flag_code = sem[0].get("code", "")

    correction = {
        "fields": row["fields"],
        "human_verdict": verdict,
        "note": note,
        "flag_code": flag_code,
    }
    memory.append(correction, tenant=batch.get("tenant", memory.DEFAULT_TENANT))

    row.setdefault("reviews", []).append({
        "human_verdict": verdict,
        "note": note,
        "flag_code": flag_code,
        "at": _now_iso(),
    })
    _save_batch(batch)
    return {"ok": True, "correction": correction}


@app.post("/api/batches/{batch_id}/rerun")
def rerun_semantic(batch_id: str):
    batch = _load_batch(batch_id)
    _apply_semantic(batch)
    compute_verdicts(batch["rows"])
    _save_batch(batch)
    return batch


# ---------------------------------------------------------------------------
# v2: tenant rulepacks (template ingestion)
# ---------------------------------------------------------------------------

@app.post("/api/tenants/{tenant}/rulepack")
async def upload_rulepack(tenant: str, file: UploadFile = File(...)):
    """Upload a marketplace's Excel template; extract validations + required
    columns; store a rulepack for this tenant. Every subsequent batch upload
    scoped to this tenant validates against it.
    """
    _require_not_demo()
    tmp = os.path.join(DATA_DIR, f"_template_{uuid.uuid4().hex}.xlsx")
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(await file.read())
        try:
            rulepack = template.read_template(tmp)
        except Exception as e:
            raise HTTPException(400, f"could not parse template: {type(e).__name__}: {e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    rulepack["source"] = file.filename or rulepack["source"]
    template.save_rulepack(rulepack, tenant)
    return rulepack


@app.get("/api/tenants/{tenant}/rulepack")
def get_rulepack(tenant: str):
    rp = template.load_rulepack(tenant)
    if rp is None:
        raise HTTPException(404, f"no rulepack for tenant {tenant!r}")
    return rp


@app.delete("/api/tenants/{tenant}/rulepack")
def delete_rulepack(tenant: str):
    template.clear_rulepack(tenant)
    return {"ok": True}


# ---------------------------------------------------------------------------
# v2: error-sheet learning loop
# ---------------------------------------------------------------------------

@app.post("/api/batches/{batch_id}/errors")
async def ingest_error_sheet(batch_id: str, file: UploadFile = File(...)):
    """Upload the marketplace tool's returned error xlsx. Each row it lists is
    turned into a `confirmed_error` correction (fields snapshot + validator
    message) and appended to this batch's tenant memory. Next semantic run
    folds them in as few-shot."""
    _require_not_demo()
    batch = _load_batch(batch_id)
    tenant = batch.get("tenant", memory.DEFAULT_TENANT)

    tmp = os.path.join(DATA_DIR, f"_errors_{uuid.uuid4().hex}.xlsx")
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(await file.read())
        try:
            records = error_sheet.parse_error_sheet(tmp)
        except Exception as e:
            raise HTTPException(400, f"could not read error sheet: {type(e).__name__}: {e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    resolved  = error_sheet.resolve_to_rows(records, batch)
    matched   = [r for r in resolved if r.get("row") is not None]
    unmatched = [r for r in resolved if r.get("row") is None]
    corrections = error_sheet.as_corrections(resolved)
    added = memory.append_many(corrections, tenant=tenant)
    return {
        "records_in_sheet":  len(records),
        "matched_to_rows":   len(matched),
        "unmatched":         len(unmatched),
        "corrections_added": added,
        "tenant":            tenant,
    }


@app.get("/api/batches/{batch_id}/simulated_errors")
def simulated_error_sheet(batch_id: str):
    """Build a plausible tool-side error xlsx from this batch's own
    rejected rows — so the demo has something to feed the ingest endpoint
    without a real cataloging tool."""
    from openpyxl import Workbook
    batch = _load_batch(batch_id)
    compute_verdicts(batch["rows"])
    wb = Workbook()
    ws = wb.active
    ws.title = "errors"
    ws.append(["row_id", "partner_sku", "gtin", "field", "error_message"])
    for row in batch["rows"]:
        if row.get("verdict_schema") != "reject":
            continue
        for f in row.get("flags", []):
            if f.get("layer") != "schema" or f.get("severity") != "reject" or f.get("auto_fixed"):
                continue
            ws.append([
                row["row_id"],
                row["fields"].get("partner_sku", ""),
                row["fields"].get("gtin", ""),
                f.get("field", ""),
                f.get("reason", ""),
            ])
    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    stem = (batch.get("source_filename") or "batch").rsplit(".", 1)[0]
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}_errors.xlsx"'},
    )
