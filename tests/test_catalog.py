"""Tests for the catalog resolver.

The mock is deterministic, so the demo and these tests agree exactly, and the
"off" default makes no lookups (the offline-core guarantee).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog


def _rows(*gtins):
    return [{"row_id": f"r{i}", "fields": {"gtin": g, "title": f"Item {i}"}, "flags": []}
            for i, g in enumerate(gtins)]


def test_off_is_default(monkeypatch):
    monkeypatch.delenv("CATALOG_RESOLVER", raising=False)
    assert catalog.current_resolver() == "off"


def test_off_makes_no_findings(monkeypatch):
    monkeypatch.delenv("CATALOG_RESOLVER", raising=False)
    out = catalog.check_catalog(_rows("4006381333931", "9780306406157"))
    assert all(f["status"] == "new" for f in out.values())


def test_mock_is_deterministic(monkeypatch):
    monkeypatch.setenv("CATALOG_RESOLVER", "mock")
    # A GTIN divisible by the modulus resolves; same GTIN -> same ZSKU.
    mod = catalog._MOCK_HIT_MODULUS
    hit_digits = str(mod * 1000000000000)[:13].ljust(13, "0")
    # Construct a guaranteed hit and a guaranteed miss.
    hit = str(mod * 100000000000)           # divisible by mod
    miss = str(mod * 100000000000 + 1)      # not divisible
    r1 = catalog._mock_lookup(hit)
    r2 = catalog._mock_lookup(hit)
    assert r1 is not None and r1 == r2
    assert r1["zsku"].startswith("ZSKU-")
    assert catalog._mock_lookup(miss) is None


def test_mock_check_catalog_marks_found(monkeypatch):
    monkeypatch.setenv("CATALOG_RESOLVER", "mock")
    mod = catalog._MOCK_HIT_MODULUS
    hit = str(mod * 100000000000)
    miss = str(mod * 100000000000 + 1)
    out = catalog.check_catalog(_rows(hit, miss))
    assert out["r0"]["status"] == "found"
    assert out["r0"]["zsku"].startswith("ZSKU-")
    assert out["r1"]["status"] == "new"


def test_blank_gtin_is_new(monkeypatch):
    monkeypatch.setenv("CATALOG_RESOLVER", "mock")
    out = catalog.check_catalog(_rows(""))
    assert out["r0"]["status"] == "new"


def test_make_flag_shape():
    flag = catalog.make_flag({"status": "found", "zsku": "ZSKU-12345678", "title": None})
    assert flag["layer"] == "catalog"
    assert flag["code"] == "ALREADY_IN_CATALOG"
    assert flag["zsku"] == "ZSKU-12345678"
    assert flag["confidence"] == 1.0
    assert "ZSKU-12345678" in flag["reason"]


def test_demo_batch_has_a_handful_of_matches(monkeypatch):
    monkeypatch.setenv("CATALOG_RESOLVER", "mock")
    xlsx = os.path.join(os.path.dirname(__file__), "..", "demo_batch.xlsx")
    if not os.path.exists(xlsx):
        import subprocess
        subprocess.check_call([sys.executable,
                               os.path.join(os.path.dirname(__file__), "..", "generator.py")])
    from ingest import ingest
    rows = ingest(xlsx)
    out = catalog.check_catalog(rows)
    found = [rid for rid, f in out.items() if f["status"] == "found"]
    # A clear handful, not a wall and not zero.
    assert 1 <= len(found) <= 10, found
