# Sample Bank — EOD position ladder (BSD1 daily grid)

**What this is.** One CSV per calendar day of the BSD1 reporting week ending on the Sample
Bank's latest reporting date (`positions_2026-06-24.csv` … `positions_2026-06-30.csv`): the
bank's **liquid-asset book** — `SECURITY_HOLDING` (GoG bills and bonds) and
`INTERBANK_PLACEMENT` positions from the API-push source — as at close of business that day.
Each file is a **rung** of the daily position ladder that BSD1 (Liquidity Reserve Return)
reads: `bsd1.daily` sums the snapshots whose `as_of_date` is exactly the column's day, and a
day without a rung stays `input_required` (a balance is never copied across the week).
`positions_template.csv` is the header-only template.

**How a rung enters (the TIME-1 ladder path).** Push each file as its own batch with
`as_of_date` = that day (idempotency key per day) — the same three-call flow as any position
push (`docs/API_INTEGRATION.md` §2, §3.4):

```
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2026-06-24 --idempotency-key eod-ladder-2026-06-24 \
  --reason "Sample Bank EOD ladder 2026-06-24" \
  --entity position=onboarding/sample_bank/eod_ladder/positions_2026-06-24.csv
```

Re-pushing a `source_reference` with a **different** `as_of_date` ADDS a snapshot rung: the
position row is reused and the month-end rung is untouched (`ingestion._persist_canonical`
scopes snapshot supersession to the same as-of). Re-pushing the reporting-date file
(`positions_2026-06-30.csv`) restates the close already ingested at month-end for these
positions — same values, that day's rungs superseded by identical ones.

**Movements in the sample.** Securities accrete daily at the position's own yield back from
the month-end carrying value (Guide BSD1 24–25: cost + discount/interest earned to date);
placements carry constant principal and appear only from their value date (`origination_date`);
weekend files (Sat 27 / Sun 28) carry the Friday close plus accrual. Every position keeps its
`source_reference`, product, counterparty, GL code and attributes across the week.

**Scope — read before loading to a real tenant.**

- The Data Engine derives a reporting period's facts and live metrics from the positions
  pushed for that exact `as_of_date` (`fact_derivation._load_canonical`), so a rung push
  also mints a `bank_reporting_periods` row for that day and derives that day's facts from
  what the push carried. These files carry the liquid-asset book only (the deposit and loan
  sub-ledgers — ~275k rows/day for the Sample Bank — are not repo-sized): loading them as-is
  therefore creates six intra-month periods whose facts see no deposits or loans, and the
  platform's prior-period comparisons (liquidity EWIs, implied-rating history) read those
  periods. Load a partial-book ladder only once the platform distinguishes a ladder rung from
  a period close (or push the full book per day). This is recorded as a framework ask in
  `docs/bog_returns/bsd1_line_map.md`.
- BSD1 itself is safe with a partial-population ladder: `bsd1.daily` scopes each line's rung
  to the line's `position_types`, so a liquid-asset rung fills the securities / cash /
  placement rows and leaves the deposit and loan rows `input_required` for that day rather
  than reading a fabricated `0`.
- Positions ingested through another source system (`DB_DIRECT`) are keyed by
  `(source_reference, source_system)`; they are NOT in these files — that source pushes its
  own ladder. Mixing sources would create duplicate positions and double-count period-end sums.
