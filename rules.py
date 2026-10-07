"""Deterministic semantic checks — the LLM-free engine.

`check_rules(rows, corrections)` returns exactly the shape of
`semantic.check_semantic`, so the pipeline, metrics, export and UI accept it
unchanged. The only addition to each flag is `engine: "rules"`.

The four codes are the same four the model produced:

    TITLE_BRAND_MISMATCH   brand duplicated into the title (brand_policy=forbid)
    UNIT_IMPLAUSIBLE       a title/description unit, or a size value, that the
                           category can't carry
    TITLE_SIZE_MISMATCH    a quantity in the title that the size fields contradict
    DESC_CONTRADICTION     pack count, quantity, material or colour in the
                           description that contradicts the title/fields

What the LLM did and this does NOT: open-ended reasoning over free text
("waterproof" vs "not water resistant"). That is a deliberate cut, measured
honestly against the committed holdout batch rather than hidden.

Learning loop: a `false_positive` correction becomes a targeted exception,
keyed per code (normalised brand / category+unit / gtin-or-title / term pair).
This is narrower than the LLM path, which generalises the pattern from the
same correction placed in its prompt. The README states that tradeoff.
"""

import re
import unicodedata

from schema_def import CATEGORIES

ALLOWED_CODES = {
    "TITLE_BRAND_MISMATCH",
    "TITLE_SIZE_MISMATCH",
    "UNIT_IMPLAUSIBLE",
    "DESC_CONTRADICTION",
}

# Confidence per rule branch. With metrics.py thresholds (>=0.75 -> "flag",
# 0.4-0.75 -> "review"), the weaker vocabulary rules land in the review queue.
CONF = {
    "brand_exact": 0.95, "brand_alias": 0.85,
    "unit_dim": 0.9, "unit_range": 0.7,
    "size_mismatch": 0.9,
    "desc_pack": 0.8, "desc_quantity": 0.7, "desc_material": 0.6, "desc_colour": 0.55,
}

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# base units: volume -> ml, mass -> g, length -> cm
UNIT_DIM = {
    "ml": "volume", "l": "volume", "cl": "volume",
    "g": "mass", "kg": "mass", "mg": "mass", "oz": "mass", "lb": "mass",
    "cm": "length", "mm": "length", "m": "length",
}
UNIT_BASE = {
    "ml": 1.0, "l": 1000.0, "cl": 10.0,
    "g": 1.0, "kg": 1000.0, "mg": 0.001, "oz": 28.3495, "lb": 453.592,
    "cm": 1.0, "mm": 0.1, "m": 100.0,
}
_UNIT_ALT = sorted(UNIT_DIM.keys(), key=len, reverse=True)  # longest first (kg before g)
_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(_UNIT_ALT) + r")\b",
    re.IGNORECASE,
)

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "single": 1, "pair": 2, "dozen": 12,
}
_PACK_PATTERNS = [
    re.compile(r"\bpack\s+of\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bset\s+of\s+(\w+)", re.IGNORECASE),
    re.compile(r"\b(\d+)\s*[-– ]?\s*pack\b", re.IGNORECASE),
    re.compile(r"\b(\d+)\s*x\s*\d", re.IGNORECASE),   # the N in "2x250g"
    re.compile(r"\bx\s*(\d+)\b", re.IGNORECASE),
]


def _to_number(tok: str):
    tok = tok.strip().lower()
    if tok.isdigit():
        return int(tok)
    return NUMBER_WORDS.get(tok)


def find_units(text: str):
    """Return [(value, unit, dim, base_value), ...] for every number+unit token."""
    out = []
    for m in _QTY_RE.finditer(text or ""):
        raw = m.group(1).replace(",", ".")
        unit = m.group(2).lower()
        try:
            value = float(raw)
        except ValueError:
            continue
        out.append((value, unit, UNIT_DIM[unit], value * UNIT_BASE[unit]))
    return out


def find_pack(text: str):
    """Return a pack/multipack/set count found in `text`, or None."""
    t = text or ""
    for pat in _PACK_PATTERNS:
        m = pat.search(t)
        if m:
            n = _to_number(m.group(1))
            if n and n > 1:
                return n
    return None


# ---------------------------------------------------------------------------
# Brand matching
# ---------------------------------------------------------------------------
# Aliases map a normalised brand to extra normalised forms to also search for.
# Punctuation/accent normalisation already collapses many variants (L'Oreal ->
# "loreal"); this table is the fallback for spellings normalisation misses.
BRAND_ALIASES = {
    "loreal": ["l oreal"],
    "johnson johnson": ["jnj", "j and j"],
}


def _normalise(text: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = t.replace("'", "").replace("’", "")  # possessives: scholl's -> scholls, l'oreal -> loreal
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def _tokens(norm_text: str):
    return norm_text.split()


def _contains_run(hay_tokens, needle_tokens) -> bool:
    """True if needle_tokens appear as a contiguous run inside hay_tokens."""
    if not needle_tokens or len(needle_tokens) > len(hay_tokens):
        return False
    n = len(needle_tokens)
    for i in range(len(hay_tokens) - n + 1):
        if hay_tokens[i:i + n] == needle_tokens:
            return True
    return False


# ---------------------------------------------------------------------------
# Material / colour vocabularies (longest-first so "stainless steel" wins)
# ---------------------------------------------------------------------------
MATERIALS = [
    "stainless steel", "carbon steel", "cast iron",
    "polyester", "cotton", "linen", "merino wool", "merino", "wool", "silk",
    "nylon", "leather", "suede", "denim", "cashmere", "velvet",
    "copper", "ceramic", "stoneware", "porcelain", "bamboo", "glass",
    "aluminium", "aluminum", "plastic", "silicone", "titanium", "brass",
]
COLOURS = [
    "black", "white", "red", "blue", "navy", "green", "grey", "gray",
    "brown", "beige", "pink", "purple", "orange", "yellow", "gold",
    "silver", "cream", "tan", "maroon", "teal", "olive", "burgundy",
]


def _extract_vocab(text: str, vocab) -> set:
    t = " " + _normalise(text) + " "
    found = set()
    for term in vocab:   # vocab is ordered longest-first for materials
        tn = _normalise(term)
        if f" {tn} " in t:
            found.add(tn)
            t = t.replace(f" {tn} ", "  ")  # consume so "steel" inside "carbon steel" isn't double counted
    return found


# ---------------------------------------------------------------------------
# Detectors — each returns (code, field, reason, confidence, supp_key) or None
# ---------------------------------------------------------------------------

def _is_metric_unit(u: str) -> bool:
    """A metric unit is written lowercase in schema_def (ml, l, g, kg, cm).
    Apparel ("L", "M", "XL") and footwear ("EU 42") sizes are not — this guard
    stops "L" (large) colliding with "l" (litre) and "M" with "m" (metre)."""
    return u == u.lower() and u.lower() in UNIT_BASE


def _dims_of_category(category: str) -> set:
    meta = CATEGORIES.get(category)
    if not meta:
        return set()
    return {UNIT_DIM[u.lower()] for u in meta["size_units"] if _is_metric_unit(u)}


def _field_metric(fields):
    """Return (base_value, dim) for the size fields, or None if non-metric.

    Uses the lowercase guard so an apparel size_unit of "M"/"L" is not read as
    metre/litre."""
    unit = (fields.get("size_unit") or "").strip()
    size = (fields.get("size") or "").strip().replace(",", ".")
    if not _is_metric_unit(unit):
        return None
    try:
        return float(size) * UNIT_BASE[unit], UNIT_DIM[unit]
    except ValueError:
        return None


def _detect_brand(fields):
    brand = fields.get("brand", "")
    title = fields.get("title", "")
    nb = _normalise(brand)
    if not nb:
        return None
    title_tokens = _tokens(_normalise(title))
    btoks = _tokens(nb)
    # exact normalised run, or the spaceless form for multi-token brands
    if _contains_run(title_tokens, btoks) or (
        len(btoks) > 1 and "".join(btoks) in title_tokens
    ):
        return ("TITLE_BRAND_MISMATCH", "title",
                f"brand {brand!r} appears in the title; this marketplace stores "
                f"brand separately (duplication).", CONF["brand_exact"], nb)
    for alias in BRAND_ALIASES.get(nb, []):
        at = _tokens(_normalise(alias))
        if _contains_run(title_tokens, at) or "".join(at) in title_tokens:
            return ("TITLE_BRAND_MISMATCH", "title",
                    f"brand {brand!r} (as {alias!r}) appears in the title.",
                    CONF["brand_alias"], nb)
    return None


def _detect_unit(fields):
    category = fields.get("category", "")
    meta = CATEGORIES.get(category)
    if not meta:
        return None
    dims = _dims_of_category(category)
    units = meta["size_units"]

    # (a) a volume/mass unit mentioned in title or description whose dimension
    #     the category can't carry. Length units are never flagged; categories
    #     with no size_units (e.g. Audio) are skipped.
    if units:
        for text, where in ((fields.get("title", ""), "title"),
                            (fields.get("description", ""), "description")):
            for value, unit, dim, _base in find_units(text):
                if dim == "length":
                    continue
                if dim not in dims:
                    return ("UNIT_IMPLAUSIBLE", "size_unit",
                            f"the {where} mentions {int(value) if value.is_integer() else value}"
                            f"{unit} ({dim}), which is implausible for {category}.",
                            CONF["unit_dim"], f"{category}|{unit}")

    # (b) size value outside the category's range. Only applied to single-unit
    #     categories, where the range's unit is unambiguous — avoids cross-unit
    #     conversion guesswork on mixed-unit categories.
    rng = meta["size_range"]
    metric_units = [u for u in units if _is_metric_unit(u)]
    if rng and len(metric_units) == 1:
        fm = _field_metric(fields)
        if fm is not None:
            value_base, _dim = fm
            lo, hi = rng  # expressed in that single unit's own scale
            scale = UNIT_BASE[metric_units[0].lower()]
            value_in_unit = value_base / scale
            if value_in_unit < lo or value_in_unit > hi:
                return ("UNIT_IMPLAUSIBLE", "size",
                        f"size {value_in_unit:g}{metric_units[0]} is outside the "
                        f"plausible range {lo}-{hi}{metric_units[0]} for {category}.",
                        CONF["unit_range"], f"{category}|range")
    return None


def _match_qty(candidate_base, field_base, pack):
    tol = max(0.5, 0.01 * max(candidate_base, field_base))
    if abs(candidate_base - field_base) <= tol:
        return True
    if pack and abs(candidate_base * pack - field_base) <= tol:
        return True
    return False


def _detect_title_size(fields, unit_flagged_dim):
    fm = _field_metric(fields)
    if fm is None:
        return None
    field_base, field_dim = fm
    title = fields.get("title", "")
    pack = find_pack(title)
    same_dim = [u for u in find_units(title)
                if u[2] == field_dim and u[2] != unit_flagged_dim]
    if not same_dim:
        return None
    if any(_match_qty(u[3], field_base, pack) for u in same_dim):
        return None
    v, unit, _dim, _b = same_dim[0]
    gtin = fields.get("gtin", "").strip()
    key = gtin or _normalise(title)
    return ("TITLE_SIZE_MISMATCH", "size",
            f"the title says {int(v) if v.is_integer() else v}{unit} but the size "
            f"fields say {fields.get('size','')} {fields.get('size_unit','')}.",
            CONF["size_mismatch"], key)


def _detect_desc(fields):
    title = fields.get("title", "")
    desc = fields.get("description", "")
    gtin = fields.get("gtin", "").strip()
    key_base = gtin or _normalise(title)

    # pack
    title_pack = find_pack(title) or 1
    desc_pack = find_pack(desc)
    if desc_pack and desc_pack != title_pack:
        return ("DESC_CONTRADICTION", "description",
                f"the description says {desc_pack} items but the title implies "
                f"{title_pack}.", CONF["desc_pack"], f"pack:{title_pack}:{desc_pack}")

    # quantity in description vs size fields
    fm = _field_metric(fields)
    if fm is not None:
        field_base, field_dim = fm
        dp = desc_pack or 1
        dq = [u for u in find_units(desc) if u[2] == field_dim]
        if dq and not any(_match_qty(u[3], field_base, dp) for u in dq):
            v, unit, _d, _b = dq[0]
            return ("DESC_CONTRADICTION", "description",
                    f"the description states {int(v) if v.is_integer() else v}{unit}, "
                    f"which contradicts the size fields.", CONF["desc_quantity"], key_base)

    # material
    tm = _extract_vocab(title, MATERIALS)
    dm = _extract_vocab(desc, MATERIALS)
    if tm and dm and tm.isdisjoint(dm):
        pair = " vs ".join([",".join(sorted(tm)), ",".join(sorted(dm))])
        return ("DESC_CONTRADICTION", "description",
                f"the title material ({', '.join(sorted(tm))}) differs from the "
                f"description ({', '.join(sorted(dm))}).", CONF["desc_material"],
                f"material:{pair}")

    # colour
    tc = _extract_vocab(title, COLOURS)
    dc = _extract_vocab(desc, COLOURS)
    if tc and dc and tc.isdisjoint(dc):
        pair = " vs ".join([",".join(sorted(tc)), ",".join(sorted(dc))])
        return ("DESC_CONTRADICTION", "description",
                f"the title colour ({', '.join(sorted(tc))}) differs from the "
                f"description ({', '.join(sorted(dc))}).", CONF["desc_colour"],
                f"colour:{pair}")
    return None


def _detect_all(fields):
    """Run every detector; return a list of (code, field, reason, conf, key)."""
    results = []
    brand = _detect_brand(fields)
    if brand:
        results.append(brand)
    unit = _detect_unit(fields)
    if unit:
        results.append(unit)
    unit_flagged_dim = None
    if unit and unit[0] == "UNIT_IMPLAUSIBLE" and unit[1] == "size_unit":
        # the dim of the title unit that fired (a); skip it in title-size
        m = re.search(r"\|(\w+)$", unit[4])
        if m and m.group(1) in UNIT_DIM:
            unit_flagged_dim = UNIT_DIM[m.group(1)]
    size = _detect_title_size(fields, unit_flagged_dim)
    if size:
        results.append(size)
    desc = _detect_desc(fields)
    if desc:
        results.append(desc)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _has_schema_reject(row: dict) -> bool:
    return any(
        f.get("layer") == "schema"
        and f.get("severity") == "reject"
        and not f.get("auto_fixed")
        for f in row.get("flags", [])
    )


def _make_flag(code, field, reason, confidence):
    return {
        "layer": "semantic",
        "code": code,
        "field": field,
        "severity": "warn",
        "auto_fixed": False,
        "fix": None,
        "reason": reason,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "engine": "rules",
    }


def _build_suppressions(corrections) -> dict:
    """From false_positive corrections, recompute the (code, key) each would
    have produced and collect them. Running the SAME detectors here guarantees
    the key matches what a live row produces."""
    suppressed: dict[str, set] = {c: set() for c in ALLOWED_CODES}
    for c in corrections or []:
        if c.get("human_verdict") != "false_positive":
            continue
        code = c.get("flag_code")
        if code not in ALLOWED_CODES:
            continue
        fields = c.get("fields") or {}
        for rc, _field, _reason, _conf, key in _detect_all(fields):
            if rc == code:
                suppressed[code].add(key)
    return suppressed


def check_rules(rows, corrections) -> dict:
    """LLM-free semantic layer. Same return shape as semantic.check_semantic."""
    suppressed = _build_suppressions(corrections)
    row_flags = {r["row_id"]: [] for r in rows}
    eligible = [r for r in rows if not _has_schema_reject(r)]
    for row in eligible:
        flags = []
        for code, field, reason, conf, key in _detect_all(row["fields"]):
            if key in suppressed.get(code, set()):
                continue
            flags.append(_make_flag(code, field, reason, conf))
        row_flags[row["row_id"]] = flags
    return {
        "row_flags": row_flags,
        "status": "ok",
        "error_detail": None,
        "batches_run": 1 if eligible else 0,
        "batches_failed": 0,
    }
