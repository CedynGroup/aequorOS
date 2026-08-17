"""``interest_accruals`` — the accrued-interest sub-ledger (feeds the 19
"Accrued interest" lines of BSD2 *Statement of Assets and Liabilities*; BSD6
reads BSD2's totals).

BSD2 asks for accrued interest as its own line under each balance block —
claims on Bank of Ghana, on depository / other financial institutions,
non-resident borrowings and deposits, domestic borrowings, deposits of
financial institutions and of the public. No canonical entity carries an
accrual (positions are principal balances), so the bank supplies its accruals
sub-ledger: **one row per accrual balance at the reporting date**, tagged to
the official BSD2 line it belongs to. Balances are STOCKS as at ``as_of_date``
(not the period's interest flow), in cedis (``accrued_interest_ghs``; the
native amount may ride along in ``accrued_interest_native``).

Tagging (documented for the bank in docs/data_engine/datasets/interest_accruals.md):
``bsd2_row`` is the ROW NUMBER of the "Accrued interest" line on the official
``BSD2`` sheet of *FORM BSD2 REVISED.xls* — the number printed down the left of
the template the bank already files (rows 20, 29, 32 on the asset side; 141,
145, 151, 156, 161, 166, 177, 195, 204, 211, 218, 225, 234, 242, 250, 258 on
the liability side). ``side`` says which side of the balance sheet the accrual
sits on and must agree with the row. ``currency`` places the balance in the
Domestic (bank's base currency) or Foreign (any other) column per the Guide.

**One reporting date per push** (batch ``as_of_date`` = the reporting date):
BSD2 for a period reads the latest batch on/before the period end, so a batch
must carry that date's full sub-ledger (a multi-date file would be read as one
date; an omitted line reads 0, not blank).
"""

from __future__ import annotations

from . import ReferenceSchema, register

#: Official ``BSD2`` sheet rows whose label is "Accrued interest" (asset side).
ASSET_ROWS: tuple[str, ...] = ("20", "29", "32")
#: … and on the liability side.
LIABILITY_ROWS: tuple[str, ...] = (
    "141",
    "145",
    "151",
    "156",
    "161",
    "166",
    "177",
    "195",
    "204",
    "211",
    "218",
    "225",
    "234",
    "242",
    "250",
    "258",
)
BSD2_ROWS: tuple[str, ...] = (*ASSET_ROWS, *LIABILITY_ROWS)
SIDES: tuple[str, ...] = ("asset", "liability")

#: What each row is, in the template's own words (section → line), so a bank
#: can tag without reading the platform's line map.
_NONRES = "C.20 Deposits of non-residents"
_FI = "D.24 Deposits of financial institutions"
_PUBLIC = "D.25 Deposits of non-financial institutions, public and govt"
BSD2_ROW_LABELS: dict[str, str] = {
    "20": "B.6(b) Claims on Bank of Ghana — (v) Accrued interest",
    "29": "B.6(c) Claims on other depository institutions — (vi) Accrued interest",
    "32": "B.6(d) Claims on other financial institutions — (ii) Accrued interest",
    "141": "C.18 Short-term borrowings (non-resident) — (c) Accrued interest",
    "145": "C.19 Long-term borrowing (non-resident) — (c) Accrued interest",
    "151": f"{_NONRES}, (a) demand — (iv) Accrued interest",
    "156": f"{_NONRES}, (b) savings — (iv) Accrued interest",
    "161": f"{_NONRES}, (c) time — (iv) Accrued interest",
    "166": f"{_NONRES}, (d) certificates of deposit — (iv) Accrued interest",
    "177": "D.21 Long-term borrowings (domestic) — (f) Accrued interest",
    "195": "D.23 Short-term borrowing (domestic) — (f) Accrued interest",
    "204": f"{_FI}, (a) demand — (iv) Accrued interest",
    "211": f"{_FI}, (b) savings — (iv) Accrued interest",
    "218": f"{_FI}, (c) time — (iv) Accrued interest",
    "225": f"{_FI}, (d) certificates of deposit — (iv) Accrued interest",
    "234": f"{_PUBLIC}, (a) demand — (vii) Accrued interest",
    "242": f"{_PUBLIC}, (b) savings — (vii) Accrued interest",
    "250": f"{_PUBLIC}, (c) time — (vii) Accrued interest",
    "258": f"{_PUBLIC}, (d) certificates of deposit — (vii) Accrued interest",
}

SCHEMA = register(
    ReferenceSchema(
        kind="interest_accruals",
        description=(
            "Accrued-interest sub-ledger: accrual balances at the reporting date tagged to the "
            "official BSD2 'Accrued interest' row (bsd2_row), side and currency, in cedis"
        ),
        grain=(
            "one row per (as_of_date, bsd2_row, side, currency, gl_account_code | "
            "position_reference); one reporting date per push (as_of_date = period end)"
        ),
        required=("as_of_date", "bsd2_row", "side", "currency", "accrued_interest_ghs"),
        optional=(
            "gl_account_code",
            "position_reference",
            "counterparty_reference",
            "accrued_interest_native",
            "notes",
        ),
        numeric=("accrued_interest_ghs", "accrued_interest_native"),
        dates=("as_of_date",),
        enums={"bsd2_row": BSD2_ROWS, "side": SIDES},
    )
)


def validate_accrual_row(row: dict) -> list[str]:
    """Schema problems plus the cross-field rule: ``side`` must agree with ``bsd2_row``."""
    problems = SCHEMA.validate_row(row)
    bsd2_row = str(row.get("bsd2_row") or "").strip()
    side = str(row.get("side") or "").strip().lower()
    if bsd2_row in ASSET_ROWS and side == "liability":
        problems.append(f"bsd2_row {bsd2_row} is an asset-side line; side must be 'asset'")
    elif bsd2_row in LIABILITY_ROWS and side == "asset":
        problems.append(f"bsd2_row {bsd2_row} is a liability-side line; side must be 'liability'")
    return problems
