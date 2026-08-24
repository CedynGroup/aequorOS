# AequorOS SDI — 10-year multi-cadence ingestion dataset

A Ghanaian savings-&-loans as a continuous time series, **2016-08-31 → 2026-08-31** (10y monthly · 5y weekly · 730d daily = 777 reporting dates). Each account keeps a stable `source_reference`, valued daily — so the platform reconciles daily → weekly → monthly.

## Load it
Per-book files carry an `as_of_date` column. `push_sdi.py` groups by date and runs the
three-call flow per date — start light, then widen:
```bash
BASE_URL=http://localhost:8001 TOKEN=<admin or aeq_live_… key> BANK=BK-XREAZES1 \
  python onboarding/aequoros_sdi/push_sdi.py --cadence monthly   # 10y month-ends
#                                            --cadence weekly    # + Friday LMTD closes
#                                            --cadence daily     # + recent daily EOD
```

## Trajectory (month-ends)
- 2016-08-31: deposits GHS 208,947,739 · loans GHS 176,238,805 · NPL 1.2%
- 2026-08-31: deposits GHS 494,302,712 · loans GHS 443,980,725 · NPL 12.2%

## Files
`positions_{deposits,loans,cash,securities}.csv` `gl_accounts.csv` `capital_structure.csv` `daily_cashflows.csv` (all with `as_of_date`) + `counterparties.csv` `products.csv` `behavioral_assumptions.csv` + `as_of_calendar.csv`.
