"""Generate the demo batch xlsx plus answer-key JSON.

Produces exactly 60 rows: 30 clean, 18 schema-broken, 12 semantic-trap.
Column headers in the xlsx are deliberately messy to exercise the ingest
synonym mapper. The answer key is written alongside for honesty auditing
only — the pipeline never reads it.
"""

import json
import os
import random

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from gtin import make_valid_gtin
from schema_def import CATEGORIES, FIELDS

random.seed(42)

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(OUT_DIR, "demo_batch.xlsx")
ANSWER_KEY_PATH = os.path.join(OUT_DIR, "demo_batch.answer_key.json")
TEMPLATE_PATH = os.path.join(OUT_DIR, "demo_template.xlsx")

MESSY_HEADERS = [
    "Category",
    "Brand Name",
    "product title",
    "EAN/GTIN",
    "Size",
    "Size Unit ",
    "Image Link",
    "Video URL",
    "Description",
    "Partner SKU",
]

CANONICAL_KEYS = [
    "category",
    "brand",
    "title",
    "gtin",
    "size",
    "size_unit",
    "image_url",
    "video_url",
    "description",
    "partner_sku",
]


def _fresh_gtin() -> str:
    prefix = "".join(str(random.randint(0, 9)) for _ in range(12))
    return make_valid_gtin(prefix)


def _mangled_checksum_gtin() -> str:
    good = _fresh_gtin()
    last = int(good[-1])
    bad = (last + 1) % 10
    if bad == last:
        bad = (last + 2) % 10
    return good[:-1] + str(bad)


def _img(slug: str) -> str:
    return f"https://cdn.example.com/img/{slug}.jpg"


CLEAN_PRODUCTS = [
    # Beauty > Fragrance (4)
    ("Beauty > Fragrance", "Elysian", "Aqua Bloom Eau de Parfum", "100", "ml",
     "Fresh citrus and jasmine top notes fade into a warm amber base. Long-lasting spray for daily wear."),
    ("Beauty > Fragrance", "Noctis", "Midnight Musk Eau de Toilette", "50", "ml",
     "Deep woody musk with hints of vanilla and oud. Designed for evening wear and cooler seasons."),
    ("Beauty > Fragrance", "Vireo", "Green Fig Perfume Spray", "75", "ml",
     "Crisp fig leaf and cedar accord in an alcohol-based spray. Comes in a matte glass bottle."),
    ("Beauty > Fragrance", "Aureum", "Golden Rose Travel Atomizer", "30", "ml",
     "Rose absolute layered with sandalwood in a compact travel-friendly atomizer."),
    # Beauty > Skincare (4)
    ("Beauty > Skincare", "PurePeak", "Hydrating Facial Serum", "30", "ml",
     "Hyaluronic acid serum for daily hydration. Lightweight, fragrance-free formula suitable for sensitive skin."),
    ("Beauty > Skincare", "Loraya", "Vitamin C Brightening Cream", "50", "g",
     "Vitamin C and niacinamide cream to even skin tone. Absorbs quickly with a matte finish."),
    ("Beauty > Skincare", "Botanix", "Green Tea Cleansing Gel", "150", "ml",
     "Gentle daily cleanser with green tea extract. Removes makeup and impurities without stripping the skin."),
    ("Beauty > Skincare", "Zenith", "Retinol Night Cream", "50", "g",
     "Overnight retinol treatment to smooth fine lines. Use every other night; apply sunscreen the following morning."),
    # Grocery > Beverages (4)
    ("Grocery > Beverages", "Fjord", "Sparkling Mineral Water Bottle", "500", "ml",
     "Naturally carbonated mineral water sourced from mountain springs. Sold in a recyclable glass bottle."),
    ("Grocery > Beverages", "Meraki", "Cold Brew Coffee", "250", "ml",
     "Slow-steeped Arabica cold brew, unsweetened. Serve chilled over ice or with milk."),
    ("Grocery > Beverages", "Tinca", "Organic Iced Green Tea Carton", "1", "l",
     "Unsweetened brewed green tea in a Tetra Pak carton. Ready to drink, no preservatives added."),
    ("Grocery > Beverages", "Halcyon", "Pressed Pomegranate Juice", "750", "ml",
     "Pressed pomegranate juice with no added sugar. Rich source of antioxidants."),
    # Grocery > Snacks (4)
    ("Grocery > Snacks", "Crumble Co", "Salted Caramel Shortbread Cookies", "200", "g",
     "Handmade shortbread cookies with a caramel drizzle and sea salt finish. Contains gluten and dairy."),
    ("Grocery > Snacks", "SnackyBox", "Roasted Salted Almonds", "500", "g",
     "Whole almonds roasted with sea salt. High-protein snack packed in a resealable pouch."),
    ("Grocery > Snacks", "Grainful", "Multigrain Seed Crackers", "150", "g",
     "Baked crackers made from oats, rye and flaxseed. Pairs well with cheese and dips."),
    ("Grocery > Snacks", "Twiggle", "Dark Chocolate Bar Single Origin", "100", "g",
     "70 percent cocoa dark chocolate bar, single origin. Vegan and gluten free."),
    # Fashion > Apparel (4)
    ("Fashion > Apparel", "Everweave", "Cotton Crew Neck T-Shirt", "M", "M",
     "Classic short-sleeve tee in soft combed cotton. Regular fit with reinforced neckline."),
    ("Fashion > Apparel", "Stanton", "Slim Fit Denim Jeans", "L", "L",
     "Mid-rise slim fit jeans in stretch denim. Five-pocket styling with metal rivet reinforcements."),
    ("Fashion > Apparel", "Kestrel", "Merino Wool Sweater", "S", "S",
     "Fine gauge merino wool crewneck sweater. Machine washable on the wool cycle."),
    ("Fashion > Apparel", "Marlow", "Linen Button-Down Shirt", "XL", "XL",
     "Lightweight linen shirt with a relaxed fit. Ideal for warm weather layering."),
    # Fashion > Footwear (3)
    ("Fashion > Footwear", "Traverse", "Trail Running Shoes", "EU 42", "EU 42",
     "Cushioned trail runners with a grippy outsole and breathable mesh upper. Suitable for mixed terrain."),
    ("Fashion > Footwear", "Alpen", "Leather Chelsea Boots", "EU 40", "EU 40",
     "Full-grain leather boots with elastic side panels and pull tabs. Rubber sole with light tread."),
    ("Fashion > Footwear", "Cadence", "Canvas Low-Top Sneakers", "EU 38", "EU 38",
     "Low-top canvas sneakers with vulcanised rubber sole. Lace-up front with reinforced eyelets."),
    # Electronics > Audio (4)
    ("Electronics > Audio", "Sonora", "Wireless Over-Ear Headphones", "", "",
     "Bluetooth 5.3 headphones with active noise cancellation and thirty hour battery life. USB-C fast charging."),
    ("Electronics > Audio", "Beacon", "Portable Waterproof Bluetooth Speaker", "", "",
     "Waterproof portable speaker with twelve hour battery life. Supports voice assistants and stereo pairing."),
    ("Electronics > Audio", "Auralis", "In-Ear Sport Earbuds", "", "",
     "Sweat resistant earbuds with over-ear hooks for a secure fit. Includes charging case and three eartip sizes."),
    ("Electronics > Audio", "Rythmix", "Studio Monitor Speaker Pair", "", "",
     "Powered near-field studio monitors with balanced XLR and TRS inputs. Sold as a matched pair."),
    # Home > Cookware (3)
    ("Home > Cookware", "Copperline", "Copper Frying Pan Stainless Interior", "28", "cm",
     "Tri-ply copper frying pan with stainless steel interior. Riveted stay-cool handle, oven-safe to 220 degrees."),
    ("Home > Cookware", "Hearthstone", "Cast Iron Enamelled Dutch Oven", "5", "l",
     "Enamelled cast iron dutch oven with tight-fitting lid. Suitable for stovetop, oven and induction."),
    ("Home > Cookware", "Ironclad", "Carbon Steel Non-Stick Wok", "30", "cm",
     "Carbon steel wok with ceramic non-stick coating. Wooden handle, flat bottom for induction and gas."),
]
assert len(CLEAN_PRODUCTS) == 30


def build_rows():
    """Return (rows_dicts, answer_key_map).

    rows_dicts is a list of 60 dicts keyed by canonical field names.
    answer_key_map maps row_id -> list of expected error codes.
    """
    rows = []
    answer_key = {}

    # ---- 30 clean rows (r001..r030) --------------------------------------
    # First 10 have blank partner_sku (exercises generation, not an error).
    for i, (cat, brand, title, size, unit, desc) in enumerate(CLEAN_PRODUCTS):
        rid = f"r{i + 1:03d}"
        slug = f"{brand.lower().replace(' ', '')}-{i + 1:03d}"
        sku = "" if i < 10 else f"AE-{100 + i:06d}"
        row = {
            "category": cat,
            "brand": brand,
            "title": title,
            "gtin": _fresh_gtin(),
            "size": size,
            "size_unit": unit,
            "image_url": _img(slug),
            "video_url": "",
            "description": desc,
            "partner_sku": sku,
            "_row_id": rid,
        }
        rows.append(row)
        answer_key[rid] = []

    # ---- 18 schema-broken rows (r031..r048) ------------------------------
    def _base_clean(idx):
        """Return a copy of a clean row's fields (varying across categories)."""
        src = CLEAN_PRODUCTS[idx % len(CLEAN_PRODUCTS)]
        cat, brand, title, size, unit, desc = src
        return {
            "category": cat,
            "brand": brand,
            "title": title,
            "gtin": _fresh_gtin(),
            "size": size,
            "size_unit": unit,
            "image_url": _img(f"{brand.lower().replace(' ', '')}-b{idx}"),
            "video_url": "",
            "description": desc,
            "partner_sku": f"AE-{200 + idx:06d}",
        }

    schema_specs = [
        # (row_index_seed, mutator, expected_codes)
        (0,  lambda r: r.update(category=""),                              ["MISSING_FIELD"]),
        (5,  lambda r: r.update(brand=""),                                 ["MISSING_FIELD"]),
        (9,  lambda r: r.update(gtin=""),                                  ["MISSING_FIELD"]),
        (1,  lambda r: r.update(gtin="ABC123XYZ89"),                       ["BAD_FORMAT"]),
        (6,  lambda r: r.update(gtin="12345"),                             ["BAD_FORMAT"]),
        (10, lambda r: r.update(gtin="gtin-invalid"),                      ["BAD_FORMAT"]),
        (2,  lambda r: r.update(gtin=_mangled_checksum_gtin()),            ["GTIN_CHECKSUM"]),
        (7,  lambda r: r.update(gtin=_mangled_checksum_gtin()),            ["GTIN_CHECKSUM"]),
        (11, lambda r: r.update(gtin=_mangled_checksum_gtin()),            ["GTIN_CHECKSUM"]),
        (12, lambda r: r.update(size_unit="ml")),                           # Snacks (g/kg) -- code below
        (0,  lambda r: r.update(size_unit="l")),                            # Fragrance (ml only)
        (8,  lambda r: r.update(size_unit="g")),                            # Beverages (ml/l)
        (3,  lambda r: r.update(image_url="not-a-url")),
        (13, lambda r: r.update(image_url="https://example.com/img.pdf")),
        (4,  lambda r: r.update(title="Hi")),
        (14, lambda r: r.update(title="Ok")),
        (15, lambda r: r.update(partner_sku="SA-000501")),
        (16, lambda r: r.update(partner_sku="SA-000502")),
    ]

    # Expected codes for the last 9 (fill in here for clarity):
    tail_codes = [
        ["BAD_ENUM"], ["BAD_ENUM"], ["BAD_ENUM"],
        ["BAD_FORMAT"], ["BAD_FORMAT"],
        ["BAD_FORMAT"], ["BAD_FORMAT"],
        ["REGION_MISMATCH"], ["REGION_MISMATCH"],
    ]
    # Rebuild schema_specs with matching codes for the last 9 (positional).
    schema_specs = [
        (schema_specs[i][0], schema_specs[i][1],
         schema_specs[i][2] if i < 9 else tail_codes[i - 9])
        for i in range(len(schema_specs))
    ]

    for j, (seed_idx, mutate, codes) in enumerate(schema_specs):
        rid = f"r{31 + j:03d}"
        row = _base_clean(seed_idx)
        mutate(row)
        row["_row_id"] = rid
        rows.append(row)
        answer_key[rid] = list(codes)

    # ---- 12 semantic-trap rows (r049..r060) ------------------------------
    # All schema-clean; each carries exactly one semantic defect.
    semantic_rows = []

    # (2) brand duplicated in title
    semantic_rows.append((
        {
            "category": "Beauty > Fragrance",
            "brand": "Elysian",
            "title": "Elysian Aqua Bloom Eau de Parfum",
            "gtin": _fresh_gtin(),
            "size": "100", "size_unit": "ml",
            "image_url": _img("elysian-sem-1"),
            "video_url": "",
            "description": "Fresh citrus and jasmine top notes fade into a warm amber base. Long lasting daily wear scent.",
            "partner_sku": "AE-000601",
        },
        ["TITLE_BRAND_MISMATCH"],
    ))
    semantic_rows.append((
        {
            "category": "Beauty > Skincare",
            "brand": "Loraya",
            "title": "Loraya Vitamin C Brightening Cream",
            "gtin": _fresh_gtin(),
            "size": "50", "size_unit": "g",
            "image_url": _img("loraya-sem-2"),
            "video_url": "",
            "description": "Vitamin C and niacinamide cream to even skin tone. Absorbs quickly with a matte finish.",
            "partner_sku": "AE-000602",
        },
        ["TITLE_BRAND_MISMATCH"],
    ))

    # (3) title says one volume but size field says another
    semantic_rows.append((
        {
            "category": "Grocery > Beverages",
            "brand": "Fjord",
            "title": "Sparkling Mineral Water 200 ml Glass Bottle",
            "gtin": _fresh_gtin(),
            "size": "500", "size_unit": "ml",
            "image_url": _img("fjord-sem-3"),
            "video_url": "",
            "description": "Naturally carbonated mineral water sourced from mountain springs. Recyclable glass packaging.",
            "partner_sku": "AE-000603",
        },
        ["TITLE_SIZE_MISMATCH"],
    ))
    semantic_rows.append((
        {
            "category": "Beauty > Skincare",
            "brand": "PurePeak",
            "title": "Hydrating Facial Serum 30 ml Pump Bottle",
            "gtin": _fresh_gtin(),
            "size": "100", "size_unit": "ml",
            "image_url": _img("purepeak-sem-4"),
            "video_url": "",
            "description": "Hyaluronic acid serum for daily hydration. Lightweight, fragrance-free formula for sensitive skin.",
            "partner_sku": "AE-000604",
        },
        ["TITLE_SIZE_MISMATCH"],
    ))
    semantic_rows.append((
        {
            "category": "Beauty > Fragrance",
            "brand": "Aureum",
            "title": "Rose Absolute Perfume 50 ml Travel Size",
            "gtin": _fresh_gtin(),
            "size": "100", "size_unit": "ml",
            "image_url": _img("aureum-sem-5"),
            "video_url": "",
            "description": "Rose absolute layered with sandalwood in a compact travel-friendly atomizer.",
            "partner_sku": "AE-000605",
        },
        ["TITLE_SIZE_MISMATCH"],
    ))

    # (2) ml in title on Fashion > Apparel — schema-clean because size_unit is a valid apparel enum
    semantic_rows.append((
        {
            "category": "Fashion > Apparel",
            "brand": "Everweave",
            "title": "Cotton Crew Neck T-Shirt 500 ml Water Repellent Finish",
            "gtin": _fresh_gtin(),
            "size": "M", "size_unit": "M",
            "image_url": _img("everweave-sem-6"),
            "video_url": "",
            "description": "Classic short-sleeve tee in soft combed cotton. Regular fit with reinforced neckline.",
            "partner_sku": "AE-000606",
        },
        ["UNIT_IMPLAUSIBLE"],
    ))
    semantic_rows.append((
        {
            "category": "Fashion > Apparel",
            "brand": "Kestrel",
            "title": "Merino Wool Sweater 250 ml Care Kit",
            "gtin": _fresh_gtin(),
            "size": "L", "size_unit": "L",
            "image_url": _img("kestrel-sem-7"),
            "video_url": "",
            "description": "Fine gauge merino wool crewneck sweater. Machine washable on the wool cycle.",
            "partner_sku": "AE-000607",
        },
        ["UNIT_IMPLAUSIBLE"],
    ))

    # (4) description contradicts attributes
    semantic_rows.append((
        {
            "category": "Fashion > Apparel",
            "brand": "Stanton",
            "title": "Cotton Crew Neck T-Shirt Regular Fit",
            "gtin": _fresh_gtin(),
            "size": "M", "size_unit": "M",
            "image_url": _img("stanton-sem-8"),
            "video_url": "",
            "description": "One hundred percent polyester athletic tee with moisture wicking mesh panels for training.",
            "partner_sku": "AE-000608",
        },
        ["DESC_CONTRADICTION"],
    ))
    semantic_rows.append((
        {
            "category": "Home > Cookware",
            "brand": "Copperline",
            "title": "Stainless Steel Frying Pan Riveted Handle",
            "gtin": _fresh_gtin(),
            "size": "28", "size_unit": "cm",
            "image_url": _img("copperline-sem-9"),
            "video_url": "",
            "description": "Carbon steel skillet with a wooden handle. Season before first use; hand wash only.",
            "partner_sku": "AE-000609",
        },
        ["DESC_CONTRADICTION"],
    ))
    semantic_rows.append((
        {
            "category": "Grocery > Snacks",
            "brand": "SnackyBox",
            "title": "Roasted Salted Almonds",
            "gtin": _fresh_gtin(),
            "size": "500", "size_unit": "g",
            "image_url": _img("snackybox-sem-10"),
            "video_url": "",
            "description": "Pack of three resealable pouches of whole roasted almonds with sea salt. High-protein snack.",
            "partner_sku": "AE-000610",
        },
        ["DESC_CONTRADICTION"],
    ))
    semantic_rows.append((
        {
            "category": "Grocery > Beverages",
            "brand": "Tinca",
            "title": "Organic Iced Green Tea Carton",
            "gtin": _fresh_gtin(),
            "size": "1", "size_unit": "l",
            "image_url": _img("tinca-sem-11"),
            "video_url": "",
            "description": "Pack of six one-litre cartons of unsweetened brewed green tea. No preservatives added.",
            "partner_sku": "AE-000611",
        },
        ["DESC_CONTRADICTION"],
    ))

    # (1) duplicate GTIN — reuse the GTIN from r001; otherwise clean.
    # Caught by the v2 cross-row check as DUPLICATE_GTIN on both r001 and r060.
    duplicated_gtin = rows[0]["gtin"]
    semantic_rows.append((
        {
            "category": "Beauty > Fragrance",
            "brand": "Noctis",
            "title": "Amber Oud Eau de Toilette Spray",
            "gtin": duplicated_gtin,
            "size": "50", "size_unit": "ml",
            "image_url": _img("noctis-sem-12"),
            "video_url": "",
            "description": "Warm amber and oud with balsamic undertones. Long-lasting evening scent in an atomizer bottle.",
            "partner_sku": "AE-000612",
        },
        ["DUPLICATE_GTIN"],
    ))

    assert len(semantic_rows) == 12
    for k, (row, codes) in enumerate(semantic_rows):
        rid = f"r{49 + k:03d}"
        row["_row_id"] = rid
        rows.append(row)
        answer_key[rid] = list(codes)

    assert len(rows) == 60
    return rows, answer_key


def write_xlsx(rows, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "listings"
    ws.append(MESSY_HEADERS)
    for row in rows:
        ws.append([row.get(k, "") for k in CANONICAL_KEYS])
    wb.save(path)


def write_template(path):
    """Emit a marketplace-style upload template that template.read_template
    can ingest into a rulepack:

    - Row 1 headers using canonical field names.
    - Required columns filled yellow (`FFF2CC`) — the "required = coloured
      header" convention every partner-portal template uses.
    - Data validation dropdowns on `category` (all category names) and
      `size_unit` (union of all valid units).
    - A `Config` sheet mapping category → allowed size_units so the rulepack
      can carry per-category unit rules.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "listings"

    field_names = [name for name, _req, _t in FIELDS]
    ws.append(field_names)

    required_fill = PatternFill(fill_type="solid",
                                start_color="FFFFF2CC", end_color="FFFFF2CC")
    header_font = Font(bold=True)
    for col_idx, (name, required, _) in enumerate(FIELDS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        if required:
            cell.fill = required_fill
        ws.column_dimensions[get_column_letter(col_idx)].width = 22

    # A blank first data row so partners have something to type into.
    ws.append([""] * len(field_names))

    def _dv_for(col_index, values):
        dv = DataValidation(type="list",
                            formula1='"' + ",".join(values) + '"',
                            allow_blank=True)
        letter = get_column_letter(col_index)
        dv.add(f"{letter}2:{letter}1000")
        ws.add_data_validation(dv)

    for col_idx, (name, _req, _t) in enumerate(FIELDS, start=1):
        if name == "category":
            _dv_for(col_idx, list(CATEGORIES.keys()))
        elif name == "size_unit":
            all_units = []
            seen = set()
            for meta in CATEGORIES.values():
                for u in meta["size_units"]:
                    if u not in seen:
                        seen.add(u)
                        all_units.append(u)
            _dv_for(col_idx, all_units)

    cfg = wb.create_sheet("Config")
    cfg.append(["category", "size_units"])
    for cat, meta in CATEGORIES.items():
        cfg.append([cat, ",".join(meta["size_units"])])
    cfg.column_dimensions["A"].width = 32
    cfg.column_dimensions["B"].width = 40

    wb.save(path)


def main():
    rows, answer_key = build_rows()
    write_xlsx(rows, XLSX_PATH)
    with open(ANSWER_KEY_PATH, "w", encoding="utf-8") as f:
        json.dump(answer_key, f, indent=2)
    write_template(TEMPLATE_PATH)
    print(f"Wrote {XLSX_PATH} ({len(rows)} rows)")
    print(f"Wrote {ANSWER_KEY_PATH}")
    print(f"Wrote {TEMPLATE_PATH} (marketplace template with validations)")


if __name__ == "__main__":
    main()
