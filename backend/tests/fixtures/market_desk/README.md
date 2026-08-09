# Market-desk harvest fixtures (Ghana)

Real market data captured 2026-08-09 for the AequorOS market-desk build. `raw/` holds
byte-exact fetched responses (parser-test fixtures), `series/` holds cleaned
ISO-normalized CSV time series (calibration inputs), `manifest.json` records
source URL + sha256 per artifact and the honest failure list. Nothing here is
synthetic; every value came off the wire from the sources below.

---

## 1. Bank of Ghana wpDataTables (www.bog.gov.gh)

### Transport quirks (apply to every BoG request)

- **Broken TLS chain** — always `curl -sk` (or an explicitly relaxed verifier).
- **Bot filter** — requests without a browser `User-Agent` are rejected. Use e.g.
  `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36`.
- A `Referer` header naming the page the table lives on must accompany data requests.

### Fetch mechanics

1. `GET` the page that hosts the table; extract the per-table nonce from
   `name="wdtNonceFrontendServerSide_<table_id>" value="<10-hex>"`.
   Nonces rotate; re-extract per session. The four `raw/bog_page_*.html`
   files are fixtures for exactly this step.
2. `POST https://www.bog.gov.gh/wp-admin/admin-ajax.php?action=get_wdtable&table_id=<N>`
   with form body (brackets URL-encoded):
   `draw=1&wdtNonce=<nonce>&start=<offset>&length=<page>&order[0][column]=0&order[0][dir]=desc`
3. Response JSON: `{draw, recordsTotal, recordsFiltered, data: [[...], ...]}`.
   **`recordsFiltered` is the real row count of the view; `recordsTotal` is the
   shared underlying table and is often wildly larger** (all four interbank
   views report recordsTotal=38145; the FX views report 144647).
4. Page with `start`/`length` (length=1000 works) until `start >= recordsFiltered`.
5. **Server-side filtering:** per-column search works —
   `columns[i][data]=i&columns[i][searchable]=true&columns[i][search][value]=<v>&columns[i][search][regex]=false`.
   **The global `search[value]` is broken** on this deployment: it returns a
   nonzero `recordsFiltered` but an empty `data` array. Never use it.

### Table registry (as mapped 2026-08-09)

| table_id | Page (Referer) | Columns | What it is |
|---|---|---|---|
| 2 | /treasury-and-the-markets/treasury-bill-rates/ | Issue Date, Tender, Security Type, Discount Rate, Interest Rate | GoG tender results: bills AND notes/bonds. View exposes 1361 of 1985 rows (2013-08-26 onward only). |
| 3 | /treasury-and-the-markets/bank-of-ghana-bill-rates/ | same as 2 | BoG bill/instrument tender results, 585 rows (2016-12-23..2026-03-16). |
| 69 | /treasury-and-the-markets/interbank-interest-rates/ | daily_interest_rate_ID, Effective Date, Rate (%) | **Daily weighted-average interbank rate** — the cointegration series. 1722 rows (2019-08-05..2026-08-07). |
| 70 | same page | avg_interest_rate_ID, Week Ending, Average Rate (%) | Weekly average interbank rate, 364 rows. |
| 62 | same page | mpc_rate_ID, Effective Date, Rate (%) | **MPC policy rate** by decision date, 119 rows (2002-11-21..2026-07-22). |
| 63 | same page | mpc_rate_ID, Effective Date, Rate (%) | Same 119 rows as 62 minus exactly 200bps — a derived corridor-floor display, not independent data. |
| 31 | /treasury-and-the-markets/daily-interbank-fx-rates/ | Date, Currency, Currency Pair, Buying, Selling, Mid Rate | Daily interbank FX — **latest day only** (recordsFiltered=19 pairs). Useless for history. |
| 32 | same page | one text cell | Banner: `Day's Weighted Median Rate:   11.7615` (USDGHS). Parse with a regex, not as a table. |
| 40 | /treasury-and-the-markets/historical-interbank-fx-rates/ | same as 31 | **Full FX history**, 144,647 rows, ~19 pairs/day. Filter per-column on Currency Pair (col 2) to pull one pair. |
| 21 | /economic-data/interest-rates/ | Year, Variables, Jan..Dec | Monthly matrix, 13 variables (GRR, MPR, monthly-avg T-bill rates, avg lending, deposits, interbank). 371 rows but **data ends 2023** and there is a junk `Year=0` group. |

DOM order on a page does NOT equal table_id order: on the interbank page,
`table_1..table_4` map to wpdatatable ids 69, 70, 62, 63 (read
`data-wpdatatable_id` on each `<table>`; never assume).

### Data quirks the adapter must handle

- Dates are `"07 Aug 2026"` (`%d %b %Y`, English month abbreviations).
- ID columns carry thousands separators (`"55,496"`).
- **Empty date cells exist** (table 62 has two rows with `''` Effective Date).
- **Exact duplicate rows exist** (table 2 has 4; table 69 has 6 duplicate dates,
  one of which — 2020-02-25 — carries *conflicting* values 16.14 vs 16.12).
- Rates are plain decimal strings, no % sign, 2–4 dp.
- Table 21 wide-format uses `0.00` as its missing-month placeholder — zero is
  never a real value for these rates.

## 2. BoG auction results

- Indexes: `https://www.bog.gov.gh/gog_auction_results/` and
  `https://www.bog.gov.gh/bog_auction_results/` (same TLS/UA quirks).
- GoG tender slugs: `results-of-gog-tender-<N>/` — **N is the tender number
  (currently ~2019), not a year**. BoG slugs:
  `results-of-tender-<N>-held-on-<d-month-yyyy>/`; the same tender number
  recurs across days in one week (873 ran 03 Aug and 05 Aug 2026).
- Tender pages contain **no inline tables** — the security breakdown is a
  linked PDF under `wp-content/uploads/`.
- PDF content (extractable with `pdftotext -layout`):
  - GoG (`Auctresult<N>.pdf`, "NOTICE TO BANKS AND PUBLIC NO. BG/FMD/…"):
    per-ISIN rows with security, bids tendered/accepted (`GH¢ 3,701.66` —
    currency sign + thousands separators), bid-rate ranges (`5.4254 – 5.6976`,
    en-dash or hyphen, sometimes missing spaces: `5.5000– 5.6795`,
    `11.3868-12.0000`), allotted ranges split Discount/Interest, weighted-avg
    rates for the following week, weekly target, plus a summary of the prior
    tender.
  - BoG (`BOG-Auctresults-<N>-….pdf`, "NOTICE TO BANKS AND PUBLIC NO. <N>"):
    per-ISIN bid-rate range, allotted-in-full range (discount+interest),
    weighted averages, `TOTAL AMOUNT SOLD: GH¢ 8,478.44 Million.`

## 3. BoG publications (economic data)

- The `/economic-data/` page is a hub with no publication links; use site search
  `https://www.bog.gov.gh/?s=<query>`.
- Summary of Economic and Financial Data: bimonthly under
  `/econ_fin_data/summary-of-economic-and-financial-data-<month>-<year>/`;
  latest = July 2026 (captured). Each page also links the prior editions and a
  companion "Charts" PDF.
- APR of Banks: monthly notices under
  `/notice/annual-percentage-rates-apr-of-banks-as-at-<month>-<year>/`;
  latest = May 2026 (captured, 11 pages, bank-by-bank APR/AI).
- **Ghana Reference Rate notice: NOT FOUND** — site search for
  "ghana reference rate"/"GRR" returns nothing relevant. GRR monthly values
  were obtained from GSS statsbank (§5) and corroborated by BoG table 21.

## 4. GFIM (gfim.com.gh)

Normal TLS, no UA games. Report lists are rendered client-side by the
**FileBird Document Library** WordPress plugin:

1. `GET` the listing page (`/daily-trading-reports/`, `/monthly-reports/`).
2. Each year-tab is a `div.njt-fbdl` whose `data-json` attribute (HTML-entity
   encoded) holds `{request: {pagination, search, orderBy, orderType,
   selectedFolder: ["<opaque base64 folder id>"]}}`. Tab labels (2026, 2025, …)
   are in the surrounding `wpb_tabs_nav`; first widget = newest year.
3. `var fbdl = {json_url, rest_nonce, ...}` in the page gives the API base.
4. `POST <json_url>/get-attachments` (= `https://gfim.com.gh/wp-json/filebird/v1/get-attachments`)
   with the `request` object as the JSON body and header `X-WP-Nonce: <rest_nonce>`.
   Response: `{files: [{title, type, size, url, link, modified}], foundPosts, maxNumPages}`.
5. Files themselves are plain `wp-content/uploads/` GETs.

Artifacts: daily trading reports are ~555KB XLSX named
`TRADING-REPORT-FOR-GFIM-DDMMYYYY.xlsx` (some have a stray trailing `-` before
`.xlsx`); the 2026 folder held 147 of them. Sheets: `SUMMARY`,
`NEW GOG NOTES AND BONDS`, `DDEP BONDS`, `OLD GOG NOTES AND BONDS ` (note
trailing spaces in sheet names!), `CORPORATE  `, `TREASURY BILLS`,
`SELL BUY BACK TRADES- GOG BONDS`. Per-security columns: NO., TENOR, SECURITY
DESCRIPTION (embeds maturity/issue/coupon, e.g. `GOG-BD-29/03/33-A6155-2001-12.50`),
ISIN, OPENING/CLOSING YIELD, END OF DAY CLOSING PRICE, VOLUME, NUMBER TRADED,
DAY LOW/HIGH YIELD, DAYS TO MATURITY, MATURITY DATE, APPLICABLE DATE. Header
cells contain literal `\r\n`.

Monthly "GFIM Status Report" PDFs are **~11.5MB each** (two captured —
prune/LFS if repo size matters).

## 5. GSS statsbank (statsbank.statsghana.gov.gh)

Standard PxWeb API:

- `GET /api/v1/en/` → database list. The relevant db is **`Macroeconomic
  Indicators`** (the near-duplicate "Macro Economic Indicators" entry 404s).
- `GET /api/v1/en/Macroeconomic%20Indicators/Monetary%20and%20Financial%20Sector/`
  → `fin_sound.px`, `interest.px`, `monetary.px`.
- `GET .../interest.px` → metadata: `Month` (643 values `1971M01..2024M07`) ×
  `Rate` (6 series: Average lending rate, Ghana reference rate, Interbank
  weighted average rate, Monetary policy rate, Savings deposits rate,
  Treasury bill rate (91-day)). Value codes ARE the display texts.
- `POST .../interest.px` with
  `{"query":[{"code":"Rate","selection":{"filter":"item","values":[...]}}],"response":{"format":"json"}}`
  → `{columns, comments, data: [{key: ["2024M07","<rate>"], values: ["30.7"]}]}`.
- Quirks: response starts with a **UTF-8 BOM**; missing observations are simply
  absent cells (series have different lengths); **the table is stale — last
  updated 2024-09-16, data ends 2024M07**; months are `YYYYMmm`.

---

## series/ inventory

| file | columns | rows | range | source |
|---|---|---|---|---|
| tbill_rates.csv | date,tender,security,discount_rate,interest_rate | 1357 | 2013-08-26..2026-08-03 | BoG table 2 (full view; 4 exact dupes dropped) |
| bog_bill_rates.csv | same | 585 | 2016-12-23..2026-03-16 | BoG table 3 (full view) |
| interbank_rate.csv | date,rate | 1716 | 2019-08-05..2026-08-07 | BoG table 69 (daily weighted avg; deduped, 2020-02-25 conflict kept 16.14) |
| interbank_weekly_avg.csv | week_ending,avg_rate | 363 | 2019-07-26..2026-08-07 | BoG table 70 |
| mpc_policy_rate.csv | date,rate | 117 | 2002-11-21..2026-07-22 | BoG table 62 (2 empty-date rows dropped) |
| usdghs_interbank.csv | date,buy,sell,mid | 742 | 2023-08-09..2026-08-07 | BoG table 40, per-column USDGHS filter, 3-year window |
| grr_monthly.csv | date,value | 76 | 2018-04-01..2024-07-01 | GSS statsbank interest.px (stale after 2024M07) |
| gss_interest_rates_monthly.csv | date,rate_name,value | 2235 | 1971-01-01..2024-07-01 | GSS statsbank interest.px, all 6 series long-format |

All series: header row, ISO `YYYY-MM-DD` dates, ascending, values as published
(no unit rescaling). Raw files are byte-exact as fetched.

## Top things the ingestion adapters must handle

1. **Nonce lifecycle + view-vs-truth on BoG**: every wpDataTables pull needs a
   fresh per-table nonce scraped from the host page, and row counts must be read
   from `recordsFiltered`, never `recordsTotal`. Some views are windows
   (table 2 starts 2013; table 31 is latest-day only — history lives in
   table 40 on a different page).
2. **Broken global search**: only per-column `columns[i][search][value]`
   filtering works; `search[value]` silently returns zero rows.
3. **Dirty rows are normal**: empty date cells, exact duplicates, one
   same-date conflict (2020-02-25), `0.00`-as-missing in wide tables,
   thousands-separated IDs, `"%d %b %Y"` dates — parsers need explicit
   drop/dedup/conflict policies, not assertions.
4. **PDF-only auction data**: per-tender security breakdowns exist only as
   layout-formatted PDFs with `GH¢`+thousands amounts and inconsistently
   spaced/hyphenated rate ranges (`5.5000– 5.6795`, `11.3868-12.0000`);
   `pdftotext -layout` is viable but the range tokenizer must be tolerant.
5. **Staleness is source-dependent**: GSS statsbank ends 2024M07 and BoG
   table 21 ends 2023, while tables 69/40 are T+0/T+1 fresh — adapters must
   attach per-source as-of dates and never let a stale monthly source shadow a
   live daily one (matches the market-data supersession-within-source rule).
