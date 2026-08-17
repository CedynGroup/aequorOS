# BSD13 — Net Open Position (Form FXP): line / cell map

**Official workbook:** `FORM BSD13 REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo · **Unit:** currency UNITS on the named columns; cedi '000 on `FOREX OPEN POSITION`; cedi 'Million on the schedules' Other Currencies column; annexure amounts currency 'Million

**Sheets (4, official order):** `FOREX OPEN POSITION`, `FOREX OPEN POSITION-SCHEDULE-A`, `FOREX OPEN POSITION-SCHEDULE-B`, `FOREX OPEN POSITION-SCHEDULE-C`

**Depends on:** BSD2A

Generated from `bog_forms/linemaps/bsd13.py` + `layouts/BSD13.json` (tables are generated — do not hand-edit them; regenerate).


## What the form asks and where each figure comes from

FORM BSD13 (Form FXP) is the monthly forex-position return. Its four sheets ship
with EMPTY data grids (no `0` placeholders), so the official data cells are named
explicitly from the header labels (`grid_lines`); the cell atlas below is the
authority and `tests/services/bog_forms/test_bsd13.py` pins it.

| Component | Sheet / cells | Platform source |
|---|---|---|
| **(A) Net Assets** | `FOREX OPEN POSITION` row 19 (E/H/K/N); composition on `SCHEDULE-A` | `bsd13.nop` measure `net_assets` = the `fx_position` fact's `assets_ccy − liabilities_ccy` (the FX engine's own basis: LOAN + SECURITY_HOLDING + INTERBANK_PLACEMENT less DEPOSIT + INTERBANK_BORROWING, `fact_derivation._FX_ASSET_TYPES`/`_FX_LIABILITY_TYPES`); Schedule A rows are per-currency `bsd13.positions_ccy` sums by nature |
| **(B) Liabilities on contingent credits** | row 21; `SCHEDULE-B` | **input_required** — crystallised contingents carry no canonical flag; the FX engine's NOP excludes off-balance contingents (`LC_GUARANTEE` are excluded from the fact) |
| **(C) Net Trading Position** | row 24; `SCHEDULE-C` rows 9/11/16/18 + annexure | row 24 = the fact's `net_derivatives_ccy`; Schedule C = the `FX_HEDGE` contract book split spot/forward × purchase/sale (`bsd13.nop` measures `spot_long` … `forward_short`); annexure = `bsd13.forward_contract` per-deal listing |
| **NOP (i+ii+iii)** | row 29 | the FX run's `metrics.currencies[].net_ccy` (fact `net_ccy` fallback) — identical to the FX-NOP return / DBK-DAILY / BSD1B |
| **Cedi equivalent (in '000) of NOP** | row 32; section II rows 44–47 | the run's `net_ghs` per currency (cedis; sheet unit thousands) |
| **AFOP / NOF / AFOP % of NOF / regulatory limit** | C50 / C52 / C53 / C55 | run metrics `nop_ghs` (= max(Σ long, Σ short) — the engine's rule) / `tier1_ghs` / `nop_pct_tier1` / `nop_aggregate_limit_pct` (percent, unscaled) |
| **Management limit** | row 35 | **input_required** — the bank's own limit |

### Units (as the template prints them)

* Named currency columns (US DOLLAR / GB POUND / DEM) — **units of the currency**, bound `unscaled`.
* `FOREX OPEN POSITION` cedi cells ("Cedi equivalent (in '000)", section II "Amounts in cedi equivalent '000") — sheet unit **thousands** (catalog).
* Schedules' *Other Currencies (in equiv. Cedi 'Million)* column — sheet unit **millions**; the resolvers return cedi base units for `other`.
* Schedule C annexure amounts — "Amounts in Currencies unit in 'Million": raw currency units through the sheet's million divisor; rate / points / period are unscaled.

### Conventions (documented, not guessed)

* **DEM column** is bound literally to currency `DEM` (the template predates the euro); EUR reports under *Other Currencies*. A bank that wants EUR in that column changes one `currency` param — no engine change.
* **`other`** = every non-base currency outside {USD, GBP, DEM}, in cedi equivalent (`balance_ghs` attribute, else the fx_position fact's spot, else the run's spot, else the platform's preferred market spot at period end).
* **Contract sides** follow the FX engine (`fact_derivation._fx_hedge_deltas`): `balance` = notional in the SELL currency; buy leg = `notional × contract_rate` (buy units per sell unit); a buy leg without a positive rate is excluded. **Spot vs forward:** `attributes.settlement` wins; else an instrument slug of spot; else settlement ≤ T+2 is spot; everything else outstanding (forward / NDF / option / CCS) is forward at full notional — so Schedule C's NET TRADING POSITION reconciles to the fact's `net_derivatives_ccy` when facts are derived from positions.
* **Sales are negative** — the template's own formulas are `Net Spot = Spot Purchase + Spot Sale` "(L + or S −)".
* **Schedule A partition** (per currency): 1 Cash on hand = CASH without counterparty; 2a/2b/2c current a/cs = CASH/INTERBANK_PLACEMENT with NO contractual maturity at non-resident banks / BoG / resident banks (NULL residency treated as resident); 3a/3b placements = INTERBANK_PLACEMENT WITH a maturity at resident / non-resident banks; 4 SECURITY_HOLDING; 5 LOAN; 6 OTHER_ASSET; 7a non-resident DEPOSIT; 7b(i)/(ii) resident DEPOSIT split by `attributes.fx_account_type` external/fea vs internal/fca (unset ⇒ internal, the common FCA); 8 INTERBANK_BORROWING; 9 OTHER_LIABILITY with `instrument` term_borrowing/borrowing/bond_issued; 10 every other OTHER_LIABILITY. Row 17 (placements held on customer account) is a memo outside the TA formula and is input_required (fiduciary book not flagged).
* **Reconciliation note:** the FX engine's `fx_position` fact nets LOAN/SEC/IBP against DEPOSIT/IBB only; FX cash on hand, nostro CASH rows, OTHER_ASSET and OTHER_LIABILITY appear on Schedule A but not in the engine's NOP. Where a bank holds those in foreign currency, Schedule A NET ASSETS ≠ row 19 by exactly that amount — this is an engine-scope observation (see Framework asks), not a line-map choice.

## Sheet `FOREX OPEN POSITION`

Status legend — **mapped**: fed from platform data via the named resolver (status resolves per cell at generation: a resolver returning nothing yields `input_required` for that cell); **input_required**: bank must supply (no canonical source); **constant**: the template's own shipped value, kept verbatim.

Cells bound: **32** — mapped 24 · input_required 8 · constant 0.

| Row | Cells | Official line | Status | Source (resolver → params) | Unit | Note |
|---|---|---|---|---|---|---|
| 19 | usd→E19, gbp→H19, dem→K19 | Net Assets | mapped | `bsd13.nop` measure='net_assets' | unscaled (sheet unit already) |  |
| 21 | usd→E21, gbp→H21, dem→K21 | Liabilities on | input_required |  | unscaled (sheet unit already) | crystallised liabilities under contingent credits (LCs / guarantees / other commitments called and unpaid) — canonical positions carry no crystallisation flag; the FX engine's NOP excludes off-balance contingents; bank must supply |
| 24 | usd→E24, gbp→H24, dem→K24 | Net Trading | mapped | `bsd13.nop` measure='net_trading' | unscaled (sheet unit already) |  |
| 29 | usd→E29, gbp→H29, dem→K29 | POSITION(NOP) | mapped | `bsd13.nop` measure='net' | unscaled (sheet unit already) |  |
| 35 | usd→E35, gbp→H35, dem→K35 | Management Limit, if any, | input_required |  | unscaled (sheet unit already) | the bank's own management limit on the NOP in this currency — bank must supply |
| 32 | usd→E32, gbp→H32, dem→K32 | Cedi equivalent (in ' 000) | mapped | `bsd13.nop` measure='net_ghs' | base → sheet unit |  |
| 19 | other→N19 | Net Assets | mapped | `bsd13.nop` measure='net_assets' | base → sheet unit |  |
| 21 | other→N21 | Liabilities on | input_required |  | base → sheet unit | crystallised liabilities under contingent credits (LCs / guarantees / other commitments called and unpaid) — canonical positions carry no crystallisation flag; the FX engine's NOP excludes off-balance contingents; bank must supply |
| 24 | other→N24 | Net Trading | mapped | `bsd13.nop` measure='net_trading' | base → sheet unit |  |
| 29 | other→N29 | POSITION(NOP) | mapped | `bsd13.nop` measure='net_ghs' | base → sheet unit |  |
| 32 | other→N32 | Cedi equivalent (in ' 000) | mapped | `bsd13.nop` measure='net_ghs' | base → sheet unit |  |
| 35 | other→N35 | Management Limit, if any, | input_required |  | base → sheet unit | the bank's own management limit on the NOP in this currency — bank must supply |
| 44 | nop→C44 | US DOLLAR | mapped | `bsd13.nop` measure='net_ghs'; currency='USD' | base → sheet unit |  |
| 45 | nop→C45 | GB POUND | mapped | `bsd13.nop` measure='net_ghs'; currency='GBP' | base → sheet unit |  |
| 46 | nop→C46 | DEM | mapped | `bsd13.nop` measure='net_ghs'; currency='DEM' | base → sheet unit |  |
| 47 | nop→C47 | Other Currencies | mapped | `bsd13.nop` measure='net_ghs'; currency='other' | base → sheet unit |  |
| 50 | nop→C50 | AGGREGATE FOREX ** | mapped | `bsd13.nop` measure='afop' | base → sheet unit |  |
| 52 | nop→C52 | Net own funds (NOF) *** | mapped | `bsd13.nop` measure='net_worth' | base → sheet unit |  |
| 53 | nop→C53 | AFOP as % of NOF | mapped | `bsd13.nop` measure='afop_pct_nof' | unscaled (sheet unit already) |  |
| 55 | nop→C55 | Regulatory(ie BoG) limit, | mapped | `bsd13.nop` measure='aggregate_limit_pct' | unscaled (sheet unit already) |  |

## Sheet `FOREX OPEN POSITION-SCHEDULE-A`

Status legend — **mapped**: fed from platform data via the named resolver (status resolves per cell at generation: a resolver returning nothing yields `input_required` for that cell); **input_required**: bank must supply (no canonical source); **constant**: the template's own shipped value, kept verbatim.

Cells bound: **64** — mapped 60 · input_required 4 · constant 0.

| Row | Cells | Official line | Status | Source (resolver → params) | Unit | Note |
|---|---|---|---|---|---|---|
| 8 | usd→C8, gbp→D8, dem→E8 | Cash on Hand | mapped | `bsd13.positions_ccy` position_types=['CASH']; has_counterparty=False | unscaled (sheet unit already) |  |
| 10 | usd→C10, gbp→D10, dem→E10 | a. In overseas banks | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; resident=False; has_maturity=False | unscaled (sheet unit already) |  |
| 11 | usd→C11, gbp→D11, dem→E11 | b. In BoG | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; counterparty_types=['CENTRAL_BANK'] | unscaled (sheet unit already) |  |
| 12 | usd→C12, gbp→D12, dem→E12 | c. In Ghana banks | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; resident='unknown_as_resident'; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; has_maturity=False | unscaled (sheet unit already) |  |
| 15 | usd→C15, gbp→D15, dem→E15 | a. At Ghana Banks | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_PLACEMENT']; resident='unknown_as_resident'; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; has_maturity=True | unscaled (sheet unit already) |  |
| 16 | usd→C16, gbp→D16, dem→E16 | b. at overseas banks | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_PLACEMENT']; resident=False; has_maturity=True | unscaled (sheet unit already) |  |
| 17 | usd→C17, gbp→D17, dem→E17 | A. Placements at overseas banks | input_required |  | unscaled (sheet unit already) | placements at overseas banks held on CUSTOMER account (memo — outside the own-books TA formula); the fiduciary book is not flagged in canonical data |
| 19 | usd→C19, gbp→D19, dem→E19 | Securities Investments | mapped | `bsd13.positions_ccy` position_types=['SECURITY_HOLDING'] | unscaled (sheet unit already) |  |
| 20 | usd→C20, gbp→D20, dem→E20 | Loans & advances (net) | mapped | `bsd13.positions_ccy` position_types=['LOAN'] | unscaled (sheet unit already) |  |
| 21 | usd→C21, gbp→D21, dem→E21 | Other assets / receivables | mapped | `bsd13.positions_ccy` position_types=['OTHER_ASSET'] | unscaled (sheet unit already) |  |
| 28 | usd→C28, gbp→D28, dem→E28 | a. Non-residents | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident=False | unscaled (sheet unit already) |  |
| 30 | usd→C30, gbp→D30, dem→E30 | i) foreign exchange a/cs (external) | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident='unknown_as_resident'; attribute_in={'fx_account_type': ['external', 'fea']} | unscaled (sheet unit already) |  |
| 31 | usd→C31, gbp→D31, dem→E31 | ii) foreign exchange a/cs (internal) | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident='unknown_as_resident'; attribute_in={'fx_account_type': ['internal', 'fca']}; attribute_missing_ok=True | unscaled (sheet unit already) |  |
| 33 | usd→C33, gbp→D33, dem→E33 | Borrowings in inter-bank market | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_BORROWING'] | unscaled (sheet unit already) |  |
| 34 | usd→C34, gbp→D34, dem→E34 | Other borrowings | mapped | `bsd13.positions_ccy` position_types=['OTHER_LIABILITY']; attribute_in={'instrument': ['term_borrowing', 'borrowing', 'bond_issued']} | unscaled (sheet unit already) |  |
| 35 | usd→C35, gbp→D35, dem→E35 | Other liabilities / payables | mapped | `bsd13.positions_ccy` position_types=['OTHER_LIABILITY']; attribute_not_in={'instrument': ['term_borrowing', 'borrowing', 'bond_issued']} | unscaled (sheet unit already) |  |
| 8 | other→F8 | Cash on Hand | mapped | `bsd13.positions_ccy` position_types=['CASH']; has_counterparty=False | base → sheet unit |  |
| 10 | other→F10 | a. In overseas banks | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; resident=False; has_maturity=False | base → sheet unit |  |
| 11 | other→F11 | b. In BoG | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; counterparty_types=['CENTRAL_BANK'] | base → sheet unit |  |
| 12 | other→F12 | c. In Ghana banks | mapped | `bsd13.positions_ccy` position_types=['CASH', 'INTERBANK_PLACEMENT']; resident='unknown_as_resident'; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; has_maturity=False | base → sheet unit |  |
| 15 | other→F15 | a. At Ghana Banks | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_PLACEMENT']; resident='unknown_as_resident'; counterparty_types=['BANK_OECD', 'BANK_NON_OECD']; has_maturity=True | base → sheet unit |  |
| 16 | other→F16 | b. at overseas banks | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_PLACEMENT']; resident=False; has_maturity=True | base → sheet unit |  |
| 17 | other→F17 | A. Placements at overseas banks | input_required |  | base → sheet unit | placements at overseas banks held on CUSTOMER account (memo — outside the own-books TA formula); the fiduciary book is not flagged in canonical data |
| 19 | other→F19 | Securities Investments | mapped | `bsd13.positions_ccy` position_types=['SECURITY_HOLDING'] | base → sheet unit |  |
| 20 | other→F20 | Loans & advances (net) | mapped | `bsd13.positions_ccy` position_types=['LOAN'] | base → sheet unit |  |
| 21 | other→F21 | Other assets / receivables | mapped | `bsd13.positions_ccy` position_types=['OTHER_ASSET'] | base → sheet unit |  |
| 28 | other→F28 | a. Non-residents | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident=False | base → sheet unit |  |
| 30 | other→F30 | i) foreign exchange a/cs (external) | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident='unknown_as_resident'; attribute_in={'fx_account_type': ['external', 'fea']} | base → sheet unit |  |
| 31 | other→F31 | ii) foreign exchange a/cs (internal) | mapped | `bsd13.positions_ccy` position_types=['DEPOSIT']; resident='unknown_as_resident'; attribute_in={'fx_account_type': ['internal', 'fca']}; attribute_missing_ok=True | base → sheet unit |  |
| 33 | other→F33 | Borrowings in inter-bank market | mapped | `bsd13.positions_ccy` position_types=['INTERBANK_BORROWING'] | base → sheet unit |  |
| 34 | other→F34 | Other borrowings | mapped | `bsd13.positions_ccy` position_types=['OTHER_LIABILITY']; attribute_in={'instrument': ['term_borrowing', 'borrowing', 'bond_issued']} | base → sheet unit |  |
| 35 | other→F35 | Other liabilities / payables | mapped | `bsd13.positions_ccy` position_types=['OTHER_LIABILITY']; attribute_not_in={'instrument': ['term_borrowing', 'borrowing', 'bond_issued']} | base → sheet unit |  |

## Sheet `FOREX OPEN POSITION-SCHEDULE-B`

Status legend — **mapped**: fed from platform data via the named resolver (status resolves per cell at generation: a resolver returning nothing yields `input_required` for that cell); **input_required**: bank must supply (no canonical source); **constant**: the template's own shipped value, kept verbatim.

Cells bound: **12** — mapped 0 · input_required 12 · constant 0.

| Row | Cells | Official line | Status | Source (resolver → params) | Unit | Note |
|---|---|---|---|---|---|---|
| 9 | usd→C9, gbp→D9, dem→E9, other→F9 | Letters of Credit | input_required |  | base → sheet unit | crystallised letters of credit in currency — bank must supply |
| 11 | usd→C11, gbp→D11, dem→E11, other→F11 | Guarantees | input_required |  | base → sheet unit | crystallised guarantees in currency — bank must supply |
| 13 | usd→C13, gbp→D13, dem→E13, other→F13 | Other Commitments | input_required |  | base → sheet unit | crystallised other commitments in currency — bank must supply |

## Sheet `FOREX OPEN POSITION-SCHEDULE-C`

Status legend — **mapped**: fed from platform data via the named resolver (status resolves per cell at generation: a resolver returning nothing yields `input_required` for that cell); **input_required**: bank must supply (no canonical source); **constant**: the template's own shipped value, kept verbatim.

Cells bound: **142** — mapped 128 · input_required 0 · constant 14.

| Row | Cells | Official line | Status | Source (resolver → params) | Unit | Note |
|---|---|---|---|---|---|---|
| 9 | usd→C9, gbp→D9, dem→E9 | Spot Purchase | mapped | `bsd13.nop` measure='spot_long' | unscaled (sheet unit already) |  |
| 11 | usd→C11, gbp→D11, dem→E11 | Spot Sale | mapped | `bsd13.nop` measure='spot_short' | unscaled (sheet unit already) |  |
| 16 | usd→C16, gbp→D16, dem→E16 | Forward Purchase * | mapped | `bsd13.nop` measure='forward_long' | unscaled (sheet unit already) |  |
| 18 | usd→C18, gbp→D18, dem→E18 | Forward sale * | mapped | `bsd13.nop` measure='forward_short' | unscaled (sheet unit already) |  |
| 9 | other→F9 | Spot Purchase | mapped | `bsd13.nop` measure='spot_long' | base → sheet unit |  |
| 11 | other→F11 | Spot Sale | mapped | `bsd13.nop` measure='spot_short' | base → sheet unit |  |
| 16 | other→F16 | Forward Purchase * | mapped | `bsd13.nop` measure='forward_long' | base → sheet unit |  |
| 18 | other→F18 | Forward sale * | mapped | `bsd13.nop` measure='forward_short' | base → sheet unit |  |
| 35 | serial→A35 | row 35 | constant | `constant` value=1 | unscaled (sheet unit already) | template serial number |
| 36 | serial→A36 | row 36 | constant | `constant` value=2 | unscaled (sheet unit already) | template serial number |
| 37 | serial→A37 | row 37 | constant | `constant` value=3 | unscaled (sheet unit already) | template serial number |
| 38 | serial→A38 | row 38 | constant | `constant` value=4 | unscaled (sheet unit already) | template serial number |
| 39 | serial→A39 | row 39 | constant | `constant` value=5 | unscaled (sheet unit already) | template serial number |
| 40 | serial→A40 | row 40 | constant | `constant` value=6 | unscaled (sheet unit already) | template serial number |
| 41 | serial→A41 | row 41 | constant | `constant` value=7 | unscaled (sheet unit already) | template serial number |
| 35 | date→B35, counterparty→C35, currency→D35, period→F35, rate→G35, points→H35, delivery→I35 | row 35 | mapped | `bsd13.forward_contract` side='purchase'; index=1 | unscaled (sheet unit already) | outstanding forward purchase contract #1 (FX_HEDGE book) |
| 36 | date→B36, counterparty→C36, currency→D36, period→F36, rate→G36, points→H36, delivery→I36 | row 36 | mapped | `bsd13.forward_contract` side='purchase'; index=2 | unscaled (sheet unit already) | outstanding forward purchase contract #2 (FX_HEDGE book) |
| 37 | date→B37, counterparty→C37, currency→D37, period→F37, rate→G37, points→H37, delivery→I37 | row 37 | mapped | `bsd13.forward_contract` side='purchase'; index=3 | unscaled (sheet unit already) | outstanding forward purchase contract #3 (FX_HEDGE book) |
| 38 | date→B38, counterparty→C38, currency→D38, period→F38, rate→G38, points→H38, delivery→I38 | row 38 | mapped | `bsd13.forward_contract` side='purchase'; index=4 | unscaled (sheet unit already) | outstanding forward purchase contract #4 (FX_HEDGE book) |
| 39 | date→B39, counterparty→C39, currency→D39, period→F39, rate→G39, points→H39, delivery→I39 | row 39 | mapped | `bsd13.forward_contract` side='purchase'; index=5 | unscaled (sheet unit already) | outstanding forward purchase contract #5 (FX_HEDGE book) |
| 40 | date→B40, counterparty→C40, currency→D40, period→F40, rate→G40, points→H40, delivery→I40 | row 40 | mapped | `bsd13.forward_contract` side='purchase'; index=6 | unscaled (sheet unit already) | outstanding forward purchase contract #6 (FX_HEDGE book) |
| 41 | date→B41, counterparty→C41, currency→D41, period→F41, rate→G41, points→H41, delivery→I41 | row 41 | mapped | `bsd13.forward_contract` side='purchase'; index=7 | unscaled (sheet unit already) | outstanding forward purchase contract #7 (FX_HEDGE book) |
| 35 | amount→E35 | row 35 | mapped | `bsd13.forward_contract` side='purchase'; index=1 | base → sheet unit | outstanding forward purchase contract #1 (FX_HEDGE book) |
| 36 | amount→E36 | row 36 | mapped | `bsd13.forward_contract` side='purchase'; index=2 | base → sheet unit | outstanding forward purchase contract #2 (FX_HEDGE book) |
| 37 | amount→E37 | row 37 | mapped | `bsd13.forward_contract` side='purchase'; index=3 | base → sheet unit | outstanding forward purchase contract #3 (FX_HEDGE book) |
| 38 | amount→E38 | row 38 | mapped | `bsd13.forward_contract` side='purchase'; index=4 | base → sheet unit | outstanding forward purchase contract #4 (FX_HEDGE book) |
| 39 | amount→E39 | row 39 | mapped | `bsd13.forward_contract` side='purchase'; index=5 | base → sheet unit | outstanding forward purchase contract #5 (FX_HEDGE book) |
| 40 | amount→E40 | row 40 | mapped | `bsd13.forward_contract` side='purchase'; index=6 | base → sheet unit | outstanding forward purchase contract #6 (FX_HEDGE book) |
| 41 | amount→E41 | row 41 | mapped | `bsd13.forward_contract` side='purchase'; index=7 | base → sheet unit | outstanding forward purchase contract #7 (FX_HEDGE book) |
| 48 | serial→A48 | row 48 | constant | `constant` value=1 | unscaled (sheet unit already) | template serial number |
| 49 | serial→A49 | row 49 | constant | `constant` value=2 | unscaled (sheet unit already) | template serial number |
| 50 | serial→A50 | row 50 | constant | `constant` value=3 | unscaled (sheet unit already) | template serial number |
| 51 | serial→A51 | row 51 | constant | `constant` value=4 | unscaled (sheet unit already) | template serial number |
| 52 | serial→A52 | row 52 | constant | `constant` value=5 | unscaled (sheet unit already) | template serial number |
| 53 | serial→A53 | row 53 | constant | `constant` value=6 | unscaled (sheet unit already) | template serial number |
| 54 | serial→A54 | row 54 | constant | `constant` value=7 | unscaled (sheet unit already) | template serial number |
| 48 | date→B48, counterparty→C48, currency→D48, period→F48, rate→G48, points→H48, delivery→I48 | row 48 | mapped | `bsd13.forward_contract` side='sale'; index=1 | unscaled (sheet unit already) | outstanding forward sale contract #1 (FX_HEDGE book) |
| 49 | date→B49, counterparty→C49, currency→D49, period→F49, rate→G49, points→H49, delivery→I49 | row 49 | mapped | `bsd13.forward_contract` side='sale'; index=2 | unscaled (sheet unit already) | outstanding forward sale contract #2 (FX_HEDGE book) |
| 50 | date→B50, counterparty→C50, currency→D50, period→F50, rate→G50, points→H50, delivery→I50 | row 50 | mapped | `bsd13.forward_contract` side='sale'; index=3 | unscaled (sheet unit already) | outstanding forward sale contract #3 (FX_HEDGE book) |
| 51 | date→B51, counterparty→C51, currency→D51, period→F51, rate→G51, points→H51, delivery→I51 | row 51 | mapped | `bsd13.forward_contract` side='sale'; index=4 | unscaled (sheet unit already) | outstanding forward sale contract #4 (FX_HEDGE book) |
| 52 | date→B52, counterparty→C52, currency→D52, period→F52, rate→G52, points→H52, delivery→I52 | row 52 | mapped | `bsd13.forward_contract` side='sale'; index=5 | unscaled (sheet unit already) | outstanding forward sale contract #5 (FX_HEDGE book) |
| 53 | date→B53, counterparty→C53, currency→D53, period→F53, rate→G53, points→H53, delivery→I53 | row 53 | mapped | `bsd13.forward_contract` side='sale'; index=6 | unscaled (sheet unit already) | outstanding forward sale contract #6 (FX_HEDGE book) |
| 54 | date→B54, counterparty→C54, currency→D54, period→F54, rate→G54, points→H54, delivery→I54 | row 54 | mapped | `bsd13.forward_contract` side='sale'; index=7 | unscaled (sheet unit already) | outstanding forward sale contract #7 (FX_HEDGE book) |
| 48 | amount→E48 | row 48 | mapped | `bsd13.forward_contract` side='sale'; index=1 | base → sheet unit | outstanding forward sale contract #1 (FX_HEDGE book) |
| 49 | amount→E49 | row 49 | mapped | `bsd13.forward_contract` side='sale'; index=2 | base → sheet unit | outstanding forward sale contract #2 (FX_HEDGE book) |
| 50 | amount→E50 | row 50 | mapped | `bsd13.forward_contract` side='sale'; index=3 | base → sheet unit | outstanding forward sale contract #3 (FX_HEDGE book) |
| 51 | amount→E51 | row 51 | mapped | `bsd13.forward_contract` side='sale'; index=4 | base → sheet unit | outstanding forward sale contract #4 (FX_HEDGE book) |
| 52 | amount→E52 | row 52 | mapped | `bsd13.forward_contract` side='sale'; index=5 | base → sheet unit | outstanding forward sale contract #5 (FX_HEDGE book) |
| 53 | amount→E53 | row 53 | mapped | `bsd13.forward_contract` side='sale'; index=6 | base → sheet unit | outstanding forward sale contract #6 (FX_HEDGE book) |
| 54 | amount→E54 | row 54 | mapped | `bsd13.forward_contract` side='sale'; index=7 | base → sheet unit | outstanding forward sale contract #7 (FX_HEDGE book) |


## Residual unmapped lines — data the bank must supply

* `FOREX OPEN POSITION` row 21 (E/H/K/N) and `SCHEDULE-B` rows 9/11/13 (C–F): crystallised liabilities under contingent credits (LCs / guarantees / other commitments called and unpaid) — no canonical crystallisation flag.
* `FOREX OPEN POSITION` row 35 (E/H/K/N): the bank's management limit on the NOP per currency.
* `SCHEDULE-A` row 17 (C–F): placements at overseas banks held on customer account (memo; fiduciary book not flagged in canonical data).
* Any DEM cell: blank until the bank books DEM (never 0).
* Any cell whose source is the FX run (row 29 fallback aside; C50/C52/C53/C55): blank until a succeeded baseline FX run exists for the period.

## Cross-form dependencies

* `depends_on = ("BSD2A",)` — the catalogue orders BSD2A first; no BSD13 cell links to BSD2A by formula (the template has no external links). BSD13's figures come from the FX engine so they agree with FX-NOP / DBK-DAILY / BSD1B by construction.

## Framework asks

* **Engine scope, not line map:** `fact_derivation._FX_ASSET_TYPES` / `_FX_LIABILITY_TYPES` exclude CASH, OTHER_ASSET, OTHER_LIABILITY and LC_GUARANTEE from the FX net. BSD13 (A) reports the engine's basis so the return never disagrees with FX-NOP/DBK; a bank with FX cash on hand or FX other assets/liabilities will see Schedule A NET ASSETS differ from row 19 by that amount. If BoG's NOP is to include them, the change belongs in the FX engine (one place), after which BSD13 follows automatically.
* `render._header_text` leaves `Position(s) as on` (B10) and the `…………..` day/month/year placeholders (D10:F10) verbatim — a "POSITION(S) AS ON" prompt could join the period-prompt regex.
* Header cells `B1 NAME OF BANK:` / `B2 PERIOD:` are filled by the renderer's prompt regexes (verified in the framework export test).
