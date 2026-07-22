"""Tests for memory.py — corrections load/append round-trip.

Uses monkeypatch to redirect the store to a tempfile so tests don't touch
the real data/corrections.json.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory, "_TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(memory, "_V1_LEGACY_PATH", str(tmp_path / "corrections.json"))


def test_load_empty_when_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert memory.load() == []


def test_append_and_load_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    entry = {
        "fields": {"brand": "Elysian", "title": "Aqua Bloom Eau de Parfum"},
        "human_verdict": "false_positive",
        "note": "brand name matches, but the perfume line is separately called Aqua",
        "flag_code": "TITLE_BRAND_MISMATCH",
    }
    memory.append(entry)
    loaded = memory.load()
    assert loaded == [entry]


def test_append_preserves_order(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    for i in range(3):
        memory.append({"flag_code": "TITLE_SIZE_MISMATCH", "seq": i,
                       "human_verdict": "confirmed_error", "note": "", "fields": {}})
    loaded = memory.load()
    assert [c["seq"] for c in loaded] == [0, 1, 2]


def test_clear_removes_store(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    memory.append({"flag_code": "OTHER", "human_verdict": "confirmed_error",
                   "note": "", "fields": {}})
    assert memory.load() != []
    memory.clear()
    assert memory.load() == []


def test_load_survives_corrupt_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    memory.append({"flag_code": "OTHER", "human_verdict": "confirmed_error",
                   "note": "", "fields": {}})
    with open(memory._tenant_path("default"), "w", encoding="utf-8") as f:
        f.write("not json at all {][")
    assert memory.load() == []


def test_tenant_isolation(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    memory.append({"flag_code": "A", "human_verdict": "confirmed_error",
                   "note": "", "fields": {}}, tenant="megacart")
    memory.append({"flag_code": "B", "human_verdict": "false_positive",
                   "note": "", "fields": {}}, tenant="acme")
    assert len(memory.load("megacart")) == 1
    assert len(memory.load("acme")) == 1
    assert memory.load("megacart")[0]["flag_code"] == "A"
    assert memory.load("acme")[0]["flag_code"] == "B"
    assert set(memory.list_tenants()) == {"megacart", "acme"}


def test_v1_legacy_file_migrates_to_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # Simulate a pre-tenant install: data/corrections.json exists.
    import json as _json, os as _os
    _os.makedirs(memory._DATA_DIR, exist_ok=True)
    with open(memory._V1_LEGACY_PATH, "w", encoding="utf-8") as f:
        _json.dump([{"flag_code": "OLD", "human_verdict": "confirmed_error",
                     "note": "legacy", "fields": {}}], f)
    # First load in v2 should migrate transparently.
    loaded = memory.load("default")
    assert len(loaded) == 1
    assert loaded[0]["flag_code"] == "OLD"
    assert not _os.path.exists(memory._V1_LEGACY_PATH)
    assert _os.path.exists(memory._tenant_path("default"))
