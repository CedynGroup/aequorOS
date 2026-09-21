# Citation manifest: `gh/bog_icaap_2026_02_ed.json`

This file is the source register for the Ghana ICAAP framework. Every citation in the
framework JSON must appear in the **Citations** table below;
`tests/domain/icaap/test_framework_sources.py` parses this file and fails on any citation
that is missing, on any row whose document is not listed, and on any row nothing cites.

Rules for editing:

- Requirement text in the JSON is **paraphrase**. It never quotes the source (no double
  quotes, at most 280 characters per item). Section titles are the regulator's printed
  ¶49 headings.
- Add a row here in the same change that adds a citation. Do not cite a paragraph whose
  text has not been read; list it as `pending` in the *Not yet recovered* list instead.
- A published framework version is never edited in place. A corrected extraction ships
  as a new version with a `section_key_map`, and cycles move with `rebase`.

## Documents

Machine-parsed. Columns are fixed; `sha256` is `pending` when the PDF is not held.

| doc_id | title | issuer | issued | status | pages | bytes | sha256 |
|---|---|---|---|---|---|---|---|
| BOG-ICAAP-ED | Guideline on Internal Capital Adequacy Assessment Process (ICAAP) | Bank of Ghana | 2026-02 | exposure_draft | 37 | 695115 | pending |
| BOG-STRESS-ED | Guideline on Stress Testing | Bank of Ghana | 2026-02 | exposure_draft | 47 | 729359 | 3f240bebab965706b716a6e6538f863da1ed8c7cadb02987e369494cf089d602 |
| BOG-LRMD-ED | Liquidity Risk Management Directive | Bank of Ghana | 2026-02 | exposure_draft | 31 | 495404 | d442cdd4df97aa1b0699b48cf5fae9cea6c5361da1ef778877d78ac0253abfa3 |

## Extractions

Machine-parsed. How the text behind each citation was read.

| extract_id | doc_id | method | date | coverage |
|---|---|---|---|---|
| E1 | BOG-ICAAP-ED | text extraction of the published PDF | 2026-07-16 | ¶42 (first sentence), ¶43–¶52(b), ¶71–¶82 with fn 8, Appendix headings 1–14 and items 1(a)–(e), 2(a)–(g) |
| E2 | BOG-ICAAP-ED | text extraction of the published PDF | 2026-08-21 | ¶1–¶10, ¶28, ¶29(a)–(b), Appendix items 7(a)–(b), 8(a)–(d), 9(a) |
| L1 | BOG-STRESS-ED | `pdftotext -layout` of the held PDF (sha256 above) | 2026-09-19 | whole document |
| L2 | BOG-LRMD-ED | `pdftotext -layout` of the held PDF (sha256 above) | 2026-09-19 | whole document |

`text_status` values: `verbatim_extract` (paragraph read in full), `partial_extract`
(paragraph read in part; the item paraphrases only the part read), `heading_extract`
(only the heading was read; used for section and category titles), `local_pdf` (read
from the held PDF). `page_basis` is `printed` (page number printed on the document) or
`pdf` (1-based PDF page); `n/r` means the page was not recorded.

## Citations

Machine-parsed. `cite_id` is `<doc_id>:<ref>` exactly as the JSON builds it.

| cite_id | doc_id | ref | page | page_basis | text_status | extract |
|---|---|---|---|---|---|---|
| `BOG-ICAAP-ED:3` | BOG-ICAAP-ED | 3 | 1 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:4` | BOG-ICAAP-ED | 4 | 1 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:5` | BOG-ICAAP-ED | 5 | 1 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:9` | BOG-ICAAP-ED | 9 | 6 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:28` | BOG-ICAAP-ED | 28 | n/r | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:29(a)` | BOG-ICAAP-ED | 29(a) | n/r | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:29(b)` | BOG-ICAAP-ED | 29(b) | n/r | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:42` | BOG-ICAAP-ED | 42 | n/r | printed | partial_extract | E1 |
| `BOG-ICAAP-ED:43` | BOG-ICAAP-ED | 43 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:44` | BOG-ICAAP-ED | 44 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:45` | BOG-ICAAP-ED | 45 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:46` | BOG-ICAAP-ED | 46 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:47` | BOG-ICAAP-ED | 47 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:48` | BOG-ICAAP-ED | 48 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:49` | BOG-ICAAP-ED | 49 | 15 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:49(a)` | BOG-ICAAP-ED | 49(a) | 15 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(b)` | BOG-ICAAP-ED | 49(b) | 15 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(c)` | BOG-ICAAP-ED | 49(c) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(d)` | BOG-ICAAP-ED | 49(d) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(e)` | BOG-ICAAP-ED | 49(e) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(e)-fn5` | BOG-ICAAP-ED | 49(e)-fn5 | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:49(f)` | BOG-ICAAP-ED | 49(f) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(g)` | BOG-ICAAP-ED | 49(g) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(h)` | BOG-ICAAP-ED | 49(h) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(i)` | BOG-ICAAP-ED | 49(i) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(j)` | BOG-ICAAP-ED | 49(j) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(k)` | BOG-ICAAP-ED | 49(k) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(l)` | BOG-ICAAP-ED | 49(l) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(m)` | BOG-ICAAP-ED | 49(m) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(n)` | BOG-ICAAP-ED | 49(n) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(o)` | BOG-ICAAP-ED | 49(o) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(p)` | BOG-ICAAP-ED | 49(p) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:49(q)` | BOG-ICAAP-ED | 49(q) | 16 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:50(a)` | BOG-ICAAP-ED | 50(a) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(b)(i)` | BOG-ICAAP-ED | 50(b)(i) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(b)(ii)` | BOG-ICAAP-ED | 50(b)(ii) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(c)` | BOG-ICAAP-ED | 50(c) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(d)` | BOG-ICAAP-ED | 50(d) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(e)` | BOG-ICAAP-ED | 50(e) | 16 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(f)` | BOG-ICAAP-ED | 50(f) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(g)` | BOG-ICAAP-ED | 50(g) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:50(h)` | BOG-ICAAP-ED | 50(h) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(a)` | BOG-ICAAP-ED | 51(a) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(b)` | BOG-ICAAP-ED | 51(b) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(c)` | BOG-ICAAP-ED | 51(c) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(d)` | BOG-ICAAP-ED | 51(d) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(e)` | BOG-ICAAP-ED | 51(e) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:51(f)` | BOG-ICAAP-ED | 51(f) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:52(a)` | BOG-ICAAP-ED | 52(a) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:52(b)` | BOG-ICAAP-ED | 52(b) | 17 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:71` | BOG-ICAAP-ED | 71 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:72` | BOG-ICAAP-ED | 72 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:73` | BOG-ICAAP-ED | 73 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:73-fn8` | BOG-ICAAP-ED | 73-fn8 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:74` | BOG-ICAAP-ED | 74 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:75` | BOG-ICAAP-ED | 75 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:76` | BOG-ICAAP-ED | 76 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:77` | BOG-ICAAP-ED | 77 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:78` | BOG-ICAAP-ED | 78 | 26 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:79` | BOG-ICAAP-ED | 79 | 27 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:80` | BOG-ICAAP-ED | 80 | 27 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:81` | BOG-ICAAP-ED | 81 | 27 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:82` | BOG-ICAAP-ED | 82 | 27 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App` | BOG-ICAAP-ED | App | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1` | BOG-ICAAP-ED | App-1 | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1(a)` | BOG-ICAAP-ED | App-1(a) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1(b)` | BOG-ICAAP-ED | App-1(b) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1(c)` | BOG-ICAAP-ED | App-1(c) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1(d)` | BOG-ICAAP-ED | App-1(d) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-1(e)` | BOG-ICAAP-ED | App-1(e) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2` | BOG-ICAAP-ED | App-2 | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(a)` | BOG-ICAAP-ED | App-2(a) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(b)` | BOG-ICAAP-ED | App-2(b) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(c)` | BOG-ICAAP-ED | App-2(c) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(d)` | BOG-ICAAP-ED | App-2(d) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(e)` | BOG-ICAAP-ED | App-2(e) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(f)` | BOG-ICAAP-ED | App-2(f) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-2(g)` | BOG-ICAAP-ED | App-2(g) | 28 | printed | verbatim_extract | E1 |
| `BOG-ICAAP-ED:App-3` | BOG-ICAAP-ED | App-3 | 29 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-4` | BOG-ICAAP-ED | App-4 | 29 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-5` | BOG-ICAAP-ED | App-5 | 29 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-6` | BOG-ICAAP-ED | App-6 | 29 | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-7` | BOG-ICAAP-ED | App-7 | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-7(a)` | BOG-ICAAP-ED | App-7(a) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-7(b)` | BOG-ICAAP-ED | App-7(b) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-8` | BOG-ICAAP-ED | App-8 | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-8(a)` | BOG-ICAAP-ED | App-8(a) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-8(b)` | BOG-ICAAP-ED | App-8(b) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-8(c)` | BOG-ICAAP-ED | App-8(c) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-8(d)` | BOG-ICAAP-ED | App-8(d) | 30 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-9` | BOG-ICAAP-ED | App-9 | 31 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-9(a)` | BOG-ICAAP-ED | App-9(a) | 31 | printed | verbatim_extract | E2 |
| `BOG-ICAAP-ED:App-10` | BOG-ICAAP-ED | App-10 | n/r | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-11` | BOG-ICAAP-ED | App-11 | n/r | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-12` | BOG-ICAAP-ED | App-12 | n/r | printed | partial_extract | E1 |
| `BOG-ICAAP-ED:App-13` | BOG-ICAAP-ED | App-13 | n/r | printed | heading_extract | E1 |
| `BOG-ICAAP-ED:App-14` | BOG-ICAAP-ED | App-14 | n/r | printed | heading_extract | E1 |
| `BOG-LRMD-ED:12` | BOG-LRMD-ED | 12 | 11 | pdf | local_pdf | L2 |
| `BOG-LRMD-ED:24` | BOG-LRMD-ED | 24 | 14 | pdf | local_pdf | L2 |
| `BOG-LRMD-ED:25` | BOG-LRMD-ED | 25 | 14 | pdf | local_pdf | L2 |
| `BOG-LRMD-ED:26` | BOG-LRMD-ED | 26 | 14 | pdf | local_pdf | L2 |
| `BOG-LRMD-ED:27` | BOG-LRMD-ED | 27 | 14 | pdf | local_pdf | L2 |
| `BOG-STRESS-ED:20` | BOG-STRESS-ED | 20 | 14 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:34` | BOG-STRESS-ED | 34 | 18 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:35` | BOG-STRESS-ED | 35 | 18 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:36` | BOG-STRESS-ED | 36 | 18 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:63` | BOG-STRESS-ED | 63 | 24 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67` | BOG-STRESS-ED | 67 | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(a)` | BOG-STRESS-ED | 67(a) | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(b)` | BOG-STRESS-ED | 67(b) | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(c)` | BOG-STRESS-ED | 67(c) | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(d)` | BOG-STRESS-ED | 67(d) | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(e)` | BOG-STRESS-ED | 67(e) | 25 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(f)` | BOG-STRESS-ED | 67(f) | 26 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(g)` | BOG-STRESS-ED | 67(g) | 26 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(h)` | BOG-STRESS-ED | 67(h) | 26 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:67(i)` | BOG-STRESS-ED | 67(i) | 26 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:68` | BOG-STRESS-ED | 68 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:69` | BOG-STRESS-ED | 69 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:70` | BOG-STRESS-ED | 70 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:71` | BOG-STRESS-ED | 71 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:72` | BOG-STRESS-ED | 72 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:73` | BOG-STRESS-ED | 73 | 27 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:74` | BOG-STRESS-ED | 74 | 27-28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:75(a)` | BOG-STRESS-ED | 75(a) | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:75(b)` | BOG-STRESS-ED | 75(b) | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:75(c)` | BOG-STRESS-ED | 75(c) | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:76` | BOG-STRESS-ED | 76 | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:77` | BOG-STRESS-ED | 77 | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:78` | BOG-STRESS-ED | 78 | 28 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:79` | BOG-STRESS-ED | 79 | 28-29 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:80` | BOG-STRESS-ED | 80 | 29 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:81` | BOG-STRESS-ED | 81 | 29 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-II-4` | BOG-STRESS-ED | App-II-4 | 38 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-II-FP(a)` | BOG-STRESS-ED | App-II-FP(a) | 40 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-II-FP(b)` | BOG-STRESS-ED | App-II-FP(b) | 40 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-II-FP(c)` | BOG-STRESS-ED | App-II-FP(c) | 41 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-II-T5` | BOG-STRESS-ED | App-II-T5 | 45 | pdf | local_pdf | L1 |
| `BOG-STRESS-ED:App-III` | BOG-STRESS-ED | App-III | 46 | pdf | local_pdf | L1 |

## Not yet recovered (pending primary text)

Recorded so that nobody fills the gap from memory or from another regulator's text.

- **BOG-ICAAP-ED ¶53–¶70**: the paragraphs behind sections (d)–(q). Fragments seen in
  extraction (a Pillar 2 item on defining scenarios, an internal-audit scope item, a
  challenge-scope item, and a data-scope item) are not used: their paragraph numbers and
  full text are unknown.
- **BOG-ICAAP-ED ¶11–¶41** (except ¶28, ¶29(a)–(b) and the first sentence of ¶42), including
  ¶24 and ¶26 (headings only) and ¶27 (fragment).
- **BOG-ICAAP-ED ¶52(c) onward**, ¶29(c) onward, and Appendix items for categories 3–6
  and 10–14.
- **BOG-ICAAP-ED ¶7 footnote 2** (unexpected versus expected loss): partial only.
- The ICAAP PDF itself: obtain it, record its sha256 above, re-extract ¶53–¶70, and ship
  the result as framework version `2026.02-ed.2` with an identity `section_key_map`.
