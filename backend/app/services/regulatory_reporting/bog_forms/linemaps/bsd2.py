"""BSD2 — Statement of Assets and Liabilities (the balance-sheet spine).

Official layout: sheet ``BSD2`` (283 rows: label / DOMESTIC CURRENCY / FOREIGN
CURRENCY / TOTAL, ▲ marks BoG's own roll-ups, every TOTAL and subtotal is a
template formula), ``BSD2-Summary`` (all formulas ← BSD2) and Annexes 1–17.
Line/cell map: docs/bog_returns/bsd2_line_map.md.

Every one of the 205 leaf rows on ``BSD2`` is bound below. Sources follow the
Guide's definitions (Domestic = payable in cedis, Foreign = payable in a foreign
currency; non-resident = foreign assets/liabilities in section A / 18–19; own
books only). Where the platform's canonical model cannot honestly produce the
official split (e.g. treasury bills by tenor, deposits by depositor class ×
account type, fixed assets by class), the row is ``input_required`` and says
what the bank must supply — the official cell is still emitted.

Annex sheets (Wave 1b, ``_annex_lines()`` below): ``BSD2-Summary`` is all
formulas (nothing to bind). Annex 2a and Annex 4 ship numeric placeholders and
are bound with :func:`leaf_lines`; every other annex is a BLANK detail schedule
(name / amount rows with no ``0`` placeholder — the layout captured no input
cells), bound with :func:`grid_lines` over the official grid read from the
sheet's headers and labels. Detail rows are ``input_required`` ("detail
schedule row N — populated from position-level data in a later wave"); a
schedule TOTAL row is bound only where the SAME declaration BSD2 uses for the
line the annex analyses fills it honestly (Annex 6 ↔ line 11, Annex 7 ↔ 15(e),
Annex 15 ↔ line 29; Annex 16's rows partition line 33 so its template total
``I11`` = BSD2 ``D282``), so annex total = spine line by construction.
Annex 4's rows partition line 8 the same way (``G13`` = ``D68``). Per-row "Total" COLUMNS
(Annex 6 D, 14 D, 15 E) carry no template formula and are left to the bank's
arithmetic — never bound as inputs.
"""

from __future__ import annotations

from ..spec import LineSpec
from ._common import (
    BANK_COA_MAPPING,
    INPUT_REQUIRED,
    RowSource,
    facts,
    grid_lines,
    leaf_lines,
    positions,
)

_BANKS = ["BANK_OECD", "BANK_NON_OECD"]
_NBFI = ["NBFI"]
_GOV = ["SOVEREIGN"]
_GOV_ENTITY = ["GOVERNMENT_ENTITY"]
_CORP = ["CORPORATE", "SME"]
_RETAIL = ["RETAIL_INDIVIDUAL"]


def accrued_interest(row: int, what: str) -> RowSource:
    """An official "Accrued interest" line, fed from the bank's accruals sub-ledger
    (reference dataset ``interest_accruals``, docs/data_engine/datasets/interest_accruals.md):
    Σ ``accrued_interest_ghs`` of the rows the bank tagged ``bsd2_row=<this row>``
    (the row number printed on the official BSD2 sheet), Domestic/Foreign by each
    row's ``currency`` per the Guide. Blank (input_required) until the sub-ledger
    is ingested; 0 once it is and no row carries this tag."""
    return RowSource(
        "refs.sum",
        {
            "kind": "interest_accruals",
            "value_field": "accrued_interest_ghs",
            "filters": {"bsd2_row": str(row)},
            "currency_field": "currency",
        },
        notes=(
            f"accrued interest {what} — Σ interest_accruals rows tagged bsd2_row={row} "
            "(accruals sub-ledger; Domestic/Foreign by row currency)"
        ),
    )


def fixed_assets(field: str, what: str, *classes: str) -> RowSource:
    """An official item-12 fixed-asset line (rows 115–121 at cost, 123 accumulated
    depreciation), fed from the bank's fixed-asset / capex register (reference
    dataset ``capital_expenditure``, docs/data_engine/datasets/capital_expenditure.md):
    Σ ``field`` over the register rows of the given ``asset_class`` values (all
    classes when none given) at the latest period end on/before the reporting
    date, Domestic/Foreign by each row's booking ``currency`` per the Guide (a row
    with no currency is a base-currency row). Blank (input_required) until the
    register is ingested; 0 once it is and no row carries the class."""
    params: dict[str, object] = {
        "kind": "capital_expenditure",
        "value_field": field,
        "currency_field": "currency",
    }
    if classes:
        params["filters"] = {"asset_class": list(classes)}
    scope = " + ".join(classes) if classes else "all asset classes"
    return RowSource(
        "refs.sum",
        params,
        notes=(
            f"{what} — Σ capital_expenditure register {field} over {scope} (fixed-asset "
            "sub-ledger stock at the latest period end on/before the reporting date; "
            "Domestic/Foreign by row currency)"
        ),
    )


_ROWS: dict[int, RowSource] = {
    # ---- A. FOREIGN ASSETS (Annex 1): claims on non-residents -----------------
    7: positions(position_types=["CASH"], attribute_eq={"instrument": "fx_notes_coins"}),
    8: positions(
        position_types=["INTERBANK_PLACEMENT", "CASH"], resident=False, counterparty_types=_BANKS
    ),
    9: positions(position_types=["OTHER_ASSET", "SECURITY_HOLDING"], resident=False),
    10: positions(position_types=["LOAN"], resident=False),
    11: RowSource(
        "positions.sum",
        {
            "position_types": ["SECURITY_HOLDING"],
            "resident": False,
            "regulatory_categories": ["EQUITY", "EQUITY_INVESTMENT"],
        },
        notes="equity/non-liquid investments abroad by product category",
    ),
    # ---- B.6 Cash and balances due from other financial institutions ----------
    14: facts("balance_sheet", "cash_vault"),
    16: facts("balance_sheet", "bog_excess_reserves"),
    17: facts("balance_sheet", "bog_required_reserves"),
    18: positions(
        position_types=["FX_HEDGE", "DERIVATIVE"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"leg": "receivable"},
    ),
    19: positions(
        position_types=["OTHER_ASSET"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "repo_receivable"},
    ),
    20: accrued_interest(20, "on claims on Bank of Ghana"),
    22: positions(position_types=["INTERBANK_PLACEMENT"], resident=True, counterparty_types=_BANKS),
    23: positions(
        position_types=["INTERBANK_PLACEMENT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "rural_bank"},
    ),
    25: positions(
        position_types=["INTERBANK_PLACEMENT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "discount_house", "tenor": "call"},
    ),
    26: positions(
        position_types=["INTERBANK_PLACEMENT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "discount_house"},
    ),
    27: positions(
        position_types=["INTERBANK_PLACEMENT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "savings_and_loans"},
    ),
    28: positions(
        position_types=["INTERBANK_PLACEMENT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "credit_union"},
    ),
    29: accrued_interest(29, "on claims on other depository institutions"),
    31: positions(
        position_types=["INTERBANK_PLACEMENT", "OTHER_ASSET"],
        resident=True,
        counterparty_types=_NBFI,
    ),
    32: accrued_interest(32, "on claims on other financial institutions"),
    33: positions(
        position_types=["OTHER_ASSET"], attribute_eq={"instrument": "cheques_for_clearing"}
    ),
    # ---- B.7 Bills / short-term investments (by issuer × tenor) --------------
    36: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "tbill", "tenor_days": 91},
    ),
    37: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "tbill", "tenor_days": 182},
    ),
    38: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "gog_bond", "tenor_years": 1},
    ),
    39: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "tbill_other"},
    ),
    41: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bill", "tenor_days": 28},
    ),
    42: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bill", "tenor_days": 56},
    ),
    43: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bill", "tenor_days": 91},
    ),
    44: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bill", "tenor_days": 182},
    ),
    45: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bond", "tenor_years": 1},
    ),
    46: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_other"},
    ),
    48: positions(position_types=["SECURITY_HOLDING"], counterparty_types=_BANKS, resident=True),
    49: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "discount_house"},
    ),
    50: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "other_depository"},
    ),
    51: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "other_financial"},
    ),
    52: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_institution"},
    ),
    54: positions(position_types=["SECURITY_HOLDING"], attribute_eq={"instrument": "cocoa_bill"}),
    55: positions(position_types=["SECURITY_HOLDING"], attribute_eq={"instrument": "grains_bill"}),
    56: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_enterprise", "instrument": "bill"},
    ),
    58: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_CORP,
        regulatory_categories=["BOND", "CORPORATE_BOND"],
    ),
    59: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_CORP,
        regulatory_categories=["EQUITY", "EQUITY_INVESTMENT"],
    ),
    # ---- B.8 Loans, overdrafts and other advances (by borrower class) --------
    61: positions(position_types=["LOAN"], counterparty_types=_GOV),
    62: positions(
        position_types=["LOAN"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"borrower_class": "public_institution"},
    ),
    64: positions(position_types=["LOAN"], attribute_eq={"scheme": "cocoa_syndicated"}),
    65: positions(
        position_types=["LOAN"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"borrower_class": "public_enterprise"},
    ),
    66: positions(position_types=["LOAN"], counterparty_types=_CORP),
    67: positions(position_types=["LOAN"], counterparty_types=_RETAIL),
    69: RowSource(
        "facts.sum",
        {"group": "loan_exposure", "categories": ["specific_provision", "total_debt_provision"]},
        notes=(
            "total debt provision — POSITIVE: the template subtracts it "
            "(B60 = B68 − SUM(B69:B71))"
        ),
    ),
    70: RowSource(None, notes="interest in suspense — suspense sub-ledger required"),
    71: RowSource(None, notes="revaluation gains on NPLs — bank must supply"),
    # ---- B.9 Long-term investments (bonds by issuer/tenor) -------------------
    74: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "ggilb"},
    ),
    75: positions(position_types=["SECURITY_HOLDING"], attribute_eq={"instrument": "tor_bond"}),
    76: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "gog_bond", "tenor_years": 2},
    ),
    77: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "gog_bond", "tenor_years": 3},
    ),
    78: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "gog_bond_other"},
    ),
    80: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bond", "tenor_years": 2},
    ),
    81: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=["CENTRAL_BANK"],
        attribute_eq={"instrument": "bog_bond_other"},
    ),
    84: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_BANKS,
        resident=True,
        regulatory_categories=["BOND"],
    ),
    85: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_BANKS,
        resident=True,
        regulatory_categories=["OTHER"],
    ),
    86: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "rural_bank"},
    ),
    87: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "discount_house", "long_term": True},
    ),
    88: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "savings_and_loans"},
    ),
    89: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "credit_union"},
    ),
    91: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        regulatory_categories=["BOND"],
    ),
    92: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        regulatory_categories=["OTHER"],
    ),
    94: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_institution"},
        regulatory_categories=["BOND"],
    ),
    95: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_institution"},
        regulatory_categories=["OTHER"],
    ),
    97: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_enterprise"},
        regulatory_categories=["BOND"],
    ),
    98: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"issuer_class": "public_enterprise"},
        regulatory_categories=["OTHER"],
    ),
    100: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_CORP,
        regulatory_categories=["BOND", "CORPORATE_BOND"],
        attribute_eq={"long_term": True},
    ),
    101: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_CORP,
        regulatory_categories=["OTHER"],
        attribute_eq={"long_term": True},
    ),
    # ---- B.10 Investments in subsidiaries/associates -------------------------
    104: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_BANKS,
        attribute_eq={"relationship": "subsidiary_or_associate"},
    ),
    105: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"relationship": "subsidiary_or_associate", "institution_class": "rural_bank"},
    ),
    106: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={
            "relationship": "subsidiary_or_associate",
            "institution_class": "savings_and_loans",
        },
    ),
    107: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={
            "relationship": "subsidiary_or_associate",
            "institution_class": "credit_union",
        },
    ),
    108: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_NBFI,
        attribute_eq={"relationship": "subsidiary_or_associate"},
    ),
    109: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"relationship": "subsidiary_or_associate"},
    ),
    110: positions(
        position_types=["SECURITY_HOLDING"],
        counterparty_types=_CORP,
        attribute_eq={"relationship": "subsidiary_or_associate"},
    ),
    112: RowSource(None, notes="impairment in value of investments — bank must supply"),
    # ---- B.11–13 Other assets, fixed assets --------------------------------
    113: facts("balance_sheet", "other_assets"),
    # item 12 = sub-total of rows 115–121 (COST) less row 123 (accumulated depreciation),
    # BoG's own formulas — so the register's cost / WIP balance / accumulated
    # depreciation are bound, never its NBV (that would double-deduct depreciation)
    115: fixed_assets("closing_cost_ghs", "bank land and premises at cost", "land_buildings"),
    116: fixed_assets(
        "closing_cost_ghs",
        "land and premises for staff and staff amenities at cost",
        "staff_land_premises",
    ),
    117: fixed_assets("closing_cost_ghs", "computers at cost", "computers"),
    118: fixed_assets(
        "closing_cost_ghs",
        "furniture, fixtures and equipment at cost (BSD10's furniture and equipment + other "
        "office equipment classes)",
        "furniture_equipment",
        "other_office_equipment",
    ),
    119: fixed_assets("closing_cost_ghs", "motor vehicles at cost", "motor_vehicles"),
    120: fixed_assets(
        "closing_cost_ghs",
        "other property acquired by legal rights at cost",
        "other_property_legal_rights",
    ),
    121: fixed_assets("wip_closing_ghs", "capital work-in-progress balance"),
    123: fixed_assets("accumulated_depreciation_ghs", "accumulated depreciation"),
    # ---- LIABILITIES 14–17: capital and reserves ---------------------------
    128: facts("capital_component", "paid_up_capital", "ordinary_share_capital"),
    130: facts("capital_component", "statutory_reserves", "statutory_reserve"),
    131: facts("capital_component", "revaluation_reserves", "revaluation_reserve"),
    132: facts("capital_component", "income_surplus", "retained_earnings"),
    133: facts("capital_component", "current_year_profit", "profit_and_loss_to_date"),
    134: facts("capital_component", "other_reserves"),
    136: RowSource(None, notes="other amounts allowed as capital — bank must supply"),
    # ---- 18–19 Foreign liabilities: non-resident deposits/borrowings ---------
    139: positions(
        position_types=["DEPOSIT", "INTERBANK_BORROWING"], resident=False, counterparty_types=_BANKS
    ),
    140: positions(
        position_types=["DEPOSIT", "INTERBANK_BORROWING", "OTHER_LIABILITY"],
        resident=False,
        counterparty_types_not=_BANKS,  # 18(a) already carries non-resident FIs
    ),
    141: accrued_interest(141, "on non-resident short-term borrowings"),
    143: positions(
        position_types=["INTERBANK_BORROWING"],
        resident=False,
        counterparty_types=_BANKS,
        attribute_eq={"instrument": "term_borrowing"},
    ),
    144: positions(
        position_types=["INTERBANK_BORROWING", "OTHER_LIABILITY"],
        resident=False,
        counterparty_types_not=_BANKS,  # 19(a) already carries non-resident FIs
        attribute_eq={"instrument": "term_borrowing"},
    ),
    145: accrued_interest(145, "on non-resident long-term borrowing"),
    # ---- 20 Domestic deposits by account type × depositor class -------------
    # (a) Demand/current
    148: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "CURRENT"},
    ),
    149: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "CURRENT"},
    ),
    150: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "CURRENT"},
    ),
    151: accrued_interest(151, "on non-resident demand deposits"),
    # (b) Savings
    153: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "SAVINGS"},
    ),
    154: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "SAVINGS"},
    ),
    155: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "SAVINGS"},
    ),
    156: accrued_interest(156, "on non-resident savings accounts"),
    # (c) Time/fixed
    158: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "FIXED"},
    ),
    159: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "FIXED"},
    ),
    160: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "FIXED"},
    ),
    161: accrued_interest(161, "on non-resident time deposits"),
    # (d) Call / other
    163: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "CALL"},
    ),
    164: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "CALL"},
    ),
    165: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "CALL"},
    ),
    166: accrued_interest(166, "on non-resident certificates of deposit"),
    # ---- 21 Balances due to domestic financial institutions -----------------
    169: positions(
        position_types=["INTERBANK_BORROWING", "DEPOSIT"], counterparty_types=["CENTRAL_BANK"]
    ),
    171: positions(
        position_types=["INTERBANK_BORROWING", "DEPOSIT"], resident=True, counterparty_types=_BANKS
    ),
    172: positions(
        position_types=["INTERBANK_BORROWING", "DEPOSIT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "discount_house"},
    ),
    173: positions(
        position_types=["INTERBANK_BORROWING", "DEPOSIT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "other_depository"},
    ),
    174: positions(
        position_types=["INTERBANK_BORROWING", "DEPOSIT"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"institution_class": "other_financial"},
    ),
    175: positions(position_types=["DEPOSIT", "INTERBANK_BORROWING"], counterparty_types=_GOV),
    176: RowSource(None, notes="other balances due — bank must classify"),
    177: accrued_interest(177, "on domestic long-term borrowings"),
    # ---- 22 Money at call from FIs; 23 secured borrowings -------------------
    # ---- 22 Cheques for clearing (liability side: uncleared items due) -----
    180: positions(
        position_types=["OTHER_LIABILITY"],
        counterparty_types=_BANKS,
        attribute_eq={"instrument": "cheques_for_clearing"},
    ),
    181: positions(
        position_types=["OTHER_LIABILITY"],
        counterparty_types=_NBFI,
        attribute_eq={"instrument": "cheques_for_clearing", "institution_class": "discount_house"},
    ),
    182: positions(
        position_types=["OTHER_LIABILITY"],
        attribute_eq={
            "instrument": "cheques_for_clearing",
            "institution_class": "other_depository",
        },
    ),
    183: positions(
        position_types=["OTHER_LIABILITY"],
        counterparty_types=_NBFI,
        attribute_eq={"instrument": "cheques_for_clearing", "institution_class": "other_financial"},
    ),
    186: positions(position_types=["OTHER_LIABILITY"], attribute_eq={"instrument": "repo_payable"}),
    187: positions(position_types=["FX_HEDGE", "DERIVATIVE"], attribute_eq={"leg": "payable"}),
    188: RowSource(None, notes="other secured borrowings — bank must supply"),
    # ---- 24 Term borrowings from domestic FIs -------------------------------
    190: positions(
        position_types=["INTERBANK_BORROWING"],
        resident=True,
        counterparty_types=_BANKS,
        attribute_eq={"instrument": "term_borrowing"},
    ),
    191: positions(
        position_types=["INTERBANK_BORROWING"],
        resident=True,
        attribute_eq={"instrument": "term_borrowing", "institution_class": "other"},
    ),
    192: positions(
        position_types=["INTERBANK_BORROWING"],
        resident=True,
        counterparty_types=_NBFI,
        attribute_eq={"instrument": "term_borrowing"},
    ),
    193: positions(
        position_types=["INTERBANK_BORROWING", "OTHER_LIABILITY"],
        counterparty_types=_GOV,
        attribute_eq={"instrument": "term_borrowing"},
    ),
    194: RowSource(None, notes="other term borrowings — bank must classify"),
    195: accrued_interest(195, "on domestic short-term borrowing"),
    # ---- 25 Borrowings by tenor bucket × lender (BoG / banks / OFIs) --------
    # (a) up to 1 month … (d) over 12 months — bank must supply the tenor split
    198: BANK_COA_MAPPING,
    200: BANK_COA_MAPPING,
    201: BANK_COA_MAPPING,
    202: BANK_COA_MAPPING,
    203: BANK_COA_MAPPING,
    204: accrued_interest(204, "on demand deposits of financial institutions"),
    206: BANK_COA_MAPPING,
    208: BANK_COA_MAPPING,
    209: BANK_COA_MAPPING,
    210: BANK_COA_MAPPING,
    211: accrued_interest(211, "on savings accounts of financial institutions"),
    213: BANK_COA_MAPPING,
    215: BANK_COA_MAPPING,
    216: BANK_COA_MAPPING,
    217: BANK_COA_MAPPING,
    218: accrued_interest(218, "on time deposits of financial institutions"),
    220: BANK_COA_MAPPING,
    222: BANK_COA_MAPPING,
    223: BANK_COA_MAPPING,
    224: BANK_COA_MAPPING,
    225: accrued_interest(225, "on certificates of deposit of financial institutions"),
    # ---- 26 Deposits by depositor class × account type (second block) -------
    228: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "CURRENT", "block": "26"},
    ),
    229: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "CURRENT", "block": "26"},
    ),
    230: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV,
        attribute_eq={"deposit_account_type": "CURRENT"},
    ),
    231: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "CURRENT", "depositor_class": "public_enterprise"},
    ),
    232: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "CURRENT", "depositor_class": "public_institution"},
    ),
    233: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "CURRENT", "block": "26"},
    ),
    234: accrued_interest(234, "on public/govt demand deposits"),
    236: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "SAVINGS", "block": "26"},
    ),
    237: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "SAVINGS", "block": "26"},
    ),
    238: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV,
        attribute_eq={"deposit_account_type": "SAVINGS"},
    ),
    239: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "SAVINGS", "depositor_class": "public_enterprise"},
    ),
    240: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "SAVINGS", "depositor_class": "public_institution"},
    ),
    241: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "SAVINGS", "block": "26"},
    ),
    242: accrued_interest(242, "on public/govt savings accounts"),
    244: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "FIXED", "block": "26"},
    ),
    245: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "FIXED", "block": "26"},
    ),
    246: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV,
        attribute_eq={"deposit_account_type": "FIXED"},
    ),
    247: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "FIXED", "depositor_class": "public_enterprise"},
    ),
    248: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "FIXED", "depositor_class": "public_institution"},
    ),
    249: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "FIXED", "block": "26"},
    ),
    250: accrued_interest(250, "on public/govt time deposits"),
    252: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_RETAIL,
        attribute_eq={"deposit_account_type": "CALL", "block": "26"},
    ),
    253: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_CORP,
        attribute_eq={"deposit_account_type": "CALL", "block": "26"},
    ),
    254: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV,
        attribute_eq={"deposit_account_type": "CALL"},
    ),
    255: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "CALL", "depositor_class": "public_enterprise"},
    ),
    256: positions(
        position_types=["DEPOSIT"],
        counterparty_types=_GOV_ENTITY,
        attribute_eq={"deposit_account_type": "CALL", "depositor_class": "public_institution"},
    ),
    257: positions(
        position_types=["DEPOSIT"],
        counterparty_types=["OTHER", "NBFI"],
        attribute_eq={"deposit_account_type": "CALL", "block": "26"},
    ),
    258: accrued_interest(258, "on public/govt certificates of deposit"),
    # ---- 27 Other deposits / margin accounts by class -----------------------
    261: BANK_COA_MAPPING,
    262: BANK_COA_MAPPING,
    263: BANK_COA_MAPPING,
    264: BANK_COA_MAPPING,
    265: BANK_COA_MAPPING,
    266: BANK_COA_MAPPING,
    268: BANK_COA_MAPPING,
    269: BANK_COA_MAPPING,
    270: BANK_COA_MAPPING,
    271: BANK_COA_MAPPING,
    272: BANK_COA_MAPPING,
    273: BANK_COA_MAPPING,
    275: positions(position_types=["DEPOSIT"], attribute_eq={"instrument": "tor_margin_account"}),
    276: RowSource(None, notes="other margin accounts — Annex 14 detail required"),
    # ---- 28–34 --------------------------------------------------------------
    277: positions(position_types=["OTHER_LIABILITY"], attribute_eq={"instrument": "bond_issued"}),
    278: positions(position_types=["OTHER_LIABILITY"]),
    282: positions(position_types=["LC_GUARANTEE"], measure="notional"),
    283: RowSource(
        None, notes="managed funds (contra) — fiduciary book, outside own-books returns"
    ),
}

# ===========================================================================
# Annex sheets (Wave 1b)
# ===========================================================================
#
# Grid extents below are read from each annex's own headers/labels (see the
# layout dump in docs/bog_returns/bsd2_line_map.md §Annex sheets): data rows
# start under the column-header rows and end at the schedule's "Total" label
# (or the last labelled item + its blank detail rows). Nothing outside those
# rows is bound.


def _detail(rows: range | list[int], note: str) -> dict[int, RowSource]:
    """``input_required`` per detail row, note formatted with the row number."""
    return {row: RowSource(None, notes=note.format(n=row)) for row in rows}


_DETAIL_LATER_WAVE = "detail schedule row {n} — populated from position-level data in a later wave"
_TOTAL_NO_FORMULA = (
    "schedule total — the template carries no formula here; Σ of the rows above "
    "(same-sheet sums are a framework ask)"
)

# ---- Annex 1: breakdown of FOREIGN ASSETS / FOREIGN LIABILITIES ------------
# Columns: C foreign currency amount · D exchange rate · E cedi equivalent.
# Rows 4 / 11 / 12 / 13 items, 5–10 blank per-currency detail under item 1,
# 14 total; liabilities 19 / 20 / 21 items, 22 total.
_A1_ROWS = [*range(4, 15), *range(19, 23)]
_A1_CEDI: dict[int, RowSource] = {
    4: positions(
        position_types=["CASH"], attribute_eq={"instrument": "fx_notes_coins"}, currency="FX"
    ),
    11: positions(
        position_types=["INTERBANK_PLACEMENT", "CASH"],
        resident=False,
        counterparty_types=_BANKS,
        currency="FX",
    ),
    12: RowSource(
        None,
        notes="foreign bills — split of BSD2 A.3 other claims on non-residents; bank must supply",
    ),
    13: RowSource(None, notes="other foreign assets — bank must supply"),
    14: RowSource(None, notes=_TOTAL_NO_FORMULA + " (= BSD2 A. FOREIGN ASSETS)"),
    19: positions(
        position_types=["DEPOSIT"], resident=False, counterparty_types=_BANKS, currency="FX"
    ),
    20: positions(
        position_types=["INTERBANK_BORROWING"],
        resident=False,
        counterparty_types=_BANKS,
        currency="FX",
    ),
    21: RowSource(None, notes="other foreign liabilities — bank must supply"),
    22: RowSource(None, notes=_TOTAL_NO_FORMULA + " (= BSD2 C. FOREIGN LIABILITIES)"),
    **_detail(
        range(5, 11),
        "detail row {n} — per-currency breakdown of foreign currency notes and coins; "
        "populated from position-level data in a later wave",
    ),
}
_A1_FCY: dict[int, RowSource] = {
    **{
        row: RowSource(
            None,
            notes="foreign currency amount and exchange rate — per-currency detail from "
            "position-level data in a later wave",
        )
        for row in _A1_ROWS
    },
    **_detail(range(5, 11), _DETAIL_LATER_WAVE),
}

# ---- Annex 2a: Bank of Ghana account reconciliation ------------------------
_A2A_RECON: dict[int, RowSource] = {
    10: RowSource(
        None,
        notes="balance per Bank of Ghana statement — external (BoG statement); bank must supply",
    ),
    12: RowSource(
        None, notes="reconciling item — bank's BoG account reconciliation; bank must supply"
    ),
    13: RowSource(
        None, notes="reconciling item — bank's BoG account reconciliation; bank must supply"
    ),
    15: RowSource(
        None, notes="reconciling item — bank's BoG account reconciliation; bank must supply"
    ),
    16: RowSource(
        None, notes="reconciling item — bank's BoG account reconciliation; bank must supply"
    ),
}
_A2A_LISTING = _detail(
    range(26, 51),
    "reconciling items over 1 month old — listing row {n} (date / description / amount); "
    "bank must supply",
)

# ---- Annex 4: loans by borrower class × facility type (leaf_lines) ---------
# Columns B..F = Scheduled / Unscheduled / Overdraft / Acceptances / Others
# (Guide Annex 4 definitions). Facility type is the ``facility_type`` snapshot
# attribute (values: scheduled · unscheduled · overdraft · acceptance · other);
# staff advances carry ``scheme = staff_advance``.
_A4_COLUMNS: dict[str, tuple[str, str]] = {
    "scheduled": ("B", "scheduled"),
    "unscheduled": ("C", "unscheduled"),
    "overdraft": ("D", "overdraft"),
    "acceptances": ("E", "acceptance"),
    "others": ("F", "other"),
}


def _a4_rows(facility: str) -> dict[int, RowSource]:
    return {
        8: positions(
            position_types=["LOAN"],
            counterparty_types=[*_GOV, *_GOV_ENTITY],
            attribute_eq={"facility_type": facility},
            currency="all",
        ),
        9: RowSource(
            None,
            notes="bad-debt provisions and interest in suspense on public-sector advances by "
            "facility type — provisions sub-ledger required",
        ),
        10: positions(
            position_types=["LOAN"],
            counterparty_types=[*_CORP, *_RETAIL],
            attribute_eq={"facility_type": facility},
            currency="all",
        ),
        11: positions(
            position_types=["LOAN"],
            counterparty_types=_RETAIL,
            attribute_eq={"facility_type": facility, "scheme": "staff_advance"},
            currency="all",
        ),
        12: RowSource(
            None,
            notes="bad-debt provisions and interest in suspense on private-sector advances by "
            "facility type — provisions sub-ledger required",
        ),
    }


# ---- Annex 16: statement of contingent liabilities -------------------------
# Rows 6–10 = acceptances / documentary credits / guarantees / endorsements /
# other obligations; C fcy amount · D rate · E/F cedi-equivalent performing /
# non-performing (FX book) · G/H cedi contingents performing / non-performing;
# I = template row totals, I11 = grand total (= BSD2 line 33 when the
# obs_category × obs_status attributes partition the LC_GUARANTEE book).
_A16_CATEGORY: dict[int, str] = {
    6: "acceptance",
    7: "letter_of_credit",
    8: "guarantee",
    9: "endorsement",
    10: "other_obligation",
}


def _a16(status: str, currency: str) -> dict[int, RowSource]:
    return {
        row: positions(
            position_types=["LC_GUARANTEE"],
            measure="notional",
            attribute_eq={"obs_category": category, "obs_status": status},
            currency=currency,
        )
        for row, category in _A16_CATEGORY.items()
    }


_A16_FCY = {
    row: RowSource(
        None,
        notes="foreign currency amount and conversion rate — per-currency support schedule "
        "(Guide Annex 16); bank must supply",
    )
    for row in _A16_CATEGORY
}

# ---- Annex 17: number of customers / accounts by deposit type --------------
_A17_COUNT = RowSource(
    None,
    notes="number of customers and number of accounts by deposit type — a count over "
    "DEPOSIT positions per counterparty (counting resolver is a framework ask); unscaled count",
    unscaled=True,
)


def _annex_lines() -> dict[str, tuple[LineSpec, ...]]:  # noqa: PLR0915 — one block per official annex
    a2b_note = (
        "special deposit — listing row {n} (description / amount) from position-level data "
        "in a later wave"
    )
    a2c_note = (
        "swap deal receivable — listing row {n} (description / amount) from position-level "
        "data in a later wave"
    )
    a2d_note = (
        "repo receivable — listing row {n} (description / amount) from position-level data "
        "in a later wave"
    )
    a3_note = (
        "short-term investment (≤ 1 year) — listing row {n} (description / nominal / book value) "
        "from SECURITY_HOLDING positions in a later wave"
    )
    a5_note = (
        "long-term investment (> 1 year) — listing row {n} (description / nominal / book value) "
        "from SECURITY_HOLDING positions in a later wave"
    )
    a6_note = (
        "other asset — listing row {n} (description / domestic / foreign) from position-level "
        "data in a later wave"
    )
    a7_note = "other reserve — listing row {n} (type / amount); bank must supply"
    a8_note = (
        "short-term foreign borrowing — listing row {n} (source / currency amount / cedi "
        "equivalent / rate / maturity) from INTERBANK_BORROWING positions in a later wave"
    )
    a9_note = (
        "long-term foreign borrowing — listing row {n} (source / currency amount / cedi "
        "equivalent / rate / maturity) from INTERBANK_BORROWING positions in a later wave"
    )
    a10_note = (
        "long-term domestic borrowing — listing row {n} (source / amount / rate / maturity) "
        "from INTERBANK_BORROWING positions in a later wave"
    )
    a11_note = (
        "short-term domestic borrowing — listing row {n} (source / amount / rate / maturity) "
        "from INTERBANK_BORROWING positions in a later wave"
    )
    a12_sections = {
        "Bank of Ghana": range(8, 18),
        "commercial banks": range(19, 31),
        "other banks": range(32, 44),
        "other financial institutions": range(45, 57),
    }
    a13_sections = {
        "demand deposits": range(8, 16),
        "savings accounts": range(17, 26),
        "time deposits": range(27, 38),
        "certificates of deposit": range(39, 49),
    }
    a14_note = (
        "other margin against contingent liabilities — listing row {n} (description / domestic "
        "/ foreign) from position-level data in a later wave"
    )
    a15_note = (
        "other liability — listing row {n} (description / domestic / foreign) from "
        "OTHER_LIABILITY positions in a later wave"
    )

    def grid(
        sheet: str,
        rows: range | list[int],
        cols: dict[str, str],
        src: dict[int, RowSource],
        prefix: str,
    ) -> tuple[LineSpec, ...]:
        return grid_lines(
            "BSD2", sheet, rows=rows, value_columns=cols, row_sources=src, code_prefix=prefix
        )

    lines: dict[str, tuple[LineSpec, ...]] = {}
    lines["BSD2-Annex 1"] = (
        *grid("BSD2-Annex 1", _A1_ROWS, {"cedi_equivalent": "E"}, _A1_CEDI, "BSD2.Annex1.cedi"),
        *grid(
            "BSD2-Annex 1",
            _A1_ROWS,
            {"fcy_amount": "C", "exchange_rate": "D"},
            _A1_FCY,
            "BSD2.Annex1.fcy",
        ),
    )
    lines["BSD2-Annex 2a"] = (
        *leaf_lines(
            "BSD2",
            "BSD2-Annex 2a",
            value_columns={"amount": "C"},
            row_sources=_A2A_RECON,
            code_prefix="BSD2.Annex2a",
        ),
        *grid("BSD2-Annex 2a", range(26, 51), {"amount": "C"}, _A2A_LISTING, "BSD2.Annex2a.list"),
    )
    lines["BSD2-Annex 2b"] = grid(
        "BSD2-Annex 2b",
        range(6, 49),
        {"amount": "B"},
        _detail(range(6, 49), a2b_note),
        "BSD2.Annex2b",
    )
    lines["BSD2-Annex 2c"] = grid(
        "BSD2-Annex 2c",
        range(6, 49),
        {"amount": "B"},
        _detail(range(6, 49), a2c_note),
        "BSD2.Annex2c",
    )
    lines["BSD2-Annex 2d"] = grid(
        "BSD2-Annex 2d",
        range(6, 49),
        {"amount": "B"},
        _detail(range(6, 49), a2d_note),
        "BSD2.Annex2d",
    )
    lines["BSD2-Annex 3 "] = grid(
        "BSD2-Annex 3 ",
        range(6, 50),
        {"nominal_value": "B", "book_value": "C"},
        _detail(range(6, 50), a3_note),
        "BSD2.Annex3",
    )
    lines["BSD2-Annex 4 "] = tuple(
        line
        for key, (letter, facility) in _A4_COLUMNS.items()
        for line in leaf_lines(
            "BSD2",
            "BSD2-Annex 4 ",
            value_columns={key: letter},
            row_sources=_a4_rows(facility),
            code_prefix=f"BSD2.Annex4.{key}",
        )
    )
    lines["BSD2-Annex 5 "] = grid(
        "BSD2-Annex 5 ",
        range(6, 50),
        {"nominal_value": "B", "book_value": "C"},
        _detail(range(6, 50), a5_note),
        "BSD2.Annex5",
    )
    lines["BSD2-Annex 6"] = grid(
        "BSD2-Annex 6",
        range(7, 52),
        {"domestic": "B", "foreign": "C"},
        {**_detail(range(7, 51), a6_note), 51: facts("balance_sheet", "other_assets")},
        "BSD2.Annex6",
    )
    lines["BSD2-Annex7"] = grid(
        "BSD2-Annex7",
        range(6, 51),
        {"amount": "C"},
        {
            **_detail(range(6, 50), a7_note),
            50: facts("capital_component", "other_reserves", currency="all"),
        },
        "BSD2.Annex7",
    )
    for sheet, note, prefix, spine in (
        ("BSD2-Annex 8", a8_note, "BSD2.Annex8", "BSD2 line 18"),
        ("BSD2-Annex 9", a9_note, "BSD2.Annex9", "BSD2 line 19"),
    ):
        lines[sheet] = (
            *grid(
                sheet,
                range(7, 51),
                {"fcy_amount": "B", "cedi_equivalent": "C", "rate": "D", "maturity_date": "E"},
                _detail(range(7, 51), note),
                prefix,
            ),
            *grid(
                sheet,
                [51],
                {"cedi_equivalent": "C"},
                {51: RowSource(None, notes=f"{_TOTAL_NO_FORMULA} (= {spine})")},
                f"{prefix}.total",
            ),
        )
    for sheet, note, prefix, spine in (
        ("BSD2-Annex 10", a10_note, "BSD2.Annex10", "BSD2 line 21"),
        ("BSD2-Annex 11", a11_note, "BSD2.Annex11", "BSD2 line 23"),
    ):
        lines[sheet] = (
            *grid(
                sheet,
                range(7, 51),
                {"amount": "B", "rate": "C", "maturity_date": "D"},
                _detail(range(7, 51), note),
                prefix,
            ),
            *grid(
                sheet,
                [51],
                {"amount": "B"},
                {51: RowSource(None, notes=f"{_TOTAL_NO_FORMULA} (= {spine})")},
                f"{prefix}.total",
            ),
        )
    a12_sources: dict[int, RowSource] = {}
    for section, rows in a12_sections.items():
        a12_sources.update(
            _detail(
                rows,
                f"deposit of a financial institution ({section}) — listing row {{n}} "
                "(name / amount) from DEPOSIT positions in a later wave",
            )
        )
    a12_sources[57] = RowSource(
        None,
        notes=f"{_TOTAL_NO_FORMULA} (= BSD2 line 24, itself awaiting the bank's "
        "chart-of-accounts mapping)",
    )
    lines["BSD2-Annex 12"] = grid(
        "BSD2-Annex 12", sorted(a12_sources), {"amount": "C"}, a12_sources, "BSD2.Annex12"
    )
    a13_sources: dict[int, RowSource] = {}
    for section, rows in a13_sections.items():
        a13_sources.update(
            _detail(
                rows,
                f"public enterprise {section} — listing row {{n}} (name of enterprise / "
                "amount) from DEPOSIT positions (GOVERNMENT_ENTITY, depositor_class = "
                "public_enterprise) in a later wave",
            )
        )
    lines["BSD2-Annex 13"] = grid(
        "BSD2-Annex 13", sorted(a13_sources), {"amount": "B"}, a13_sources, "BSD2.Annex13"
    )
    lines["BSD2-Annex 14"] = grid(
        "BSD2-Annex 14",
        range(7, 52),
        {"domestic": "B", "foreign": "C"},
        {
            **_detail(range(7, 51), a14_note),
            51: RowSource(
                None, notes=f"{_TOTAL_NO_FORMULA} (= BSD2 27(ii), itself input_required)"
            ),
        },
        "BSD2.Annex14",
    )
    lines["BSD2-Annex 15"] = grid(
        "BSD2-Annex 15",
        range(6, 52),
        {"domestic": "C", "foreign": "D"},
        {**_detail(range(6, 51), a15_note), 51: positions(position_types=["OTHER_LIABILITY"])},
        "BSD2.Annex15",
    )
    a16_rows = list(_A16_CATEGORY)
    lines["BSD2-Annex 16"] = (
        *grid(
            "BSD2-Annex 16",
            a16_rows,
            {"fcy_amount": "C", "exchange_rate": "D"},
            _A16_FCY,
            "BSD2.Annex16.fcy",
        ),
        *grid(
            "BSD2-Annex 16",
            a16_rows,
            {"fx_performing": "E"},
            _a16("performing", "FX"),
            "BSD2.Annex16.E",
        ),
        *grid(
            "BSD2-Annex 16",
            a16_rows,
            {"fx_non_performing": "F"},
            _a16("non_performing", "FX"),
            "BSD2.Annex16.F",
        ),
        *grid(
            "BSD2-Annex 16",
            a16_rows,
            {"cedi_performing": "G"},
            _a16("performing", "GHS"),
            "BSD2.Annex16.G",
        ),
        *grid(
            "BSD2-Annex 16",
            a16_rows,
            {"cedi_non_performing": "H"},
            _a16("non_performing", "GHS"),
            "BSD2.Annex16.H",
        ),
    )
    lines["BSD2-Annex 17"] = grid(
        "BSD2-Annex 17",
        [5, 7, 9, 11],
        {"number_of_customers": "C", "number_of_accounts": "D"},
        dict.fromkeys((5, 7, 9, 11), _A17_COUNT),
        "BSD2.Annex17",
    )
    return lines


LINES = {
    "BSD2": leaf_lines(
        "BSD2",
        "BSD2",
        value_columns={"domestic": "B", "foreign": "C"},
        row_sources=_ROWS,
        code_prefix="BSD2",
        default=INPUT_REQUIRED,
    ),
    **_annex_lines(),
}
