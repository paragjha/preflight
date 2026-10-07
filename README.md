# PreFlight

A pre-flight check for marketplace catalog uploads. It sits between a partner's
messy Excel sheet and the marketplace's cataloging tool, and catches two kinds
of error:

1. **What the tool would bounce** — missing fields, bad GTIN check digits,
   wrong enums, region-mismatched SKUs. A full round-trip saved per sheet.
2. **What the tool accepts but gets wrong** — a title that says "200 ml" when
   the size field says 500, a brand duplicated into the title, "ml" on a
   t-shirt, a description that contradicts the attributes. These upload clean
   and go live wrong. Nobody counts them, because catalog ops is measured on
   throughput, not on whether the listing was correct once live.

The second class is the point. The marketplace's validator is structurally
blind to it; PreFlight makes it visible, one row at a time.

## The review surface

The work happens in the drawer. Catalog ops is a volume job, so the drawer is
built for doing it twelve times in a row, not once:

- A **shopper card** at the top — the listing's image and its title as a buyer
  sees it — so a reviewer judges the *listing*, not a row of fields.
- **Keyboard-driven**: `j`/`k` move through the queue, `1` confirms an error,
  `2` marks a false positive, and it auto-advances to the next undecided row.
- **Most-uncertain-first**: the "Needs you" queue sorts by confidence ascending,
  so a reviewer spends attention where the system is least sure.
- **It learns**: every "false positive" becomes an exception, so the next batch
  doesn't ask the same question again. "Reviewed N of M" tracks the session.

Two numbers lead the dashboard: how many rows would have **bounced**, and how
many **silent errors** were caught — the ones the validator can't see.

## The numbers (honest, reproducible)

The engine is **deterministic rules, no model, no network** — so these numbers
reproduce exactly on any machine with no key. Scored by `python proof.py`
against an answer key the pipeline never reads.

Two batches. The **demo batch** (60 rows) is what the rules were developed
against. The **holdout batch** (20 rows) was written with *different phrasings*
and committed to the repo **before any rule code existed** — it measures the
rules on cases they were not fitted to.

| Batch | Schema caught | Semantic traps caught | False positives |
|---|---|---|---|
| demo (60 rows) | 19 / 19 | **11 / 11** | 0 / 30 clean |
| holdout (20 rows) | — | **11 / 12** | 1 / 8 clean |

The holdout has exactly two named gaps, both deliberate and both committed in
advance rather than tuned away:

- **The one miss** — "Waterproof …" in a title vs "not water resistant" in the
  description. Catching that needs open-ended reasoning over free text, which
  the rules don't attempt. That's the one job the model still does better, and
  it's measurable here rather than hidden.
- **The one false positive** — a cast-iron dutch oven whose title states its
  1.2 kg weight, in a category whose sizes are volumes. The rule can't tell a
  product weight from a size. Kept as-is rather than special-cased against the
  holdout.

Both are pinned as strict `xfail` tests, so the suite fails if either silently
changes. Run it yourself:

```bash
python proof.py --batch both
```

### Why rules, not the model

PreFlight shipped first on an LLM (Gemini). That version scored 11/12 on the
demo batch in July — but it depended on a free-tier key that kept hitting its
daily quota, which also made the scores irreproducible. Two findings made the
model unnecessary for three of the four checks: the category table already
states which units are plausible where, and the ingest step already computes
the column positions the parser needs.

So the model was replaced with rules where the rules hold, and kept behind a
flag where they don't. The architecture is the argument: it measured what the
model was worth rather than assuming. Set `SEMANTIC_ENGINE=both` with a key to
run them side by side.

## Quick start

No key needed — the default engine is deterministic rules.

```bash
cd preflight
pip install -r requirements.txt
python generator.py                 # builds demo_batch.xlsx (gitignored)
python -m uvicorn app:app --host 127.0.0.1 --port 8021
# open http://127.0.0.1:8021/  →  "Load demo batch"
```

Flow: **Load demo batch** → the "Silent" filter opens on the interesting rows →
click one (or press `j`/`k`) → decide with `1`/`2` → **Re-run** folds the
corrections in → **Export corrected sheet** writes an upload-ready .xlsx
(canonical column order, red-filled cells where fixes are still needed).

## Running it on real data

The real-data instance is **local only — never deployed**:

- Bind to `127.0.0.1`. With the default `rules` engine there is **no outbound
  call at all**, so partner data never leaves the machine.
- The only thing your browser fetches externally is the drawer's listing image,
  from the partner's own image URL. Set `PREFLIGHT_SHOW_IMAGES=0` for zero
  outbound traffic.
- Point it at a marketplace's own template (**Load template**) and PreFlight
  reads the dropdown validations as enums and the coloured headers as required
  columns — it onboards the way you'd onboard a human, by handing it the
  template.

## The public demo

A deployed, shareable instance that runs only the synthetic batch:

- `PREFLIGHT_DEMO_ONLY=1` disables the upload routes (they return 403) and hides
  the file picker, so no one can push data to it. "Load demo batch" is the whole
  experience.
- `render.yaml` deploys it to Render's free plan with no key and no Docker.
  Connect the repo in the Render dashboard; the blueprint supplies the rest.
  (Free instances sleep when idle, so the first load after a lull takes ~30s,
  and the shared in-memory batch list resets on restart.)

## How it's built

```
preflight/
  app.py              FastAPI routes (thin); GET /api/config drives the UI flags
  ingest.py           xlsx -> canonical rows (header synonym mapping)
  schema_def.py       the one source of truth: fields, categories, regions
  validate_schema.py  deterministic schema checks + autofix + cross-row uniqueness
  rules.py            the LLM-free semantic engine (the default)
  semantic.py         the original Gemini path (lazy-loaded, behind the flag)
  engine.py           selects rules | llm | both via SEMANTIC_ENGINE
  memory.py           per-tenant corrections store
  metrics.py          row verdicts + the dashboard numbers
  export.py           upload-ready xlsx (canonical order, red fill on rejects)
  template.py         marketplace template -> rulepack
  errors.py           validator error sheet -> corrections
  gtin.py             GS1 check digit
  generator.py        builds the demo batch, holdout batch, and demo template
  proof.py            runs the pipeline and scores vs the answer keys
  static/             index.html + opal.css (one page, no build step)
  tests/              69 tests (67 pass, 2 documented xfails)
```

Stack: Python 3.11+, FastAPI, uvicorn, openpyxl, httpx, python-multipart.
No pandas, no database, no ORM, no front-end build. The LLM path is the only
thing that ever makes a network call, and it's off by default.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEMANTIC_ENGINE` | `rules` | `rules` (no key) · `llm` (Gemini) · `both` |
| `GEMINI_API_KEY` | — | only read when the engine is `llm` or `both` |
| `PREFLIGHT_DEMO_ONLY` | off | disable uploads; demo batch only |
| `PREFLIGHT_SHOW_IMAGES` | on | load listing images in the drawer |
| `CATALOG_RESOLVER` | `off` | `off` · `mock` (seeded demo) · `http` (customer endpoint) |
| `CATALOG_RESOLVER_URL` | — | the customer's catalog endpoint, when resolver is `http` |
| `CATALOG_RESOLVER_KEY` | — | bearer token for that endpoint, if it needs one |

## Catalog dedup (GTIN → existing ZSKU)

Before minting a new SKU, check the catalog by barcode: if a GTIN already
exists, attach an offer to the existing listing instead of creating a
duplicate. No *public* endpoint maps a GTIN to a marketplace's internal ZSKU
— that mapping lives only inside the marketplace — so this ships as a **slot,
not a baked-in integration**, chosen by `CATALOG_RESOLVER`:

- `off` (default) — no lookups; the core stays offline and reproducible.
- `mock` — a seeded, deterministic in-repo catalog, so the demo shows real
  "already listed → here's the ZSKU" matches without any external service.
- `http` — POSTs each GTIN to `CATALOG_RESOLVER_URL` (a customer's own
  catalog API), expecting `{"zsku": "...", "title": "..."}` back. Nothing
  proprietary is bundled; the customer supplies their own endpoint and key.

A match surfaces as an `ALREADY_IN_CATALOG` flag with the ZSKU, an "Already
listed" metric, an "In catalog" filter, and an `existing_zsku` column plus an
`offer_existing` status in the exported sheet. The ZSKU format here
(`ZSKU-xxxxxxxx`) is invented for the demo, not any real marketplace's scheme.

## What's next

Phase 2 of the build plan — writing fixes back into the *original* workbook,
preserving its dropdowns and formatting, instead of emitting a new canonical
sheet — is designed but not yet built; it's gated on having a real partner
template to test the round-trip against.
