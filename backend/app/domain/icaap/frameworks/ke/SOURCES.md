# Citation manifest: `ke/cbk_icaap_gn_2016_11.json`

This file is the source register for the framework above. Every citation in the framework
JSON must appear in the **Citations** table below; `tests/domain/icaap/test_framework_sources.py`
parses this file and fails on any citation that is missing, on any row whose document is not
listed, and on any row nothing cites.

Rules for editing: requirement text is **paraphrase** (no double quotes, at most 280 characters);
section titles are the regulator's printed headings; numeric regulatory values are never
written into the JSON — items reference governed parameter codes (`{param:<code>}`, D-024).

## Provenance of this manifest

Read this before trusting a row below.

- The primary text was extracted and the framework JSON built in the P5 **design** session of
  2026-09-19 (`.ai/icaap/agent-reports/P5-DESIGN.md` §5, generator `.ai/icaap/drafts/p5/gen_ke.py`).
  The **Extractions** row records that reading.
- The PDF itself is **not committed to this repository**. The `sha256` below is the fingerprint of
  the file that was read in that session; it therefore **cannot be re-verified from a checkout**,
  and nothing in the test suite re-hashes it. Treat it as the identifier of the copy to obtain,
  not as a check that has been run here.
- The agent that promoted these files into `app/domain/icaap/frameworks/` (P5-E, 2026-09-20) did
  **not** independently re-read the primary text, and it invented no citation and added or removed
  no requirement. Its edits were: the governed-parameter conversion of the materiality thresholds;
  the cross-framework map's `direction` key; restricting each section's `data_blocks` to the block
  types a cycle can create today, with every dropped type preserved as a `block:` evidence token
  on an item of the same section; and **rewording two requirements** — `ke_4iva_horizon` (¶4(iv)(a))
  and `ke_002b_horizon` (¶2(b)) — so that each references only the governed MINIMUM horizon and
  asks in words whether the longer horizon the Guidance Note prefers was used. The preferred
  horizon has no governed parameter code yet, and D-024 forbids writing the figure into the data.
  Framework note `preferred_horizon_not_yet_governed` records that; see
  `.ai/icaap/agent-reports/P5-E.md` §6.2.
- To raise the assurance: obtain the PDF, confirm the fingerprint below, and re-read the
  paragraphs. A correction ships as a **new framework version** with a `section_key_map` — a
  published version is never edited in place.

## Documents

Machine-parsed. Columns are fixed; `sha256` is `pending` when the PDF is not held.

| doc_id | title | issuer | issued | status | pages | bytes | sha256 |
|---|---|---|---|---|---|---|---|
| CBK-ICAAP-GN-2016 | Guidance Note on Internal Capital Adequacy Assessment Process (ICAAP) | Central Bank of Kenya | 2016-11 | final | 11 | 336211 | 44d28da0d0df0cd8ac5a04f6ae43c8bb09900aaf68719a98348aadaa5c257053 |

## Extractions

Machine-parsed. How the text behind each citation was read.

| extract_id | doc_id | method | date | coverage |
|---|---|---|---|---|
| L1 | CBK-ICAAP-GN-2016 | `pdftotext` of the held PDF (sha256 above; centralbank.go.ke, read in full) | 2026-09-19 | whole document |

`text_status` values as in `gh/SOURCES.md`. `page_basis` is `printed` (page number printed on
the document) or `pdf` (1-based PDF page); `n/r` means the page was not recorded.

## Citations

Machine-parsed. `cite_id` is `<doc_id>:<ref>` exactly as the JSON builds it.

| cite_id | doc_id | ref | page | page_basis | text_status | extract |
|---|---|---|---|---|---|---|
| `CBK-ICAAP-GN-2016:1(a)` | CBK-ICAAP-GN-2016 | 1(a) | 3 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:1(d)` | CBK-ICAAP-GN-2016 | 1(d) | 3 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:1(e)` | CBK-ICAAP-GN-2016 | 1(e) | 3 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(a)` | CBK-ICAAP-GN-2016 | 2(a) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(b)` | CBK-ICAAP-GN-2016 | 2(b) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(c)` | CBK-ICAAP-GN-2016 | 2(c) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(d)` | CBK-ICAAP-GN-2016 | 2(d) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(e)` | CBK-ICAAP-GN-2016 | 2(e) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(f)` | CBK-ICAAP-GN-2016 | 2(f) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(g)` | CBK-ICAAP-GN-2016 | 2(g) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(h)` | CBK-ICAAP-GN-2016 | 2(h) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:2(i)` | CBK-ICAAP-GN-2016 | 2(i) | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:3` | CBK-ICAAP-GN-2016 | 3 | 4 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(a)` | CBK-ICAAP-GN-2016 | 4(i)(a) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(b)` | CBK-ICAAP-GN-2016 | 4(i)(b) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(c)` | CBK-ICAAP-GN-2016 | 4(i)(c) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(d)` | CBK-ICAAP-GN-2016 | 4(i)(d) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(e)` | CBK-ICAAP-GN-2016 | 4(i)(e) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(i)(f)` | CBK-ICAAP-GN-2016 | 4(i)(f) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(a)` | CBK-ICAAP-GN-2016 | 4(ii)(a) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(b)` | CBK-ICAAP-GN-2016 | 4(ii)(b) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(c)` | CBK-ICAAP-GN-2016 | 4(ii)(c) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(d)` | CBK-ICAAP-GN-2016 | 4(ii)(d) | 5 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(e)` | CBK-ICAAP-GN-2016 | 4(ii)(e) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(ii)(f)` | CBK-ICAAP-GN-2016 | 4(ii)(f) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(a)` | CBK-ICAAP-GN-2016 | 4(iii)(a) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(b)` | CBK-ICAAP-GN-2016 | 4(iii)(b) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(c)` | CBK-ICAAP-GN-2016 | 4(iii)(c) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(d)` | CBK-ICAAP-GN-2016 | 4(iii)(d) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(e)` | CBK-ICAAP-GN-2016 | 4(iii)(e) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(f)` | CBK-ICAAP-GN-2016 | 4(iii)(f) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(g)` | CBK-ICAAP-GN-2016 | 4(iii)(g) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(h)` | CBK-ICAAP-GN-2016 | 4(iii)(h) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iii)(i)` | CBK-ICAAP-GN-2016 | 4(iii)(i) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(a)` | CBK-ICAAP-GN-2016 | 4(iv)(a) | 6 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(b)` | CBK-ICAAP-GN-2016 | 4(iv)(b) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(c)` | CBK-ICAAP-GN-2016 | 4(iv)(c) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(d)` | CBK-ICAAP-GN-2016 | 4(iv)(d) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(e)` | CBK-ICAAP-GN-2016 | 4(iv)(e) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(f)` | CBK-ICAAP-GN-2016 | 4(iv)(f) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(g)` | CBK-ICAAP-GN-2016 | 4(iv)(g) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(h)` | CBK-ICAAP-GN-2016 | 4(iv)(h) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(i)` | CBK-ICAAP-GN-2016 | 4(iv)(i) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(iv)(j)` | CBK-ICAAP-GN-2016 | 4(iv)(j) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(v)` | CBK-ICAAP-GN-2016 | 4(v) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(v)(b)` | CBK-ICAAP-GN-2016 | 4(v)(b) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(v)(c)` | CBK-ICAAP-GN-2016 | 4(v)(c) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(v)(d)` | CBK-ICAAP-GN-2016 | 4(v)(d) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(vi)(a)` | CBK-ICAAP-GN-2016 | 4(vi)(a) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(vi)(b)` | CBK-ICAAP-GN-2016 | 4(vi)(b) | 7 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:4(vi)(c)` | CBK-ICAAP-GN-2016 | 4(vi)(c) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:5(a)` | CBK-ICAAP-GN-2016 | 5(a) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:5(b)` | CBK-ICAAP-GN-2016 | 5(b) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:5(c)` | CBK-ICAAP-GN-2016 | 5(c) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:5(d)` | CBK-ICAAP-GN-2016 | 5(d) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:5(f)` | CBK-ICAAP-GN-2016 | 5(f) | 8 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-a` | CBK-ICAAP-GN-2016 | AnnexA-a | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-b` | CBK-ICAAP-GN-2016 | AnnexA-b | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-c` | CBK-ICAAP-GN-2016 | AnnexA-c | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-d` | CBK-ICAAP-GN-2016 | AnnexA-d | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-e` | CBK-ICAAP-GN-2016 | AnnexA-e | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-f` | CBK-ICAAP-GN-2016 | AnnexA-f | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-g` | CBK-ICAAP-GN-2016 | AnnexA-g | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-h` | CBK-ICAAP-GN-2016 | AnnexA-h | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-i` | CBK-ICAAP-GN-2016 | AnnexA-i | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-j` | CBK-ICAAP-GN-2016 | AnnexA-j | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-k` | CBK-ICAAP-GN-2016 | AnnexA-k | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-l` | CBK-ICAAP-GN-2016 | AnnexA-l | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexA-m` | CBK-ICAAP-GN-2016 | AnnexA-m | 9 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB` | CBK-ICAAP-GN-2016 | AnnexB | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-1(a)` | CBK-ICAAP-GN-2016 | AnnexB-1(a) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-1(b)` | CBK-ICAAP-GN-2016 | AnnexB-1(b) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-1(c)` | CBK-ICAAP-GN-2016 | AnnexB-1(c) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(a)` | CBK-ICAAP-GN-2016 | AnnexB-2(a) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(b)` | CBK-ICAAP-GN-2016 | AnnexB-2(b) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(c)` | CBK-ICAAP-GN-2016 | AnnexB-2(c) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(d)` | CBK-ICAAP-GN-2016 | AnnexB-2(d) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(e)` | CBK-ICAAP-GN-2016 | AnnexB-2(e) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-2(f)` | CBK-ICAAP-GN-2016 | AnnexB-2(f) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-3(a)` | CBK-ICAAP-GN-2016 | AnnexB-3(a) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-3(b)` | CBK-ICAAP-GN-2016 | AnnexB-3(b) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-3(c)` | CBK-ICAAP-GN-2016 | AnnexB-3(c) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-3(d)` | CBK-ICAAP-GN-2016 | AnnexB-3(d) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-3(e)` | CBK-ICAAP-GN-2016 | AnnexB-3(e) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-4(a)` | CBK-ICAAP-GN-2016 | AnnexB-4(a) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-4(b)` | CBK-ICAAP-GN-2016 | AnnexB-4(b) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-5(a)` | CBK-ICAAP-GN-2016 | AnnexB-5(a) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-5(b)` | CBK-ICAAP-GN-2016 | AnnexB-5(b) | 10 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-5(c)` | CBK-ICAAP-GN-2016 | AnnexB-5(c) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-5(d)` | CBK-ICAAP-GN-2016 | AnnexB-5(d) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-6(a)` | CBK-ICAAP-GN-2016 | AnnexB-6(a) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-6(b)` | CBK-ICAAP-GN-2016 | AnnexB-6(b) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(a)` | CBK-ICAAP-GN-2016 | AnnexB-7(a) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(b)` | CBK-ICAAP-GN-2016 | AnnexB-7(b) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(c)` | CBK-ICAAP-GN-2016 | AnnexB-7(c) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(d)` | CBK-ICAAP-GN-2016 | AnnexB-7(d) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(e)` | CBK-ICAAP-GN-2016 | AnnexB-7(e) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(f)` | CBK-ICAAP-GN-2016 | AnnexB-7(f) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(g)` | CBK-ICAAP-GN-2016 | AnnexB-7(g) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(h)` | CBK-ICAAP-GN-2016 | AnnexB-7(h) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(i)` | CBK-ICAAP-GN-2016 | AnnexB-7(i) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(j)` | CBK-ICAAP-GN-2016 | AnnexB-7(j) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(k)` | CBK-ICAAP-GN-2016 | AnnexB-7(k) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:AnnexB-7(l)` | CBK-ICAAP-GN-2016 | AnnexB-7(l) | 11 | printed | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:cover` | CBK-ICAAP-GN-2016 | cover | 1 | pdf | local_pdf | L1 |
| `CBK-ICAAP-GN-2016:preface` | CBK-ICAAP-GN-2016 | preface | 2 | printed | local_pdf | L1 |

## Not yet recovered / not used

- Nothing in the Guidance Note was left unread.
- On 2026-09-10 the CBK invited comments on draft revised Prudential Guidelines and Guidance Notes (published as a zip
  archive). Whether it contains a revised ICAAP guidance note was NOT checked (the archive was not downloaded). If it
  does, it ships as a new framework version with a `section_key_map` from `2016.11`, and cycles move by rebase.
- CBK/PG/02, CBK/PG/03 and CBK/PG/20 are named but were not read; no requirement is sourced from them.
- Numeric values (submission months, horizons, review intervals) live in governed parameters, not in this file (D-024).
