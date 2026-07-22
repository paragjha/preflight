"""Tests for gtin.py — known-good GS1 GTINs plus a handful of malformed inputs."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gtin import is_valid_gtin, make_valid_gtin


def test_known_good_gtin13():
    # Standard GS1 example (Ferrero Rocher 200g)
    assert is_valid_gtin("4006381333931")


def test_known_good_gtin_variants():
    assert is_valid_gtin("0012345678905")  # GTIN-13
    assert is_valid_gtin("012345678905")   # GTIN-12 (UPC-A)
    assert is_valid_gtin("40170725")       # GTIN-8
    assert is_valid_gtin("10614141000415") # GTIN-14


def test_rejects_wrong_checksum():
    assert not is_valid_gtin("4006381333932")
    assert not is_valid_gtin("0012345678900")


def test_rejects_non_digits():
    assert not is_valid_gtin("40063813339A1")
    assert not is_valid_gtin("abcdefghijklm")
    assert not is_valid_gtin("")


def test_rejects_wrong_length():
    assert not is_valid_gtin("123456")           # 6 digits
    assert not is_valid_gtin("1234567890")       # 10 digits
    assert not is_valid_gtin("123456789012345")  # 15 digits


def test_rejects_non_string():
    assert not is_valid_gtin(1234567890123)  # type: ignore[arg-type]
    assert not is_valid_gtin(None)           # type: ignore[arg-type]


def test_make_valid_gtin_is_valid():
    for prefix in ["400638133393", "012345678901", "999888777666"]:
        full = make_valid_gtin(prefix)
        assert len(full) == 13
        assert is_valid_gtin(full)


def test_make_valid_gtin_matches_known():
    assert make_valid_gtin("400638133393") == "4006381333931"


def test_make_valid_gtin_rejects_bad_input():
    import pytest
    with pytest.raises(ValueError):
        make_valid_gtin("12345")
    with pytest.raises(ValueError):
        make_valid_gtin("40063813339A")
    with pytest.raises(ValueError):
        make_valid_gtin("4006381333930")  # 13 digits — wrong length for prefix12
