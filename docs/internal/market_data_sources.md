# Market-Data Source Selection — the three-source model

**Status:** building (2026-08-11). **Owner:** Markets/Treasury. **Audience:** engineers.

Enterprise market-data systems (Bloomberg, Refinitiv/LSEG, QRM, Murex) never
present a single opaque "the market data." They present **planes** — the
vendor's golden copy, the institution's own marks, and any private
adjustment — and let the institution choose which plane drives its risk
engines. AequorOS already stores every plane; what was missing is (a) a
per-bank **source preference** the arbitration honours, and (b) a UI that
shows the planes side-by-side and lets the bank pick. This document freezes
the contract for both.

## 1. Planes (what already exists)

Canonical market rows carry `source_system` (`app/adapters/market_data/pull_runner.py`):

| Plane (UI label) | `source_system` values | Meaning |
|---|---|---|
| **AequorOS** (our data) | `AEQUOR_DESK` | the desk's determined golden copy (curves, indices, FX) |
| **Bank** (your data) | `MANUAL_UPLOAD`, `API_PUSH`, core-banking sources | market data the bank uploaded/pushed itself |
| **Vendor** | `BLOOMBERG`, `REFINITIV` | the bank's licensed vendor feed |
| **Overlay** (a *layer*, not a base) | `market_data_overlays` rows | the bank's additive/fixed/multiplicative adjustment, composed at read time |

`Overlay` is **not** a base source — it is an additive layer applied on top of
whichever base plane the bank selected (`market_data_overlays.compose_curve`).

## 2. Per-bank preference (new)

One row per bank. Three **categories** — `curves`, `fx`, `rates` — each with a
base-source choice and an overlay toggle.

- Base source ∈ `{"aequor", "bank", "vendor"}`.
- Overlay ∈ `bool` (compose the bank's overlays onto the chosen base).

**Category → `source_system` set** (`resolve_source_systems`):

| category source | resolves to |
|---|---|
| `aequor` | `("AEQUOR_DESK",)` |
| `vendor` | `("BLOOMBERG", "REFINITIV")` |
| `bank`   | everything else present for the bank (`MANUAL_UPLOAD`, `API_PUSH`, …) — i.e. NOT aequor and NOT vendor |

**Defaults** (when no row exists, and for a category the bank has never set):
`curves=aequor`, `fx=aequor`, `rates=aequor`; overlay = **on** for every
category (preserves today's behaviour — overlays already compose in FTP). These
defaults reproduce the current published Markets tab for the demo bank (its
reference indices are all `AEQUOR_DESK`).

**Graceful fallback (mandatory):** if the selected source yields **no** servable
row for a scope at `as_of`, the getter falls back to the historical
any-source arbitration (latest date, then most-recent ingest) and the returned
`SourceAttribution` is flagged `fell_back=True` with `requested_source` and the
`served_source`. Calculations must never break because a preferred plane has a
gap. This is the one place the live toggle is softened — a governance note, not
a governance gate (per the founder's "live toggle drives everything" call).

## 3. Arbitration wiring (`app/services/market_data.py`)

The getters gain an **optional** `source_systems: tuple[str,...] | None` and
`overlay: bool` — when `None`, behaviour is **byte-identical** to today (this
keeps every existing caller and the whole test suite green). A thin resolver
loads the bank's preference and calls the getter with the resolved set:

- `get_yield_curve`, `list_yield_curves`, `get_discount_curve` — add
  `source_systems` to the `.where(...)`; when `overlay`, run the result through
  `market_data_overlays.compose_curve`.
- `get_fx_spot` (+ history) — same source filter.
- the indices getter (reference rates) — same source filter.

New resolver functions (same module or `market_data_sources.py`):
- `preferred_curve(db, org, bank, ccy, as_of, *, category="curves")` →
  resolves preference, calls `get_yield_curve` with the source set + overlay,
  applies fallback.
- equivalents for discount, fx, indices.

`fact_derivation` and the engines keep calling the **existing** getter names;
we route them through the preference-aware resolver so IRRBB/FTP automatically
consume the bank's selected plane. **No official-run hashing changes** — the
resolved facts still hash value-based (`bank-facts-v2`); the source choice
changes *which values*, exactly as a vendor switch should.

## 4. Endpoints (backend)

Under `/api/v1/banks/{bank_id}/market-data`:

- `GET  /source-preferences` → `{curves:{source,overlay}, fx:{…}, rates:{…}, updated_at, updated_by}` (defaults synthesised when no row).
- `PUT  /source-preferences` → body same shape (partial allowed); audited (`MutationTenant`, reason optional); returns the resolved row.
- `GET  /planes?category={curves|fx|rates}&as_of=YYYY-MM-DD` → the **same scope resolved under each available plane**, side-by-side:
  ```
  { category, as_of, selected_source, overlay_enabled,
    planes: [ { source: "aequor"|"bank"|"vendor", available: bool,
                items: [ …CurveView|FxRateView|IndexView… ],
                attribution: {…}, is_selected: bool } ],
    overlay: { available: bool, delta_preview: [...] } }
  ```
  Powers the transparency comparison. `available:false` when that plane has no
  data for the category at `as_of` (UI greys the option, never offers an empty pick).

- `GET  /curves/{curve_name}/forward-grid?as_of=YYYY-MM-DD` (FC-5/G1) → the
  published forward grid for a desk curve:
  ```
  { curve_name, currency, as_of, methodology_ref, interpolation,
    rows: [ { start: date, end: date, discount_factor: str, forward_yield: str } ],
    pillars: [ { tenor, instrument, quote } ] }
  ```
  Source: the APPROVED `DeskDetermination.derived_values` grid for the curve
  (built by `curve_construction`). Decimal fractions for rates/yields.

All rate/yield values on these payloads are **decimal fractions** (0.15 = 15%);
index reference-rate values remain **percent-valued** (15.0) per their existing
canonical convention — the UI formatter already distinguishes them (RatesBoard
renders indices as-is; curves ×100).

## 5. UI (dashboard `app/(app)/markets`)

Redesign the single-scroll Markets page into a modern, tabbed, enterprise
surface. Tabs:

- **Overview** — headline board (selected-source reference rates fixed at
  correct scale, FX, curve thumbnails, freshness), the as-of scrubber.
- **Curves** — CurvesExplorer (existing), now source-aware.
- **Forward** *(new)* — published forward-grid viewer: Start/End/DF/Yield table
  + forward-curve chart + Convert-to basis (reuse `ForwardGrid.tsx`), reading
  `/curves/{name}/forward-grid`.
- **FX** — FxBoard, source-aware.
- **Rates** — RatesBoard (bug fixed), source-aware.
- **Sources** *(new)* — the three-plane control room: per-category
  **AequorOS / Bank / Vendor** selector (segmented control) + **Overlay**
  toggle, each row showing what's available and what's selected; below it, the
  **side-by-side plane comparison** (`/planes`) so the bank sees exactly what
  each choice would feed the engines before committing. A banner states that
  the selection flows live into IRRBB/FTP.

Design language: match the dark-default token system already in the dashboard
(`components/ui`, tnum, `text-h2`/`text-caption`, `border-border`,
`bg-surface`). No literal currency/regulator strings (jurisdiction rules).

## 6. Non-goals (this pass)

- No new vendor pull plumbing — planes are read from what's already ingested.
- No approval gate on the source switch (founder chose the live toggle).
- Consensus/cross-vendor blending stays out (spec §Phase 3).
