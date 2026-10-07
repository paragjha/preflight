"""Tests for the deterministic rules engine.

One case per rule branch, parser edge cases, the learning-as-exceptions loop,
and the two deliberate limitations written as strict xfails so the gaps are
documented in the suite rather than hidden.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from rules import (
    check_rules, find_pack, find_units, _normalise, _detect_all,
)


def _row(rid="rt", **fields):
    base = {
        "category": "Beauty > Fragrance", "brand": "Elysian",
        "title": "Aqua Bloom Eau de Parfum", "gtin": "4006381333931",
        "size": "100", "size_unit": "ml",
        "image_url": "https://x/y.jpg", "video_url": "",
        "description": "Fresh citrus and jasmine notes over a warm amber base for daily wear.",
        "partner_sku": "AE-000001",
    }
    base.update(fields)
    return {"row_id": rid, "fields": base, "flags": []}


def _codes(row):
    res = check_rules([row], [])
    return {f["code"] for f in res["row_flags"][row["row_id"]]}


# ---- parser edge cases ----------------------------------------------------

def test_parse_decimal_comma():
    assert find_units("1,5 L")[0][3] == 1500.0   # base ml


def test_parse_multipack():
    assert find_pack("Trail Mix 2x250g Pouches") == 2
    units = find_units("2x250g")
    assert any(u[1] == "g" and u[0] == 250 for u in units)


def test_parse_number_words_and_hyphen():
    assert find_pack("Pack of three") == 3
    assert find_pack("set of four") == 4
    assert find_pack("3-pack") == 3
    assert find_pack("Set of 2") == 2


def test_normalise_strips_possessive_and_accents():
    assert _normalise("Dr. Scholl's") == "dr scholls"
    assert _normalise("L'Oreal") == "loreal"


# ---- one case per rule branch ---------------------------------------------

def test_brand_exact_in_title():
    assert "TITLE_BRAND_MISMATCH" in _codes(_row(brand="Noctis", title="Noctis Midnight Oud Spray"))


def test_brand_punctuation_normalises_into_title():
    row = _row(category="Fashion > Footwear", brand="Dr. Scholl's",
               title="Dr Scholls Gel Comfort Insoles", size="EU 42", size_unit="EU 42")
    assert "TITLE_BRAND_MISMATCH" in _codes(row)


def test_brand_substring_is_not_a_match():
    # "Copperline" must not match the token "Copper" in the title.
    row = _row(category="Home > Cookware", brand="Copperline",
               title="Copper Frying Pan Stainless Interior", size="28", size_unit="cm",
               description="Tri-ply copper frying pan with a stainless steel interior.")
    assert "TITLE_BRAND_MISMATCH" not in _codes(row)


def test_unit_implausible_volume_in_apparel():
    row = _row(category="Fashion > Apparel", brand="Everweave",
               title="Cotton Hoodie 500 ml Finish", size="M", size_unit="M")
    assert "UNIT_IMPLAUSIBLE" in _codes(row)


def test_unit_implausible_mass_in_apparel():
    row = _row(category="Fashion > Apparel", brand="Everweave",
               title="Cotton Hoodie 16 oz Heavyweight", size="M", size_unit="M")
    assert "UNIT_IMPLAUSIBLE" in _codes(row)


def test_apparel_size_letter_not_treated_as_metric():
    # size_unit "L"/"M" must not be read as litre/metre -> no spurious flags.
    row = _row(category="Fashion > Apparel", brand="Marlow",
               title="Linen Button-Down Shirt", size="L", size_unit="L",
               description="Lightweight linen shirt with a relaxed fit for warm weather.")
    assert _codes(row) == set()


def test_unit_implausible_range_single_unit_category():
    # Fragrance range is 5-500 ml; 800 ml is out of range.
    row = _row(category="Beauty > Fragrance", brand="Vireo",
               title="Big Bottle Eau de Parfum", size="800", size_unit="ml")
    assert "UNIT_IMPLAUSIBLE" in _codes(row)


def test_title_size_mismatch_simple():
    row = _row(title="Rose Eau de Parfum 30 ml", size="50", size_unit="ml")
    assert "TITLE_SIZE_MISMATCH" in _codes(row)


def test_title_size_matches_when_units_differ_but_equal():
    # 1 L title vs 1 l field -> equal -> no flag.
    row = _row(category="Grocery > Beverages", brand="Halcyon",
               title="Pressed Juice 1 L Bottle", size="1", size_unit="l",
               description="Cold pressed juice bottled in recyclable glass, no added sugar.")
    assert "TITLE_SIZE_MISMATCH" not in _codes(row)


def test_title_size_multipack_matches_total():
    # 2 x 250 g with a 500 g field -> total matches -> no flag.
    row = _row(category="Grocery > Snacks", brand="SnackyBox",
               title="Trail Mix 2x250g Pouches", size="500", size_unit="g",
               description="Trail mix of nuts and seeds in resealable pouches.")
    assert "TITLE_SIZE_MISMATCH" not in _codes(row)


def test_desc_pack_contradiction():
    row = _row(category="Grocery > Snacks", brand="SnackyBox",
               title="Roasted Salted Almonds", size="500", size_unit="g",
               description="Pack of three resealable pouches of roasted almonds.")
    assert "DESC_CONTRADICTION" in _codes(row)


def test_desc_pack_agreement_is_clean():
    row = _row(category="Grocery > Beverages", brand="Meraki",
               title="Cold Brew Coffee Set of 2", size="250", size_unit="ml",
               description="Set of two 250 ml bottles of unsweetened cold brew coffee.")
    assert "DESC_CONTRADICTION" not in _codes(row)


def test_desc_material_contradiction():
    row = _row(category="Fashion > Apparel", brand="Marlow",
               title="Linen Summer Trousers", size="L", size_unit="L",
               description="One hundred percent polyester trousers with a tapered leg.")
    assert "DESC_CONTRADICTION" in _codes(row)


def test_desc_material_multiword_longest_first():
    # "stainless steel" vs "carbon steel" are disjoint; "steel" alone must not
    # make them intersect.
    row = _row(category="Home > Cookware", brand="Copperline",
               title="Stainless Steel Frying Pan", size="28", size_unit="cm",
               description="Carbon steel skillet with a wooden handle, season before use.")
    assert "DESC_CONTRADICTION" in _codes(row)


def test_desc_colour_contradiction():
    row = _row(category="Fashion > Footwear", brand="Alpen",
               title="Black Leather Chelsea Boots", size="EU 42", size_unit="EU 42",
               description="Navy blue full-grain leather boots with elastic side panels.")
    assert "DESC_CONTRADICTION" in _codes(row)


def test_clean_row_has_no_flags():
    assert _codes(_row()) == set()


# ---- learning loop: false positive becomes a targeted exception -----------

def test_false_positive_correction_suppresses_same_brand():
    row = _row(brand="Noctis", title="Noctis Midnight Oud Spray")
    assert "TITLE_BRAND_MISMATCH" in _codes(row)
    corrections = [{
        "fields": row["fields"], "human_verdict": "false_positive",
        "flag_code": "TITLE_BRAND_MISMATCH", "note": "",
    }]
    res = check_rules([row], corrections)
    assert res["row_flags"][row["row_id"]] == []


def test_confirmed_error_correction_does_not_suppress():
    row = _row(brand="Noctis", title="Noctis Midnight Oud Spray")
    corrections = [{
        "fields": row["fields"], "human_verdict": "confirmed_error",
        "flag_code": "TITLE_BRAND_MISMATCH", "note": "",
    }]
    res = check_rules([row], corrections)
    assert {f["code"] for f in res["row_flags"][row["row_id"]]} == {"TITLE_BRAND_MISMATCH"}


# ---- documented limitations (deliberate, committed to the holdout) --------

@pytest.mark.xfail(strict=True, reason="open-ended reasoning is out of scope; "
                                       "needs the LLM engine")
def test_open_ended_contradiction_is_a_known_miss():
    row = _row(category="Electronics > Audio", brand="Beacon",
               title="Waterproof Bluetooth Speaker", size="", size_unit="",
               description="This speaker is not water resistant and must stay dry.")
    assert "DESC_CONTRADICTION" in _codes(row)


@pytest.mark.xfail(strict=True, reason="a product weight in a volume category's "
                                       "title is a known false-positive class")
def test_product_weight_in_cookware_title_is_a_known_fp():
    row = _row(category="Home > Cookware", brand="Hearthstone",
               title="Cast Iron Dutch Oven 1.2 kg", size="5", size_unit="l",
               description="Enamelled cast iron dutch oven suitable for oven and induction.")
    # The rule WILL flag this; the xfail documents that we accept it rather
    # than tune it away against the holdout.
    assert _codes(row) == set()
