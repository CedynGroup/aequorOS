# Power BI Stage A — governed exports into Power BI: the loading template

**Audience:** a bank analyst or MIS officer who wants AequorOS figures in a Power BI
Desktop model without a report-server integration. **Status:** as-built for
`docs/bi.md` §Phase 2 "Power BI Stage A: governed exports plus a documented
template", written 2026-09-29 (audit A360-7 S4 found the spec promised this
document and nothing existed). **Companion:** `powerbi_stage_b.md` is the machine
feed for a report server — read that when you want a scheduled refresh rather than
a file.

## What Stage A is, and is not

Stage A is a **file**: a person with BI read authority exports one governed query
as CSV, an Excel workbook or a PDF, and opens it in Power BI Desktop with
**Get Data**. There is no credential to hold, no connection string and no scheduled
refresh — to refresh, export again. That is the whole of Stage A, and it is the
right first step for a bank that is still deciding whether to switch its separate
Power BI project off, because it costs nothing to set up and everything in the file
is already authorized, watermarked and recorded.

"Template" here means the loading recipe and the modelling rules below. AequorOS
does **not** ship a Power BI template binary (`.pbit`); if your team wants one,
build it from this recipe once and keep it in your own repository.

Stage A is not a live connection. A number in your model is as old as the export
that produced it, and the export says so (see "As at" below).

## Getting the file

From the product: **Explore → Export**, then CSV, Excel workbook or PDF
(`components/bi/ExportActions.tsx`). "Print or save as PDF" on the same menu is
the browser's own print dialog over the screen and carries no provenance block; use
the governed PDF for anything that leaves the building.

From the API, the same thing:

```
POST /api/v1/banks/{BK-XXXXXXXX}/bi/export
Authorization: Bearer <a signed-in user's access token>
{ "query": { …a BiQuery… }, "format": "csv" | "xlsx" | "pdf" }
```

- Up to `BI_EXPORT_ASYNC_THRESHOLD_ROWS` (10,000 by default) the response IS the
  file (`200`, `Content-Disposition: attachment`).
- Above it the response is `202` with a job; poll
  `GET /api/v1/banks/{BK}/bi/exports/{job_id}` until `state` is `ready` and follow
  the short-lived `download_url`. Only the principal who asked for the export can
  read that job — anyone else gets `404`.
- The export row cap is `BI_EXPORT_ROW_CAP` (100,000 by default) and the SQL runs
  under `BI_EXPORT_TIMEOUT_MS` (120 s). Larger questions are Stage B's.
- The file is named `{BK}-analytics-{window}.{csv|xlsx|pdf}` — institution,
  reporting window, format. No clock, so two exports of the same question have the
  same name.

**Who may.** A summary export (aggregated measures) needs a `view` sentence for
every module and sensitivity the query touches. A record-level or confidential
export additionally needs `export`, which only the Analyst bundle carries. The
class is read from the catalogue members the query touches — there is no "this is
confidential" flag on the request — and it is re-checked after compilation, where
the second check can only refuse. Every export writes `bi_query_log` and an
`audit_events` row.

## What is in the file

Every format carries the same provenance fields, in this order, as the
platform names them (`services/bi/exports/context.py::METADATA_FIELDS` — read the
tuple for the authoritative list; one entry below is marked removed):

| Field | What to do with it in your model |
|---|---|
| **Query** | The catalogue member ids the export answers. Keep it as a text measure or in a provenance table; it is the only unambiguous statement of what a column is. |
| **As at** | The reporting date (or window) the figures describe. Put it on the report page. A Stage A model has no other freshness signal. |
| **Data confidence** | **REMOVED BY FOUNDER DECISION 2026-09-29.** This field carried a reconciliation verdict (`Reconciled` / `Reconciled with exceptions` / `Does not reconcile` / `Not assessed`) of the BI figures against the platform's regulatory returns. BI is analytics over your institution's own treasury data and carries no such verdict; the row is kept so a file exported before the change still reads. Freshness is **As at** together with the build the export records. |
| **Catalogue version** | Which measure definitions produced the file. A version change means a column may have changed meaning; do not merge exports across versions without reading the change. |
| **Data scope** | `Whole institution`, or the slice the exporter's grant admits (`Branches: B001, B002`; `Regions: Northern · 4 branches in scope`). A file that looks institution-wide and is not is exactly the misreading this field exists to prevent — display it. |
| **Exported by** | The person. The watermark repeats it. |

Then the grid: the header row in the compiler's column order, then the rows.
Column names are **catalogue member ids** (`loans.balance_rc`, `branch.code`) —
stable wire keys, never display labels.

- **CSV** (`services/bi/exports/csv.py`): a `field,value` provenance block (the
  six fields, then supplementary ones), one blank line, the header, the rows.
  Cells are plain decimals with a minus sign, no thousands separators and no unit
  symbols; the unit is stated once in the provenance block and in the amount
  columns' headings. Any text cell that could execute in a spreadsheet is made
  inert before it is written.
- **XLSX** (`services/bi/exports/xlsx.py`): two sheets, both protected against
  in-place edits. **`Data`** is the grid and nothing else — header row, then rows,
  native numeric types — so a pivot or Power Query reads it without a skip count.
  **`Export metadata`** is the provenance block. The page header carries the
  watermark (`<institution> · <exporter>`) and the footer the standing note
  *"Management information. Not a regulatory return and not a signed record of
  filing."*
- **PDF** (`services/bi/exports/pdf.py`): byte-deterministic, the same block and
  grid, for circulation rather than modelling.

**Prefer the workbook for Power BI.** Its `Data` sheet needs no parsing of the
provenance block, and the block itself arrives as a second table you can keep in
the model.

## The loading recipe (Power Query M)

Two queries from the same workbook. Replace the path; nothing else needs editing.

```
// Query 1 — the figures
let
    Source  = Excel.Workbook(File.Contents("C:\exports\BK-XXXXXXXX-analytics-2026-08-31.xlsx"), null, true),
    Data    = Source{[Item = "Data", Kind = "Sheet"]}[Data],
    Headers = Table.PromoteHeaders(Data, [PromoteAllScalars = true])
in
    Headers
```

```
// Query 2 — the provenance, kept in the model so the report can display it
let
    Source   = Excel.Workbook(File.Contents("C:\exports\BK-XXXXXXXX-analytics-2026-08-31.xlsx"), null, true),
    Metadata = Source{[Item = "Export metadata", Kind = "Sheet"]}[Data],
    Fields   = Table.RenameColumns(Metadata, {{"Column1", "Field"}, {"Column2", "Value"}})
in
    Fields
```

For CSV, the provenance block precedes the grid, so a plain `Csv.Document` load
puts the block into the first rows. Load with `Csv.Document(File.Contents(path))`,
find the first blank row, skip past it and promote the next row to headers. Because
the block's length is not fixed (supplementary fields follow the six), do not
hard-code a skip count; find the blank line.

## Modelling rules the file assumes

1. **Empty is not zero.** A blank cell or missing value means "nothing to measure
   here" — a loan with no stated collateral, a branch whose whole book is
   unconverted foreign currency. Leave it `null`. Do not `COALESCE` to 0, do not
   "Replace Values" to 0, and set your visuals to show blanks as blanks. A zero in a
   provision column is a statement about the bank.
2. **Amounts are decimals in the institution's reporting currency**, stated in the
   provenance block. Set the column type to Decimal Number, not Fixed Decimal, and
   do not add a currency symbol from your own locale.
3. **Ratios are already ratios.** A `*_pct` column is a percentage the platform
   computed as a ratio of sums over the whole population it names. Do not average a
   ratio column across rows; if you need a different grain, export that grain.
4. **Column names are wire keys.** Rename them for display in the visual, not in
   the query, so the member id survives to the next export.
5. **Do not merge exports of different Data scope or Catalogue version** without
   reading both fields first. A branch-scoped file and an institution-wide file are
   different populations under the same headings.
6. **Show As at on every page** that shows a figure. A board pack built from a
   Stage A export is a claim about a date; the file carries it so the report can
   too. (Until 2026-09-29 this rule also asked for Data confidence — removed by
   founder decision, see the field table above.)

## Residency and record

The file leaves the platform and lands wherever your workstation puts it. The
platform records who exported what and when (`bi_query_log`, `audit_events`) and
watermarks every page with the institution and the exporter; what happens to the
file after that is governed by your institution's own information-security
position. For the data-residency caveat that applies once you connect a report
server (Stage B), read `powerbi_stage_b.md` §5 — it is the same question, asked
about a connection instead of a file.

## When to move to Stage B

Stage A stops being the right answer when any of these becomes true: you want a
scheduled refresh; more than one person maintains the model; you need more than
100,000 rows; or the model must restate a past month when the platform does. Each
of those is what the Stage B feed's cursor and replace-by-reporting-date contract
exist for.
