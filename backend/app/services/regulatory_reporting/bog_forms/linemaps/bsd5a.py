"""BSD5A — Capital Adequacy Return (bank, solo, monthly).

Official workbook ``FORM BSD5A REVISED.xls``: sheet ``CAR FORMAT`` (item
number column C, amount column E; 45 amount inputs, 51 item-number inputs, 12
BoG formulas — Tier 1, Net Tier 1, Tier 2, ADJUSTED CAPITAL BASE, Claims on
BoG/Government subtotals, Adjusted Total Assets, contingents, Net Contingent
Liabs, ADJUSTED ASSET BASE, the ratio E70 ``=E25/E69`` and the surplus/deficit
test E71 ``=E25-(E69*6%)``), sheet ``NEW RISK WEIGHTS`` (the risk-weight table:
item No · Basle · Proposed · Existing — 139 printed percentages) and sheet
``PROVISION`` (the % provision × days-of-delinquency ladder — 10 printed
values). Line/cell map: docs/bog_returns/bsd5a_line_map.md.

Sources (all read-only over existing platform state; the sheet's own printed
percentages are the ONLY factors applied — never a platform weight):

* capital constituents and deductions — ``capital_component`` facts by the
  category vocabulary below and/or the platform tier
  (``sources_ext/bsd5.py::bsd5.capital_facts``);
* TOTAL ASSETS — Σ balance-sheet asset facts (``bsd5.balance_sheet_side``);
  zero-weight deductions — the balance-sheet cash / BoG / securities facts;
  partially-weighted classes — the printed % of the matching canonical
  positions or ``loan_exposure`` fact (``bsd5.pct_of``);
* contingents — the platform's LC/guarantee book (``off_balance`` facts) with
  positions tagged ``obs_category=letter_of_credit`` on their own row;
* 50% of NOP and the 3-year average gross income — the latest SUCCEEDED
  capital baseline run's persisted line items (``bsd5.run_line`` /
  ``bsd5.avg_gross_income``);
* NEW RISK WEIGHTS / PROVISION / item numbers — ``constant`` = the template's
  own printed value, read from the committed layout so it can never drift.

Where the platform has no honest source (forex cash split, public-sector NBFI
claims, guaranteed-loan coverage, export finance, acceptances / endorsements /
RUF / NIF / standby LCs, class-1/2 off-balance-sheet split) the row is
``input_required`` with a note; the official cell is still emitted.
"""

from __future__ import annotations

from typing import Any

from ..layout import load_layout
from ._common import INPUT_REQUIRED, RowSource, leaf_lines, positions

FORM = "BSD5A"
SHEET_CAR = "CAR FORMAT"
SHEET_RW = "NEW RISK WEIGHTS"
SHEET_PROV = "PROVISION"

# ---------------------------------------------------------------------------
# capital_component category vocabulary (lower-cased ``capital_component`` names
# of the bank's capital_structure register — the same convention BSD2 §14–17
# uses; a bank whose register uses other names maps them at ingestion)
# ---------------------------------------------------------------------------
PAID_UP: tuple[str, ...] = (
    "paid_up_capital",
    "ordinary_share_capital",
    "paid_up_ordinary_share_capital",
    "stated_capital",
    "share_capital",
    "paid_up_share_capital",
    "ordinary_shares",
)
PERMANENT_PREFERENCE: tuple[str, ...] = (
    "permanent_preference_shares",
    "perpetual_preference_shares",
    "perpetual_non_cumulative_preference_shares",
    "non_cumulative_preference_shares",
)
UNDISCLOSED: tuple[str, ...] = (
    "undisclosed_reserves",
    "current_year_profit",
    "current_year_profit_loss",
    "current_year_result",
    "profit_for_the_period",
    "interim_profit",
    "unaudited_profit",
)
REVALUATION_FIXED: tuple[str, ...] = (
    "revaluation_reserve",
    "revaluation_reserves",
    "fixed_asset_revaluation_reserve",
    "property_revaluation_reserve",
)
LATENT_REVALUATION: tuple[str, ...] = (
    "latent_revaluation_reserve",
    "latent_revaluation_reserves",
    "unrealised_revaluation_reserve",
)
REVALUATION: tuple[str, ...] = (*REVALUATION_FIXED, *LATENT_REVALUATION)
CAPITALISED_REVALUATION: tuple[str, ...] = (
    "capitalised_revaluation_reserve",
    "capitalised_revaluation_reserves",
    "capitalized_revaluation_reserve",
    "capitalized_revaluation_reserves",
)
MINORITY: tuple[str, ...] = (
    "minority_interest",
    "minority_interests",
    "non_controlling_interest",
    "non_controlling_interests",
)
MINORITY_TIER2_PREFERENCE: tuple[str, ...] = (
    "minority_interests_tier2_preference_shares",
    "minority_interests_in_tier_2_preferred_shares",
    "minority_interest_tier2_preferred",
)
GOODWILL: tuple[str, ...] = (
    "goodwill",
    "intangibles",
    "intangible_assets",
    "goodwill_and_intangibles",
    "goodwill_intangibles",
    "deferred_expenditure",
    "preliminary_expenses",
)
LOSSES_NOT_PROVIDED: tuple[str, ...] = (
    "losses_not_provided_for",
    "unprovided_losses",
    "unprovided_loan_losses",
    "under_provision",
    "provision_shortfall",
)
INVESTMENTS_SUBSIDIARIES: tuple[str, ...] = (
    "investments_in_subsidiaries",
    "investment_in_subsidiaries",
    "investments_in_unconsolidated_subsidiaries",
    "unconsolidated_subsidiaries",
    "investments_in_associates",
    "investment_in_associates",
)
INVESTMENTS_OTHER_BANKS: tuple[str, ...] = (
    "investments_in_other_banks",
    "investments_in_financial_institutions",
    "investments_in_capital_of_other_banks",
    "investments_in_banks_and_financial_institutions",
    "holdings_of_other_banks_capital",
)
CONNECTED_LENDING: tuple[str, ...] = (
    "connected_lending",
    "connected_lending_long_term",
    "connected_lending_of_long_term_nature",
    "connected_lending_capital_nature",
    "intra_group_lending",
)
SUBORDINATED_DEBT: tuple[str, ...] = (
    "subordinated_debt",
    "subordinated_term_debt",
    "sub_debt",
    "subordinated_loans",
    "subordinated_term_loans",
)
HYBRID: tuple[str, ...] = (
    "hybrid_capital",
    "hybrid_instruments",
    "hybrid_capital_instruments",
    "hybrid_debt_equity_instruments",
)
CUMULATIVE_PREFERENCE: tuple[str, ...] = (
    "cumulative_preference_shares",
    "cumulative_preference_share_capital",
)
#: deduction-flagged facts of these names still reduce Disclosed Reserves (an
#: income-surplus deficit is a negative reserve, not a Basel deduction)
RESERVE_DEFICITS: tuple[str, ...] = (
    "income_surplus",
    "income_surplus_account",
    "retained_earnings",
    "retained_profits",
    "revenue_reserve",
    "accumulated_losses",
    "accumulated_deficit",
    "retained_deficit",
)
#: every named category that has its OWN row — excluded from the CET1 residual
#: that feeds Disclosed Reserves so nothing lands twice
_NAMED_ELSEWHERE: tuple[str, ...] = (
    *PAID_UP,
    *PERMANENT_PREFERENCE,
    *UNDISCLOSED,
    *REVALUATION,
    *CAPITALISED_REVALUATION,
    *MINORITY,
    *MINORITY_TIER2_PREFERENCE,
    *GOODWILL,
    *LOSSES_NOT_PROVIDED,
    *INVESTMENTS_SUBSIDIARIES,
    *INVESTMENTS_OTHER_BANKS,
    *CONNECTED_LENDING,
    *SUBORDINATED_DEBT,
    *HYBRID,
    *CUMULATIVE_PREFERENCE,
)


def capital(*categories: str, notes: str = "", **params: Any) -> RowSource:
    """Signed Σ of ``capital_component`` facts by name (see ``bsd5.capital_facts``)."""
    return RowSource("bsd5.capital_facts", {"categories": list(categories), **params}, notes=notes)


def capital_deduction(*categories: str, notes: str = "") -> RowSource:
    return RowSource(
        "bsd5.capital_facts", {"categories": list(categories), "deduction": True}, notes=notes
    )


def pct_of(pct: int, inner: RowSource, notes: str = "") -> RowSource:
    """The sheet's printed percentage of another binding's value."""
    return RowSource(
        "bsd5.pct_of",
        {"pct": pct, "source": inner.source, "params": dict(inner.params)},
        notes=notes or inner.notes,
    )


def facts(group: str, *categories: str, notes: str = "", **params: Any) -> RowSource:
    """``facts.sum`` binding with a completion-sheet note (the shared helper has none)."""
    return RowSource(
        "facts.sum", {"group": group, "categories": list(categories), **params}, notes=notes
    )


def template_constants(
    form: str, sheet: str, column: str, *, notes: str, unscaled: bool = True
) -> dict[int, RowSource]:
    """One ``constant`` per input cell of ``column`` = the template's own printed
    value (item numbers, the NEW RISK WEIGHTS table, the PROVISION ladder) —
    read from the committed layout, so a BoG weight can never be retyped."""
    layout = load_layout(form).sheet(sheet)
    return {
        cell.row: RowSource("constant", {"value": cell.value}, notes=notes, unscaled=unscaled)
        for cell in layout.input_cells
        if cell.ref.startswith(column) and cell.ref[len(column) :].isdigit()
    }


# ---------------------------------------------------------------------------
# shared bindings (also the BSD5A cells BSD5B links to)
# ---------------------------------------------------------------------------
_BANKS = ["BANK_OECD", "BANK_NON_OECD"]
_NBFI = ["NBFI"]

GOODWILL_ROW = capital_deduction(*GOODWILL)
LOSSES_ROW = capital_deduction(*LOSSES_NOT_PROVIDED)
INV_SUBSIDIARIES_ROW = capital_deduction(
    *INVESTMENTS_SUBSIDIARIES,
    notes="aggregate equity holdings in subsidiaries whose accounts are not integrated (Guide)",
)
INV_OTHER_BANKS_ROW = capital_deduction(*INVESTMENTS_OTHER_BANKS)
CONNECTED_LENDING_ROW = capital_deduction(
    *CONNECTED_LENDING,
    notes="lending of a long-term nature to subsidiaries/associates and all intra-group "
    "holdings (Guide BSD5 item 6)",
)
LC_POSITIONS = positions(
    position_types=["LC_GUARANTEE"],
    measure="notional",
    attribute_eq={"obs_category": "letter_of_credit"},
    currency="all",
)
_CHEQUES = positions(
    position_types=["OTHER_ASSET"],
    attribute_eq={"instrument": "cheques_for_clearing"},
    currency="all",
)
_DISCOUNT_HOUSES = positions(
    position_types=["INTERBANK_PLACEMENT", "SECURITY_HOLDING", "LOAN"],
    counterparty_types=_NBFI,
    attribute_eq={"institution_class": "discount_house"},
    currency="all",
)
_OTHER_BANKS = positions(
    position_types=["INTERBANK_PLACEMENT", "CASH", "SECURITY_HOLDING", "LOAN"],
    counterparty_types=_BANKS,
    currency="all",
)
_NOP = RowSource(
    "bsd5.run_line",
    {"section": "market_rwa", "line_code": "fx_charge", "field": "exposure_amount"},
    notes="larger net open FX position of the latest succeeded capital baseline run "
    "(input_required until the capital engine has run for the period)",
)

_CAR_ROWS: dict[int, RowSource] = {
    # ---- Tier 1 (Guide: Composition of Capital 1.i–ii) ----------------------
    7: capital(*PAID_UP, notes="issued and fully paid ordinary shares (stated capital)"),
    8: RowSource(
        "bsd5.capital_facts",
        {
            "tiers": ["CET1"],
            "exclude": list(_NAMED_ELSEWHERE),
            "include_deductions": list(RESERVE_DEFICITS),
        },
        notes="every other CET1 component: statutory reserve fund, income surplus / "
        "retained earnings, share premium, other disclosed reserves (Guide); an "
        "income-surplus deficit reduces the line",
    ),
    9: RowSource(
        "bsd5.capital_facts",
        {
            "tiers": ["AT1"],
            "categories": list(PERMANENT_PREFERENCE),
            "exclude": [*HYBRID, *CUMULATIVE_PREFERENCE, *MINORITY],
        },
        notes="non-redeemable non-cumulative preference shares; the platform's AT1 "
        "components report here (the form's only non-common Tier 1 line) — an AT1 "
        "instrument BoG treats as hybrid must be re-tagged T2 hybrid_capital",
    ),
    # ---- Less: Tier 1 deductions ---------------------------------------------
    12: GOODWILL_ROW,
    13: LOSSES_ROW,
    14: INV_SUBSIDIARIES_ROW,
    15: INV_OTHER_BANKS_ROW,
    16: CONNECTED_LENDING_ROW,
    # ---- Tier 2 (Guide: Composition of Capital 2.i–v) -----------------------
    20: capital(
        *UNDISCLOSED,
        notes="Guide: relates to current-year profit/loss to the reporting date "
        "(unpublished retained earnings accepted by BSD)",
    ),
    21: capital(
        *REVALUATION,
        notes="fixed-asset revaluation reserves and latent revaluation of long-term "
        "equity holdings (Guide)",
    ),
    22: capital(
        *SUBORDINATED_DEBT,
        notes="conventional unsecured subordinated term debt; the 50%-of-Tier-1 limit is "
        "BoG's — the template carries the gross figure",
    ),
    23: capital(
        *HYBRID,
        *CUMULATIVE_PREFERENCE,
        notes="hybrid debt/equity instruments incl. cumulative preference shares (Guide)",
    ),
    # ---- Adjusted asset base: total assets less zero-weight items -----------
    27: RowSource(
        "bsd5.balance_sheet_side",
        {"side": "asset"},
        notes="Σ balance-sheet asset facts (cash, BoG balances, securities, gross loans, "
        "other assets) — the platform's on-balance-sheet total; contra items are not "
        "carried in the platform balance sheet",
    ),
    29: facts(
        "balance_sheet",
        "cash_vault",
        currency="all",
        notes="GL vault cash; the platform's cash line is not currency-split, so the whole "
        "balance sits here (both cash rows are zero-weighted — the asset base is unaffected)",
    ),
    30: RowSource(
        None,
        notes="forex notes & coins — supply the forex portion of vault cash (the GL cash "
        "line is not currency-split; reported within row 21 until split)",
    ),
    32: facts(
        "balance_sheet",
        "bog_required_reserves",
        "bog_excess_reserves",
        currency="all",
        notes="cleared balances with Bank of Ghana (Guide BSD5 item 7): required + excess "
        "reserve balances",
    ),
    33: RowSource(
        None,
        notes="forex account balance with BoG — the platform's BoG balances are not "
        "currency-split (reported within row 23.i until split)",
    ),
    34: positions(
        position_types=["FX_HEDGE", "DERIVATIVE"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"leg": "receivable"},
        currency="all",
    ),
    35: facts(
        "balance_sheet",
        "securities_bog_bills",
        currency="all",
        notes="the platform's BoG-bills balance-sheet line (SECURITY_HOLDING bills)",
    ),
    36: positions(
        position_types=["OTHER_ASSET"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "repo_receivable"},
        currency="all",
    ),
    39: facts(
        "balance_sheet",
        "securities_gog_bonds",
        currency="all",
        notes="the platform's GoG-bonds balance-sheet line (SECURITY_HOLDING bonds); "
        "treasury bills are carried in row 23.iv (bills) by the platform's split",
    ),
    40: RowSource(
        None,
        notes="Government stocks — no canonical instrument separates stocks from bonds; "
        "GoG paper is reported on row 24.i (supply the split if stocks are held)",
    ),
    41: pct_of(80, _CHEQUES, notes="80% × cheques drawn on other banks in course of clearing"),
    42: GOODWILL_ROW,
    43: INV_SUBSIDIARIES_ROW,
    44: INV_OTHER_BANKS_ROW,
    45: CONNECTED_LENDING_ROW,
    46: pct_of(
        80,
        _DISCOUNT_HOUSES,
        notes="80% × placements with / securities of / loans to discount houses "
        "(canonical NBFI counterparties tagged institution_class=discount_house)",
    ),
    47: pct_of(
        80,
        _OTHER_BANKS,
        notes="80% × nostro balances, placements, securities and loans with bank "
        "counterparties (cedis and forex)",
    ),
    48: RowSource(
        None,
        notes="claims on public-sector financial institutions — no canonical attribute marks "
        "public-sector ownership of an NBFI counterparty; supply 50% of the balance",
    ),
    49: RowSource(
        None,
        notes="loans guaranteed by government — the platform's crm_guarantee_* convention "
        "carries guarantee value by haircut class, not a sovereign flag; supply 80% of "
        "the guaranteed balances",
    ),
    50: RowSource(
        None,
        notes="loans guaranteed by multilateral banks (Guide note 2: AfDB, IBRD, IFC, "
        "EximBank, ECGA, SSNIT …) — supply 80% of the guaranteed balances",
    ),
    51: pct_of(
        50,
        facts("loan_exposure", "residential_mortgage", currency="all"),
        notes="50% × the residential-mortgage loan exposure (Guide note 4)",
    ),
    52: RowSource(
        None,
        notes="export financing loans (Guide note 3 eligibility) — no canonical export-finance "
        "flag; supply 50% of the eligible balances",
    ),
    # ---- Add: contingent liabilities ----------------------------------------
    55: RowSource(
        LC_POSITIONS.source,
        dict(LC_POSITIONS.params),
        notes="LC_GUARANTEE positions tagged obs_category=letter_of_credit (documented "
        "attribute convention), notional",
    ),
    56: RowSource(
        "bsd5.off_balance_residual",
        {"less": [dict(LC_POSITIONS.params)]},
        notes="the platform's LC/guarantee book (off_balance facts — classified as "
        "guarantees/indemnities by default) less the letters of credit on row 38",
    ),
    57: RowSource(None, notes="acceptances — no canonical instrument tag; bank must supply"),
    58: RowSource(None, notes="endorsements — no canonical instrument tag; bank must supply"),
    59: RowSource(
        None, notes="revolving underwriting facilities — no canonical tag; bank must supply"
    ),
    60: RowSource(None, notes="note issuance facilities — no canonical tag; bank must supply"),
    61: RowSource(
        None, notes="standby letters of credit to other banks — no canonical tag; bank must supply"
    ),
    63: RowSource(
        None,
        notes="50% of class-1 off-balance-sheet items (bonds: performance, bid, warranties — "
        "NEW RISK WEIGHTS row 34) — the class split is not canonical; bank must supply",
    ),
    64: RowSource(
        None,
        notes="80% of class-2 off-balance-sheet items (short-term self-liquidating LCs — "
        "NEW RISK WEIGHTS row 35) — the class split is not canonical; bank must supply",
    ),
    # ---- Add: market and operational add-ons --------------------------------
    67: pct_of(50, _NOP, notes="50% × " + _NOP.notes),
    68: RowSource(
        "bsd5.avg_gross_income",
        {"years": 3},
        notes="mean of the latest three gross_income_<year> lines of the capital baseline "
        "run (100% of the 3-year average annual gross income)",
    ),
}

LINES = {
    SHEET_CAR: (
        *leaf_lines(
            FORM,
            SHEET_CAR,
            value_columns={"amount": "E"},
            row_sources=_CAR_ROWS,
            code_prefix="BSD5A.CAR",
            default=INPUT_REQUIRED,
        ),
        *leaf_lines(
            FORM,
            SHEET_CAR,
            value_columns={"no": "C"},
            row_sources=template_constants(
                FORM, SHEET_CAR, "C", notes="official item number as printed in the template"
            ),
            code_prefix="BSD5A.CAR.NO",
        ),
    ),
    SHEET_RW: tuple(
        line
        for key, column, notes in (
            ("NO", "B", "official item number as printed in the template"),
            ("BASLE", "D", "Risk Weight Basle Committee — the template's printed percentage"),
            ("PROPOSED", "E", "Risk Weight Proposed — the template's printed percentage"),
            (
                "EXISTING",
                "F",
                "Risk Weight Existing Position — the template's printed percentage",
            ),
        )
        for line in leaf_lines(
            FORM,
            SHEET_RW,
            value_columns={key.lower(): column},
            row_sources=template_constants(FORM, SHEET_RW, column, notes=notes),
            code_prefix=f"BSD5A.RW.{key}",
        )
    ),
    SHEET_PROV: (
        *leaf_lines(
            FORM,
            SHEET_PROV,
            value_columns={"pct_provision": "D"},
            row_sources=template_constants(
                FORM,
                SHEET_PROV,
                "D",
                notes="% provision per BoG classification (Current 1 / OLEM 10 / Substandard 25 / "
                "Doubtful 50 / Loss 100) — the template's printed ladder; the platform holds no "
                "BoG provisioning-rate parameter (IFRS 9 ECL assumptions are stage PD/LGD)",
            ),
            code_prefix="BSD5A.PROV",
        ),
        *leaf_lines(
            FORM,
            SHEET_PROV,
            value_columns={"no": "B"},
            row_sources=template_constants(
                FORM, SHEET_PROV, "B", notes="official item number as printed in the template"
            ),
            code_prefix="BSD5A.PROV.NO",
        ),
    ),
}
