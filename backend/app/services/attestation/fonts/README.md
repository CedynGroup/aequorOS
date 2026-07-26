# Bundled signature faces

Four script typefaces an officer can adopt a **typed** signature mark in
(`app/services/attestation/typed_fonts.py`), plus the licence each one ships
under. They are committed rather than fetched at build or request time: the
appearance of a signature on a filed regulatory return may not depend on a
third party being reachable, and the dashboard's preview must show the signer
the same face the PDF will stamp — one file, both consumers.

| Key              | File                        | Family        | Licence                            |
| ---------------- | --------------------------- | ------------- | ---------------------------------- |
| `caveat`         | `Caveat-Regular.ttf`        | Caveat        | SIL Open Font License 1.1 (`Caveat-OFL.txt`) |
| `dancing_script` | `DancingScript-Regular.ttf` | Dancing Script| SIL Open Font License 1.1 (`DancingScript-OFL.txt`) |
| `great_vibes`    | `GreatVibes-Regular.ttf`    | Great Vibes   | SIL Open Font License 1.1 (`GreatVibes-OFL.txt`) |
| `allura`         | `Allura-Regular.ttf`        | Allura        | SIL Open Font License 1.1 (`Allura-OFL.txt`) |

The OFL is the reason these four and not others: §1 permits redistribution of
the font files, and §1's "may be … embedded" clause covers embedding a subset
into a document we then hand to a regulator. A face we could not lawfully embed
would make the filed PDF itself defective, which is worse than an ugly mark.

Homemade Apple is a natural fifth candidate and was **not** bundled: Google
Fonts ships it under Apache-2.0, not the OFL, and a licence-mixed font directory
invites the wrong file being added later. Allura takes that slot.

**Every file here must have `unitsPerEm == 1000`** — asserted in the tests.
pyHanko's OpenType path writes raw font-unit advances into the CIDFont `/W`
array, which PDF reads as thousandths of an em, so a face on a 2048-unit grid
(Sacramento and Parisienne, both otherwise fine OFL scripts) stamps a signature
with its letters flung apart. Check a candidate's `head.unitsPerEm` before
adding it.

## Provenance

Static Latin `.ttf` instances as served by Google Fonts' v1 CSS API
(`fonts.googleapis.com/css?family=…` with a TrueType-era user agent, which
resolves to `fonts.gstatic.com/s/<family>/…​.ttf`); licence texts verbatim from
`github.com/google/fonts` (`ofl/<family>/OFL.txt`). Caveat and Dancing Script
are variable fonts upstream — the files here are the regular-weight static
instances Google Fonts itself distributes, so no local instancing step stands
between upstream and what we embed.

Replacing a file means replacing its licence file in the same commit, and
adding a face means adding it to `TYPED_SIGNATURE_FONTS`
(`app/models/attestation.py`) *and* to the dashboard's preview map — a face the
backend stamps but the browser cannot preview is a signer adopting a mark they
were never shown.
