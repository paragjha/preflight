"""GS1 GTIN validation and generation. Pure functions, no I/O."""

_VALID_LENGTHS = {8, 12, 13, 14}


def _checksum_digit(digits_no_check: str) -> int:
    """Compute the GS1 mod-10 check digit for a numeric string of length 7/11/12/13.

    Pads to 13 on the left, then applies the alternating 3/1 weights from left.
    """
    padded = digits_no_check.rjust(13, "0")
    total = 0
    for i, ch in enumerate(padded):
        weight = 3 if i % 2 == 0 else 1
        total += int(ch) * weight
    return (10 - total % 10) % 10


def is_valid_gtin(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s.isdigit():
        return False
    if len(s) not in _VALID_LENGTHS:
        return False
    expected = _checksum_digit(s[:-1])
    return expected == int(s[-1])


def make_valid_gtin(prefix12: str) -> str:
    """Given a 12-digit numeric prefix, return the full 13-digit GTIN-13 with check digit."""
    if not (isinstance(prefix12, str) and prefix12.isdigit() and len(prefix12) == 12):
        raise ValueError("prefix12 must be a 12-digit numeric string")
    return prefix12 + str(_checksum_digit(prefix12))
