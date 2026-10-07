"""Tests for validate_schema.py — one per check, plus autofix and demo-batch split."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import ingest
from validate_schema import check_cross_row, check_schema


def _mk(**overrides):
    """Build a row with a clean baseline; overrides win."""
    fields = {
        "category": "Beauty > Fragrance",
        "brand": "Elysian",
        "title": "Aqua Bloom Eau de Parfum",
        "gtin": "4006381333931",
        "size": "100",
        "size_unit": "ml",
        "image_url": "https://cdn.example.com/img/x.jpg",
        "video_url": "",
        "description": "Fresh citrus and jasmine top notes fade into a warm amber base. "
                       "Long-lasting spray for daily wear.",
        "partner_sku": "AE-000123",
    }
    fields.update(overrides)
    return {
        "row_id": "rt",
        "fields": fields,
        "flags": [],
        "verdict_schema": None,
        "verdict_semantic": None,
    }


def _codes(flags):
    return [f["code"] for f in flags]


def test_clean_row_has_no_flags():
    row = _mk()
    flags = check_schema(row, region="AE", counters={})
    assert flags == []


def test_missing_required_field():
    row = _mk(brand="")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "MISSING_FIELD" and f["field"] == "brand" for f in flags)


def test_missing_category_yields_missing_field():
    row = _mk(category="")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "MISSING_FIELD" and f["field"] == "category" for f in flags)


def test_bad_gtin_format_letters():
    row = _mk(gtin="ABC123XYZ89")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "gtin" for f in flags)


def test_bad_gtin_format_short():
    row = _mk(gtin="12345")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "gtin" for f in flags)


def test_gtin_checksum_fail():
    # 4006381333931 valid; flip last digit
    row = _mk(gtin="4006381333932")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "GTIN_CHECKSUM" for f in flags)


def test_size_unit_not_in_category_enum():
    row = _mk(size_unit="l")  # Fragrance allows only ml
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_ENUM" and f["field"] == "size_unit" for f in flags)


def test_bad_image_url_not_a_url():
    row = _mk(image_url="not-a-url")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "image_url" for f in flags)


def test_bad_image_url_wrong_extension():
    row = _mk(image_url="https://example.com/img.pdf")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "image_url" for f in flags)


def test_title_too_short_is_reject():
    row = _mk(title="Hi")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "title"
               and f["severity"] == "reject" for f in flags)


def test_description_too_short_is_reject():
    row = _mk(description="Short desc.")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "BAD_FORMAT" and f["field"] == "description"
               and f["severity"] == "reject" for f in flags)


def test_region_mismatch_flag():
    row = _mk(partner_sku="SA-000501")
    flags = check_schema(row, region="AE", counters={})
    assert any(f["code"] == "REGION_MISMATCH" for f in flags)


def test_partner_sku_autogen_when_blank():
    row = _mk(partner_sku="")
    counters = {}
    flags = check_schema(row, region="AE", counters=counters)
    autos = [f for f in flags if f["auto_fixed"] and f["field"] == "partner_sku"]
    assert len(autos) == 1
    assert row["fields"]["partner_sku"] == "AE-000001"


def test_partner_sku_counter_increments_per_region():
    counters = {}
    r1 = _mk(partner_sku="")
    r2 = _mk(partner_sku="")
    r3 = _mk(partner_sku="")
    check_schema(r1, region="AE", counters=counters)
    check_schema(r2, region="AE", counters=counters)
    check_schema(r3, region="SA", counters=counters)
    assert r1["fields"]["partner_sku"] == "AE-000001"
    assert r2["fields"]["partner_sku"] == "AE-000002"
    assert r3["fields"]["partner_sku"] == "SA-000001"


def test_decimal_comma_normalisation():
    row = _mk(size="0,9", size_unit="l", category="Grocery > Beverages")
    check_schema(row, region="AE", counters={})
    assert row["fields"]["size"] == "0.9"


def test_whitespace_trim_autofix():
    row = _mk(brand="  Elysian  ")
    check_schema(row, region="AE", counters={})
    assert row["fields"]["brand"] == "Elysian"


def test_title_case_brand_autofix():
    row = _mk(brand="elysian")
    flags = check_schema(row, region="AE", counters={})
    assert row["fields"]["brand"] == "Elysian"
    assert any(f["auto_fixed"] and f["field"] == "brand" for f in flags)


def test_audio_category_blank_size_ok():
    row = _mk(
        category="Electronics > Audio",
        size="", size_unit="",
        title="Wireless Over-Ear Headphones",
        description="Bluetooth 5.3 headphones with active noise cancellation "
                    "and thirty hour battery life.",
    )
    flags = check_schema(row, region="AE", counters={})
    assert flags == []


def test_gtin_is_never_autofixed():
    row = _mk(gtin="4006381333932")  # bad checksum
    check_schema(row, region="AE", counters={})
    # Value must be unchanged; only a flag reports the problem
    assert row["fields"]["gtin"] == "4006381333932"


def test_supplied_wrong_region_sku_is_never_autofixed():
    row = _mk(partner_sku="SA-000501")
    check_schema(row, region="AE", counters={})
    assert row["fields"]["partner_sku"] == "SA-000501"


def test_cross_row_duplicate_gtin():
    # Give each row a unique partner_sku so only the GTIN collision fires.
    a = _mk(gtin="4006381333931", partner_sku="AE-000501"); a["row_id"] = "ra"
    b = _mk(gtin="4006381333931", partner_sku="AE-000502"); b["row_id"] = "rb"
    c = _mk(gtin="9780306406157", partner_sku="AE-000503"); c["row_id"] = "rc"
    out = check_cross_row([a, b, c])
    assert [f["code"] for f in out["ra"]] == ["DUPLICATE_GTIN"]
    assert [f["code"] for f in out["rb"]] == ["DUPLICATE_GTIN"]
    assert out["rc"] == []


def test_cross_row_duplicate_sku():
    # Distinct GTINs so only the SKU collision fires.
    a = _mk(gtin="4006381333931", partner_sku="AE-000501"); a["row_id"] = "ra"
    b = _mk(gtin="9780306406157", partner_sku="AE-000501"); b["row_id"] = "rb"
    out = check_cross_row([a, b])
    assert [f["code"] for f in out["ra"]] == ["DUPLICATE_SKU"]
    assert [f["code"] for f in out["rb"]] == ["DUPLICATE_SKU"]


def test_cross_row_ignores_blank_values():
    # Both blank on partner_sku AND distinct on GTIN → no cross-row flags.
    a = _mk(gtin="4006381333931", partner_sku=""); a["row_id"] = "ra"
    b = _mk(gtin="9780306406157", partner_sku=""); b["row_id"] = "rb"
    out = check_cross_row([a, b])
    assert out["ra"] == [] and out["rb"] == []


def test_demo_batch_flags_18_broken_and_cross_row_dupe():
    """DoD: per-row schema flags the 18 broken; cross-row (v2) additionally
    catches r001+r060 (duplicate GTIN); the 11 pure semantic traps remain
    schema-clean."""
    from validate_schema import check_schema_batch

    xlsx = os.path.join(os.path.dirname(__file__), "..", "demo_batch.xlsx")
    xlsx = os.path.abspath(xlsx)
    assert os.path.exists(xlsx), "run `python generator.py` before this test"

    rows = ingest(xlsx)
    assert len(rows) == 60
    # Reset the persistent SKU counter so the 10 blank-SKU rows autogenerate
    # from AE-000001 (otherwise a stale counter can mint SKUs that collide with
    # the hardcoded AE-0001xx SKUs and trip DUPLICATE_SKU).
    import validate_schema as _vs
    if os.path.exists(_vs.SKU_COUNTERS_PATH):
        os.remove(_vs.SKU_COUNTERS_PATH)
    check_schema_batch(rows, region="AE")

    def non_autofix(row):
        return [f for f in row["flags"] if not f["auto_fixed"]]

    clean       = [r for r in rows if 1  <= int(r["row_id"][1:]) <= 30]
    broken      = [r for r in rows if 31 <= int(r["row_id"][1:]) <= 48]
    semantic    = [r for r in rows if 49 <= int(r["row_id"][1:]) <= 60]

    # r001 and r060 share the duplicated GTIN — both get DUPLICATE_GTIN.
    r001_codes = {f["code"] for f in non_autofix(rows[0])}
    r060_codes = {f["code"] for f in non_autofix(rows[59])}
    assert "DUPLICATE_GTIN" in r001_codes, r001_codes
    assert "DUPLICATE_GTIN" in r060_codes, r060_codes

    # r002..r030 stay schema-clean (no duplicate involvement).
    unexpectedly = [r["row_id"] for r in clean[1:] if non_autofix(r)]
    assert unexpectedly == [], f"clean rows unexpectedly flagged: {unexpectedly}"

    # Every schema-broken row still caught.
    assert all(non_autofix(r) for r in broken)

    # Pure semantic traps r049..r059 stay schema-clean (r060 is now the
    # cross-row catch and DOES get schema-flagged).
    pure_semantic_flagged = [r["row_id"] for r in semantic[:-1] if non_autofix(r)]
    assert pure_semantic_flagged == [], (
        f"semantic-only traps r049..r059 must stay schema-clean; "
        f"flagged: {pure_semantic_flagged}"
    )
