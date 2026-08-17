"""BSD13 — Net Open Position (Form FXP): the monthly forex position return.

Official workbook ``FORM BSD13 REVISED.xls`` — four sheets:

* ``FOREX OPEN POSITION`` — I. currency-wise positions (rows: (A) Net Assets,
  (B) Liabilities on contingent credits, (C) Net Trading Position, NOP =
  i+ii+iii, its cedi equivalent in '000, management limit) × columns US
  DOLLAR (E) / GB POUND (H) / DEM (K) / Other Currencies (N) — the "Nature of
  position" cells beside each amount are template labels; II. AGGREGATE FOREX
  OPEN POSITION (cedi '000): NOP per currency (C44:C47), AFOP (C50), Net own
  funds (C52), AFOP as % of NOF (C53), regulatory limit (C55).
* ``SCHEDULE-A`` — composition of Net Assets by nature (assets 1–6,
  liabilities 7–10) per currency; every sub-total, TA, TL and NET ASSETS is a
  template formula.
* ``SCHEDULE-B`` — liabilities (crystallised) under contingents: LCs,
  guarantees, other commitments; TOTAL is a template formula.
* ``SCHEDULE-C`` — Net Trading Position: spot purchase / sale, forward
  purchase / sale (net = purchase + sale, sales negative — the template's own
  formulas), plus the annexure listing every outstanding forward purchase /
  sale contract (7 slots each: SI No · date · counterparty · currency · amount
  · period · rate · forward points · delivery date).

Every data cell of the four sheets is bound below. The templates ship these
grids EMPTY, so :func:`_common.grid_lines` names the official cells from the
header labels (see docs/bog_returns/bsd13_line_map.md for the cell atlas).
Units follow the templates: per-currency columns are foreign-currency UNITS
(``unscaled=True``); the main sheet's cedi cells are '000 (sheet unit
``thousands``); the schedules' *Other Currencies* column is cedi 'Million
(sheet unit ``millions``); the annexure amounts are currency 'Million (the
sheet divisor applies to raw currency units).

Sources (see ``sources_ext/bsd13.py``): the FX engine's latest succeeded
baseline run + the ``fx_position`` facts it consumed feed the main sheet;
canonical positions filtered to one currency feed Schedule A; the
``FX_HEDGE`` contract book feeds Schedule C. Crystallised contingent
liabilities (Schedule B, main-sheet row (B)) and the bank's management limit
have no canonical source and are ``input_required``.
"""

from __future__ import annotations

from typing import Any

from ._common import RowSource, grid_lines, leaf_lines

FORM = "BSD13"
MAIN = "FOREX OPEN POSITION"
SCHEDULE_A = "FOREX OPEN POSITION-SCHEDULE-A"
SCHEDULE_B = "FOREX OPEN POSITION-SCHEDULE-B"
SCHEDULE_C = "FOREX OPEN POSITION-SCHEDULE-C"

#: Main-sheet section I: currency amount columns (the "Nature of position"
#: cells F/I/L/O are template labels).
MAIN_CCY_COLUMNS: dict[str, str] = {"usd": "E", "gbp": "H", "dem": "K"}
MAIN_OTHER_COLUMN: dict[str, str] = {"other": "N"}
#: Schedules: per-currency columns + the "Other Currencies (in equiv. Cedi
#: 'Million)" column.
SCHEDULE_CCY_COLUMNS: dict[str, str] = {"usd": "C", "gbp": "D", "dem": "E"}
SCHEDULE_OTHER_COLUMN: dict[str, str] = {"other": "F"}

CONTINGENTS_INPUT = RowSource(
    None,
    notes=(
        "crystallised liabilities under contingent credits (LCs / guarantees / other "
        "commitments called and unpaid) — canonical positions carry no crystallisation "
        "flag; the FX engine's NOP excludes off-balance contingents; bank must supply"
    ),
)
MANAGEMENT_LIMIT_INPUT = RowSource(
    None, notes="the bank's own management limit on the NOP in this currency — bank must supply"
)


def nop(measure: str, *, unscaled: bool = False, **params: Any) -> RowSource:
    return RowSource("bsd13.nop", {"measure": measure, **params}, unscaled=unscaled)


def positions_ccy(*, unscaled: bool = False, **params: Any) -> RowSource:
    return RowSource("bsd13.positions_ccy", params, unscaled=unscaled)


# ---------------------------------------------------------------------------
# FOREX OPEN POSITION — I. currency-wise positions
# ---------------------------------------------------------------------------

#: Row → measure for the named-currency columns (currency units).
_MAIN_ROWS_CCY: dict[int, RowSource] = {
    19: nop("net_assets", unscaled=True),  # (A) Net Assets — vide Schedule A
    21: CONTINGENTS_INPUT,  # (B) Liabilities on contingent credits — Schedule B
    24: nop("net_trading", unscaled=True),  # (C) Net Trading Position — Schedule C
    29: nop("net", unscaled=True),  # NOP (i+ii+iii) in currency = the FX run's net_ccy
    35: MANAGEMENT_LIMIT_INPUT,
}
#: The "Other Currencies" column is a cedi equivalent (sheet unit '000).
_MAIN_ROWS_OTHER: dict[int, RowSource] = {
    19: nop("net_assets"),
    21: CONTINGENTS_INPUT,
    24: nop("net_trading"),
    29: nop("net_ghs"),
    32: nop("net_ghs"),
    35: MANAGEMENT_LIMIT_INPUT,
}
#: Row 32 "Cedi equivalent (in '000) (of NOP in currency)" for the named columns.
_MAIN_ROW_32: dict[int, RowSource] = {32: nop("net_ghs")}

# ---------------------------------------------------------------------------
# FOREX OPEN POSITION — II. aggregate forex open position (cedi '000)
# ---------------------------------------------------------------------------

_AGGREGATE_ROWS: dict[int, RowSource] = {
    44: nop("net_ghs", currency="USD"),
    45: nop("net_ghs", currency="GBP"),
    46: nop("net_ghs", currency="DEM"),
    47: nop("net_ghs", currency="other"),
    50: nop("afop"),  # a. AGGREGATE FOREX OPEN POSITION — the FX run's nop_ghs
    52: nop("net_worth"),  # b. Net own funds — Tier 1 (the FX limit denominator)
}
_AGGREGATE_PCT_ROWS: dict[int, RowSource] = {
    53: nop("afop_pct_nof", unscaled=True),  # c. AFOP as % of NOF
    55: nop("aggregate_limit_pct", unscaled=True),  # regulatory (BoG) limit on AFOP, % of NOF
}

# ---------------------------------------------------------------------------
# SCHEDULE A — composition of net assets by nature, per currency
# ---------------------------------------------------------------------------

_BANKS = ["BANK_OECD", "BANK_NON_OECD"]
_CASH_LIKE = ["CASH", "INTERBANK_PLACEMENT"]

_SCHEDULE_A_ROWS: dict[int, RowSource] = {
    # A. ASSETS
    8: positions_ccy(position_types=["CASH"], has_counterparty=False),  # 1. Cash on hand
    # 2. Funds in current a/cs (no contractual maturity)
    10: positions_ccy(position_types=_CASH_LIKE, resident=False, has_maturity=False),
    11: positions_ccy(position_types=_CASH_LIKE, counterparty_types=["CENTRAL_BANK"]),
    12: positions_ccy(
        position_types=_CASH_LIKE,
        resident="unknown_as_resident",
        counterparty_types=_BANKS,
        has_maturity=False,
    ),
    # 3. Placements / deposits / repos on own account (with a contractual maturity)
    15: positions_ccy(
        position_types=["INTERBANK_PLACEMENT"],
        resident="unknown_as_resident",
        counterparty_types=_BANKS,
        has_maturity=True,
    ),
    16: positions_ccy(position_types=["INTERBANK_PLACEMENT"], resident=False, has_maturity=True),
    17: RowSource(
        None,
        notes=(
            "placements at overseas banks held on CUSTOMER account (memo — outside the "
            "own-books TA formula); the fiduciary book is not flagged in canonical data"
        ),
    ),
    19: positions_ccy(position_types=["SECURITY_HOLDING"]),  # 4. Securities investments
    20: positions_ccy(position_types=["LOAN"]),  # 5. Loans & advances (net)
    21: positions_ccy(position_types=["OTHER_ASSET"]),  # 6. Other assets / receivables
    # B. LIABILITIES — 7. Deposits
    28: positions_ccy(position_types=["DEPOSIT"], resident=False),  # a. Non-residents
    # b. Residents: i) foreign exchange a/cs (external) — attribute
    #    fx_account_type external/fea; ii) (internal) — internal/fca or unset
    30: positions_ccy(
        position_types=["DEPOSIT"],
        resident="unknown_as_resident",
        attribute_in={"fx_account_type": ["external", "fea"]},
    ),
    31: positions_ccy(
        position_types=["DEPOSIT"],
        resident="unknown_as_resident",
        attribute_in={"fx_account_type": ["internal", "fca"]},
        attribute_missing_ok=True,
    ),
    33: positions_ccy(position_types=["INTERBANK_BORROWING"]),  # 8. Borrowings inter-bank
    34: positions_ccy(  # 9. Other borrowings
        position_types=["OTHER_LIABILITY"],
        attribute_in={"instrument": ["term_borrowing", "borrowing", "bond_issued"]},
    ),
    35: positions_ccy(  # 10. Other liabilities / payables
        position_types=["OTHER_LIABILITY"],
        attribute_not_in={"instrument": ["term_borrowing", "borrowing", "bond_issued"]},
    ),
}
_SCHEDULE_A_DATA_ROWS = tuple(_SCHEDULE_A_ROWS)


def _unscaled(rows: dict[int, RowSource]) -> dict[int, RowSource]:
    """Named-currency columns are foreign-currency UNITS — never scaled."""
    return {
        row: RowSource(src.source, dict(src.params), src.notes, unscaled=True)
        for row, src in rows.items()
    }


# ---------------------------------------------------------------------------
# SCHEDULE B — liabilities (crystallised) under contingents
# ---------------------------------------------------------------------------

_SCHEDULE_B_ROWS: dict[int, RowSource] = {
    9: RowSource(None, notes="crystallised letters of credit in currency — bank must supply"),
    11: RowSource(None, notes="crystallised guarantees in currency — bank must supply"),
    13: RowSource(None, notes="crystallised other commitments in currency — bank must supply"),
}

# ---------------------------------------------------------------------------
# SCHEDULE C — net trading position (contract book) + annexure
# ---------------------------------------------------------------------------

_SCHEDULE_C_ROWS: dict[int, RowSource] = {
    9: nop("spot_long"),  # (i) Spot Purchase
    11: nop("spot_short"),  # (ii) Spot Sale (negative: L + / S −)
    16: nop("forward_long"),  # (iii) Forward Purchase
    18: nop("forward_short"),  # (iv) Forward sale (negative)
}
#: Annexure: A. PURCHASE CONTRACTS rows 35–41, B. SALE CONTRACTS rows 48–54.
PURCHASE_ROWS = tuple(range(35, 42))
SALE_ROWS = tuple(range(48, 55))
#: Annexure columns: B date · C name of seller/buyer · D currency · E amount ·
#: F forward period · G rate · H forward points · I delivery / maturity date.
ANNEX_TEXT_COLUMNS: dict[str, str] = {
    "date": "B",
    "counterparty": "C",
    "currency": "D",
    "period": "F",
    "rate": "G",
    "points": "H",
    "delivery": "I",
}
ANNEX_AMOUNT_COLUMN: dict[str, str] = {"amount": "E"}


def _annex(side: str, rows: tuple[int, ...], *, unscaled: bool) -> dict[int, RowSource]:
    return {
        row: RowSource(
            "bsd13.forward_contract",
            {"side": side, "index": index},
            notes=f"outstanding forward {side} contract #{index} (FX_HEDGE book)",
            unscaled=unscaled,
        )
        for index, row in enumerate(rows, start=1)
    }


def _serials(rows: tuple[int, ...]) -> dict[int, RowSource]:
    """The annexure's SI No. cells ship 1..7 in the template — kept as such."""
    return {
        row: RowSource("constant", {"value": index}, notes="template serial number", unscaled=True)
        for index, row in enumerate(rows, start=1)
    }


LINES = {
    MAIN: (
        *grid_lines(
            FORM,
            MAIN,
            rows=[19, 21, 24, 29, 35],
            value_columns=MAIN_CCY_COLUMNS,
            row_sources=_unscaled(_MAIN_ROWS_CCY),
            code_prefix="BSD13.FXP",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            MAIN,
            rows=[32],
            value_columns=MAIN_CCY_COLUMNS,
            row_sources=_MAIN_ROW_32,
            code_prefix="BSD13.FXP",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            MAIN,
            rows=[19, 21, 24, 29, 32, 35],
            value_columns=MAIN_OTHER_COLUMN,
            row_sources=_MAIN_ROWS_OTHER,
            code_prefix="BSD13.FXP.OTHER",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            MAIN,
            rows=[44, 45, 46, 47, 50, 52],
            value_columns={"nop": "C"},
            row_sources=_AGGREGATE_ROWS,
            code_prefix="BSD13.AFOP",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            MAIN,
            rows=[53, 55],
            value_columns={"nop": "C"},
            row_sources=_AGGREGATE_PCT_ROWS,
            code_prefix="BSD13.AFOP",
            label_column="B",
        ),
    ),
    SCHEDULE_A: (
        *grid_lines(
            FORM,
            SCHEDULE_A,
            rows=_SCHEDULE_A_DATA_ROWS,
            value_columns=SCHEDULE_CCY_COLUMNS,
            row_sources=_unscaled(_SCHEDULE_A_ROWS),
            code_prefix="BSD13.SCHA",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            SCHEDULE_A,
            rows=_SCHEDULE_A_DATA_ROWS,
            value_columns=SCHEDULE_OTHER_COLUMN,
            row_sources=_SCHEDULE_A_ROWS,
            code_prefix="BSD13.SCHA.OTHER",
            label_column="B",
        ),
    ),
    SCHEDULE_B: grid_lines(
        FORM,
        SCHEDULE_B,
        rows=tuple(_SCHEDULE_B_ROWS),
        value_columns={**SCHEDULE_CCY_COLUMNS, **SCHEDULE_OTHER_COLUMN},
        row_sources=_SCHEDULE_B_ROWS,
        code_prefix="BSD13.SCHB",
        default=CONTINGENTS_INPUT,
        label_column="B",
    ),
    SCHEDULE_C: (
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=tuple(_SCHEDULE_C_ROWS),
            value_columns=SCHEDULE_CCY_COLUMNS,
            row_sources=_unscaled(_SCHEDULE_C_ROWS),
            code_prefix="BSD13.SCHC",
            label_column="B",
        ),
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=tuple(_SCHEDULE_C_ROWS),
            value_columns=SCHEDULE_OTHER_COLUMN,
            row_sources=_SCHEDULE_C_ROWS,
            code_prefix="BSD13.SCHC.OTHER",
            label_column="B",
        ),
        # Annexure — A. purchase contracts
        *leaf_lines(
            FORM,
            SCHEDULE_C,
            value_columns={"serial": "A"},
            row_sources=_serials(PURCHASE_ROWS),
            code_prefix="BSD13.SCHC.ANNEX.PURCHASE.SI",
            only_rows=PURCHASE_ROWS,
        ),
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=PURCHASE_ROWS,
            value_columns=ANNEX_TEXT_COLUMNS,
            row_sources=_annex("purchase", PURCHASE_ROWS, unscaled=True),
            code_prefix="BSD13.SCHC.ANNEX.PURCHASE",
        ),
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=PURCHASE_ROWS,
            value_columns=ANNEX_AMOUNT_COLUMN,
            row_sources=_annex("purchase", PURCHASE_ROWS, unscaled=False),
            code_prefix="BSD13.SCHC.ANNEX.PURCHASE.AMOUNT",
        ),
        # Annexure — B. sale contracts
        *leaf_lines(
            FORM,
            SCHEDULE_C,
            value_columns={"serial": "A"},
            row_sources=_serials(SALE_ROWS),
            code_prefix="BSD13.SCHC.ANNEX.SALE.SI",
            only_rows=SALE_ROWS,
        ),
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=SALE_ROWS,
            value_columns=ANNEX_TEXT_COLUMNS,
            row_sources=_annex("sale", SALE_ROWS, unscaled=True),
            code_prefix="BSD13.SCHC.ANNEX.SALE",
        ),
        *grid_lines(
            FORM,
            SCHEDULE_C,
            rows=SALE_ROWS,
            value_columns=ANNEX_AMOUNT_COLUMN,
            row_sources=_annex("sale", SALE_ROWS, unscaled=False),
            code_prefix="BSD13.SCHC.ANNEX.SALE.AMOUNT",
        ),
    ),
}
