"""Tests for the engine selector.

Proves the default engine is rules, that the default path makes no outbound
call (even if httpx.post would raise), and that `both` de-duplicates.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine


def _rows():
    return [{
        "row_id": "r1",
        "fields": {
            "category": "Beauty > Fragrance", "brand": "Noctis",
            "title": "Noctis Midnight Oud Spray", "gtin": "4006381333931",
            "size": "50", "size_unit": "ml", "image_url": "https://x/y.jpg",
            "video_url": "", "description": "Warm amber and oud for evening wear in a bottle.",
            "partner_sku": "AE-000001",
        },
        "flags": [],
    }]


def test_default_engine_is_rules(monkeypatch):
    monkeypatch.delenv("SEMANTIC_ENGINE", raising=False)
    assert engine.current_engine() == "rules"


def test_invalid_engine_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("SEMANTIC_ENGINE", "banana")
    assert engine.current_engine() == "rules"


def test_default_path_makes_no_network_call(monkeypatch):
    # If the rules engine tried to reach the network, this would blow up.
    import httpx
    monkeypatch.delenv("SEMANTIC_ENGINE", raising=False)
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call!")))
    res = engine.run_semantic(_rows(), [])
    assert res["status"] == "ok"
    assert res["engine"] == "rules"
    assert any(f["code"] == "TITLE_BRAND_MISMATCH" for f in res["row_flags"]["r1"])


def test_rules_flags_carry_engine_tag(monkeypatch):
    monkeypatch.delenv("SEMANTIC_ENGINE", raising=False)
    res = engine.run_semantic(_rows(), [])
    flags = res["row_flags"]["r1"]
    assert flags and all(f.get("engine") == "rules" for f in flags)


def test_both_merges_and_dedupes_on_code():
    rules_res = {
        "row_flags": {"r1": [{"code": "TITLE_BRAND_MISMATCH", "confidence": 0.95, "engine": "rules"}]},
        "status": "ok", "error_detail": None, "batches_run": 1, "batches_failed": 0,
    }
    llm_res = {
        "row_flags": {"r1": [
            {"code": "TITLE_BRAND_MISMATCH", "confidence": 0.9},   # dup -> dropped
            {"code": "DESC_CONTRADICTION", "confidence": 0.8},     # unique -> kept
        ]},
        "status": "ok", "error_detail": None, "batches_run": 1, "batches_failed": 0,
    }
    merged = engine._merge(rules_res, llm_res)
    codes = [f["code"] for f in merged["row_flags"]["r1"]]
    assert codes.count("TITLE_BRAND_MISMATCH") == 1
    assert "DESC_CONTRADICTION" in codes
    # rules flag preferred on the clash
    brand = next(f for f in merged["row_flags"]["r1"] if f["code"] == "TITLE_BRAND_MISMATCH")
    assert brand.get("engine") == "rules"
