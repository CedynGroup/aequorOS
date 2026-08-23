# How to build a BoG form's line map (Waves 1b–4)

The framework (`backend/app/services/regulatory_reporting/bog_forms/`) already makes every
official form **registered, structure-exported template-faithfully, formula-evaluated and
governed** (see `00_full_return_registry.md` §4 and `tests/services/test_bog_forms_framework.py`).
Per-form work is exactly three deliverables — nothing else may be edited:

| Deliverable | Path (one per form; create it) |
|---|---|
| Line map | `backend/app/services/regulatory_reporting/bog_forms/linemaps/<form>.py` |
| Extra resolvers (only if needed) | `backend/app/services/regulatory_reporting/bog_forms/sources_ext/<form>.py` |
| Line/cell map doc | `docs/bog_returns/<form>_line_map.md` |
| Tests | `backend/tests/services/bog_forms/test_<form>.py` |

**Do not edit** `spec.py`, `engine.py`, `render.py`, `catalog.py`, `sources.py`, `registry.py`,
`layouts/*.json`, or another form's files. If the framework genuinely blocks you, write the need
into your doc under "Framework asks" and stop there.

## Ground rules (from the brief — non-negotiable)

1. **Never invent a BoG line.** Every INPUT cell already exists in `layouts/<FORM>.json`; you
   bind sources to them. Formula cells are BoG's arithmetic — the engine evaluates them; a line
   map that binds a formula cell fails the framework test.
2. **Every leaf row is bound** — use `_common.leaf_lines()` (it enumerates the sheet's captured input
   cells so you cannot omit one). **Blank data grids:** some sheets leave their data cells EMPTY (no
   `0` placeholder — BSD2A, the BSD3 ranked rows, BSD11 registers, BSD1A, BSD8-Annexure …), so the
   layout captured no inputs for them; bind those with `_common.grid_lines(form, sheet, rows=…,
   value_columns={…}, row_sources=…)` naming the official data rows/columns you read off the header
   labels (never rows outside the official grid). Formula cells are skipped automatically. Rows without an honest platform source are `INPUT_REQUIRED` /
   `BANK_COA_MAPPING` with a note saying what the bank must supply. The structure is never dropped.
3. **Guide definitions apply**: Domestic = payable in cedis, Foreign = payable in a foreign
   currency (the resolvers apply this per column automatically); own books only (no fiduciary),
   syndication participants excluded, non-resident = foreign assets/liabilities; consolidation
   only on BSD7B/BSD9 (+ the GROUP variants BSD3B/BSD5B). BSD1 = Ghana branches only.
4. **Units**: resolvers return BASE units (cedis); the sheet's unit (¢'Million / percent / count)
   is applied at export. For a percentage or a count set `unscaled=True` on the RowSource.
5. **Read the template first** — `load_layout("<FORM>")` and inspect `sheet.input_cells`,
   `label_for_row`, `formula_cells`; run the dump the framework's discovery used:
   `uv run python -c "from app.services.regulatory_reporting.bog_forms.layout import load_layout; ..."`.
   Cross-form links: use `form_cell("BSD2", "BSD2", "D68")` (the dependency is computed first).
6. **Sources you may use** (see `sources.py`): `facts.sum` (bank_facts: fact_group × category ×
   currency), `positions.sum` (canonical positions/snapshots: position_types, counterparty_types,
   resident, country_codes, regulatory_categories, product_codes, attribute_eq, encumbered,
   measure=balance|notional, sign, currency=GHS|FX|all), `run.metric` (latest succeeded
   RegulatoryRun metrics for module/scenario), `form.cell`, `constant`. Add new resolvers ONLY in
   `sources_ext/<form>.py` with `@resolver("<form>.<name>")`; they receive `(rc, params)` where
   `rc` = `ResolveContext(db, ctx, bank, period, column, dependencies, cache)`. Read-only; never
   compute a BoG figure by a new rule — aggregate/select existing platform state.
7. **Tests** (hermetic, `db_client` + `materialize_canonical_test_book`; see
   `tests/services/test_bog_forms_framework.py` for the pattern): (a) generate the form through
   `POST /api/v1/banks/{bank}/regulatory-packages`; (b) assert every leaf row is bound and the
   mapped share is what your doc claims; (c) assert 2–5 **critical totals/relationships** hold on
   the exported values (e.g. a section total = Σ of its leaves via BoG's formula; a cross-form
   equality like BSD6 total = BSD2 line; a Domestic+Foreign = Total row); (d) if you add a
   resolver, unit-test it against rows you insert. Reference conversion for style:
   `linemaps/bsd2.py`, `docs/bog_returns/bsd2_line_map.md`.
8. **Doc** (`<form>_line_map.md`): official workbook, frequency/limit/basis, sheets, then a
   row-by-row table (row · official label · status · source/filters · note), a "Residual unmapped
   lines — data the bank must supply" list, and cross-form dependencies. Generate the table from
   the line map (see how `bsd2_line_map.md` was produced) so it cannot drift.
9. Gates before you finish: `uv run ruff check <your files>` clean, `uv run basedpyright <your
   files>` clean, `DATABASE_URL="" uv run pytest tests/services/test_bog_forms_framework.py
   tests/services/bog_forms/test_<form>.py -q -p no:cacheprovider` green. **Do not commit.**

Report back: files created, mapped/input_required/coa-mapping counts per sheet, the critical
totals your tests prove, and any framework asks.
