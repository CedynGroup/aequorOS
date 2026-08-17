# BSD1B — Daily Net Open Position (weekly return): line / cell map

**Official workbook:** `FORM BSD1B.XLS` · **Frequency:** weekly · **Time limit:** 9 days · **Basis:** solo

**Sheets (official order):** `FORM FXP`, `AFOP`, `SCHEDULE B`

Generated from `bog_forms/linemaps/bsd1b.py` + `layouts/BSD1B.json` (row tables are generated — regenerate, do not hand-edit them).

Status legend — **mapped**: fed from platform data via the named resolver (blank at run time when no succeeded FX baseline run exists for the period — the cell is then `input_required`, exactly as DBK-DAILY refuses without a run); **input_required**: bank must supply. **Unit**: `sheet unit` = base cedis scaled at export (`FORM FXP`, `SCHEDULE B`: ¢'Million; `AFOP`: cedis); `unscaled` = the cell's own unit (units of currency, ¢'000, rates, %).

### Source — the FX engine's NOP (`bsd1b.nop`)

The latest **succeeded baseline run of the FX module** (`RegulatoryRun.module='fx'`) — the same run DBK-DAILY reconstructs the daily NOP from — supplies per currency `net_ccy` (NOP in units of the currency), `net_ghs` (cedi equivalent), `side`, `spot_ghs`, `abs_pct_tier1`, and in aggregate `nop_ghs` (AFOP), `sum_long_ghs` / `sum_short_ghs`, `tier1_ghs` (Net Own Funds — the Tier 1 proxy DBK-DAILY uses), `nop_aggregate_limit_pct`. The run's own inputs (the period's `fx_position` facts) supply the on-balance decomposition `assets_ccy` / `liabilities_ccy` / `net_derivatives_ccy`, so **I) net assets + iii) net trading position = NOP** is the engine's decomposition, not a new rule. 'Other Currencies' = every run currency other than USD/GBP/EUR: its cedi cells are the signed Σ; its units-of-currency and rate cells have no single unit and stay input_required. Nature cells carry the template's own notation `( L )` / `( S )` / `-` from the sign (as `SCHEDULE B`'s IF formula does).

**Contingents.** Neither the FX run nor the canonical `LC_GUARANTEE` positions carry a crystallisation flag or an LC / guarantee / other-commitment split (research gap G5 — DBK 102 is empty for the same reason), so FXP row ii and every `SCHEDULE B` cell are `input_required`; `SCHEDULE B`'s `SUM` totals and `IF(...,"( S )",...)` long/short flags are BoG's formulas and evaluate over what the bank enters.

## Sheet `FORM FXP` — 58 bound cells (0 captured `0`-placeholder input cells + 58 blank data cells bound explicitly) · 1 template formulas · sheet unit: millions

**Binding:** 42 mapped · 16 input_required · 0 coa-mapping (72% platform-fed).

Columns `C/D` US Dollar (Amount / Nature), `E/F` GBP, `G/H` EURO, `I/J` Other Currencies. Rows 14–19 are in units of currency ('1. CURRENCY-WISE POSITIONS — Amounts in Units of Currencies'); row 22 is ¢'000 by its label; rows 31–37 are cedi (the sheet's ¢'Million); `C38 = C36/C37` is BoG's.

| Row | Official line | Cells | Status | Source (resolver → filters) | Unit | Note |
|---|---|---|---|---|---|---|
| 14 | Net Assets | C14, D14, E14, F14, G14, H14, I14, J14 | mapped | `bsd1b.nop` measure=net_assets | unscaled | assets_ccy − liabilities_ccy of the run's fx_position input for the currency, units of currency (unscaled); 'Other Currencies' mixes units ⇒ input_required; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 16 | Liabilities on contingent credits | C16, D16, E16, F16, G16, H16, I16, J16 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply |
| 18 | Net Trading Position (under contracts outstanding) | C18, D18, E18, F18, G18, H18, I18, J18 | mapped | `bsd1b.nop` measure=net_derivatives | unscaled | net_derivatives_ccy (signed FX_HEDGE deltas) of the run's fx_position input, units of currency (unscaled); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 19 | NET OPEN POSITION (NOP) (I+ii+iii) | C19, D19, E19, F19, G19, H19, I19, J19 | mapped | `bsd1b.nop` measure=net | unscaled | NOP = net_ccy of the FX run's per-currency result (assets − liabilities + derivatives), units of currency (unscaled); nature column = the run's side as ( L ) / ( S ); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 22 | Cedi equivalent (in 000) (of NOP in currency) | C22, E22, G22, I22 | mapped | `bsd1b.nop` measure=net_ghs_thousands | unscaled | cedi equivalent of the currency's NOP (net_ghs) in ¢'000 as the label states; 'Other Currencies' = Σ signed net_ghs of every run currency other than USD/GBP/EUR; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 24 | Single curr. Open Position | C24, E24, G24, I24 | input_required |  | sheet unit | 'Single curr. Open Position' — the template does not state the unit (¢ or % of NOF); the FX run's \|NOP\| as % of NOF (abs_pct_tier1) is available once BoG confirms the basis; bank must supply meanwhile |
| 26 | Management limit, if any, on NOP in currency | C26, E26, G26, I26 | input_required |  | sheet unit | management (internal) limit on the currency's NOP — bank policy, not platform state |
| 27 | Revaluation Rates | C27, E27, G27, I27 | mapped | `bsd1b.nop` measure=spot | unscaled | revaluation rate = the run's spot_ghs (cedis per 1 unit) for the currency; not a single pair for 'Other Currencies' ⇒ input_required; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 31 | US Dollar | C31, D31 | mapped | `bsd1b.nop` measure=net_ghs; currency=USD | sheet unit | NOP in cedi (base units → ¢'Million) for USD; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 32 | GB Pound | C32, D32 | mapped | `bsd1b.nop` measure=net_ghs; currency=GBP | sheet unit | NOP in cedi for GBP; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 33 | EURO | C33, D33 | mapped | `bsd1b.nop` measure=net_ghs; currency=EUR | sheet unit | NOP in cedi for EUR; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 34 | Other Currencies | C34, D34 | mapped | `bsd1b.nop` measure=net_ghs; currency=OTHER | sheet unit | NOP in cedi for other currencies = Σ signed net_ghs of run currencies not USD/GBP/EUR; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 36 | a.  AGGREGATE FOREX OPEN POSITION (AFOP) | C36 | mapped | `bsd1b.nop` measure=afop | sheet unit | AFOP = the run's nop_ghs (aggregate NOP, as DBK-DAILY reports it); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 37 | b. Net own funds (NOF)[Networth] | C37 | mapped | `bsd1b.nop` measure=nof | sheet unit | Net own funds = the run's tier1_ghs (Tier 1 proxy for NOF, as DBK-DAILY); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |

## Sheet `AFOP` — 11 bound cells (0 captured `0`-placeholder input cells + 11 blank data cells bound explicitly) · 1 template formulas · sheet unit: units

**Binding:** 11 mapped · 0 input_required · 0 coa-mapping (100% platform-fed).

'(Amounts in cedi equiv.)' — the sheet is catalogued in cedis (units); `B15 = B13/B14` is BoG's; `B17` = the regulatory AFOP limit (% of NOF) from the run's parameters.

| Row | Official line | Cells | Status | Source (resolver → filters) | Unit | Note |
|---|---|---|---|---|---|---|
| 8 | US Dollar | B8, C8 | mapped | `bsd1b.nop` measure=net_ghs; currency=USD | sheet unit | NOP in cedi for USD (sheet unit: cedis); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 9 | GB Pound | B9, C9 | mapped | `bsd1b.nop` measure=net_ghs; currency=GBP | sheet unit | NOP in cedi for GBP; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 10 | EURO | B10, C10 | mapped | `bsd1b.nop` measure=net_ghs; currency=EUR | sheet unit | NOP in cedi for EUR; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 11 | Other Currencies | B11, C11 | mapped | `bsd1b.nop` measure=net_ghs; currency=OTHER | sheet unit | NOP in cedi for other currencies (Σ signed net_ghs); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 13 | a.  AGGREGATE FOREX OPEN POSITION (AFOP) | B13 | mapped | `bsd1b.nop` measure=afop | sheet unit | AFOP = the run's nop_ghs; no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 14 | b. Net own funds (NOF)[Networth] | B14 | mapped | `bsd1b.nop` measure=nof | sheet unit | Net own funds = the run's tier1_ghs (Tier 1 proxy, as DBK-DAILY); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |
| 17 | Regulatory (ie BoG)limit, if any, on AFOP. | B17 | mapped | `bsd1b.nop` measure=afop_limit_pct | unscaled | regulatory limit on AFOP = the run's nop_aggregate_limit_pct (% of NOF); no succeeded FX baseline run for the period ⇒ input_required (run the FX engine) |

## Sheet `SCHEDULE B` — 15 bound cells (12 captured `0`-placeholder input cells + 3 blank data cells bound explicitly) · 8 template formulas · sheet unit: millions

**Binding:** 0 mapped · 15 input_required · 0 coa-mapping (0% platform-fed).

Columns `B` USD, `C` GBP, `D` DEM (a legacy column BoG kept), `E` EURO (blank in the template, bound explicitly), `F` Other Currencies in equiv. ¢'Million. `B10…F10 = SUM(6:8)` and `B12…F12` long/short flags are template formulas (no `E10`/`E12` exist in the official file).

| Row | Official line | Cells | Status | Source (resolver → filters) | Unit | Note |
|---|---|---|---|---|---|---|
| 6 | a.   Letters of Credit | B6, C6, D6, F6 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply |
| 7 | b.   Guarantees | B7, C7, D7, F7 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply |
| 8 | c.   Other Commitments | B8, C8, D8, F8 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply |
| 6 | a.   Letters of Credit | E6 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply (EURO column: blank in the template) |
| 7 | b.   Guarantees | E7 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply (EURO column: blank in the template) |
| 8 | c.   Other Commitments | E8 | input_required |  | sheet unit | crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply (EURO column: blank in the template) |

## Residual unmapped lines — data the bank must supply

- **crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply** — `FORM FXP` row 16 (Liabilities on contingent credits), `SCHEDULE B` row 6 (a.   Letters of Credit), `SCHEDULE B` row 7 (b.   Guarantees), `SCHEDULE B` row 8 (c.   Other Commitments)
- **'Single curr. Open Position' — the template does not state the unit (¢ or % of NOF); the FX run's |NOP| as % of NOF (abs_pct_tier1) is available once BoG confirms the basis; bank must supply meanwhile** — `FORM FXP` row 24 (Single curr. Open Position)
- **management (internal) limit on the currency's NOP — bank policy, not platform state** — `FORM FXP` row 26 (Management limit, if any, on NOP in currency)
- **crystallised liabilities under contingents by currency — the FX run and the canonical LC_GUARANTEE positions carry notionals but no crystallisation flag or LC/guarantee/other split (research gap G5, as DBK 102); bank must supply (EURO column: blank in the template)** — `SCHEDULE B` row 6 (a.   Letters of Credit), `SCHEDULE B` row 7 (b.   Guarantees), `SCHEDULE B` row 8 (c.   Other Commitments)

## Cross-form dependencies

- None in-workbook or external. BSD13 (monthly Net Open Position) reports the same FX-run figures on the monthly layout; BSD2A analyses the BSD2 foreign column.

## Framework / data asks

- **Crystallised contingents by currency and instrument** (LC / guarantee / other) — a `crystallised` flag + `instrument` attribute on `LC_GUARANTEE` snapshots (or a contingents dataset) would feed FXP row ii and `SCHEDULE B`; the resolver seam is `sources_ext/bsd1b.py`.
- **'Single curr. Open Position' (FXP row 24)** — the template does not state the unit; the FX run's `abs_pct_tier1` is the candidate once BoG confirms % of NOF is meant.
- **Daily cadence.** BSD1B is a *daily* NOP reported weekly; the platform's FX run is per reporting period. A per-business-day FX run (or the LiveMetricSnapshot ladder carrying the per-currency breakdown) would let the weekly package carry five daily positions instead of the period-end one.
