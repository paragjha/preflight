"""Catalog resolver — does this GTIN already exist, and what's its ZSKU?

The real catalog-ops win: before minting a new SKU, check the catalog by
barcode. If the product already exists, attach an offer to the existing ZSKU
instead of creating a duplicate listing.

No PUBLIC endpoint maps a GTIN to a marketplace's internal ZSKU — that mapping
only lives inside the marketplace. So this module provides the SLOT, not a
baked-in integration, chosen by `CATALOG_RESOLVER`:

    off   (default)  no lookups — the core stays offline and reproducible
    mock             a seeded, deterministic in-repo catalog for the demo
    http             POST each GTIN to a customer-supplied endpoint
                     (CATALOG_RESOLVER_URL, optional CATALOG_RESOLVER_KEY);
                     expects a JSON body {"zsku": "...", "title": "..."} or
                     404 / {"zsku": null} for "not found"

check_catalog(rows) -> dict[row_id, finding]
    finding = {"status": "found"|"new", "zsku": str|None, "title": str|None}

The ZSKU format here (ZSKU-xxxxxxxx) is INVENTED for the demo — deliberately
not any real marketplace's scheme.
"""

import os

RESOLVER_ENV = "CATALOG_RESOLVER"
URL_ENV = "CATALOG_RESOLVER_URL"
KEY_ENV = "CATALOG_RESOLVER_KEY"
DEFAULT_RESOLVER = "off"
VALID_RESOLVERS = {"off", "mock", "http"}

# How often the mock pretends a GTIN already exists (~1 in N). Tuned so the
# 60-row demo surfaces a clear handful of matches, not a wall of them.
_MOCK_HIT_MODULUS = 13


def current_resolver() -> str:
    r = (os.environ.get(RESOLVER_ENV) or DEFAULT_RESOLVER).strip().lower()
    return r if r in VALID_RESOLVERS else DEFAULT_RESOLVER


def _digits(gtin: str) -> str:
    return "".join(c for c in (gtin or "") if c.isdigit())


def _mock_lookup(gtin: str) -> dict | None:
    """Deterministic stand-in for a real catalog. A GTIN 'exists' when its
    numeric value is divisible by the modulus; its ZSKU is derived from the
    GTIN so the same barcode always resolves to the same ZSKU."""
    d = _digits(gtin)
    if len(d) < 8:
        return None
    if int(d) % _MOCK_HIT_MODULUS == 0:
        return {"zsku": "ZSKU-" + d[-8:], "title": None}
    return None


def _http_lookup(gtin: str) -> dict | None:
    """Call the customer's own catalog endpoint. Only reached when
    CATALOG_RESOLVER=http and a URL is configured."""
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        return None
    import httpx
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.post(url, headers=headers, json={"gtin": gtin}, timeout=15.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        zsku = data.get("zsku")
        return {"zsku": zsku, "title": data.get("title")} if zsku else None
    except Exception:
        # A resolver failure must never crash the pipeline — treat as "new".
        return None


def _lookup(gtin: str, mode: str) -> dict | None:
    if mode == "mock":
        return _mock_lookup(gtin)
    if mode == "http":
        return _http_lookup(gtin)
    return None


def check_catalog(rows: list[dict]) -> dict[str, dict]:
    """Resolve every row's GTIN against the catalog. Returns row_id -> finding.
    When the resolver is off, every row is 'new' and no lookup happens."""
    out = {r["row_id"]: {"status": "new", "zsku": None, "title": None} for r in rows}
    mode = current_resolver()
    if mode == "off":
        return out
    for row in rows:
        gtin = (row["fields"].get("gtin") or "").strip()
        if not gtin:
            continue
        hit = _lookup(gtin, mode)
        if hit and hit.get("zsku"):
            out[row["row_id"]] = {"status": "found", "zsku": hit["zsku"], "title": hit.get("title")}
    return out


def make_flag(finding: dict) -> dict:
    """Build the catalog Flag for a 'found' finding (layer: 'catalog')."""
    zsku = finding["zsku"]
    title = finding.get("title")
    reason = (f"GTIN already listed as {zsku}"
              + (f" ('{title}')" if title else "")
              + " — attach an offer instead of creating a new SKU.")
    return {
        "layer": "catalog",
        "code": "ALREADY_IN_CATALOG",
        "field": "gtin",
        "severity": "warn",
        "auto_fixed": False,
        "fix": None,
        "reason": reason,
        "confidence": 1.0,
        "zsku": zsku,
    }
