# BSD17 — Foreign Inward Remittances: line / cell map

**Official workbook:** `FORM BSD17 REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo · **Unit:** amounts in US$ (template column headers "Amount in US$"; catalogue unit `units` on both sheets — no scaling) · **Depends on:** —

**Sheets (2, official order):** `BSG17-SHEET 1` (recipients — the tab is misspelt "BSG17" in the official file and is reproduced as such), `BSD17 -SHEET 2` (sending region).

Generated from `bog_forms/linemaps/bsd17.py` + `layouts/BSD17.json` (row tables in §3 produced by the snippet in §7 — do not hand-edit; regenerate).

## 1. What the template is, and how it is bound

Two short schedules whose amount cells are **BLANK in the official file**; the extractor captured only Sheet 1's item numbers `A8:A13`, `A15` (1–7, numeric) as input cells and NO formulas (the "Total (1+2+3+4+5+6)" label spells the arithmetic, but cell `C15` carries no formula). Every data cell is bound explicitly with `_common.grid_lines`:

| Sheet | Rows | Official data columns bound (column key → letter) | Not bound |
|---|---|---|---|
| `BSG17-SHEET 1` | 8 Individuals · 9 Exporters · 10 Service Providers · 11 NGOs · 12 Embassies · 13 Others · 15 Total (1+2+3+4+5+6) | `item`→A (captured item numbers 1–7, `constant`, unscaled) · `amount_usd`→C Amount in US$ | rows 7 and 14 (bordered spacer rows between header/rows/total — blank in the official file) |
| `BSD17 -SHEET 2` | 6 United Kingdom · 7 USA and Canada · 8 European Union · 9 ECOWAS · 10 Rest of Africa · 11 Others · 12 Total | `amount_usd`→B Amount in US$ | row 5 (bordered spacer row) |

**Sources — data-gap closure (2026-08-16): the `remittance_flows` reference dataset.** `docs/remittance_scoping.md` (2026-08-08) scoped remittances as absent from the platform; the dataset built here is its answer at the grain this return needs — one row per (month, direction, ISO corridor country, recipient class, channel, currency) with the bank's own US$ and cedi equivalents, pushed / uploaded through the Data Engine ONE reporting month per batch (`as_of_date` = month-end; BSD17 reads the latest batch on/before the period end; `docs/data_engine/datasets/remittance_flows.md`). Every amount cell binds `refs.sum` over `amount_usd` with `direction=inbound`: Sheet 1 rows 8–13 filter `recipient_class` (individual · exporter · service_provider · ngo · embassy · other), Sheet 2 rows 6–11 filter `region` (uk · usa_canada · eu · ecowas · rest_of_africa · other — the roll-up the bank assigns per row; ISO → region table in the dataset doc), and the two Total rows are the unfiltered inbound Σ (the bank's own total — the official cells carry no formula and no rule of ours is added). Before the register is ingested every cell resolves to `input_required` at generation, exactly as before; a register with nothing in a bucket yields `0`. Both open questions the scoping doc left for this form (recipient class; corridor → region) are settled by the dataset's vocabulary.

## 2. Documented decisions

| Item | Decision |
|---|---|
| Total rows (`C15`, `B12`) | the official file carries **no formula** — bound `refs.sum` over every inbound row of the register (the bank's own total; equals Σ of the six rows above by construction of the vocabularies). Previously `input_required`; a template `Σ rows above` is still not invented (data-gap closure 2026-08-16). |
| Item numbers `A8:A13`, `A15` | captured numeric inputs → `constant` with the template's own value (1–7) so the export reproduces the official column. |
| Currency | US$ as the sheet states: the register carries `amount_fx` (native), `amount_usd` (the bank's own US$ equivalent — the reported figure) and `amount_ghs`; the form never converts (no rate of ours). |
| Register grain | one reporting month per push (`as_of_date` = month-end); outbound rows are stored but excluded by the `direction=inbound` filter. |

## 3. Row-by-row map (generated)
### `BSG17-SHEET 1`

14 declared cells (7 captured template inputs, 7 blank-grid cells bound explicitly) · 0 template formulas · sheet unit: units · **14 mapped · 0 input_required · 0 coa-mapping**

| Row | Official label | Cells (column key → ref) | Status | Source (resolver → params) | Unscaled | Note |
|---|---|---|---|---|---|---|
| 8 | Individuals | item→A8 | mapped | `constant` value=1 | ✓ | template item number (official value kept) |
| 8 | Individuals | amount_usd→C8 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'individual'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 9 | Exporters | item→A9 | mapped | `constant` value=2 | ✓ | template item number (official value kept) |
| 9 | Exporters | amount_usd→C9 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'exporter'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 10 | Service Providers | item→A10 | mapped | `constant` value=3 | ✓ | template item number (official value kept) |
| 10 | Service Providers | amount_usd→C10 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'service_provider'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 11 | NGOs | item→A11 | mapped | `constant` value=4 | ✓ | template item number (official value kept) |
| 11 | NGOs | amount_usd→C11 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'ngo'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 12 | Embassies | item→A12 | mapped | `constant` value=5 | ✓ | template item number (official value kept) |
| 12 | Embassies | amount_usd→C12 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'embassy'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 13 | Others | item→A13 | mapped | `constant` value=6 | ✓ | template item number (official value kept) |
| 13 | Others | amount_usd→C13 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'recipient_class': 'other'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by recipient_class) |
| 15 | Total (1+2+3+4+5+6) | item→A15 | mapped | `constant` value=7 | ✓ | template item number (official value kept) |
| 15 | Total (1+2+3+4+5+6) | amount_usd→C15 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound'} |  | official Total row carries no template formula — bound to the register's own total (Σ amount_usd over every inbound row for the month) |

### `BSD17 -SHEET 2`

7 declared cells (0 captured template inputs, 7 blank-grid cells bound explicitly) · 0 template formulas · sheet unit: units · **7 mapped · 0 input_required · 0 coa-mapping**

| Row | Official label | Cells (column key → ref) | Status | Source (resolver → params) | Unscaled | Note |
|---|---|---|---|---|---|---|
| 6 | United Kingdom | amount_usd→B6 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'uk'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 7 | USA and Canada | amount_usd→B7 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'usa_canada'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 8 | European Union | amount_usd→B8 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'eu'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 9 | ECOWAS | amount_usd→B9 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'ecowas'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 10 | Rest of Africa | amount_usd→B10 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'rest_of_africa'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 11 | Others | amount_usd→B11 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound', 'region': 'other'} |  | remittance_flows register required (docs/data_engine/datasets/remittance_flows.md — inbound remittances for the month, US$ equivalent, by sending region) |
| 12 | Total | amount_usd→B12 | mapped | `refs.sum` kind='remittance_flows'; value_field='amount_usd'; filters={'direction': 'inbound'} |  | official Total row carries no template formula — bound to the register's own total (Σ amount_usd over every inbound row for the month) |

**Totals:** 21 official data cells declared (7 captured item numbers + 14 blank-grid amount cells) — **21 bound to a source (7 item-number constants + 14 `refs.sum`) · 0 input_required by construction · 0 coa-mapping**; per sheet: `BSG17-SHEET 1` 14/14, `BSD17 -SHEET 2` 7/7. At generation without the register the 14 amount cells resolve to `input_required` (7 mapped / 14 input_required — the hermetic structure test). No template formulas.

## 4. Residual unmapped lines — data the bank must supply

None once the `remittance_flows` register is ingested for the month (2026-08-16). A month with no push resolves every amount cell to `input_required`.

## 5. Critical totals proven by `tests/services/bog_forms/test_bsd10_11_16_17.py` (structure) and `tests/services/data_gaps/test_remittance_flows.py` (data-gap closure)

1. every captured item number and every blank-grid amount cell (21) is declared and bound to a source, no bound cell is a label cell; without a register the form generates 7 mapped / 14 input_required with 0 unmapped cells and no errors;
2. item numbers `A8 = 1 … A13 = 6`, `A15 = 7` in the snapshot; every amount cell blank without a register;
3. the completion notes name the `remittance_flows` register and its dataset doc for every input_required cell;
4. **with the Sample Bank register pushed through the real API** (one month, `as_of_date` = period end): `C8…C13` = Σ inbound `amount_usd` by recipient class, `C15` = the inbound total = Σ(C8:C13); `B6…B11` = Σ by region, `B12` = the same total = Σ(B6:B11); the outbound rows are excluded (Σ of every row > `C15`); export writes US$ as-is (units sheet), item numbers kept, no formulas.

## 6. Cross-form dependencies

None. (The BSD2 spine has no remittance line; the scoping doc's "which BoG form carries remittances" open question is answered by this form: BSD17, monthly, US$ by recipient class and region.)

## 7. Regenerating the row tables

```python
# DATABASE_URL="" PYTHONPATH=. uv run python -  (from backend/)
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
for sheet, lines in line_maps_for("BSD17").items():
    for line in lines:
        print(sheet, line.code, line.label, line.cells, line.source, line.params, line.unscaled, line.notes)
```

## 8. Framework asks

1. ~~**Data ask (not framework):** build `canonical_remittance_flows` per `docs/remittance_scoping.md`~~ — met 2026-08-16 by the `remittance_flows` reference dataset (monthly aggregate grain, recipient class + ISO corridor + region roll-up, `refs.sum` bindings; no form-specific resolver). If a daily canonical entity is still wanted for EWI / liquidity forecasting (scoping §4), it should roll up to exactly this vocabulary.
2. **Period-aware reference filters** (shared with BSD16): a `filters: {month: "$period_end"}` placeholder in `refs.*` would let a bank push a multi-month file once.
