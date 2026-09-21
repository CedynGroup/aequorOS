# Report body faces (ICAAP filing PDF)

The four faces the **ICAAP filing PDF** is set in, and the licence they ship
under. They exist because of deviation **DV-004** and decision **D-022**: every
other regulatory PDF is set in reportlab's standard Helvetica, whose repertoire
is Windows-1252, so the Ghana cedi sign `₵` (U+20B5) and the Ghanaian vowels
`Ɛ ɛ Ɔ ɔ Ŋ ŋ Ƒ ƒ Ʋ ʋ Ɖ ɖ` could not be drawn at all — they printed as a visible
`?` with a note saying the exact text lived in the record. A filed document that
cannot print its own currency symbol is not acceptable, so the ICAAP filing PDF
embeds a Unicode family.

| File | Face | reportlab name |
| --- | --- | --- |
| `NotoSans-Regular.ttf` | Noto Sans Regular | `AeqNotoSans` |
| `NotoSans-Bold.ttf` | Noto Sans Bold | `AeqNotoSans-Bold` |
| `NotoSans-Italic.ttf` | Noto Sans Italic | `AeqNotoSans-Italic` |
| `NotoSans-BoldItalic.ttf` | Noto Sans Bold Italic | `AeqNotoSans-BoldItalic` |
| `OFL.txt` | SIL Open Font License 1.1, verbatim | — |

**Scope, deliberately narrow.** Only the `icaap` return family renders through
these files (`exports/icaap_fonts.py` → `exports/icaap_pdf.py`). No other
exporter imports that module, so every existing return's PDF is byte-identical
to what it was before this directory existed — which is the condition D-022
attaches to the change and which the export goldens enforce.

## Why Noto Sans and why the OFL

The SIL Open Font License 1.1 §1 permits redistributing the font files and
embedding them into a document, which is what a filed PDF does — a face we could
not lawfully embed would make the filed instrument itself defective. The
upstream copyright line carries **no Reserved Font Name**, so a derived static
instance may keep the family name.

Noto Sans over the four script faces in `app/services/attestation/fonts/`:
those are signature marks (Caveat, Allura …) with a Latin-only repertoire and no
bold or italic, so none of them can set a document body.

`unitsPerEm == 1000` on all four, asserted in the tests. reportlab tolerates
other grids, but 1000 matches the rest of the repo's embedded faces and keeps
the advance arithmetic exact.

## Provenance and how to regenerate

Upstream publishes Noto Sans as **two variable fonts**, not as static weights,
so the committed files are instances cut locally from those two sources. Both
sources were taken from `github.com/google/fonts` at `main`:

| Source file | SHA-256 |
| --- | --- |
| `ofl/notosans/NotoSans[wdth,wght].ttf` | `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d` |
| `ofl/notosans/NotoSans-Italic[wdth,wght].ttf` | `58e6e0ebd1931b29a365aa2d3e2ee9a9e831a3af7cf3ad1462d4e72154f0b291` |
| `ofl/notosans/OFL.txt` | `cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a` |

Upstream project: `github.com/notofonts/latin-greek-cyrillic` (Noto Sans v2.015,
Latin / Greek / Cyrillic / Devanagari; axes `wdth` 62.5–100, `wght` 100–900).

Each committed file is the default-width instance at one weight, with the name
table updated to the static face name, and **no subsetting** — the whole
upstream glyph set is kept, so a future jurisdiction gets whatever Noto Sans
already covers rather than whatever a repertoire decision taken today allowed
for. reportlab subsets per document at embed time, so the committed size does
not reach the filed PDF.

```python
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

for source, weight, out in (
    ("NotoSans[wdth,wght].ttf", 400, "NotoSans-Regular.ttf"),
    ("NotoSans[wdth,wght].ttf", 700, "NotoSans-Bold.ttf"),
    ("NotoSans-Italic[wdth,wght].ttf", 400, "NotoSans-Italic.ttf"),
    ("NotoSans-Italic[wdth,wght].ttf", 700, "NotoSans-BoldItalic.ttf"),
):
    font = TTFont(source)
    instancer.instantiateVariableFont(
        font, {"wght": weight, "wdth": 100}, inplace=True, updateFontNames=True
    ).save(out)
```

Cut with `fonttools` 4.63.0 (the locked version). A different fontTools release
may produce different bytes; that is not a defect, but it makes the checksums
below stale, so **regenerating means updating this table in the same commit** —
`tests/services/test_icaap_fonts.py` compares the files on disk to it.

| Committed file | Bytes | SHA-256 |
| --- | --- | --- |
| `NotoSans-Regular.ttf` | 626220 | `5910c0f369838b8a926001864ddbe5bcc66128474196e4da3a4df120787fde5f` |
| `NotoSans-Bold.ttf` | 629912 | `7b2533d860a5611598408b67b68550a1e863fa97c9c624e7a7f411b6fd13b74e` |
| `NotoSans-Italic.ttf` | 646460 | `327987775f76a224a127109adab6257e6761a374149b81be9094477768ea0318` |
| `NotoSans-BoldItalic.ttf` | 648564 | `37968fdd1072d485082ebd6646c42a7a9ebfe9a3c3f1604be4ef5d33c107019b` |
| `OFL.txt` | 4396 | `cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a` |

Replacing a file means replacing `OFL.txt` in the same commit if the upstream
licence text changed, and re-running the coverage test — a face that has lost
`₵` would silently take the filing PDF back to printing `?` for the currency the
report is denominated in.
