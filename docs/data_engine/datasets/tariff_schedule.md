# `tariff_schedule` — charges / tariff register keyed by the official BSD15 rows

**Kind:** `tariff_schedule` (reference dataset; `REFERENCE_DATASET_KINDS`, migration `202608160014`)
· **Feeds:** BSD15A `Domestic charges of banks` (168 text cells, column C) · BSD15A `Range of pdts
i.r.o sav & cur ` (77 cedi-amount cells, column B) · BSD15B `International Banking Charges` (309
text cells, column B) · **Schema:** `backend/app/domain/ingestion/reference_schemas/tariff_schedule.py`
· **Sample Bank:** `backend/onboarding/sample_bank/tariff_schedule.csv` (552 rows) + `…_template.csv`
· **Loader:** `backend/scripts/ingest_push.py --reference tariff_schedule=<csv>`

## What it is

The bank's published tariff guide, laid out **exactly as BoG's two charges returns ask for it**:
one row per official tariff cell. BSD15A/BSD15B are weekly (9-day) returns of *what the bank
charges*, not of amounts earned, so the register changes only when the tariff guide changes — push
it once, re-push on every tariff revision (`as_of_date` = the date the new guide takes effect; the
returns read the latest register on/before the reporting date).

The official row is addressed by `(form, sheet, row_key)`. `row_key` is generated from the line
maps (`bog_forms/linemaps/bsd15a.tariff_row_keys`) and is stable and bank-facing:
`"<item>.<n>"` — the official item the value row sits under (the number in column A / the `n. `
label prefix, or the product code in parentheses on the Range sheet: `S1`…`S7`, `C1`…`C3`, `FX`
for the un-coded FOREIGN ACCOUNTS block) and the 1-based ordinal of the value row within that
item. The full key list is in §5 (generated — regenerate, never hand-edit).

## Grain

**One row per official tariff cell:** `(form, sheet, row_key)` unique within a push. Rows the
bank does not fill may simply be omitted (the cell stays blank and is listed in the return's
Completion notes as `input_required`).

## Fields

| Field | Type | Required | Values / unit | Notes |
|---|---|---|---|---|
| `form` | enum | yes | `BSD15A` \| `BSD15B` | official return |
| `sheet` | enum | yes | `DOMESTIC` \| `RANGE` \| `INTL` | bank-facing sheet code: `DOMESTIC` = `Domestic charges of banks`, `RANGE` = `Range of pdts i.r.o sav & cur `, `INTL` = `International Banking Charges` |
| `row_key` | text | yes | see §5 | the official row, e.g. `1.1` (BSD15A DOMESTIC: COT minimum), `S1.1` (RANGE: Savings S1 initial deposit), `17.1` (BSD15B INTL: account closure) |
| `charge_value` | text / number | yes | text on `DOMESTIC` / `INTL` (`0.5% of value, min GHS 30, max GHS 250`); a **cedi amount** on `RANGE` ("Amounts in Cedis") | **this is what the return prints in the cell**, verbatim; the platform never composes it from the structured fields |
| `label` | text | no | official label | for the bank's own readability; ignored by the return |
| `charge_basis` | enum | no | `flat` \| `percent` \| `per_item` \| `range` | structured reading of the tariff |
| `min_ghs` | number | no | cedis | floor of a percentage / range tariff |
| `max_ghs` | number | no | cedis | cap of a percentage / range tariff |
| `currency` | text | no | ISO 4217 | `GHS` unless the tariff is quoted in a foreign currency (some BSD15B lines are quoted in USD — say so in `charge_value`) |
| `effective_from` | date | no | ISO | date the tariff took effect |
| `notes` | text | no | | free text |

## Validation rules

- required: `form`, `sheet`, `row_key`, `charge_value`; enums exact (`form`, `sheet`,
  `charge_basis`); `min_ghs` / `max_ghs` numeric.
- `RANGE` rows must carry a **number** in `charge_value` (the return binds them `numeric=True`;
  text resolves to blank).
- **Never write `N/A`, `Nil`, `-`, `none`, `null`, `TBC` as `charge_value`.** The Data Engine's
  type coercer treats these as null sentinels on every ingested value (blank cell →
  `input_required`). Spell it out: `Free`, `Not applicable`, `Not offered`.
- Unknown `row_key`s are preserved (rows land verbatim) but feed no cell — check §5.
- Text is preserved as typed (NFC + whitespace-collapsed by the ETL text normaliser).

## How the returns read it (`refs.field`)

Every tariff cell of BSD15A/BSD15B is bound
`refs.field {kind: tariff_schedule, filters: {form, sheet, row_key}, field: charge_value,
numeric: <RANGE only>}` (`linemaps/bsd15a.tariff_sources`). The reader takes the latest
`as_of_date` ≤ reporting date; a missing row → `input_required` for that cell (never `0`, never
a guessed text). The two `RANGE` block sub-headings `FX.1` ("Foreign Currency Account") and
`FX.6` ("Foreign Exchange Account") are bound value rows by the "when in doubt bind" rule but
take no amount — leave them out (the Sample Bank register does).

## Examples

```csv
form,sheet,row_key,label,charge_value,charge_basis,min_ghs,max_ghs,currency,effective_from,notes
BSD15A,DOMESTIC,1.1,minimum,GHS 5.00 per month,flat,5,5,GHS,2026-01-01,
BSD15A,DOMESTIC,3.1,Bank's customers,"0.50% of value, min GHS 30, max GHS 250",percent,30,250,GHS,2026-01-01,
BSD15A,RANGE,S1.1,Initial Deposit,50,flat,,,GHS,2026-01-01,Regular Savings
BSD15B,INTL,17.1,17. ACCOUNT CLOSURE,GHS 50.00 if closed within 6 months of opening; nil thereafter,flat,50,50,GHS,2026-01-01,
```

Push (API): `{"reference": {"tariff_schedule": [ {...row...}, ... ]}}` in a page of the three-call
flow (`docs/API_INTEGRATION.md` §2); app upload: a sheet named `tariff_schedule` with these
columns and a mapping `references: [{source_table: tariff_schedule, dataset_kind: tariff_schedule}]`.

## Sample Bank dataset

`onboarding/sample_bank/tariff_schedule.csv` — 552 rows (168 DOMESTIC + 75 RANGE + 309 INTL):
illustrative 2026 tariff-guide values for a Ghanaian universal bank, effective 2026-01-01
(COT GHS 1.00 per GHS 1,000 debit turnover min/max GHS 5/500 a month; savings maintenance free;
drafts 0.5 % min/max 30/250; returned cheques GHS 60–200; lending fees 1–2 % p.a.; L/C
establishment 0.5 % per quarter min GHS 300; SWIFT outward 0.25 % min/max 50/500 …). Sika Card
lines are `Not applicable` (scheme discontinued). Loaded to the primary (`BK-0PMD7Z5M`) with
`as_of_date = 2026-06-30`. Not real bank data.

## §5 — the `row_key` vocabulary (generated from `linemaps/bsd15a.py` / `bsd15b.py`)

Regenerate with:

```python
# DATABASE_URL="" PYTHONPATH=. uv run python -  (from backend/)
from app.services.regulatory_reporting.bog_forms.linemaps import bsd15a, bsd15b
for keys in (bsd15a.DOMESTIC_KEYS, bsd15a.RANGE_KEYS, bsd15b.KEYS):
    for row in sorted(keys):
        print(row, *keys[row])
```

### `BSD15A` / `DOMESTIC` — Domestic charges of banks (text) (168 rows)

| row_key | Official row | Official cell | Official label |
|---|---|---|---|
| `1.1` | 10 | C10 | minimum |
| `1.2` | 11 | C11 | maximum |
| `1.3` | 12 | C12 | Ledger Fees |
| `1.4` | 13 | C13 | minimum |
| `1.5` | 14 | C14 | maximum |
| `2.1` | 17 | C17 | Above ¢2,000,000 |
| `2.2` | 18 | C18 | BT ¢1,000,000 - ¢2,000,000 |
| `2.3` | 19 | C19 | BT ¢500,000 - ¢1,000,000 |
| `2.4` | 20 | C20 | Bt ¢200,000.00 - ¢500,000.00 |
| `2.5` | 21 | C21 | Below ¢200,000.00 |
| `2.6` | 22 | C22 | SME's |
| `2.7` | 23 | C23 | MIA's |
| `3.1` | 26 | C26 | Bank's customers |
| `3.2` | 27 | C27 | Minimum |
| `3.3` | 28 | C28 | Maximum |
| `3.4` | 29 | C29 | Non customers |
| `3.5` | 30 | C30 | Minimum |
| `3.6` | 31 | C31 | Maximum |
| `3.7` | 32 | C32 | Cancelation of draft |
| `4.1` | 35 | C35 | Minimum |
| `4.2` | 36 | C36 | Maximum |
| `5.1` | 39 | C39 | Minimum |
| `5.2` | 40 | C40 | Maximum |
| `5.3` | 41 | C41 | Refer to Drawer |
| `5.4` | 42 | C42 | Returned uncleared effect (insufficient funds) |
| `5.5` | 43 | C43 | Returned uncleared effect (other reasons) |
| `6.1` | 46 | C46 | Telegraphic Transfers |
| `6.2` | 47 | C47 | Customers |
| `6.3` | 48 | C48 | Minimum |
| `6.4` | 49 | C49 | Maximum |
| `6.5` | 50 | C50 | Non Customers |
| `6.6` | 51 | C51 | Minimum |
| `6.7` | 52 | C52 | Maximum |
| `6.8` | 54 | C54 | Cable |
| `6.9` | 55 | C55 | Minimum |
| `6.10` | 56 | C56 | Maximum |
| `6.11` | 57 | C57 | Non customers |
| `6.12` | 58 | C58 | Minimum |
| `6.13` | 59 | C59 | Maximum |
| `6.14` | 61 | C61 | Mail Transfer |
| `6.15` | 62 | C62 | Customer |
| `6.16` | 63 | C63 | Minimum |
| `6.17` | 64 | C64 | Maximum |
| `6.18` | 65 | C65 | Non customers |
| `6.19` | 66 | C66 | Minimum |
| `6.20` | 67 | C67 | Maximum |
| `6.21` | 69 | C69 | Postage |
| `6.22` | 70 | C70 | Customer |
| `6.23` | 71 | C71 | Minimum |
| `6.24` | 72 | C72 | Maximum |
| `6.25` | 73 | C73 | Non customers |
| `6.26` | 74 | C74 | Minimum |
| `6.27` | 75 | C75 | Maximum |
| `7.1` | 78 | C78 | Commitemnt fee |
| `7.2` | 79 | C79 | Processing |
| `7.3` | 80 | C80 | Property valuation Fee (open market value) |
| `7.4` | 81 | C81 | Guarantee Commission |
| `7.5` | 82 | C82 | Minimum |
| `7.6` | 83 | C83 | Maximum |
| `7.7` | 84 | C84 | Bid Security |
| `7.8` | 85 | C85 | Bank Credit Letter (Letter of Intent) |
| `7.9` | 86 | C86 | Mobilisation Guarantee |
| `7.10` | 87 | C87 | Retention Guaratee |
| `7.11` | 88 | C88 | Performance Bond |
| `7.12` | 89 | C89 | Default or restructured |
| `7.13` | 90 | C90 | Salary Credit Processing Fee |
| `7.14` | 91 | C91 | Overdraft Processing or Renewal Fee |
| `7.15` | 92 | C92 | Revolving Acceptance Credit |
| `7.16` | 93 | C93 | Arrangement Fee for Facilities |
| `8.1` | 96 | C96 | Monthly |
| `8.2` | 97 | C97 | Screen printed |
| `8.3` | 98 | C98 | Duplicate (per sheet) |
| `8.4` | 99 | C99 | Certificate of Balance (per sheet) |
| `8.5` | 100 | C100 | Enquiries bt 1 year and 3 yrs (per sheet) |
| `8.6` | 101 | C101 | Enquiries 3yrs and above (per sheet) |
| `8.7` | 102 | C102 | Auditors Questionnaire |
| `9.1` | 105 | C105 | Minimum |
| `9.2` | 106 | C106 | Maximum |
| `10.1` | 109 | C109 | Bank's cheques |
| `10.2` | 110 | C110 | Minimum |
| `10.3` | 111 | C111 | Maximum |
| `10.4` | 112 | C112 | Country Clearing |
| `10.5` | 113 | C113 | Other banks cheques |
| `10.6` | 114 | C114 | Mimimum |
| `10.7` | 115 | C115 | Maximum |
| `10.8` | 117 | C117 | Other Bank's cheques marked |
| `10.9` | 118 | C118 | Commission on Drawer's A/c |
| `10.10` | 119 | C119 | Minimum |
| `10.11` | 120 | C120 | Maximum |
| `10.12` | 122 | C122 | Interbranch Clearing (Bank's Cheques) |
| `10.13` | 123 | C123 | Bank's branches |
| `10.14` | 124 | C124 | Other bank's Presentation: Marked Comm. A/c Drawer |
| `10.15` | 125 | C125 | Minimum |
| `10.16` | 126 | C126 | Maximum |
| `10.17` | 127 | C127 | No commission mark (Comm borne by payee) |
| `10.18` | 128 | C128 | Minimum |
| `10.19` | 129 | C129 | Maximum |
| `10.20` | 131 | C131 | Interbranch Clearing (Other Bank's Cheques) |
| `10.21` | 132 | C132 | No commission mark(Comm borne by payee) |
| `10.22` | 133 | C133 | minimum |
| `10.23` | 134 | C134 | maximum |
| `10.24` | 135 | C135 | Marked Comm.A/c Drawer |
| `10.25` | 136 | C136 | minimum |
| `10.26` | 137 | C137 | maximum |
| `10.27` | 139 | C139 | Cheque Withdrawal Outside Drawee Branch by Phone/Radio |
| `10.28` | 140 | C140 | Customer |
| `10.29` | 141 | C141 | Minimum |
| `10.30` | 142 | C142 | Maximum |
| `11.1` | 145 | C145 | minimum |
| `11.2` | 146 | C146 | maximum |
| `12.1` | 149 | C149 | minimum |
| `12.2` | 150 | C150 | maximum |
| `13.1` | 153 | C153 | Internal |
| `13.2` | 154 | C154 | minimum |
| `13.3` | 155 | C155 | maximum |
| `13.4` | 156 | C156 | External |
| `13.5` | 157 | C157 | minimum |
| `13.6` | 158 | C158 | maximum |
| `14.1` | 161 | C161 | minimum |
| `14.2` | 162 | C162 | maximum |
| `14.3` | 164 | C164 | Non Salary Transfers |
| `14.4` | 165 | C165 | minimum |
| `14.5` | 166 | C166 | maximum |
| `15.1` | 169 | C169 | Internal |
| `15.2` | 170 | C170 | minimum |
| `15.3` | 171 | C171 | maximum |
| `15.4` | 172 | C172 | External |
| `15.5` | 173 | C173 | minimum |
| `15.6` | 174 | C174 | maximum |
| `16.1` | 177 | C177 | minimum |
| `16.2` | 178 | C178 | maximum |
| `17.1` | 181 | C181 | minimum |
| `17.2` | 182 | C182 | maximum |
| `18.1` | 185 | C185 | Savings a/c |
| `18.2` | 186 | C186 | Current a/c |
| `19.1` | 189 | C189 | Envelopes (per quarter) |
| `19.2` | 190 | C190 | Boxes (per quarter) |
| `19.3` | 191 | C191 | Parcels |
| `19.4` | 192 | C192 | Others |
| `20.1` | 195 | C195 | Personal Accounts (25 leaflets) |
| `20.2` | 196 | C196 | Personal Accounts (50 leaflets) |
| `20.3` | 197 | C197 | Corporate Accounts (50 Leaflets) |
| `20.4` | 198 | C198 | Corporate Accounts (100 Leaflets) |
| `20.5` | 199 | C199 | Plus Stamp Duty ( per Leaflet) |
| `20.6` | 200 | C200 | Cheque voucher retrieval |
| `20.7` | 201 | C201 | Sale of cheque leaflet |
| `20.8` | 202 | C202 | Replacement of Lost Savings Pass Book |
| `21.1` | 205 | C205 | Issuing |
| `21.2` | 206 | C206 | Cash Machine withdrawal |
| `21.3` | 207 | C207 | Replacement of lost/damage card |
| `21.4` | 208 | C208 | Replacement of PIN |
| `21.5` | 209 | C209 | Transaction fee per withdrawal |
| `22.1` | 212 | C212 | Loading Fee: per ¢100,000 |
| `22.2` | 213 | C213 | minimum |
| `22.3` | 214 | C214 | maximum |
| `22.4` | 217 | C217 | First time purchaser |
| `22.5` | 218 | C218 | Lost/debased cards |
| `22.6` | 219 | C219 | Card replacement |
| `22.7` | 220 | C220 | Card extension |
| `22.8` | 222 | C222 | Sika Card - Merchants |
| `22.9` | 223 | C223 | License/Insurance Fee (per month) |
| `22.10` | 225 | C225 | Sika Card - Cash Back |
| `22.11` | 226 | C226 | Above ¢10 million: per ¢100,000 |
| `22.12` | 227 | C227 | minimum |
| `22.13` | 228 | C228 | maximum |
| `22.14` | 229 | C229 | Below ¢10 million: per ¢100,000 |
| `22.15` | 230 | C230 | minimum |
| `22.16` | 231 | C231 | maximum |

### `BSD15A` / `RANGE` — Range of pdts i.r.o sav & cur (cedi amounts) (77 rows)

| row_key | Official row | Official cell | Official label |
|---|---|---|---|
| `S1.1` | 13 | B13 | Initial Deposit |
| `S1.2` | 14 | B14 | Minimum Operating Bal |
| `S1.3` | 15 | B15 | Maintenance fee |
| `S1.4` | 17 | B17 | Maintenance fee |
| `S1.5` | 18 | B18 | Transaction fee |
| `S1.6` | 19 | B19 | Dormant Account Maintenance Fee |
| `S2.1` | 22 | B22 | Initial Deposit |
| `S2.2` | 23 | B23 | Minimum Operating Bal |
| `S2.3` | 24 | B24 | Maintenance fee |
| `S2.4` | 26 | B26 | Maintenance fee |
| `S2.5` | 27 | B27 | Transaction fee |
| `S2.6` | 28 | B28 | Dormant Account Maintenance Fee |
| `S3.1` | 31 | B31 | Initial Deposit |
| `S3.2` | 32 | B32 | Minimum Operating Bal |
| `S3.3` | 33 | B33 | Maintenance fee |
| `S3.4` | 35 | B35 | Maintenance fee |
| `S3.5` | 36 | B36 | Transaction fee |
| `S3.6` | 37 | B37 | Dormant Account Maintenance Fee |
| `S4.1` | 40 | B40 | Initial Deposit |
| `S4.2` | 41 | B41 | Minimum Operating Bal |
| `S4.3` | 42 | B42 | Maintenance fee |
| `S4.4` | 44 | B44 | Maintenance fee |
| `S4.5` | 45 | B45 | Transaction fee |
| `S4.6` | 46 | B46 | Dormant Account Maintenance Fee |
| `S5.1` | 49 | B49 | Initial Deposit |
| `S5.2` | 50 | B50 | Minimum Operating Bal |
| `S5.3` | 51 | B51 | Maintenance fee |
| `S5.4` | 53 | B53 | Maintenance fee |
| `S5.5` | 54 | B54 | Transaction fee |
| `S5.6` | 55 | B55 | Dormant Account Maintenance Fee |
| `S6.1` | 58 | B58 | Initial Deposit |
| `S6.2` | 59 | B59 | Minimum Operating Bal |
| `S6.3` | 60 | B60 | Maintenance fee |
| `S6.4` | 62 | B62 | Maintenance fee |
| `S6.5` | 63 | B63 | Transaction fee |
| `S6.6` | 64 | B64 | Cash Machine withdrawal fee |
| `S6.7` | 65 | B65 | Dormant Account Maintenance Fee |
| `S7.1` | 68 | B68 | Initial Deposit |
| `S7.2` | 69 | B69 | Minimum Operating Bal |
| `S7.3` | 70 | B70 | Maintenance fee |
| `S7.4` | 72 | B72 | Maintenance fee |
| `S7.5` | 73 | B73 | Transaction fee |
| `S7.6` | 74 | B74 | Dormant Account Maintenance Fee |
| `C1.1` | 77 | B77 | Initial Deposit |
| `C1.2` | 78 | B78 | Minimum Operating Bal |
| `C1.3` | 79 | B79 | Maintenance fee |
| `C1.4` | 80 | B80 | Transaction fee per non clearing debit |
| `C1.5` | 81 | B81 | Transaction fee for cheques below ¢500,000 |
| `C1.6` | 82 | B82 | Cash Machine withdrawal fee |
| `C1.7` | 83 | B83 | COT Applicable |
| `C1.8` | 84 | B84 | Dormant Account Maintenance Fee |
| `C2.1` | 88 | B88 | Initial Deposit |
| `C2.2` | 89 | B89 | Minimum Operating Bal |
| `C2.3` | 90 | B90 | Maintenance fee |
| `C2.4` | 91 | B91 | Transaction fee per non clearing debit |
| `C2.5` | 92 | B92 | Cash Machine withdrawal fee |
| `C2.6` | 94 | B94 | Transaction fee |
| `C2.7` | 95 | B95 | COT Applicable |
| `C2.8` | 96 | B96 | Dormant Account Maintenance Fee |
| `C3.1` | 99 | B99 | Initial Deposit |
| `C3.2` | 100 | B100 | Minimum Operating Bal |
| `C3.3` | 101 | B101 | Maintenance fee |
| `C3.4` | 102 | B102 | Transaction fee per non clearing debit |
| `C3.5` | 103 | B103 | Cash Machine withdrawal fee |
| `C3.6` | 105 | B105 | Transaction fee |
| `C3.7` | 106 | B106 | COT Applicable |
| `C3.8` | 107 | B107 | Dormant Account Maintenance Fee |
| `FX.1` | 110 | B110 | Foreign Currency Account |
| `FX.2` | 111 | B111 | Initial Deposit |
| `FX.3` | 112 | B112 | Minimum Operating Balance |
| `FX.4` | 113 | B113 | Interest Payable |
| `FX.5` | 114 | B114 | Maintenance charges |
| `FX.6` | 116 | B116 | Foreign Exchange Account |
| `FX.7` | 117 | B117 | Initial Deposit |
| `FX.8` | 118 | B118 | Minimum Operating Balance |
| `FX.9` | 119 | B119 | Interest Payable |
| `FX.10` | 120 | B120 | Maintenance charges |

### `BSD15B` / `INTL` — International Banking Charges (text) (309 rows)

| row_key | Official row | Official cell | Official label |
|---|---|---|---|
| `1.1` | 10 | B10 | Establishment Commission |
| `1.2` | 11 | B11 | Minimum |
| `1.3` | 12 | B12 | <3months |
| `1.4` | 13 | B13 | 3-6months |
| `1.5` | 14 | B14 | 6-9months |
| `1.6` | 15 | B15 | Over 9 months |
| `1.7` | 16 | B16 | Minimum |
| `1.8` | 18 | B18 | 60 days |
| `1.9` | 19 | B19 | Arrangement fee |
| `1.10` | 20 | B20 | 90 days |
| `1.11` | 21 | B21 | Arrangement fee |
| `1.12` | 22 | B22 | 120 days |
| `1.13` | 23 | B23 | 150 days |
| `1.14` | 24 | B24 | 180 days |
| `1.15` | 25 | B25 | Arrangement fee |
| `1.16` | 26 | B26 | Presentation under L/C Drawings |
| `1.17` | 27 | B27 | Minimum |
| `1.18` | 29 | B29 | Increase in Amount |
| `1.19` | 30 | B30 | Extention of period |
| `1.20` | 31 | B31 | Extention of period- another quarter |
| `1.21` | 32 | B32 | Discrepancy |
| `1.22` | 35 | B35 | Minimum |
| `1.23` | 36 | B36 | Maximum |
| `1.24` | 37 | B37 | Revolving credits |
| `1.25` | 38 | B38 | Cable/Telex/Swift charges |
| `1.26` | 39 | B39 | Swap charge |
| `1.27` | 40 | B40 | Confirmation commission |
| `1.28` | 41 | B41 | Minimum |
| `1.29` | 42 | B42 | Maximum |
| `1.30` | 43 | B43 | Payment commission |
| `1.31` | 44 | B44 | Minimum |
| `1.32` | 45 | B45 | Maximum |
| `1.33` | 47 | B47 | Minimum |
| `1.34` | 48 | B48 | Maximum |
| `1.35` | 49 | B49 | Exchange - (Drawing/Negitiation) |
| `1.36` | 50 | B50 | Minimum |
| `1.37` | 51 | B51 | Maximum |
| `1.38` | 52 | B52 | Cancellation fee |
| `1.39` | 53 | B53 | Correspondent Bank charges - Upfront Payment |
| `1.40` | 55 | B55 | FULLY COVERED L/CS  (IMPORTS) |
| `1.41` | 57 | B57 | Establishment |
| `1.42` | 58 | B58 | Revolving/Standby L/C |
| `1.43` | 59 | B59 | Drawings/Negotiation commission |
| `1.44` | 60 | B60 | Exchange - Drawings/Negotiation |
| `1.45` | 61 | B61 | Amendments |
| `1.46` | 62 | B62 | Increase in L/C value |
| `1.47` | 63 | B63 | Other Amendments |
| `1.48` | 64 | B64 | Extension |
| `2.1` | 68 | B68 | Advising commission |
| `2.2` | 69 | B69 | Customers |
| `2.3` | 70 | B70 | Minimum |
| `2.4` | 71 | B71 | Maximum |
| `2.5` | 72 | B72 | Non-customers |
| `2.6` | 73 | B73 | Minimum |
| `2.7` | 74 | B74 | Maximum |
| `2.8` | 75 | B75 | Handling Charges |
| `2.9` | 76 | B76 | Customers |
| `2.10` | 77 | B77 | Minimum |
| `2.11` | 78 | B78 | Maximum |
| `2.12` | 79 | B79 | Non-customers |
| `2.13` | 80 | B80 | Minimum |
| `2.14` | 81 | B81 | Maximum |
| `2.15` | 82 | B82 | Confirmation Commission |
| `2.16` | 83 | B83 | Minimum |
| `2.17` | 84 | B84 | Maximum |
| `2.18` | 85 | B85 | Amendment commission |
| `2.19` | 86 | B86 | Minimum |
| `2.20` | 87 | B87 | Maximum |
| `2.21` | 88 | B88 | Transfer charge |
| `2.22` | 89 | B89 | Minimum |
| `2.23` | 90 | B90 | Maximum |
| `2.24` | 91 | B91 | Negotiation Commission - Corporate |
| `2.25` | 92 | B92 | Minimum |
| `2.26` | 93 | B93 | Maximum |
| `2.27` | 94 | B94 | Negotiation Commission - Others |
| `2.28` | 95 | B95 | Minimum |
| `2.29` | 96 | B96 | Maximum |
| `2.30` | 97 | B97 | Exchange  Commission |
| `2.31` | 98 | B98 | Minimum |
| `2.32` | 99 | B99 | Maximum |
| `2.33` | 100 | B100 | Cancellation |
| `2.34` | 101 | B101 | Postage/Courier |
| `3.1` | 105 | B105 | Handling |
| `3.2` | 106 | B106 | Transfer |
| `3.3` | 107 | B107 | Management Fees |
| `4.1` | 111 | B111 | Handling charges - customers |
| `4.2` | 112 | B112 | Minimum |
| `4.3` | 113 | B113 | Maximum |
| `4.4` | 114 | B114 | Non customers |
| `4.5` | 115 | B115 | Minimum |
| `4.6` | 116 | B116 | Maximum |
| `4.7` | 117 | B117 | Negotiation Commission |
| `4.8` | 118 | B118 | Minimum |
| `4.9` | 119 | B119 | Maximum |
| `4.10` | 120 | B120 | Advice of fate |
| `4.11` | 121 | B121 | Bills deleted |
| `4.12` | 122 | B122 | Protest |
| `4.13` | 123 | B123 | Payment Commission |
| `4.14` | 124 | B124 | Minimum |
| `4.15` | 125 | B125 | Maximum |
| `4.16` | 126 | B126 | Holding charges (per quarter) |
| `4.17` | 128 | B128 | Own resources - FCA |
| `4.18` | 129 | B129 | Minimum |
| `4.19` | 130 | B130 | Maximum |
| `4.20` | 131 | B131 | Own resources  - FEA |
| `4.21` | 132 | B132 | Minimum |
| `4.22` | 133 | B133 | Maximum |
| `4.23` | 134 | B134 | Telex/Cable charges |
| `4.24` | 135 | B135 | Swap charge |
| `4.25` | 137 | B137 | Minimum |
| `4.26` | 138 | B138 | Maximum |
| `4.27` | 139 | B139 | Exchange |
| `4.28` | 140 | B140 | Telex/Cable charges |
| `4.29` | 143 | B143 | Minimum |
| `4.30` | 144 | B144 | Maximum |
| `4.31` | 146 | B146 | Minimum |
| `4.32` | 147 | B147 | Maximum |
| `5.1` | 150 | B150 | Handling Commission |
| `5.2` | 151 | B151 | Minimum |
| `5.3` | 152 | B152 | Maximum |
| `5.4` | 153 | B153 | Foreign bills negotiated |
| `5.5` | 154 | B154 | Minimum |
| `5.6` | 155 | B155 | Maximum |
| `5.7` | 156 | B156 | Foreign bills discounted |
| `5.8` | 157 | B157 | Minimum |
| `5.9` | 158 | B158 | Maximum |
| `5.10` | 159 | B159 | Payment Commission |
| `5.11` | 160 | B160 | Minimum |
| `5.12` | 161 | B161 | Maximum |
| `5.13` | 162 | B162 | Protest Bills |
| `5.14` | 163 | B163 | Overdue bills/qtr |
| `5.15` | 164 | B164 | Reminder on overdue bills |
| `5.16` | 165 | B165 | Courier |
| `5.17` | 166 | B166 | Advise of Fate |
| `5.18` | 167 | B167 | Bills Deleted |
| `6.1` | 170 | B170 | Commission |
| `6.2` | 171 | B171 | Minimum |
| `6.3` | 172 | B172 | Maximum |
| `7.1` | 174 | B174 | Inwards |
| `7.2` | 175 | B175 | Minimum |
| `7.3` | 176 | B176 | Maximum |
| `7.4` | 177 | B177 | Outward |
| `7.5` | 178 | B178 | Minimum |
| `7.6` | 179 | B179 | Maximum |
| `7.7` | 180 | B180 | Clearing of Foreign checques |
| `8.1` | 183 | B183 | Arrangement fee |
| `8.2` | 184 | B184 | Processing fee |
| `8.3` | 185 | B185 | Facility Fee |
| `9.1` | 189 | B189 | Handling Commission |
| `9.2` | 190 | B190 | Minimum |
| `9.3` | 191 | B191 | Maximum |
| `9.4` | 192 | B192 | Commission- Guaratees (foreign) |
| `9.5` | 193 | B193 | Minimum |
| `9.6` | 194 | B194 | Maximum |
| `9.7` | 195 | B195 | Commission-B/L indemnities |
| `9.8` | 196 | B196 | Minimum |
| `9.9` | 197 | B197 | Maximum |
| `9.10` | 198 | B198 | Default  or restructured |
| `10.1` | 201 | B201 | Form A2 |
| `10.2` | 202 | B202 | Import Declaration Form (IDF) |
| `11.1` | 206 | B206 | Inward Remittances |
| `11.2` | 208 | B208 | Minimum |
| `11.3` | 209 | B209 | Maximum |
| `11.4` | 210 | B210 | - Non customer |
| `11.5` | 211 | B211 | Minimum |
| `11.6` | 212 | B212 | Maximum |
| `11.7` | 213 | B213 | Administrative charges |
| `11.8` | 214 | B214 | Transfer to beneficiary in other banks |
| `11.9` | 215 | B215 | Minimum |
| `11.10` | 216 | B216 | Maximum |
| `11.11` | 217 | B217 | Request for drafts against transfer |
| `11.12` | 218 | B218 | Minimum |
| `11.13` | 219 | B219 | Maximum |
| `11.14` | 220 | B220 | Payment in Foreign Currency - Customer |
| `11.15` | 221 | B221 | Minimum |
| `11.16` | 222 | B222 | Maximum |
| `11.17` | 223 | B223 | - Non customer |
| `11.18` | 224 | B224 | Minimum |
| `11.19` | 225 | B225 | Maximum |
| `11.20` | 226 | B226 | Telegraphic |
| `11.21` | 227 | B227 | Minimum |
| `11.22` | 228 | B228 | Maximum |
| `11.23` | 229 | B229 | Mail |
| `11.24` | 230 | B230 | Minimum |
| `11.25` | 231 | B231 | Maximum |
| `11.26` | 232 | B232 | Swap charges |
| `11.27` | 233 | B233 | Minimum |
| `11.28` | 234 | B234 | Maximum |
| `11.29` | 235 | B235 | Claiming of charges from Remitting Banks |
| `12.1` | 239 | B239 | Foreign currency account (FCA) |
| `12.2` | 240 | B240 | Minimum |
| `12.3` | 241 | B241 | Maximum |
| `12.4` | 242 | B242 | Foreign Exchange account (FEA) |
| `12.5` | 243 | B243 | Commission   - Customer |
| `12.6` | 244 | B244 | Minimum |
| `12.7` | 245 | B245 | Maximum |
| `12.8` | 246 | B246 | - Non customer |
| `12.9` | 247 | B247 | Minimum |
| `12.10` | 248 | B248 | Maximum |
| `12.11` | 250 | B250 | FCA |
| `12.12` | 251 | B251 | Minimum |
| `12.13` | 252 | B252 | Maximum |
| `12.14` | 253 | B253 | FEA - Customer |
| `12.15` | 254 | B254 | Minimum |
| `12.16` | 255 | B255 | Maximum |
| `12.17` | 256 | B256 | - Non customer |
| `12.18` | 257 | B257 | Minimum |
| `12.19` | 258 | B258 | Maximum |
| `12.20` | 260 | B260 | Cedis against drafts - Customer |
| `12.21` | 261 | B261 | Minimum |
| `12.22` | 262 | B262 | Maximum |
| `12.23` | 263 | B263 | - Non customer |
| `12.24` | 264 | B264 | Minimum |
| `12.25` | 265 | B265 | Maximum |
| `13.1` | 269 | B269 | Telegraphic- Customer |
| `13.2` | 270 | B270 | Minimum |
| `13.3` | 271 | B271 | Maximum |
| `13.4` | 272 | B272 | - Non customer |
| `13.5` | 273 | B273 | Minimum |
| `13.6` | 274 | B274 | Maximum |
| `13.7` | 275 | B275 | Swift - Customer |
| `13.8` | 276 | B276 | Minimum |
| `13.9` | 277 | B277 | Maximum |
| `13.10` | 278 | B278 | - Non customer |
| `13.11` | 279 | B279 | Minimum |
| `13.12` | 280 | B280 | Maximum |
| `13.13` | 282 | B282 | Drafts/Money Orders - Customer |
| `13.14` | 283 | B283 | Minimum |
| `13.15` | 284 | B284 | Maximum |
| `13.16` | 285 | B285 | - Non customer |
| `13.17` | 286 | B286 | Minimum |
| `13.18` | 287 | B287 | Maximum |
| `13.19` | 289 | B289 | Customers own resources |
| `13.20` | 290 | B290 | FCA |
| `13.21` | 291 | B291 | Minimum |
| `13.22` | 292 | B292 | Maximum |
| `13.23` | 293 | B293 | FEA - Customer |
| `13.24` | 294 | B294 | Minimum |
| `13.25` | 295 | B295 | Maximum |
| `13.26` | 296 | B296 | - Non customer |
| `13.27` | 297 | B297 | Minimum |
| `13.28` | 298 | B298 | Maximum |
| `13.29` | 300 | B300 | Bank's Funds - Customer |
| `13.30` | 301 | B301 | Minimum |
| `13.31` | 302 | B302 | Maximum |
| `13.32` | 303 | B303 | - Non customer |
| `13.33` | 304 | B304 | Minimum |
| `13.34` | 305 | B305 | Maximum |
| `13.35` | 307 | B307 | Exchange - Customer |
| `13.36` | 308 | B308 | Minimum |
| `13.37` | 309 | B309 | Maximum |
| `13.38` | 310 | B310 | - Non customer |
| `13.39` | 311 | B311 | Minimum |
| `13.40` | 312 | B312 | Maximum |
| `13.41` | 314 | B314 | Travellers Cheques, Drafts, I.M.O etc credited to Forex Acc. |
| `13.42` | 316 | B316 | Telex/Swift charges |
| `14.1` | 319 | B319 | FEA - Customer |
| `14.2` | 320 | B320 | Minimum |
| `14.3` | 321 | B321 | Maximum |
| `14.4` | 322 | B322 | - Non customer |
| `14.5` | 323 | B323 | Minimum |
| `14.6` | 324 | B324 | Maximum |
| `14.7` | 325 | B325 | FCA - Customer |
| `14.8` | 326 | B326 | Minimum |
| `14.9` | 327 | B327 | Maximum |
| `14.10` | 328 | B328 | - Non customer |
| `14.11` | 329 | B329 | Minimum |
| `14.12` | 330 | B330 | Maximum |
| `15.1` | 333 | B333 | For Foreign currency - Customer |
| `15.2` | 334 | B334 | Minimum |
| `15.3` | 335 | B335 | Maximum |
| `15.4` | 336 | B336 | - Non customer |
| `15.5` | 337 | B337 | Minimum |
| `15.6` | 338 | B338 | Maximum |
| `16.1` | 341 | B341 | Local cheque |
| `16.2` | 342 | B342 | Minimum |
| `16.3` | 343 | B343 | Maximum |
| `16.4` | 344 | B344 | Foreign Cheque for collection |
| `16.5` | 345 | B345 | Minimum |
| `16.6` | 346 | B346 | Maximum |
| `16.7` | 347 | B347 | - Non customer |
| `16.8` | 348 | B348 | Minimum |
| `16.9` | 349 | B349 | Maximum |
| `16.10` | 351 | B351 | Stop cheques |
| `16.11` | 353 | B353 | Cheque issuance |
| `16.12` | 355 | B355 | Sale of Cheque Leaflet |
| `16.13` | 357 | B357 | Standing orders |
| `16.14` | 358 | B358 | Minimum |
| `16.15` | 359 | B359 | Maximum |
| `16.16` | 361 | B361 | Evacuation fee |
| `17.1` | 363 | B363 | 17. ACCOUNT CLOSURE |
| `18.1` | 366 | B366 | Returned by bank |
| `18.2` | 367 | B367 | Returned by foreign bank |
| `19.1` | 370 | B370 | Minimum |
| `19.2` | 371 | B371 | Maximum |
| `19.3` | 373 | B373 | Service charge/Fees - Corporate |
| `19.4` | 374 | B374 | Minimum |
| `19.5` | 375 | B375 | Maximum |
| `19.6` | 377 | B377 | Service charge/Fees - Others |
| `19.7` | 378 | B378 | Minimum |
| `19.8` | 379 | B379 | Maximum |
| `19.9` | 381 | B381 | Cash Withdrawal FCA |
| `19.10` | 382 | B382 | Cash Withdrawal FEA |
| `20.1` | 385 | B385 | Charges on dormant accounts |
| `21.1` | 388 | B388 | Monthly |
| `21.2` | 389 | B389 | Duplicate- per sheet |
| `21.3` | 390 | B390 | Cheque Books |
| `21.4` | 391 | B391 | Personal |
| `21.5` | 392 | B392 | Corporate |

