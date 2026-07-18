# Q02 — `sample_bank_data/` (named authoritative) vs the actual `data/` package

**Deciding:** what is the canonical Sample Bank raw-source package that steps 2/5/7 run
the adapter pipeline against.

**What the docs say:** all three specs reference `sample_bank_data/` as the MVP dataset
(`storage.md` §5.2/§12.1/Appendix B name `sample_bank_data/README.md`; the brief lists it
as the 4th authoritative doc). No directory named `sample_bank_data/` exists in the repo.

**What actually exists:** `data/` holds the dataset and `data/README.md` **is** the
referenced `sample_bank_data/README.md` (Sample Bank Limited, SBL-GH-001, as-of
2026-04-30, ~GHS 2.4bn, deterministic seed `20260521`). Raw sources present:
- Excel (bank-realistic, multi-sheet): `01_Balance_Sheet_Master.xlsx` … `06_Historical_Data.xlsx`
- CSV (one per canonical entity): `01_institution.csv` … `19_interest_rate_swaps.csv`
- Generator: `data/build_sample_bank_data.py` (byte-identical under the fixed seed)

**Critical:** `data/` is **entirely gitignored** (`git ls-files data/` = 0). So a clean
clone / CI / a second machine has **no** Sample Bank sources — which directly blocks the
brief's step 7 ("all downstream tests run against injected canonical data") and step 5/7
byte-identical multi-source verification from ever passing in CI.

**Default I'm proceeding on:** treat `data/` as the canonical `sample_bank_data/` package
and point the Excel/CSV adapter at those files. For the test-injection work (step 7), the
deterministic generator (`build_sample_bank_data.py`, seed `20260521`) is the right
reproducibility anchor — commit **the generator + a `make sample-bank-data` step** (not the
large raw files) so CI regenerates byte-identical sources, OR commit a small committed
slice as a test fixture.

**Need from Eric/Dela to lock:** (a) confirm `data/` is the intended package; (b) decide
whether we commit the generator (recommended) or a data slice so CI can run the injected
tests — otherwise step 7 cannot pass on a clean checkout.
