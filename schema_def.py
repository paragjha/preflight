"""Upload template definition: categories, fields, regions, SKU pattern, title rules.

Generic marketplace template modelled on public conventions (Amazon/Shopify feed
style + GS1). Not tied to any specific marketplace's internal template.
"""

CATEGORIES = {
    "Beauty > Fragrance":  {"size_units": ["ml"],          "size_range": (5, 500)},
    "Beauty > Skincare":   {"size_units": ["ml", "g"],     "size_range": (5, 1000)},
    "Grocery > Beverages": {"size_units": ["ml", "l"],     "size_range": (100, 5000)},
    "Grocery > Snacks":    {"size_units": ["g", "kg"],     "size_range": (10, 5000)},
    "Fashion > Apparel":   {"size_units": ["XS", "S", "M", "L", "XL", "XXL"], "size_range": None},
    "Fashion > Footwear":  {"size_units": ["EU 36", "EU 38", "EU 40", "EU 42", "EU 44"], "size_range": None},
    "Electronics > Audio": {"size_units": [],              "size_range": None},
    "Home > Cookware":     {"size_units": ["cm", "l"],     "size_range": (1, 100)},
}

FIELDS = [
    # (name,          required, type)
    ("category",      True,  "enum:CATEGORIES"),
    ("brand",         True,  "str"),
    ("title",         True,  "str"),
    ("gtin",          True,  "gtin"),
    ("size",          False, "str"),
    ("size_unit",     False, "str"),
    ("image_url",     True,  "url"),
    ("video_url",     False, "url"),
    ("description",   True,  "str"),
    ("partner_sku",   False, "str"),
]

# Region is NOT a sheet column. Batch-level setting chosen at upload time
# (mirrors real cataloging tools). Passed as a form field on POST /api/batches.
REGIONS = ["AE", "SA", "EG", "IN"]

# Partner SKU house convention — INVENTED for the demo. Format: "<REGION>-<6-digit-seq>".
SKU_PATTERN = "{region}-{seq:06d}"

# Title rules — modelled on publicly documented marketplace conventions.
# brand_policy is configuration: "forbid" (brand attribute is stored separately
# and concatenated at display time — so duplicating it in the title is noise) or
# "require" (brand must lead the title, Amazon-style).
TITLE_RULES = {
    "min_len": 5,
    "max_len": 200,
    "brand_policy": "forbid",
    "no_special_chars": True,
    "no_all_caps": True,
    "no_repeated_words": True,
}

# Characters disallowed in titles when no_special_chars is on.
TITLE_FORBIDDEN_CHARS = set("@^*#&~`|<>{}[]\\")

# Short tokens allowed to be ALL CAPS (acronyms).
TITLE_CAPS_ACRONYM_MAX_LEN = 4

DESCRIPTION_MIN_LEN = 30
DESCRIPTION_MAX_LEN = 3000

TITLE_MIN_LEN = TITLE_RULES["min_len"]
TITLE_MAX_LEN = TITLE_RULES["max_len"]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
