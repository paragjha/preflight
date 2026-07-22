"""Deterministic schema layer: required, format/type, enums, GTIN, size, SKU.

`check_schema(row, region, counters=None) -> list[Flag]` — safe autofixes are
applied in-place on `row["fields"]`; unsafe issues are left as Flag records
for a human. Flags are also appended to `row["flags"]`.
"""

import json
import os
import re

from gtin import is_valid_gtin
from schema_def import (
    CATEGORIES,
    DESCRIPTION_MAX_LEN,
    DESCRIPTION_MIN_LEN,
    FIELDS,
    IMAGE_EXTENSIONS,
    SKU_PATTERN,
    TITLE_CAPS_ACRONYM_MAX_LEN,
    TITLE_FORBIDDEN_CHARS,
    TITLE_MAX_LEN,
    TITLE_MIN_LEN,
    TITLE_RULES,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SKU_COUNTERS_PATH = os.path.join(DATA_DIR, "sku_counters.json")

URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
SKU_RE = re.compile(r"^([A-Z]{2})-(\d{6})$")
DECIMAL_COMMA_RE = re.compile(r"^\d+,\d+$")


def _make_flag(code, field, severity, reason, auto_fixed=False, fix=None):
    return {
        "layer": "schema",
        "code": code,
        "field": field,
        "severity": severity,
        "auto_fixed": auto_fixed,
        "fix": fix,
        "reason": reason,
        "confidence": 1.0,
    }


def _load_counters():
    if not os.path.exists(SKU_COUNTERS_PATH):
        return {}
    try:
        with open(SKU_COUNTERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_counters(counters):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SKU_COUNTERS_PATH, "w", encoding="utf-8") as f:
        json.dump(counters, f, indent=2)


def _next_sku(region, counters):
    n = counters.get(region, 0) + 1
    counters[region] = n
    return SKU_PATTERN.format(region=region, seq=n)


def _rulepack_required(rulepack):
    """Map field_name → required from a rulepack, if provided."""
    if rulepack:
        return {f["name"]: bool(f.get("required")) for f in rulepack.get("fields", [])}
    return None


def _rulepack_categories(rulepack):
    """Categories dict from rulepack if it declared one, else built-in."""
    if rulepack and rulepack.get("categories"):
        return rulepack["categories"]
    return CATEGORIES


def check_schema(row, region, counters=None, rulepack=None):
    """Run deterministic checks and safe autofixes on a single row.

    `counters` is a dict mapping region -> last-used-seq. If omitted, state
    is loaded from disk, mutated, and saved. Tests pass an empty dict to
    keep runs isolated.

    `rulepack` (v2): optional tenant-specific rulepack overriding required
    flags, category enum, and per-category size_unit lists. See
    `template.py`. When None, the built-in `FIELDS`/`CATEGORIES` are used
    (v1 behaviour).
    """
    if counters is None:
        counters = _load_counters()
        save_after = True
    else:
        save_after = False

    required_override = _rulepack_required(rulepack)
    categories_source = _rulepack_categories(rulepack)

    fields = row["fields"]
    flags: list[dict] = []

    # ---- Whitespace trim (all string fields) --------------------------------
    for k, v in list(fields.items()):
        if isinstance(v, str) and v != v.strip():
            fields[k] = v.strip()

    # ---- Brand title-case autofix -------------------------------------------
    brand = fields.get("brand", "")
    if brand:
        titled = brand.title()
        if titled != brand:
            flags.append(_make_flag(
                "OTHER", "brand", "warn",
                f"brand casing normalised to {titled!r}",
                auto_fixed=True, fix={"old": brand, "new": titled},
            ))
            fields["brand"] = titled

    # ---- Decimal comma autofix on size --------------------------------------
    size_raw = fields.get("size", "")
    if size_raw and DECIMAL_COMMA_RE.match(size_raw):
        new_size = size_raw.replace(",", ".")
        flags.append(_make_flag(
            "OTHER", "size", "warn",
            "decimal comma normalised to dot",
            auto_fixed=True, fix={"old": size_raw, "new": new_size},
        ))
        fields["size"] = new_size

    # ---- Required fields (rulepack override wins over built-in FIELDS) -----
    for name, required, _ in FIELDS:
        req = required_override.get(name, required) if required_override else required
        if req and not fields.get(name, "").strip():
            flags.append(_make_flag(
                "MISSING_FIELD", name, "reject",
                f"required field {name!r} is empty",
            ))

    # ---- Category enum (rulepack categories dict wins if it declares one) --
    category = fields.get("category", "")
    category_valid = category in categories_source
    if category and not category_valid:
        flags.append(_make_flag(
            "BAD_ENUM", "category", "reject",
            f"category {category!r} is not one of the allowed values",
        ))

    # ---- Title length + style rules ----------------------------------------
    title = fields.get("title", "")
    if title:
        if len(title) < TITLE_MIN_LEN:
            flags.append(_make_flag(
                "BAD_FORMAT", "title", "reject",
                f"title is only {len(title)} chars; minimum is {TITLE_MIN_LEN}",
            ))
        elif len(title) > TITLE_MAX_LEN:
            flags.append(_make_flag(
                "BAD_FORMAT", "title", "reject",
                f"title is {len(title)} chars; maximum is {TITLE_MAX_LEN}",
            ))
        if TITLE_RULES.get("no_special_chars"):
            bad = sorted({c for c in title if c in TITLE_FORBIDDEN_CHARS})
            if bad:
                flags.append(_make_flag(
                    "BAD_FORMAT", "title", "warn",
                    f"title contains disallowed characters: {''.join(bad)}",
                ))
        if TITLE_RULES.get("no_all_caps"):
            offenders = [t for t in title.split()
                         if len(t) > TITLE_CAPS_ACRONYM_MAX_LEN
                         and t.isalpha() and t.isupper()]
            if offenders:
                flags.append(_make_flag(
                    "BAD_FORMAT", "title", "warn",
                    f"title uses ALL CAPS words: {', '.join(offenders)}",
                ))
        if TITLE_RULES.get("no_repeated_words"):
            words = [w.lower() for w in re.findall(r"[A-Za-z]+", title)]
            seen, dupes = set(), set()
            for w in words:
                if w in seen:
                    dupes.add(w)
                seen.add(w)
            if dupes:
                flags.append(_make_flag(
                    "BAD_FORMAT", "title", "warn",
                    f"title has repeated words: {', '.join(sorted(dupes))}",
                ))

    # ---- Description length -------------------------------------------------
    desc = fields.get("description", "")
    if desc:
        if len(desc) < DESCRIPTION_MIN_LEN:
            flags.append(_make_flag(
                "BAD_FORMAT", "description", "reject",
                f"description is only {len(desc)} chars; minimum is {DESCRIPTION_MIN_LEN}",
            ))
        elif len(desc) > DESCRIPTION_MAX_LEN:
            flags.append(_make_flag(
                "BAD_FORMAT", "description", "reject",
                f"description is {len(desc)} chars; maximum is {DESCRIPTION_MAX_LEN}",
            ))

    # ---- URL fields ---------------------------------------------------------
    for url_field in ("image_url", "video_url"):
        url = fields.get(url_field, "")
        if not url:
            continue
        if not URL_RE.match(url):
            flags.append(_make_flag(
                "BAD_FORMAT", url_field, "reject",
                f"{url_field} is not a valid http(s) URL",
            ))
            continue
        if url_field == "image_url":
            lower = url.lower().split("?")[0]
            if not lower.endswith(IMAGE_EXTENSIONS):
                flags.append(_make_flag(
                    "BAD_FORMAT", url_field, "reject",
                    f"image_url extension must be one of {', '.join(IMAGE_EXTENSIONS)}",
                ))

    # ---- GTIN format then checksum -----------------------------------------
    gtin = fields.get("gtin", "").strip()
    if gtin:
        if not gtin.isdigit() or len(gtin) not in {8, 12, 13, 14}:
            flags.append(_make_flag(
                "BAD_FORMAT", "gtin", "reject",
                f"GTIN {gtin!r} is not 8/12/13/14 digits",
            ))
        elif not is_valid_gtin(gtin):
            flags.append(_make_flag(
                "GTIN_CHECKSUM", "gtin", "reject",
                f"GTIN {gtin!r} fails GS1 check digit",
            ))

    # ---- Category-dependent: size_unit enum + size required ---------------
    if category_valid:
        allowed_units = categories_source[category]["size_units"]
        size_unit = fields.get("size_unit", "")
        size_val = fields.get("size", "")
        if allowed_units:
            if not size_val:
                flags.append(_make_flag(
                    "MISSING_FIELD", "size", "reject",
                    f"category {category!r} requires a size",
                ))
            if not size_unit:
                flags.append(_make_flag(
                    "MISSING_FIELD", "size_unit", "reject",
                    f"category {category!r} requires a size_unit",
                ))
            else:
                # Case-insensitive match against the category's allowed set;
                # canonicalise casing on match (safe autofix).
                canonical = next(
                    (u for u in allowed_units if u.lower() == size_unit.lower()),
                    None,
                )
                if canonical is None:
                    flags.append(_make_flag(
                        "BAD_ENUM", "size_unit", "reject",
                        f"size_unit {size_unit!r} is not valid for {category!r} "
                        f"(allowed: {', '.join(allowed_units)})",
                    ))
                elif canonical != size_unit:
                    flags.append(_make_flag(
                        "OTHER", "size_unit", "warn",
                        f"size_unit casing normalised to {canonical!r}",
                        auto_fixed=True,
                        fix={"old": size_unit, "new": canonical},
                    ))
                    fields["size_unit"] = canonical
        else:
            if size_val or size_unit:
                flags.append(_make_flag(
                    "BAD_ENUM", "size_unit", "warn",
                    f"category {category!r} does not use size fields",
                ))

    # ---- Partner SKU (region-aware) ----------------------------------------
    sku = fields.get("partner_sku", "")
    if not sku:
        new_sku = _next_sku(region, counters)
        flags.append(_make_flag(
            "OTHER", "partner_sku", "warn",
            f"partner_sku autogenerated as {new_sku!r}",
            auto_fixed=True, fix={"old": "", "new": new_sku},
        ))
        fields["partner_sku"] = new_sku
    else:
        m = SKU_RE.match(sku)
        if not m:
            flags.append(_make_flag(
                "BAD_FORMAT", "partner_sku", "warn",
                f"partner_sku {sku!r} does not match the house pattern",
            ))
        else:
            prefix = m.group(1)
            if prefix != region:
                flags.append(_make_flag(
                    "REGION_MISMATCH", "partner_sku", "reject",
                    f"partner_sku {sku!r} is region {prefix!r} "
                    f"but batch region is {region!r}",
                ))

    if save_after:
        _save_counters(counters)

    row["flags"] = list(row.get("flags", [])) + flags
    return flags


def check_schema_batch(rows: list[dict], region: str, rulepack: dict | None = None) -> None:
    """Run check_schema across many rows with a single counter load/save,
    then run cross-row checks (v2) — GTIN and partner_sku uniqueness within
    the batch.

    `rulepack` (v2): a tenant-specific rulepack loaded once and passed to
    every row-level call. Skipping the disk round-trip on the counter is the
    other reason this helper exists.

    Cross-row checks run AFTER per-row checks so auto-generated partner_skus
    are already in place and can be tested for accidental collisions.
    """
    counters = _load_counters()
    for row in rows:
        check_schema(row, region=region, counters=counters, rulepack=rulepack)
    _save_counters(counters)

    cross_row_flags = check_cross_row(rows)
    for row in rows:
        extra = cross_row_flags.get(row["row_id"], [])
        if extra:
            row["flags"] = list(row.get("flags", [])) + extra


def check_cross_row(rows: list[dict]) -> dict[str, list[dict]]:
    """Deterministic cross-row checks.

    Catches the "well-formed, valid-checksum GTIN duplicated from another
    row" class — a real semantic trap the row-local pipeline can't see. Same
    idea for partner_sku collisions inside a single batch.

    Returns {row_id: [Flag, ...]}. Empty flags for clean rows.
    """
    out: dict[str, list[dict]] = {r["row_id"]: [] for r in rows}

    def _dup_check(field_name: str, code: str, severity: str = "reject") -> None:
        seen: dict[str, list[str]] = {}
        for row in rows:
            value = (row["fields"].get(field_name) or "").strip()
            if not value:
                continue
            seen.setdefault(value, []).append(row["row_id"])
        for value, rids in seen.items():
            if len(rids) < 2:
                continue
            for rid in rids:
                others = sorted(r for r in rids if r != rid)
                out[rid].append(_make_flag(
                    code, field_name, severity,
                    f"{field_name} {value!r} is duplicated across "
                    f"{len(rids)} rows in this batch (also on {', '.join(others)})",
                ))

    _dup_check("gtin", "DUPLICATE_GTIN", severity="reject")
    _dup_check("partner_sku", "DUPLICATE_SKU", severity="reject")
    return out
