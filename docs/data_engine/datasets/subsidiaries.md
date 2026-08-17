# `subsidiaries` — subsidiary register + book

**Feeds:** the consolidation-only cells of **BSD9** *Consolidated Balance Sheet* — row 30 "10. Minority
interests" (Domestic `B30` / Foreign `C30`; `D30 = B + C` is BoG's formula) and the **Annexure** "Details of
inter-company transactions" (DATE / TYPE / SUBSIDIARY / AMOUNT, rows 11–28) — and of **BSD5B** *Consolidated
Capital Adequacy Return* — `D10` "3. Minority Interests" and `D26` "18. Minority Interests in Tier 2 Preferred
Shares". It also fixes the group basis of **BSD3B** and **BSD7B** (documented below: no cell of either is
re-pointed). **Kind:** reference dataset `subsidiaries` (constants `REFERENCE_DATASET_KINDS`; migration
`202608160014`). **Schema module:** `backend/app/domain/ingestion/reference_schemas/subsidiaries.py`.
**Readers:** `refs.sum` (`filters={"consolidation_method": "full"}`, BSD9 with
`currency_field="functional_currency"`) and `refs.field` (Annexure — ranked by amount, one `LineSpec` per
column) in `bog_forms/linemaps/bsd9.py` and `bsd5b.py`.

## Why it exists

Guide, General Notes §1: subsidiaries are consolidated ONLY on BSD7B and BSD9; the GROUP templates BSD3B /
BSD5B are group-basis by their titles. The platform holds the parent's books, so every consolidation-only
cell was `input_required` and every consolidated line carried the parent's solo figure. A group finance team
already keeps a subsidiary register — who the subsidiaries are, how much the group owns and how each is
consolidated — and, at each reporting date, each subsidiary's own book, the inter-company balances and the
minority-interest workings. This dataset is that register; the platform never derives a consolidation
figure from it (a minority interest is the group's own working, stated by the bank).

## Grain and fields

One row per `(reporting_date, subsidiary_id)`. Amounts in **cedis** (a subsidiary that reports in another
currency states its `functional_currency` and the bank's closing-rate cedi equivalents).

| Field | Type | Required | Meaning |
|---|---|---|---|
| `reporting_date` | ISO date | yes | Date the subsidiary's figures are struck at (normally the group's reporting date). Equal to the push batch's `as_of_date`. Lands in the Annexure DATE column. |
| `subsidiary_id` | string | yes | The bank's stable identifier for the subsidiary (e.g. `SUB-003`). Stable across pushes; the key a future subsidiary position book / roster dataset joins on. |
| `name` | string | yes | Registered name — the Annexure SUBSIDIARY column. |
| `country_code` | ISO 3166-1 alpha-2 | yes | Country of incorporation. |
| `entity_type` | enum `bank` \| `nbfi` \| `insurance` \| `other` | yes | What the subsidiary is (a `bank` / `nbfi` subsidiary carries its own Tier 1 and RWA). |
| `functional_currency` | ISO 4217 | yes | Currency of the subsidiary's books. Guide §2: the bank's base currency ⇒ BSD9 **Domestic** column; any other ⇒ **Foreign**. |
| `ownership_pct` | number 0–100 | yes | Group's effective ownership. |
| `consolidation_method` | enum `full` \| `equity` \| `none` | yes | How the group consolidates it. Only `full` rows enter minority interests (an equity-accounted associate has none). |
| `control_via_board` | enum `true` \| `false` | yes | Whether control is exercised through board majority (BoG's connected-party / group tests). |
| `total_assets_ghs` | number | yes | The subsidiary's total assets at `reporting_date`. |
| `total_liabilities_ghs` | number | yes | Its total liabilities. |
| `equity_ghs` | number | yes | Its shareholders' equity (= assets − liabilities). |
| `net_profit_ytd_ghs` | number | yes | Its net profit for the financial year to `reporting_date`. |
| `tier1_capital_ghs` | number | no | Its regulatory Tier 1 capital — `bank` / `nbfi` subsidiaries. |
| `rwa_ghs` | number | no | Its risk-weighted assets — `bank` / `nbfi` subsidiaries. |
| `minority_interest_ghs` | number | conditional | **BSD9 row 30 / BSD5B D10** — the non-controlling interests' share of the subsidiary's equity per the group's consolidation workings. Required when `consolidation_method = full` and `ownership_pct < 100`; `0` / blank for wholly owned. |
| `minority_interest_tier2_pref_ghs` | number | no | **BSD5B D26** — non-controlling interests in the subsidiary's qualifying Tier 2 preferred shares (nil for most groups). |
| `investment_carrying_ghs` | number | no | The parent's carrying value of its investment in the subsidiary (BSD2 item 10 / BSD5A row 14 context; eliminated on full consolidation). |
| `intercompany_receivable_ghs` | number | yes | **Annexure rows 11–19 AMOUNT** — amount due FROM the subsidiary to the parent at `reporting_date` (loans, placements, fees receivable). |
| `intercompany_receivable_type` | string | no | **Annexure TYPE** for that receivable, in the bank's own words (e.g. "Term loan to subsidiary"). Blank ⇒ the TYPE cell stays `input_required`. |
| `intercompany_payable_ghs` | number | yes | **Annexure rows 20–28 AMOUNT** — amount due TO the subsidiary (its deposits / placements with the parent). |
| `intercompany_payable_type` | string | no | **Annexure TYPE** for that payable. |
| `regulator` | string | no | The subsidiary's own regulator (Bank of Ghana, SEC, NIC, …). |
| `licence_number` | string | no | Its licence reference. |
| `notes` | string | no | Free text. |

## Reading rule

BSD9 and BSD5B read the **latest** `subsidiaries` batch with `as_of_date` on/before the reporting date.

* **Minority interests** (BSD9 row 30, BSD5B D10) = Σ `minority_interest_ghs` over rows with
  `consolidation_method = full`; on BSD9 each row lands in Domestic or Foreign by its `functional_currency`.
  BSD5B D26 = Σ `minority_interest_tier2_pref_ghs` over the same rows.
* **Annexure** — rows 11–19 list the subsidiaries ranked by `intercompany_receivable_ghs` (largest first):
  A = `reporting_date`, B = `intercompany_receivable_type`, C = `name`, D = the receivable; rows 20–28 the
  same ranked by `intercompany_payable_ghs` with the payable fields. Nine slots per block: a bank with fewer
  subsidiaries leaves the rest blank; a bank with more lists the nine largest and states the remainder in
  the return's cover note. A subsidiary with a nil balance still lists (amount `0`) — the register is the
  authority on what exists.
* Blank (`input_required`) until the register is ingested at all. **One reporting date per push** — a batch
  carries the whole register at that date; an omitted subsidiary reads as absent.

### What is deliberately NOT read (documented decisions)

* **BSD9 balance-sheet lines** stay the parent's BSD2 figures (their notes say so): the register carries
  each subsidiary's total assets / liabilities / equity, but consolidating them into BSD9's 31 lines needs
  a line-by-line subsidiary balance sheet and the eliminations, not totals.
* **BSD5B solo-linked lines** (total assets, capital components, off-balance-sheet items) stay `form.cell`
  links to BSD5A: adding a subsidiary's `total_assets_ghs` / `tier1_capital_ghs` / `rwa_ghs` to a linked
  solo cell needs a combining resolver (framework ask); the register already carries the figures.
* **BSD7B** — no P&L input cell can honestly hold a subsidiary's `net_profit_ytd_ghs` (it is the net of
  the subsidiary's own interest, fees, expenses, provisions and tax; the template has no "share of
  subsidiaries' profit" line and no consolidation-adjustment cell); every BSD7B line stays the parent's
  solo figure and its note names the register.
* **BSD3B rosters** — a subsidiary's twenty largest depositors / ten largest monetary-sector / fifty
  largest non-monetary-sector exposures are a subsidiary POSITION book. Design that closes it (not built):
  a `subsidiary_exposures` reference dataset, one row per `(reporting_date, subsidiary_id, roster, rank)`
  with the sheet's fields (`name`, `account_type`, `maturity`, `foreign_ghs`, `cedi_ghs`, `on_balance_ghs`,
  `drawn_ghs`, `undrawn_ghs`, `contingent_ghs`, `security_value_ghs`, `security_type`, `remarks`), read
  through `refs.field` with `filters={"subsidiary_id": …, "roster": …}`, `order_by` amount, `index = rank
  − 1` — plus per-subsidiary emission by the framework (the catalogue says one BSD3B workbook per
  subsidiary; the generator emits one workbook, so only the first subsidiary would be emitted).

## Validation

`reference_schemas.subsidiaries.SCHEMA.validate_row` / `validate_subsidiary_row`: the fifteen required
fields; enums for `entity_type`, `consolidation_method`, `control_via_board`; every `*_ghs` and
`ownership_pct` numeric; `ownership_pct` within 0–100; a fully consolidated subsidiary owned below 100 %
must state `minority_interest_ghs`. Rows are preserved verbatim at the storage layer.

## Sample Bank onboarding dataset

`backend/onboarding/sample_bank/subsidiaries.csv` — three Ghana-incorporated, cedi-functional, fully
consolidated subsidiaries at `2026-06-30`: an investment-banking / fund-management company (100 %), a
bancassurance broker (60 %; minority GHS 2.76m) and a BoG-licensed finance house (75 %; minority GHS
6.625m; Tier 1 GHS 24.8m on RWA GHS 96.3m). Inter-company balances: receivables GHS 22.0m / 8.5m / 0.6m,
payables GHS 14.2m / 4.7m / 3.1m. Header-only template `subsidiaries_template.csv`. Illustrative, not real
bank data.

Push (from `backend/`):

```
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2026-06-30 --reason "Sample Bank onboarding: subsidiary register" \
  --reference subsidiaries=onboarding/sample_bank/subsidiaries.csv
```

## Proof

`backend/tests/services/data_gaps/test_subsidiaries.py`: the file validates and its minority workings are
the non-controlling share of equity; the line maps (BSD9 row 30 + Annexure, BSD5B D10 / D26 read the
register; BSD7B / BSD3B deliberately do not, notes say why); the register pushed through the real API lands
with lineage, BSD9 carries the minority total (Domestic) and the ranked Annexure with BoG's `D = B + C`
over row 30, BSD5B D10 / D26 carry the same, slots past the register stay blank, the export writes the
text and ¢'Million amounts; without a register the cells stay blank; malformed rows are refused.
