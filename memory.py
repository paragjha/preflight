"""Corrections store — one JSON file per tenant.

Layout:
    data/tenants/<tenant>/corrections.json

`tenant` defaults to "default" so callers written against the v1 signature
still work. On first read after upgrade, a v1-era `data/corrections.json`
(no tenant folder) is silently migrated to `data/tenants/default/corrections.json`
so existing corrections aren't lost.

A correction records a human's verdict on a semantic flag. On the next
semantic run, up to the last 10 corrections for the current tenant are
folded into the prompt as few-shot examples — no fine-tuning, no
embeddings. See semantic.py.

Shape:
    {
      "fields": {...canonical row fields at the time...},
      "human_verdict": "confirmed_error" | "false_positive",
      "note": "optional human note",
      "flag_code": "TITLE_BRAND_MISMATCH" | ...,
    }
"""

import json
import os
import shutil
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "data")
_TENANTS_DIR = os.path.join(_DATA_DIR, "tenants")
_V1_LEGACY_PATH = os.path.join(_DATA_DIR, "corrections.json")

DEFAULT_TENANT = "default"


def _tenant_dir(tenant: str) -> str:
    return os.path.join(_TENANTS_DIR, tenant)


def _tenant_path(tenant: str) -> str:
    return os.path.join(_tenant_dir(tenant), "corrections.json")


def _migrate_v1_if_needed() -> None:
    """Move a pre-tenant corrections.json into data/tenants/default/ once."""
    default_path = _tenant_path(DEFAULT_TENANT)
    if os.path.exists(_V1_LEGACY_PATH) and not os.path.exists(default_path):
        os.makedirs(_tenant_dir(DEFAULT_TENANT), exist_ok=True)
        shutil.move(_V1_LEGACY_PATH, default_path)


def load(tenant: str = DEFAULT_TENANT) -> list[dict]:
    _migrate_v1_if_needed()
    path = _tenant_path(tenant)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data: Any = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def append(correction: dict, tenant: str = DEFAULT_TENANT) -> None:
    corrections = load(tenant)
    corrections.append(correction)
    os.makedirs(_tenant_dir(tenant), exist_ok=True)
    with open(_tenant_path(tenant), "w", encoding="utf-8") as f:
        json.dump(corrections, f, indent=2)


def append_many(new_corrections: list[dict], tenant: str = DEFAULT_TENANT) -> int:
    """Bulk-append (used by the error-sheet learning loop). Returns count added."""
    if not new_corrections:
        return 0
    corrections = load(tenant)
    corrections.extend(new_corrections)
    os.makedirs(_tenant_dir(tenant), exist_ok=True)
    with open(_tenant_path(tenant), "w", encoding="utf-8") as f:
        json.dump(corrections, f, indent=2)
    return len(new_corrections)


def clear(tenant: str = DEFAULT_TENANT) -> None:
    """Wipe one tenant's store — used by the demo runner between fresh iterations."""
    path = _tenant_path(tenant)
    if os.path.exists(path):
        os.remove(path)


def list_tenants() -> list[str]:
    _migrate_v1_if_needed()
    if not os.path.isdir(_TENANTS_DIR):
        return []
    return sorted(
        d for d in os.listdir(_TENANTS_DIR)
        if os.path.isdir(os.path.join(_TENANTS_DIR, d))
    )
