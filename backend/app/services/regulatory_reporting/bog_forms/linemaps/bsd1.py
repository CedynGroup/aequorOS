"""BSD1 — Liquidity Reserve Return (weekly; Ghana branches only).

Official layout: sheet ``BSD1 `` (note the trailing space in the official tab
name) is a THURS…WED daily grid — 41 leaf rows × 7 day columns ``B``…``H`` =
287 input cells; TOTAL (``I``), AVERAGE (``J``), every sub-total, the actual
reserve ratios (rows 67–70), the required reserves (9% / 35% / 15%, rows 73–77)
and the surplus/deficit lines (80–85) are the template's own formulas.
``BSD1-Annex1`` breaks foreign-currency deposits (previous week) and balances
(current week) down by currency with the daily exchange rates as memoranda;
``BSD1-Annex2`` (branch cash & deposits) and ``BSD1-Annex3`` (largest weekly
movements in deposits/advances) are blank grids. Line/cell map:
docs/bog_returns/bsd1_line_map.md.

Time axis (from the template's own header formulas): the PERIOD date ``B3`` is
the week's Wednesday = the reporting date; DEPOSITS (rows 10–26) report the
PREVIOUS week (``B7 = B3-13`` … ``H7 = B3-7``); LIQUID ASSETS and memoranda
(rows 32–95) the CURRENT week (``B28 = B3-6`` … ``H28 = B3``).

Every daily cell is fed by ``bsd1.daily`` — Σ canonical position snapshots
whose ``as_of_date`` is exactly that day (the EOD ladder) — and is
``input_required`` for any day the ladder holds no rung; a balance is never
copied across the week. On the reporting date the monthly fact spine may fill
the one column it describes (``facts`` fallback), never the other six.
"""

from __future__ import annotations

from typing import Any

from ._common import INPUT_REQUIRED, RowSource, grid_lines, leaf_lines

SHEET = "BSD1 "
DAY_COLUMNS = {"thu": "B", "fri": "C", "sat": "D", "sun": "E", "mon": "F", "tue": "G", "wed": "H"}

_BOG = ["CENTRAL_BANK"]
_GOV = ["SOVEREIGN"]
_GOV_ALL = ["SOVEREIGN", "GOVERNMENT_ENTITY"]
_NBFI = ["NBFI"]

_LADDER_NOTE = (
    "daily balance from the EOD position ladder (same-day snapshots); days without a "
    "ladder rung stay input_required — a balance is never copied across the week"
)
_ACCRUAL_NOTE = (
    "interest accrued but not yet credited — accruals sub-ledger required (Guide BSD1 "
    "13–14: determined monthly)"
)


def daily(week: str, notes: str = _LADDER_NOTE, **filters: Any) -> RowSource:
    """A ``bsd1.daily`` binding for the given week block ("previous" | "current")."""
    return RowSource("bsd1.daily", {"week": week, **filters}, notes=notes)


def deposits(**filters: Any) -> RowSource:
    return daily("previous", position_types=["DEPOSIT"], **filters)


def liquid(**filters: Any) -> RowSource:
    return daily("current", **filters)


def _bog_bill(days: int) -> RowSource:
    return liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_BOG,
        currency="GHS",
        attribute_eq={"instrument": "bog_bill", "tenor_days": days},
    )


def _gog_note(years: int, rate_type: str) -> RowSource:
    return liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        rate_types=[rate_type],
        attribute_eq={"instrument": "gog_bond", "tenor_years": years},
        notes=_LADDER_NOTE + f"; {years}-year GoG note with snapshot rate_type={rate_type}",
    )


_ROWS: dict[int, RowSource] = {
    # ---- DEPOSITS (previous week) — A) DOMESTIC = payable in cedis --------------
    10: deposits(currency="GHS", deposit_account_types=["CURRENT", "CALL"]),
    11: deposits(currency="GHS", deposit_account_types=["FIXED"]),
    12: deposits(currency="GHS", deposit_account_types=["SAVINGS"]),
    13: deposits(
        currency="GHS",
        attribute_eq={"instrument": "certificate_of_deposit"},
        notes=_LADDER_NOTE
        + "; CDs identified by snapshot attribute instrument=certificate_of_deposit",
    ),
    14: deposits(
        currency="GHS",
        attribute_eq={"instrument": "special_deposit"},
        notes=_LADDER_NOTE
        + "; special deposits identified by attribute instrument=special_deposit",
    ),
    15: deposits(
        currency="GHS",
        attribute_eq={"instrument": "margin_against_contingent"},
        notes=_LADDER_NOTE
        + "; margins identified by attribute instrument=margin_against_contingent",
    ),
    # ---- B) FOREIGN = payable in a foreign currency (cedi equivalent) ----------
    # Line 8 on the form. The sheet's own roll-up is 11 = 8+9+10, so ordinary
    # foreign-currency deposits must EXCLUDE the special deposits and margins
    # reported on 9 and 10 — otherwise both land in the Sub-Total twice. The
    # domestic block is already safe: 1–3 filter on deposit_account_type while
    # 5 and 6 carry OTHER, so the same distinction applies here.
    18: deposits(
        currency="FX",
        deposit_account_types=["CURRENT", "CALL", "SAVINGS", "FIXED"],
        notes=_LADDER_NOTE
        + "; ordinary FX deposits only — special deposits (9) and margins (10) are"
        " reported on their own lines and would otherwise be counted twice in 11",
    ),
    19: deposits(currency="FX", attribute_eq={"instrument": "special_deposit"}),
    20: deposits(currency="FX", attribute_eq={"instrument": "margin_against_contingent"}),
    23: RowSource(None, notes=_ACCRUAL_NOTE),
    24: RowSource(None, notes=_ACCRUAL_NOTE),
    # ---- LIQUID ASSETS (current week) — A. Primary -----------------------------
    32: liquid(
        position_types=["CASH"],
        no_counterparty=True,
        currency="all",
        facts={"group": "balance_sheet", "categories": ["cash_vault"], "currency": "all"},
        notes=_LADDER_NOTE + "; cash on hand = CASH positions with no obligor (vault notes and "
        "coins, any currency, cedi equivalent); reporting-date column falls back to the "
        "period-end fact cash_vault (GL cash accounts) when the ladder has no rung",
    ),
    33: liquid(
        position_types=["CASH"],
        counterparty_types=_BOG,
        currency="GHS",
        facts={
            "group": "balance_sheet",
            "categories": ["bog_required_reserves", "bog_excess_reserves"],
            "currency": "all",
        },
        notes=_LADDER_NOTE + "; BoG current account = CASH positions with the central bank; "
        "reporting-date column falls back to the period-end facts bog_required_reserves + "
        "bog_excess_reserves (both are balances on the BoG current account) when the ladder "
        "has no rung",
    ),
    34: liquid(
        position_types=["CASH"],
        counterparty_types=_BOG,
        currency="FX",
        notes=_LADDER_NOTE + "; foreign-currency current account with BoG (cedi equivalent) — "
        "the fact spine carries no FX split, so no reporting-date fallback",
    ),
    # ---- B. Secondary ------------------------------------------------------------
    39: liquid(
        position_types=["INTERBANK_PLACEMENT"],
        counterparty_types=_NBFI,
        currency="GHS",
        attribute_eq={"institution_class": "discount_house", "tenor": "call"},
    ),
    41: liquid(
        position_types=["SECURITY_HOLDING"],
        currency="GHS",
        attribute_eq={"instrument": "cocoa_bill"},
    ),
    42: liquid(
        position_types=["SECURITY_HOLDING"],
        currency="GHS",
        attribute_eq={"instrument": "cotton_bill"},
    ),
    43: liquid(
        position_types=["SECURITY_HOLDING"],
        currency="GHS",
        attribute_eq={"instrument": "grains_bill"},
    ),
    45: _bog_bill(28),
    46: _bog_bill(30),
    47: _bog_bill(56),
    48: _bog_bill(91),
    49: _bog_bill(182),
    50: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_BOG,
        currency="GHS",
        attribute_eq={"instrument": "bog_bond", "tenor_years": 1},
    ),
    53: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        attribute_eq={"instrument": "tbill", "tenor_days": 91},
    ),
    54: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        attribute_eq={"instrument": "tbill", "tenor_days": 182},
    ),
    55: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        attribute_eq={"instrument": "gog_bond", "tenor_years": 1},
    ),
    57: _gog_note(2, "FLOATING"),
    58: _gog_note(3, "FLOATING"),
    59: _gog_note(2, "FIXED"),
    60: _gog_note(3, "FIXED"),
    61: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        attribute_eq={"instrument": "ggilb"},
    ),
    62: liquid(
        position_types=["SECURITY_HOLDING"], currency="GHS", attribute_eq={"instrument": "tor_bond"}
    ),
    63: liquid(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        currency="GHS",
        attribute_eq={"instrument": "gog_stock"},
        notes=_LADDER_NOTE + "; Ghana Government stocks (Guide BSD1 25: at cost + interest earned)",
    ),
    64: liquid(
        position_types=["SECURITY_HOLDING"],
        currency="GHS",
        attribute_eq={"instrument": "ssnit_educational_bond"},
    ),
    # ---- MEMORANDUM ITEMS (current week) -----------------------------------------
    89: liquid(
        position_types=["SECURITY_HOLDING"],
        attribute_eq={"instrument": "finsap_bond", "issuer_class": "soe"},
    ),
    90: liquid(
        position_types=["SECURITY_HOLDING"],
        attribute_eq={"instrument": "finsap_bond", "issuer_class": "private"},
    ),
    91: liquid(position_types=["LOAN"], currency="GHS"),
    92: liquid(position_types=["LOAN"], currency="FX"),
    94: liquid(
        position_types=["DEPOSIT"],
        currency="all",
        facts={
            "group": "balance_sheet",
            "categories": [
                "retail_deposits_stable",
                "retail_deposits_less_stable",
                "wholesale_operational",
                "wholesale_non_op_sme",
                "wholesale_non_op_corporate",
            ],
            "currency": "all",
        },
        notes=_LADDER_NOTE + "; total deposits this week — reporting-date column falls back to "
        "the period-end balance_sheet deposit facts when the ladder has no rung",
    ),
    95: liquid(position_types=["DEPOSIT"], counterparty_types=_GOV_ALL, currency="all"),
}

# ---- BSD1-Annex1: forex deposits (previous week) & balances (current week) --------
_ANNEX1_DEPOSIT_NOTE = (
    _LADDER_NOTE + "; foreign-currency DEPOSIT positions in this currency, cedi equivalent "
    "(sheet unit ¢'Million; ties to BSD1 row 8)"
)
_ANNEX1_BALANCE_NOTE = (
    _LADDER_NOTE + "; CASH + INTERBANK_PLACEMENT positions in this currency in UNITS OF THAT "
    "CURRENCY (the block's 'Other(s) (denominated in US$)' label shows the balances are "
    "native-unit) — unscaled"
)


def _annex1_deposit(currency: str) -> RowSource:
    return daily(
        "previous",
        _ANNEX1_DEPOSIT_NOTE,
        position_types=["DEPOSIT"],
        currency=currency,
    )


def _annex1_balance(currency: str) -> RowSource:
    return RowSource(
        "bsd1.daily",
        {
            "week": "current",
            "position_types": ["CASH", "INTERBANK_PLACEMENT"],
            "currency": currency,
            "measure": "native",
        },
        notes=_ANNEX1_BALANCE_NOTE,
        unscaled=True,
    )


def _annex1_rate(currency: str) -> RowSource:
    return RowSource(
        "bsd1.fx_spot",
        {"week": "current", "currency": currency},
        notes=(
            "same-day authoritative spot (cedis per 1 unit) from the market-data ladder; "
            "no observation that day ⇒ input_required (a quote is never carried forward)"
        ),
        unscaled=True,
    )


_ANNEX1_ROWS: dict[int, RowSource] = {
    10: _annex1_deposit("USD"),
    11: _annex1_deposit("EUR"),
    12: _annex1_deposit("GBP"),
    13: daily(
        "previous",
        _ANNEX1_DEPOSIT_NOTE,
        position_types=["DEPOSIT"],
        currency="FX",
        currencies_not_in=["USD", "EUR", "GBP"],
    ),
    19: _annex1_balance("USD"),
    20: _annex1_balance("EUR"),
    21: _annex1_balance("GBP"),
    22: RowSource(
        None,
        notes=(
            "other-currency balances re-denominated in US$ need a per-currency USD cross "
            "rate per day — bank must supply (the platform holds them in native units and "
            "cedis only)"
        ),
    ),
    27: _annex1_rate("USD"),
    28: _annex1_rate("EUR"),
    29: _annex1_rate("GBP"),
    30: RowSource(
        None,
        notes="'Other(s)' exchange rate is not a single pair — bank must state the basis used",
    ),
}

# ---- BSD1-Annex2: branch cash and deposits (blank grid; depends on branch count) ---
_ANNEX2_NOTE = (
    "per-branch daily cash and deposits — positions carry attributes.branch_id but the "
    "platform has no branch register that assigns branches to the annex's A…F slots "
    "(the Outlet register carries no branch_id link); bank must supply"
)
_ANNEX2_ROWS = (8, 9, 11, 12, 14, 15, 17, 18, 20, 21, 23, 24, 25)

# ---- BSD1-Annex3: differences in deposits and advances for the week ---------------
_ANNEX3_MOVEMENT = {
    8: RowSource(
        "bsd1.daily",
        {
            "days_before": 0,
            "position_types": ["DEPOSIT"],
            "currency": "GHS",
            "week_change": True,
        },
        notes=(
            "increase/decrease in cedi deposits over the week = Σ ladder(reporting date) − "
            "Σ ladder(reporting date − 7); input_required unless both days have a rung"
        ),
    ),
    13: RowSource(
        "bsd1.daily",
        {"days_before": 0, "position_types": ["LOAN"], "currency": "GHS", "week_change": True},
        notes="weekly change in cedi loans & overdrafts from the ladder (both days need a rung)",
    ),
    15: RowSource(
        "bsd1.daily",
        {"days_before": 0, "position_types": ["LOAN"], "currency": "FX", "week_change": True},
        notes="weekly change in foreign-currency loans & overdrafts (cedi equivalent), ladder",
    ),
}
_ANNEX3_NAMES_NOTE = (
    "largest weekly movements by customer (name + amount): needs the daily position ladder "
    "AND a per-counterparty weekly delta ranking the platform does not compute (LMTD Top-N "
    "concentration ranks levels, not movements) — bank must supply"
)

LINES = {
    SHEET: leaf_lines(
        "BSD1",
        SHEET,
        value_columns=DAY_COLUMNS,
        row_sources=_ROWS,
        code_prefix="BSD1",
        default=INPUT_REQUIRED,
    ),
    "BSD1-Annex1": leaf_lines(
        "BSD1",
        "BSD1-Annex1",
        value_columns=DAY_COLUMNS,
        row_sources=_ANNEX1_ROWS,
        code_prefix="BSD1.Annex1",
        default=INPUT_REQUIRED,
    ),
    "BSD1-Annex2": grid_lines(
        "BSD1",
        "BSD1-Annex2",
        rows=_ANNEX2_ROWS,
        value_columns=DAY_COLUMNS,
        row_sources={},
        code_prefix="BSD1.Annex2",
        default=RowSource(None, notes=_ANNEX2_NOTE),
    ),
    "BSD1-Annex3": (
        *grid_lines(
            "BSD1",
            "BSD1-Annex3",
            rows=(8, 13, 15),
            value_columns={"amount": "G"},
            row_sources=_ANNEX3_MOVEMENT,
            code_prefix="BSD1.Annex3",
        ),
        *grid_lines(
            "BSD1",
            "BSD1-Annex3",
            rows=(*range(22, 30), *range(37, 45), *range(51, 59)),
            value_columns={"name": "B"},
            row_sources={},
            code_prefix="BSD1.Annex3.name",
            default=RowSource(None, notes=_ANNEX3_NAMES_NOTE),
        ),
        *leaf_lines(
            "BSD1",
            "BSD1-Annex3",
            value_columns={"amount": "G"},
            row_sources={},
            code_prefix="BSD1.Annex3.amount",
            default=RowSource(None, notes=_ANNEX3_NAMES_NOTE),
        ),
    ),
}
