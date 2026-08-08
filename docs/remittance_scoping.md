# Remittance flows — scoping design (product.md §Phase 2 item 13)

Status: **DESIGN FOR DECISION** (2026-08-08). Remittances are confirmed absent
from the platform: no canonical field, no return section, zero matches in the
backend (validation register). This document scopes the build so the design
decision can be taken before an estimate — it deliberately builds nothing.

## 1. Why this is upstream of the monthly BSD pack (item 12)

BoG expects deposits, withdrawals **and remittances** in its own monthly
prudential form. The remittance figures therefore need to exist as *data*
before the return can carry them — a canonical entity, not a report. That
ordering is why item 13 sits upstream of item 12 in product.md, and why the
`BSD-MONTHLY` registry entry generates `template_pending` today: even with the
form in hand, the remittance columns would have nothing to draw on.

## 2. The design decision (the four axes)

| Axis | Options | Recommendation | Rationale |
|---|---|---|---|
| Direction | inbound only / inbound + outbound | **Both** | BoG's balance-of-payments interest is two-sided; storing one direction forces a schema break later. |
| Corridor | free-text country / ISO-3166 counterparty country | **ISO country code, required** | Corridors are the unit regulators aggregate on; free text cannot be aggregated. |
| Channel | bank-proprietary / MTO partner (e.g. money-transfer operators) / mobile-money interop | **Enumerated channel + free-text operator name** | The channel mix is exactly what supervision asks about; the operator name is evidence, not taxonomy. |
| Grain & frequency | per-transaction / daily aggregate / monthly aggregate | **Daily aggregate per (direction, corridor, channel, currency)** | Per-transaction is a payments-system volume the ALM platform does not need and the Data Engine should not carry; monthly is too coarse to serve a daily DBK-style ask if one arrives. Daily aggregates roll up losslessly to any monthly form. |

## 3. Proposed canonical shape (for estimation only — not built)

A new canonical entity `canonical_remittance_flows`, following every existing
canonical convention (ingestion-batch traced, lineage-linked, current-generation
supersession, RLS-forced, `as_of_date` daily):

- `direction` — `INBOUND` / `OUTBOUND`
- `corridor_country` — ISO-3166 alpha-2 of the other leg
- `channel` — `BANK`, `MTO`, `MOBILE_MONEY`, `OTHER`
- `operator_name` — free text, optional
- `currency` + `amount` + `amount_ghs` (cedi equivalent, same conversion
  discipline as positions: ingested conversion or zero-contribution, never an
  invented rate)
- `transaction_count` — integer, for the volume columns monthly forms usually
  carry

Ingestion surfaces: Excel/CSV template sheet + API push section + core-banking
adapter mapping — the standard three doors; a bank without remittance business
simply never sends the sheet.

## 4. Downstream consumers once built

1. **Monthly BSD pack (item 12)** — the remittance columns, once the form is
   obtained.
2. **EWI framework** — a remittance-inflow trend indicator is a natural custom
   EWI for remittance-funded deposit franchises (the register already accepts
   custom indicators).
3. **Liquidity forecasting (Phase 3)** — inbound remittances are a stable
   funding inflow signal for the cash-flow forecast.

## 5. Open questions before build (for Bernard / BoG)

1. Which BoG form actually carries remittances, at what breakdown — the
   monthly BSD pack, a dedicated BOP return, or both? (Determines whether the
   corridor axis needs country *and* region rollups.)
2. Are mobile-money interoperability flows in scope for the banking return, or
   reported by the e-money issuer separately?
3. Is the reporting unit transaction value only, or value + count?
4. Settlement vs initiation date — which day does BoG's "daily" mean?

## 6. Estimate shape (after the decision)

Canonical entity + migration + three ingestion doors + template sheet + docs:
one focused work item on the Data Engine spine; the return wiring rides item 12
once the form lands. No engine work — remittances are data and reporting, not
calculation.
