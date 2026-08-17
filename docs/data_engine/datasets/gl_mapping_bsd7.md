# `gl_mapping_bsd7` — chart-of-accounts → BSD7A/BSD7B P&L item mapping

**Feeds:** every P&L line of **BSD7A** *Current Year Results* (rows 6–45, Month-ended and Period-to-date,
Domestic/Foreign) and **BSD7B** *Current Year Consolidated Results* (same spine, Quarter / Period-to-date);
BSD11 depends on BSD7A. **Kind:** reference dataset `gl_mapping_bsd7` (constants
`REFERENCE_DATASET_KINDS`; migration `202608160014`). **Schema module:**
`backend/app/domain/ingestion/reference_schemas/gl_mapping_bsd7.py`. **Reader:**
`bog_forms/sources_ext/bsd7.py::bsd7.pl_line` (via `sources.reference_rows`).

## Why it exists

The platform has no chart of accounts of its own. BSD7A's lines are the bank's INCOME/EXPENSE ledger
regrouped into BoG's items, and only the bank knows which of its accounts is "Interest on bills" and
which is "Admin & other". Until now the only seam was an `attributes.bsd7_line` tag on every P&L
`gl_account` record — workable through the adapters, but it forces a re-ingestion to change a mapping.
This register states the mapping **once, as data**: it is uploaded / pushed like any other reference
dataset, versioned by batch (latest as-of on/before the reporting date wins — and within that date the most recently ingested batch, so a corrected re-push replaces rather than adds), and read by the P&L
resolver at generation time.

Two datasets therefore make BSD7A/BSD7B generate:

1. the **P&L ledger** — `gl_account` entity records with `account_class` `INCOME` / `EXPENSE` and a
   `balance` per month-end (see *Loading the P&L ledger* below);
2. this **mapping register**.

## Grain and fields

One row per **exact GL account code** (`gl_account_code`) **or** per **code prefix** (`gl_prefix` —
every account whose code starts with it). Give one selector per row, never both.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `gl_account_code` | string | one of the two | Exact `account_code` of a P&L GL account (as ingested). |
| `gl_prefix` | string | one of the two | Starts-with selector: every account whose code begins with this string. |
| `bsd7_item` | enum | yes | The official BSD7A/BSD7B item the account(s) feed — vocabulary below. |
| `sign` | enum `1` \| `-1` | no (default `1`) | Sign the account contributes with. `-1` for contra accounts (e.g. interest in suspense against loan interest). |
| `balance_basis` | enum `ytd` \| `period` | no (default `ytd`) | `ytd`: the ledger balance at each month-end is fiscal-year-to-date (trial-balance convention; month = difference of consecutive month-ends). `period`: each month-end's balance is that month's movement (the window sums them). |
| `gl_account_name` | string | no | Display name, for the reviewer. |
| `notes` | string | no | Free text. |

### `bsd7_item` vocabulary (official item numbers of FORM BSD7A / BSD7B)

| `bsd7_item` | BSD7A row | Official line |
|---|---|---|
| `1a` | 6 | 1. Interest received and receivable — (a) Overdrafts, loans & other advances |
| `1b` | 7 | (b) Bills (including discounts) |
| `1c` | 8 | (c) Investments (including discounts) |
| `2a_savings` | 11 | 2. Interest paid and payable — (a) on deposits: Savings |
| `2a_current` | 12 | — Current |
| `2a_time` | 13 | — Time |
| `2a_borrowings` | 14 | — Borrowings |
| `2b` | 15 | (b) Other interest payments |
| `4` | 17 | Profit/Loss on foreign exchange dealings (profits; losses → `26`) |
| `5` | 18 | Income from fees and commissions |
| `6` | 19 | Dividends received |
| `7` | 20 | Profit/Loss on sale of property, plant and equipment (signed net) |
| `8` | 21 | Rent receivable |
| `9` | 22 | Gain on dealing assets (gains; losses → `25`) |
| `10` | 23 | Other income |
| `12` | 25 | Operating expense — Staff |
| `13` | 26 | — Training |
| `14` | 27 | — Emoluments |
| `15` | 28 | — Others (staff) |
| `16` | 29 | — Occupancy |
| `17` | 30 | — Travel |
| `18` | 31 | — Admin. & other |
| `20` | 33 | Provisions — Depreciation |
| `21` | 34 | — Bad debts (charge for the period, not the provision stock) |
| `22` | 35 | — Other (specify) |
| `24` | 37 | Losses on sale of investment |
| `25` | 38 | Losses on dealing assets |
| `26` | 39 | Exchange losses |
| `28` | 41 | Provision for taxation |
| `30` | 43 | Extraordinary items (signed net; the template deducts it) |
| `32` | 45 | Dividends paid and payable |

Items 34–36 (reserve balances before appropriation), the averages block and staff numbers are **not**
P&L ledger lines and are not in this vocabulary (see `docs/bog_returns/bsd7a_line_map.md`).

## Precedence — how the resolver picks accounts for a line

For each INCOME/EXPENSE account (current generation, `validation_status` accepted/warning, balance
present, as-of within the fiscal year to the reporting date):

1. the account's own **`attributes.bsd7_line`** tag (ingested with the ledger) always wins — a register
   row can never re-route an account the ledger itself tagged;
2. else the register's **exact `gl_account_code`** row;
3. else the **longest matching `gl_prefix`** row (`5301` beats `530`);
4. else the line map's own `account_code_prefixes` (a declared, per-line selection; default sign/basis).

An account whose effective item is another line is not counted, even if a shorter prefix points here.
`sign` and `balance_basis` apply per account (from the row that selected it); the line map's `sign`
applies on top. A line with **no selected account is blank (input_required)** — never a guessed
figure; a line that is mapped but has nothing in one currency reads `0` in that column.

Domestic / Foreign (Guide §2): by the GL account's `currency` — no currency or the bank's base currency
⇒ Domestic; any other ⇒ Foreign. Balances are always in cedis (a P&L ledger is kept in the reporting
currency; an FX-business account carries `currency=USD` and its cedi-equivalent balance).

## Validation

`reference_schemas.gl_mapping_bsd7.SCHEMA.validate_row` / `validate_mapping_row`: `bsd7_item` required
and in the vocabulary; `sign` in {`1`,`-1`}; `balance_basis` in {`ytd`,`period`}; exactly one of
`gl_account_code` / `gl_prefix`. Rows are preserved verbatim at the storage layer.

## Loading the P&L ledger (INCOME/EXPENSE `gl_account` records)

The `gl_account` entity already carries `account_class` `INCOME`/`EXPENSE` and `balance`
(`docs/API_INTEGRATION.md` §3.1); the API push and the Excel/CSV upload both accept them, and the
canonical GL keeps **one generation per (account_code, as_of_date)** — a later push for the same date
supersedes, a push for another date adds a month-end. Push **one file per month-end** with `--as-of`
set to that trial-balance date (P&L accounts are cleared to reserves at the fiscal year end, so a
month-end balance is fiscal-year-to-date). The P&L accounts do not reference positions, so the GL /
sub-ledger reconciliation blocker never fires on them.

```bash
cd backend
for f in onboarding/sample_bank/gl_accounts_pl_20*.csv; do
  d="${f##*_}"; d="${d%.csv}"
  uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
    --bank BK-0PMD7Z5M --as-of "$d" --reason "Sample Bank onboarding: P&L ledger $d" \
    --entity gl_account="$f"
done
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2026-06-30 --reason "Sample Bank onboarding: CoA -> BSD7 mapping" \
  --reference gl_mapping_bsd7=onboarding/sample_bank/gl_mapping_bsd7.csv
```

Then `POST /banks/{id}/regulatory-packages {"return_code": "BSD7A", "reporting_date": "2026-06-30"}`:
Month-ended = June YTD − May YTD, Period-to-date = June YTD, Domestic/Foreign by account currency; BSD7B
Quarter = June YTD − March YTD.

## Sample Bank onboarding dataset

`backend/onboarding/sample_bank/gl_mapping_bsd7.csv` (36 rows: 7 prefixes + 29 exact codes, one contra
row `4109` sign `-1` overriding the `410` prefix, `530` prefix for admin & other with `5301`/`5302`
exact rows for occupancy/travel) and `gl_accounts_pl_<month-end>.csv` × 12 (2025-07-31 … 2026-06-30,
57 P&L accounts each: 26 INCOME, 31 EXPENSE; eleven USD-business accounts). Sized to the bank's balance
sheet on the primary (loans 2.16bn, securities 1.56bn, placements 0.17bn, deposits 3.88bn, GHS):
FY2026 run-rate interest income ≈ 762m, interest expense ≈ 400m (NII ≈ 362m ≈ 9.3% of earning assets),
non-interest income ≈ 152m, opex ≈ 299m (cost/income ≈ 58%), impairment ≈ 50m, tax ≈ 46m, PAT ≈ 112m
(ROA ≈ 2.6%); FY2025 at 0.88×. Templates: `gl_mapping_bsd7_template.csv`, `gl_accounts_pl_template.csv`.
Realistic, not real: the Sample Bank is the platform's onboarding sandbox.
