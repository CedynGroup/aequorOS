# Citation manifest: `ng/cbn_srp_icaap_2021_09.json`

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
  2026-09-19 (`.ai/icaap/agent-reports/P5-DESIGN.md` §5, generator `.ai/icaap/drafts/p5/gen_ng.py`).
  The **Extractions** row records that reading.
- The PDF itself is **not committed to this repository**. The `sha256` below is the fingerprint of
  the file that was read in that session; it therefore **cannot be re-verified from a checkout**,
  and nothing in the test suite re-hashes it. Treat it as the identifier of the copy to obtain,
  not as a check that has been run here.
- The agent that promoted these files into `app/domain/icaap/frameworks/` (P5-E, 2026-09-20) did
  **not** independently re-read the primary text, and it invented no citation and added, removed
  or reworded no requirement. Its edits were: the governed-parameter conversion of the materiality
  thresholds; the cross-framework map's `direction` key; and restricting each section's
  `data_blocks` to the block types a cycle can create today, with every dropped type preserved as
  a `block:` evidence token on an item of the same section. All are described in
  `.ai/icaap/agent-reports/P5-E.md` §6.
- To raise the assurance: obtain the PDF, confirm the fingerprint below, and re-read the
  paragraphs. A correction ships as a **new framework version** with a `section_key_map` — a
  published version is never edited in place.

## Documents

Machine-parsed. Columns are fixed; `sha256` is `pending` when the PDF is not held.

| doc_id | title | issuer | issued | status | pages | bytes | sha256 |
|---|---|---|---|---|---|---|---|
| CBN-SRP-ICAAP-2021 | Revised Guidelines on Supervisory Review Process of Internal Capital Adequacy Assessment Process (SRP/ICAAP) | Central Bank of Nigeria | 2021-09 | final | 23 | 283042 | 8ff844527bf802070f4b50b76a4d6f4e4e3171b3f4203ea67acdc5ad2a663756 |

## Extractions

Machine-parsed. How the text behind each citation was read.

| extract_id | doc_id | method | date | coverage |
|---|---|---|---|---|
| L1 | CBN-SRP-ICAAP-2021 | `pdftotext` of the held PDF (sha256 above; policyvault.africa mirror NGA2288.pdf, read in full) | 2026-09-19 | whole document |

`text_status` values as in `gh/SOURCES.md`. `page_basis` is `printed` (page number printed on
the document) or `pdf` (1-based PDF page); `n/r` means the page was not recorded.

## Citations

Machine-parsed. `cite_id` is `<doc_id>:<ref>` exactly as the JSON builds it.

| cite_id | doc_id | ref | page | page_basis | text_status | extract |
|---|---|---|---|---|---|---|
| `CBN-SRP-ICAAP-2021:10` | CBN-SRP-ICAAP-2021 | 10 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:11(a)` | CBN-SRP-ICAAP-2021 | 11(a) | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:11(b)` | CBN-SRP-ICAAP-2021 | 11(b) | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:11(c)` | CBN-SRP-ICAAP-2021 | 11(c) | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:12` | CBN-SRP-ICAAP-2021 | 12 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:13` | CBN-SRP-ICAAP-2021 | 13 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:14` | CBN-SRP-ICAAP-2021 | 14 | 4 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:15` | CBN-SRP-ICAAP-2021 | 15 | 4 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:16` | CBN-SRP-ICAAP-2021 | 16 | 4 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:17` | CBN-SRP-ICAAP-2021 | 17 | 4 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:18` | CBN-SRP-ICAAP-2021 | 18 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:19` | CBN-SRP-ICAAP-2021 | 19 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:20` | CBN-SRP-ICAAP-2021 | 20 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:21` | CBN-SRP-ICAAP-2021 | 21 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:22` | CBN-SRP-ICAAP-2021 | 22 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:23` | CBN-SRP-ICAAP-2021 | 23 | 5 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:24` | CBN-SRP-ICAAP-2021 | 24 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:25` | CBN-SRP-ICAAP-2021 | 25 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:26` | CBN-SRP-ICAAP-2021 | 26 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:27` | CBN-SRP-ICAAP-2021 | 27 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:28` | CBN-SRP-ICAAP-2021 | 28 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:29` | CBN-SRP-ICAAP-2021 | 29 | 6 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:30` | CBN-SRP-ICAAP-2021 | 30 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:31` | CBN-SRP-ICAAP-2021 | 31 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:32` | CBN-SRP-ICAAP-2021 | 32 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:33` | CBN-SRP-ICAAP-2021 | 33 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:34` | CBN-SRP-ICAAP-2021 | 34 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:35` | CBN-SRP-ICAAP-2021 | 35 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:36` | CBN-SRP-ICAAP-2021 | 36 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:37` | CBN-SRP-ICAAP-2021 | 37 | 7 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:38` | CBN-SRP-ICAAP-2021 | 38 | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:39(a)` | CBN-SRP-ICAAP-2021 | 39(a) | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:39(d)` | CBN-SRP-ICAAP-2021 | 39(d) | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:39(e)` | CBN-SRP-ICAAP-2021 | 39(e) | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:4(a)` | CBN-SRP-ICAAP-2021 | 4(a) | 2 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:4(b)` | CBN-SRP-ICAAP-2021 | 4(b) | 2 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:40` | CBN-SRP-ICAAP-2021 | 40 | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:41` | CBN-SRP-ICAAP-2021 | 41 | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:42` | CBN-SRP-ICAAP-2021 | 42 | 9 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:43` | CBN-SRP-ICAAP-2021 | 43 | 9 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:44` | CBN-SRP-ICAAP-2021 | 44 | 9 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:46` | CBN-SRP-ICAAP-2021 | 46 | 10 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:47` | CBN-SRP-ICAAP-2021 | 47 | 10 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:48` | CBN-SRP-ICAAP-2021 | 48 | 10 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:49` | CBN-SRP-ICAAP-2021 | 49 | 10 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:5.5` | CBN-SRP-ICAAP-2021 | 5.5 | 15 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:50` | CBN-SRP-ICAAP-2021 | 50 | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:51` | CBN-SRP-ICAAP-2021 | 51 | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:52` | CBN-SRP-ICAAP-2021 | 52 | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:53` | CBN-SRP-ICAAP-2021 | 53 | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:54` | CBN-SRP-ICAAP-2021 | 54 | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:55(a)` | CBN-SRP-ICAAP-2021 | 55(a) | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:55(b)` | CBN-SRP-ICAAP-2021 | 55(b) | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:55(c)` | CBN-SRP-ICAAP-2021 | 55(c) | 11 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:56` | CBN-SRP-ICAAP-2021 | 56 | 12 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:56(a)` | CBN-SRP-ICAAP-2021 | 56(a) | 12 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:6` | CBN-SRP-ICAAP-2021 | 6 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:60` | CBN-SRP-ICAAP-2021 | 60 | 13 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:61` | CBN-SRP-ICAAP-2021 | 61 | 13 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:63` | CBN-SRP-ICAAP-2021 | 63 | 13 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:66` | CBN-SRP-ICAAP-2021 | 66 | 14 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:7` | CBN-SRP-ICAAP-2021 | 7 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:71(g)` | CBN-SRP-ICAAP-2021 | 71(g) | 15 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:8` | CBN-SRP-ICAAP-2021 | 8 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:9` | CBN-SRP-ICAAP-2021 | 9 | 3 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-1(a)` | CBN-SRP-ICAAP-2021 | AnnexA-1(a) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-1(b)` | CBN-SRP-ICAAP-2021 | AnnexA-1(b) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-1(c)` | CBN-SRP-ICAAP-2021 | AnnexA-1(c) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(a)` | CBN-SRP-ICAAP-2021 | AnnexA-2(a) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(b)` | CBN-SRP-ICAAP-2021 | AnnexA-2(b) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(c)` | CBN-SRP-ICAAP-2021 | AnnexA-2(c) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(d)` | CBN-SRP-ICAAP-2021 | AnnexA-2(d) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(e)` | CBN-SRP-ICAAP-2021 | AnnexA-2(e) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(f)` | CBN-SRP-ICAAP-2021 | AnnexA-2(f) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-2(g)` | CBN-SRP-ICAAP-2021 | AnnexA-2(g) | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexA-note` | CBN-SRP-ICAAP-2021 | AnnexA-note | 16 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB` | CBN-SRP-ICAAP-2021 | AnnexB | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(a)` | CBN-SRP-ICAAP-2021 | AnnexB-1(a) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(b)` | CBN-SRP-ICAAP-2021 | AnnexB-1(b) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(c)` | CBN-SRP-ICAAP-2021 | AnnexB-1(c) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(d)` | CBN-SRP-ICAAP-2021 | AnnexB-1(d) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(e)` | CBN-SRP-ICAAP-2021 | AnnexB-1(e) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(f)` | CBN-SRP-ICAAP-2021 | AnnexB-1(f) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(g)` | CBN-SRP-ICAAP-2021 | AnnexB-1(g) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(h)` | CBN-SRP-ICAAP-2021 | AnnexB-1(h) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(i)` | CBN-SRP-ICAAP-2021 | AnnexB-1(i) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(j)` | CBN-SRP-ICAAP-2021 | AnnexB-1(j) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(k)` | CBN-SRP-ICAAP-2021 | AnnexB-1(k) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(l)` | CBN-SRP-ICAAP-2021 | AnnexB-1(l) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(m)` | CBN-SRP-ICAAP-2021 | AnnexB-1(m) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-1(n)` | CBN-SRP-ICAAP-2021 | AnnexB-1(n) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-10(a)` | CBN-SRP-ICAAP-2021 | AnnexB-10(a) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-10(b)` | CBN-SRP-ICAAP-2021 | AnnexB-10(b) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-10(c)` | CBN-SRP-ICAAP-2021 | AnnexB-10(c) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-10(d)` | CBN-SRP-ICAAP-2021 | AnnexB-10(d) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-10(e)` | CBN-SRP-ICAAP-2021 | AnnexB-10(e) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-11(a)` | CBN-SRP-ICAAP-2021 | AnnexB-11(a) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-11(b)` | CBN-SRP-ICAAP-2021 | AnnexB-11(b) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-12(a)` | CBN-SRP-ICAAP-2021 | AnnexB-12(a) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-12(b)` | CBN-SRP-ICAAP-2021 | AnnexB-12(b) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-2(a)` | CBN-SRP-ICAAP-2021 | AnnexB-2(a) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-2(b)` | CBN-SRP-ICAAP-2021 | AnnexB-2(b) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-2(c)` | CBN-SRP-ICAAP-2021 | AnnexB-2(c) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(a)` | CBN-SRP-ICAAP-2021 | AnnexB-3(a) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(b)` | CBN-SRP-ICAAP-2021 | AnnexB-3(b) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(c)` | CBN-SRP-ICAAP-2021 | AnnexB-3(c) | 17 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(d)` | CBN-SRP-ICAAP-2021 | AnnexB-3(d) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(e)` | CBN-SRP-ICAAP-2021 | AnnexB-3(e) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(f)` | CBN-SRP-ICAAP-2021 | AnnexB-3(f) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(g)` | CBN-SRP-ICAAP-2021 | AnnexB-3(g) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(h)` | CBN-SRP-ICAAP-2021 | AnnexB-3(h) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-3(i)` | CBN-SRP-ICAAP-2021 | AnnexB-3(i) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-4(a)` | CBN-SRP-ICAAP-2021 | AnnexB-4(a) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-4(b)` | CBN-SRP-ICAAP-2021 | AnnexB-4(b) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-4(c)` | CBN-SRP-ICAAP-2021 | AnnexB-4(c) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-4(d)` | CBN-SRP-ICAAP-2021 | AnnexB-4(d) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(a)` | CBN-SRP-ICAAP-2021 | AnnexB-5(a) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(b)` | CBN-SRP-ICAAP-2021 | AnnexB-5(b) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(c)` | CBN-SRP-ICAAP-2021 | AnnexB-5(c) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(d)` | CBN-SRP-ICAAP-2021 | AnnexB-5(d) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(e)` | CBN-SRP-ICAAP-2021 | AnnexB-5(e) | 18 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-5(f)` | CBN-SRP-ICAAP-2021 | AnnexB-5(f) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(a)` | CBN-SRP-ICAAP-2021 | AnnexB-6(a) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(b)` | CBN-SRP-ICAAP-2021 | AnnexB-6(b) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(c)` | CBN-SRP-ICAAP-2021 | AnnexB-6(c) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(d)` | CBN-SRP-ICAAP-2021 | AnnexB-6(d) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(e)` | CBN-SRP-ICAAP-2021 | AnnexB-6(e) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(f)` | CBN-SRP-ICAAP-2021 | AnnexB-6(f) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6(g)` | CBN-SRP-ICAAP-2021 | AnnexB-6(g) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6p20(a)` | CBN-SRP-ICAAP-2021 | AnnexB-6p20(a) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6p20(b)` | CBN-SRP-ICAAP-2021 | AnnexB-6p20(b) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6p20(c)` | CBN-SRP-ICAAP-2021 | AnnexB-6p20(c) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6p20(d)` | CBN-SRP-ICAAP-2021 | AnnexB-6p20(d) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-6p20(e)` | CBN-SRP-ICAAP-2021 | AnnexB-6p20(e) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(a)` | CBN-SRP-ICAAP-2021 | AnnexB-7(a) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(b)` | CBN-SRP-ICAAP-2021 | AnnexB-7(b) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(c)` | CBN-SRP-ICAAP-2021 | AnnexB-7(c) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(d)` | CBN-SRP-ICAAP-2021 | AnnexB-7(d) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(e)` | CBN-SRP-ICAAP-2021 | AnnexB-7(e) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(f)` | CBN-SRP-ICAAP-2021 | AnnexB-7(f) | 19 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-7(g)` | CBN-SRP-ICAAP-2021 | AnnexB-7(g) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(a)` | CBN-SRP-ICAAP-2021 | AnnexB-8(a) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(b)` | CBN-SRP-ICAAP-2021 | AnnexB-8(b) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(c)` | CBN-SRP-ICAAP-2021 | AnnexB-8(c) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(d)` | CBN-SRP-ICAAP-2021 | AnnexB-8(d) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(e)` | CBN-SRP-ICAAP-2021 | AnnexB-8(e) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(f)` | CBN-SRP-ICAAP-2021 | AnnexB-8(f) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(g)` | CBN-SRP-ICAAP-2021 | AnnexB-8(g) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-8(h)` | CBN-SRP-ICAAP-2021 | AnnexB-8(h) | 20 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(a)` | CBN-SRP-ICAAP-2021 | AnnexB-9(a) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(b)` | CBN-SRP-ICAAP-2021 | AnnexB-9(b) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(c)` | CBN-SRP-ICAAP-2021 | AnnexB-9(c) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(d)` | CBN-SRP-ICAAP-2021 | AnnexB-9(d) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(e)` | CBN-SRP-ICAAP-2021 | AnnexB-9(e) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(f)` | CBN-SRP-ICAAP-2021 | AnnexB-9(f) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:AnnexB-9(g)` | CBN-SRP-ICAAP-2021 | AnnexB-9(g) | 21 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:cover` | CBN-SRP-ICAAP-2021 | cover | 1 | pdf | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:fn2` | CBN-SRP-ICAAP-2021 | fn2 | 8 | printed | local_pdf | L1 |
| `CBN-SRP-ICAAP-2021:fn3` | CBN-SRP-ICAAP-2021 | fn3 | 9 | printed | local_pdf | L1 |

## Not yet recovered / not used

- Nothing in the Guidelines was left unread. The mirror host is not the CBN; obtain the copy from cbn.gov.ng and
  confirm the sha256 before publishing this framework.
- The CBN Guidelines on Stress Testing, the Supervisory Intervention Framework, the Capital Add-on Framework and the
  D-SIB framework are named but were not read; no requirement is sourced from them.
- Numeric values (submission months, review intervals) live in governed parameters, not in this file (D-024).
