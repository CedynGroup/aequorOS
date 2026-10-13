"""The catalogue of every official BoG return (all templates in docs/reporting).

Frequency, time limit, basis and scope notes come from the Guide's "List of
Prudential Returns" and General Notes; sheet names/units come from the official
workbooks themselves (``layouts/*.json``). Line maps (input cell → source) are
contributed per form by :mod:`linemaps`; a form without a line map still
generates and exports its full official structure with every input cell
``unmapped`` — the structure is never dropped.

See docs/bog_returns/00_full_return_registry.md for the registry table.
"""

from __future__ import annotations

from functools import cache

from .layout import load_layout
from .linemaps import line_maps_for
from .spec import Basis, FormSpec, Frequency, HeaderCells, SheetSpec, UnitConvention

# Guide, General Notes §1: consolidation of subsidiaries only on BSD7B and BSD9;
# the "GROUP" template variants (BSD3B, BSD5B) are group-basis by their titles.
_CONSOLIDATED: frozenset[str] = frozenset({"BSD3B", "BSD5B", "BSD7B", "BSD9"})

_GUIDE_SCOPE_NOTES: tuple[str, ...] = (
    "Guide §1: returns cover all branches/agencies in Ghana and overseas "
    "(BSD1: Ghana branches only).",
    "Guide §1: only transactions on the institution's own books (as principal); "
    "fiduciary/agent funds excluded; syndication participants' shares excluded.",
    "Guide §2: Domestic = expressed and payable in cedis; Foreign = payable in a "
    "foreign currency (converted to cedis).",
    "Guide §3: set-off only with a formal agreement/legal right, same currency, "
    "same customer or group, Ghanaian resident; overseas balances reported gross.",
)


def _units(*pairs: tuple[str, UnitConvention]) -> dict[str, UnitConvention]:
    return dict(pairs)


#: (code, title, workbook, frequency, time_limit_days, sheet-unit overrides,
#:  depends_on, extra notes)
_FORMS: tuple[
    tuple[
        str, str, str, Frequency, int, dict[str, UnitConvention], tuple[str, ...], tuple[str, ...]
    ],
    ...,
] = (
    (
        "BSD1",
        "Liquidity Reserve Return",
        "FORM BSD1 REVISED.xls",
        "weekly",
        9,
        {},
        (),
        (
            "Covers Ghana branches ONLY (Guide, General Notes §1).",
            "Guide list: Weekly, 9 days. The form's printed completion note says 'each "
            "month end / 14 days' — the List of Prudential Returns is treated as "
            "authoritative; the discrepancy is recorded here, not resolved by guesswork.",
        ),
    ),
    (
        "BSD1A",
        "Twenty Largest Withdrawals Over the Counter",
        "FORM BSD1A.xls",
        "weekly",
        9,
        {},
        (),
        (
            "Requires teller/withdrawal transaction detail — input required until a "
            "withdrawal-transactions dataset is ingested.",
        ),
    ),
    (
        "BSD1B",
        "Daily Net Open Position (weekly return)",
        "FORM BSD1B.XLS",
        "weekly",
        9,
        _units(("AFOP", "units")),
        (),
        ("Fed from the FX engine's daily NOP (DBK-DAILY) where present.",),
    ),
    (
        "BSD2",
        "Statement of Assets and Liabilities",
        "FORM BSD2 REVISED.xls",
        "monthly",
        14,
        _units(("BSD2-Annex 17", "count")),
        (),
        (
            "The balance-sheet spine other returns reference (BSD6 'FROM BSD2', BSD8 "
            "external link, BSD2A foreign column).",
        ),
    ),
    (
        "BSD2A",
        "Report on Foreign Currency Exposures",
        "FORM BSD2A REVISED.xls",
        "monthly",
        14,
        {},
        ("BSD2",),
        ("Guide BSD2A ¶1: analyses every item shown in the FOREIGN column of BSD2.",),
    ),
    (
        "BSD3A",
        "Large Exposures — Advances and Deposits",
        "FORM BSD3A REVISED.xls",
        "monthly",
        14,
        {},
        ("BSD2",),
        (
            "Sheet 1: twenty largest depositors; Sheet 2: ten largest monetary-sector "
            "exposures; Sheet 3: fifty largest non-monetary-sector exposures (Guide BSD3).",
        ),
    ),
    (
        "BSD3B",
        "Large Exposures — Advances and Deposits of Subsidiaries (Group)",
        "FORM BSD3B - REVISED GROUP.xls",
        "monthly",
        14,
        {},
        ("BSD3A",),
        (
            "Guide BSD3 ¶7: BSD3-GROUP information is required for EACH subsidiary; "
            "the workbook is emitted once per subsidiary.",
        ),
    ),
    (
        "BSD4",
        "Sectoral Analysis of Overdrafts, Loans and Other Advances",
        "FORM BSD4 REVISED.xls",
        "monthly",
        14,
        {},
        ("BSD2",),
        (),
    ),
    (
        "BSD5A",
        "Capital Adequacy Return",
        "FORM BSD5A REVISED.xls",
        "monthly",
        14,
        _units(("NEW RISK WEIGHTS", "percent"), ("PROVISION", "percent")),
        ("BSD2", "BSD8"),
        (
            "Fed from the capital engine (CAR/RWA/Tier 1-2) — the former 'CAR-RWA' "
            "reconstruction's engine, now on the official layout.",
        ),
    ),
    (
        "BSD5B",
        "Consolidated Capital Adequacy Return",
        "FORM BSD5B REVISED.xls",
        "quarterly",
        14,
        {},
        ("BSD5A",),
        (),
    ),
    (
        "BSD6",
        "Maturity Analysis of Assets and Liabilities (BSD6A cedis / BSD6B foreign currency)",
        "FORM BSD6 REVISED.xls",
        "monthly",
        14,
        {},
        ("BSD2",),
        (
            "BSD6A/BSD6B totals must agree with BSD2 ('FROM BSD2'); BSD6B Annex 1 is "
            "the ONLY place unmatured spot/forward positions are reported (Guide §2).",
        ),
    ),
    (
        "BSD7A",
        "Current Year Results",
        "FORM BSD7A REVISED.xls",
        "monthly",
        14,
        {},
        (),
        ("Month-ended and period-to-date columns.",),
    ),
    (
        "BSD7B",
        "Current Year Consolidated Results",
        "FORM BSD7B REVISED.xls",
        "quarterly",
        14,
        {},
        ("BSD7A",),
        ("Consolidated per Guide §1 (subsidiaries only on BSD7B and BSD9).",),
    ),
    (
        "BSD8",
        "Advances Subject to Adverse Classification",
        "FORM BSD8 REVISED.xls",
        "monthly",
        14,
        {},
        ("BSD2",),
        (
            "BSD8!H22 links straight to the BSD2 workbook ([1]BSD2!D38+D39) — resolved "
            "from the computed BSD2 of the same reporting date.",
        ),
    ),
    (
        "BSD9",
        "Consolidated Balance Sheet",
        "FORM BSD9 REVISED.xls",
        "quarterly",
        14,
        {},
        ("BSD2",),
        ("Consolidated per Guide §1.",),
    ),
    (
        "BSD10",
        "Capital Expenditure",
        "FORM BSD10 REVISED.xls",
        "semiannual",
        14,
        {},
        (),
        ("Requires a fixed-asset / capital-expenditure register — input required.",),
    ),
    (
        "BSD11",
        "Statutory Return",
        "FORM BSD11 REVISED.xls",
        "semiannual",
        14,
        # Sheets 5/6 mix ¢'Million amounts with counts / percentages: the sheet
        # unit is millions and the count/% lines carry unscaled=True (Wave 4).
        _units(("BSD11-Sheet-2", "count")),
        ("BSD2", "BSD7A"),
        (),
    ),
    (
        "BSD13",
        "Net Open Position",
        "FORM BSD13 REVISED.xls",
        "monthly",
        14,
        # The main sheet's cedi cells are "Cedi equivalent (in '000)" per its
        # own header; per-currency columns are foreign-currency UNITS (bound
        # unscaled); only the schedules' "Other Currencies (in equiv. Cedi
        # 'Million)" column is millions.
        _units(("FOREX OPEN POSITION", "thousands")),
        ("BSD2A",),
        ("Fed from the FX engine's NOP (FX-NOP) where present.",),
    ),
    (
        "BSD14",
        "Interest Rate (Interest & Lending Rates)",
        "FORM BSD14 REVISED.xls",
        "weekly",
        9,
        _units(("INTEREST&LENDING-RATES", "percent")),
        (),
        (
            "Offered rates by product — input required until a product rate table is "
            "ingested (FTP base rates feed the reference columns).",
        ),
    ),
    (
        "BSD15A",
        "Charges — Domestic",
        "FORM BSD15A REVISED.xls",
        "weekly",
        9,
        _units(("Domestic charges of banks", "text"), ("Range of pdts i.r.o sav & cur ", "units")),
        (),
        ("Tariff register — input required.",),
    ),
    (
        "BSD15B",
        "Charges — Foreign (International Banking Charges)",
        "FORM BSD15B REVISED.xls",
        "weekly",
        9,
        _units(("International Banking Charges", "text")),
        (),
        ("Tariff register — input required.",),
    ),
    (
        "BSD16",
        "Automated Teller Machine Operations",
        "FORM BSD16 REVISED.xls",
        "monthly",
        14,
        {},
        (),
        ("ATM/channel register — input required for card/transaction counts.",),
    ),
    (
        "BSD17",
        "Foreign Inward Remittances",
        "FORM BSD17 REVISED.xls",
        "monthly",
        14,
        _units(("BSG17-SHEET 1", "units"), ("BSD17 -SHEET 2", "units")),
        (),
        ("Remittance data — input required (see docs/remittance_scoping.md).",),
    ),
)

#: Weekly returns are struck as at Friday close of business (banking week end).
#: The Guide fixes the weekly cadence and 9-day limit but not the weekday; this
#: default is a documented platform convention, adjustable per deployment.
WEEKLY_ANCHOR_WEEKDAY = 4  # Monday=0 … Friday=4


def _default_unit(sheet_name: str, overrides: dict[str, UnitConvention]) -> UnitConvention:
    if sheet_name in overrides:
        return overrides[sheet_name]
    return "millions"


@cache
def form_spec(code: str) -> FormSpec:
    for entry in _FORMS:
        if entry[0] == code:
            (_, title, workbook, frequency, limit, unit_overrides, depends_on, notes) = entry
            layout = load_layout(code)
            maps = line_maps_for(code)
            sheets = tuple(
                SheetSpec(
                    name=sheet.name,
                    unit=_default_unit(sheet.name, unit_overrides),
                    header=HeaderCells(),
                    lines=tuple(maps.get(sheet.name, ())),
                )
                for sheet in layout.sheets
            )
            basis: Basis = "consolidated" if code in _CONSOLIDATED else "solo"
            return FormSpec(
                code=code,
                title=title,
                workbook=workbook,
                frequency=frequency,
                time_limit_days=limit,
                basis=basis,
                sheets=sheets,
                depends_on=depends_on,
                scope_notes=(*_GUIDE_SCOPE_NOTES, *notes),
                ghana_branches_only=(code == "BSD1"),
            )
    msg = f"{code!r} is not an official BoG form in the catalogue"
    raise KeyError(msg)


def all_form_codes() -> tuple[str, ...]:
    return tuple(entry[0] for entry in _FORMS)


def all_form_specs() -> tuple[FormSpec, ...]:
    return tuple(form_spec(code) for code in all_form_codes())
