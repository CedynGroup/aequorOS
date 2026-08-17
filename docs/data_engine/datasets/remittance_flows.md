# `remittance_flows` — foreign remittances by corridor / recipient class / channel

**Kind:** `remittance_flows` (reference dataset; `REFERENCE_DATASET_KINDS`, migration
`202608160014`) · **Feeds:** BSD17 Foreign Inward Remittances — `BSG17-SHEET 1` (US$ by
recipient class, rows 8–13 + total 15) and `BSD17 -SHEET 2` (US$ by sending region, rows 6–11 +
total 12) · **Schema:** `backend/app/domain/ingestion/reference_schemas/remittance_flows.py` ·
**Sample Bank:** `backend/onboarding/sample_bank/remittance_flows.csv` (12 months, ~190 rows a
month) + `remittance_flows_YYYY-MM.csv` (one push unit per month) + `…_template.csv` · **Loader:**
`backend/scripts/ingest_push.py --as-of <month-end> --reference remittance_flows=<month csv>` ·
**Design origin:** `docs/remittance_scoping.md` (2026-08-08) — this dataset is the *built* answer
to its four axes and open questions, at the grain the monthly return needs.

## What it is

The bank's remittance book for one reporting month, aggregated on the axes the scoping design
recommended and BSD17 asks for: **direction** (both stored; BSD17 reads `inbound`), **ISO
corridor country** (required — corridors are what regulators aggregate on), the sheet-2 **region
roll-up** the bank assigns per row, the sheet-1 **recipient class**, **channel** (enumerated) +
free-text operator name, currency with the bank's own US$ and cedi equivalents, and a
transaction count. Settled by this build (scoping §5): the form is BSD17 (monthly, value in US$,
by recipient class *and* region); count is carried but not reported; mobile-money interop flows
terminating in bank accounts are in scope as `channel = mobile_money`; the day is the settlement
month (aggregates are monthly).

## Grain — **one reporting month per push**

One row per `(month, direction, corridor_country, recipient_class, channel, currency)`. The batch
`as_of_date` **is the reporting month-end**; BSD17 reads the latest batch on/before the period
end, so **a push carries exactly one month** (twelve `remittance_flows_YYYY-MM.csv` push units in
the Sample Bank set; the combined file is the reference copy). Monthly aggregates were chosen
over the scoping doc's daily grain because the only consumer today is a monthly return and the
register is a reference dataset, not a canonical entity — the field set rolls up losslessly from
a daily extract if a bank prefers to send days.

## Fields

| Field | Type | Required | Values / unit | BSD17 use | Notes |
|---|---|---|---|---|---|
| `month` | date | yes | ISO month-end | — | must equal the batch `as_of_date` |
| `direction` | enum | yes | `inbound` \| `outbound` | filter `inbound` | outbound rows are stored, never reported on BSD17 |
| `corridor_country` | text | yes | ISO-3166 alpha-2 of the other leg | — | e.g. `GB`, `US`, `NG` |
| `region` | enum | yes | `uk` \| `usa_canada` \| `eu` \| `ecowas` \| `rest_of_africa` \| `other` | Sheet 2 rows 6–11 | the bank's roll-up (table below); consistent per country |
| `recipient_class` | enum | yes | `individual` \| `exporter` \| `service_provider` \| `ngo` \| `embassy` \| `other` | Sheet 1 rows 8–13 | official row vocabulary |
| `channel` | enum | yes | `bank` \| `mto` \| `mobile_money` \| `other` | — | `bank` = SWIFT / correspondent; `mto` = money-transfer operator |
| `currency` | text | yes | ISO 4217 | — | currency of `amount_fx` |
| `amount_fx` | number | yes | in `currency` | — | native amount |
| `amount_usd` | number | yes | US$ | **the reported figure** | the bank's own US$ equivalent (the platform never invents a rate) |
| `amount_ghs` | number | yes | cedis | — | the bank's cedi equivalent (same discipline) |
| `transaction_count` | integer | no | count | — | volume, for the bank's own analytics |
| `operator_name` | text | no | | — | evidence, not taxonomy (`Western Union`, `MTN MoMo interop`, `SWIFT MT103`) |
| `notes` | text | no | | — | |

### ISO → region roll-up (BSD17 Sheet 2)

| `region` | Official row | Countries |
|---|---|---|
| `uk` | United Kingdom | GB |
| `usa_canada` | USA and Canada | US, CA |
| `eu` | European Union | the 27 EU member states (DE, IT, NL, ES, FR, BE, …) |
| `ecowas` | ECOWAS | NG, CI, TG, BF, BJ, SN, ML, NE, GM, GN, GW, LR, SL, CV |
| `rest_of_africa` | Rest of Africa | every other African state (ZA, KE, GA, …) |
| `other` | Others | everything else (AE, AU, CN, SA, CH, NO, …) |

The bank assigns `region` per row (it is the reporting entity's roll-up); the return does not
re-derive it from the country code.

## Validation rules

- required: the eleven fields marked yes; enums exact and lower-case (`direction`, `region`,
  `recipient_class`, `channel`); `amount_fx` / `amount_usd` / `amount_ghs` /
  `transaction_count` numeric.
- Every row in a push carries the same `month` (= `as_of_date`); one `region` per
  `corridor_country` within a push (sanity).
- Do not write `N/A` / `-` / `none` in text fields — the Data Engine reads them as null.

## How BSD17 reads it (`refs.sum` over `amount_usd`, `direction = inbound`)

| Cell | Filter |
|---|---|
| Sheet 1 C8 … C13 | `recipient_class` = individual · exporter · service_provider · ngo · embassy · other |
| Sheet 1 C15 (Total) | none — Σ over every inbound row (the official cell carries no formula; this is the bank's own total) |
| Sheet 2 B6 … B11 | `region` = uk · usa_canada · eu · ecowas · rest_of_africa · other |
| Sheet 2 B12 (Total) | none — Σ over every inbound row |

Sheet unit is `units` (US$ as-is). No register → every amount cell `input_required`; a register
with no row for a class/region → `0` (ingested, nothing in that bucket).

## Example

```csv
month,direction,corridor_country,region,recipient_class,channel,currency,amount_fx,amount_usd,amount_ghs,transaction_count,operator_name,notes
2026-06-30,inbound,GB,uk,individual,mto,GBP,892460.03,1133424.24,12864365.07,4128,Western Union,
2026-06-30,inbound,US,usa_canada,exporter,bank,USD,267311.30,267311.30,3033983.30,8,SWIFT MT103,
2026-06-30,inbound,NG,ecowas,individual,bank,USD,100955.56,100955.56,1145845.66,256,SWIFT MT103,
2026-06-30,outbound,AE,other,other,bank,AED,345866.27,94075.62,1067758.34,10,SWIFT MT103,outward customer transfer (not on BSD17)
```

Push: `{"reference": {"remittance_flows": [ ...rows of ONE month... ]}}` with
`as_of_date = <that month-end>` and an idempotency key per month (e.g. `remit-2026-06-30`).

## Sample Bank dataset

Nineteen corridors (US, GB, CA, DE, IT, NL, ES, FR, NG, CI, TG, BF, ZA, KE, GA, AE, AU, CN, SA),
six recipient classes, four channels, twelve months 2025-07 … 2026-06 with a December peak;
inbound ≈ US$ 11–17 m a month for a mid-size Ghanaian universal bank (individuals ≈ 78 %; MTO
the dominant individual channel), plus a thin outbound book that BSD17 must exclude. Cedi
equivalents use a month-end mid rate assumption (10.45 … 12.20 GHS/USD). Illustrative,
deterministic, not real bank data. Loaded to the primary (`BK-0PMD7Z5M`) as twelve pushes,
`as_of_date` = each month-end.
