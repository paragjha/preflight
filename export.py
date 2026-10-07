"""Build an upload-ready .xlsx from a processed batch.

Columns follow the canonical FIELDS order from schema_def.py so any importer
that expects a fixed template gets exactly that. Values reflect POST-autofix
state: normalised units, generated partner_skus, trimmed whitespace, etc.

A trailing column `preflight_status` classifies each row:
    ready         — schema pass/fixed AND semantic pass
    fix_required  — schema reject (unresolved)
    review        — semantic flag or review verdict (needs human eyes)

Cells inside `fix_required` rows that correspond to unresolved reject flags
are shaded light red — the "error column" convention real cataloging tools
use, so the human can eyeball what to change without opening the app.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from schema_def import FIELDS

_REJECT_FILL = PatternFill(fill_type="solid",
                           start_color="FFF5D2D2", end_color="FFF5D2D2")
_HEADER_FILL = PatternFill(fill_type="solid",
                           start_color="FFEDE9FE", end_color="FFEDE9FE")
_HEADER_FONT = Font(bold=True, color="FF4C1D95")

_STATUS_COL = "preflight_status"


def _row_status(row: dict) -> str:
    if row.get("verdict_schema") == "reject":
        return "fix_required"
    if (row.get("catalog") or {}).get("status") == "found":
        return "offer_existing"
    if row.get("verdict_semantic") in {"flag", "review"}:
        return "review"
    return "ready"


def _any_catalog_match(batch: dict) -> bool:
    return any((r.get("catalog") or {}).get("status") == "found"
               for r in batch.get("rows", []))


def _reject_fields(row: dict) -> set[str]:
    return {
        f.get("field")
        for f in row.get("flags", [])
        if f.get("layer") == "schema"
        and f.get("severity") == "reject"
        and not f.get("auto_fixed")
        and f.get("field")
    }


def build_export_xlsx(batch: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "products"

    field_names = [name for name, _required, _examples in FIELDS]
    # Only add the existing_zsku column when the catalog resolver found matches.
    include_zsku = _any_catalog_match(batch)
    headers = field_names + [_STATUS_COL] + (["existing_zsku"] if include_zsku else [])
    ws.append(headers)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL

    for row in batch.get("rows", []):
        fields = row.get("fields", {})
        status = _row_status(row)
        extra = ([(row.get("catalog") or {}).get("zsku") or ""] if include_zsku else [])
        ws.append([fields.get(name, "") for name in field_names] + [status] + extra)
        if status == "fix_required":
            offenders = _reject_fields(row)
            excel_row = ws.max_row
            for col_idx, name in enumerate(field_names, start=1):
                if name in offenders:
                    ws.cell(row=excel_row, column=col_idx).fill = _REJECT_FILL

    for col_idx in range(1, len(headers) + 1):
        letter = ws.cell(row=1, column=col_idx).column_letter
        ws.column_dimensions[letter].width = 22

    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
