"""BSD11 — Statutory Return (Sections 41, 43, 44, 45 & 47, Banking Act 2004).

Official workbook: ``FORM BSD11 REVISED.xls`` — eight register sheets. Every
sheet is a BLANK grid (the template carries no ``0`` placeholders), so the data
cells are named explicitly with :func:`_common.grid_lines` from the header rows
of the official file (see docs/bog_returns/bsd11_line_map.md for the cell map):

- Sheet-1  summary of advances to directors / officers / employees (rows 12–15 × B–F)
- Sheet-2  particulars of directors (rows 8–13 × B–F)
- Sheet-3  facilities to firms in which directors are interested (rows 10–14 × B–H, total row 15)
- Sheet-4  schedule of facilities to directors (rows 12–17 × B–J)
- Sheet-5  unsecured facilities to officials & employees by category (NO./AMOUNT pairs)
- Sheet-6  advances, credits & guarantees as % of net worth (rows 8–17 × B–I)
- Sheet-7  immovable property accepted in satisfaction of debt (rows 9–16 × B–J)
- Sheet 8  exposure to relatives of directors / significant shareholders (rows 8–17 × A–I)

Sources: the related-party register + canonical positions through the
``bsd11.register`` resolver (sources_ext/bsd11.py) for directors, officers and
the Section 47 ranking; everything a register does not exist for (director
interests in firms, staff-loan categories, property in satisfaction of debt,
relatives of directors/shareholders, half-year movements) is ``input_required``
with the register named. Sheet-6 net worth = BSD2 line 16 Shareholders' Funds.
"""

from __future__ import annotations

from ._common import RowSource, grid_lines

_FORM = "BSD11"


def _register(register: str, *, notes: str, unscaled: bool = False, **params: object) -> RowSource:
    return RowSource(
        "bsd11.register", {"register": register, **params}, notes=notes, unscaled=unscaled
    )


def _serial(n: int) -> RowSource:
    return RowSource(
        "constant", {"value": n}, notes="template row ordinal (official value kept)", unscaled=True
    )


# ---- Sheet-1: summary (Sections 41, 43 & 44) --------------------------------
_S1_NOTE_MOVEMENTS = (
    "previous balance / granted / repaid / written off are half-year movements — the "
    "platform holds monthly balance snapshots, not a facility ledger; bank supplies them"
)
_S1_ROWS: dict[int, RowSource] = {
    12: RowSource(
        None,
        notes="firms in which directors are interested: the related-party register records "
        "parties and roles, not directors' interests in other companies — a director-interest "
        "register is required",
    ),
    13: _register(
        "summary",
        group="directors",
        notes="current balance = Σ LOAN balances to counterparties name-matched to register "
        f"parties holding a director role; {_S1_NOTE_MOVEMENTS}",
    ),
    14: _register(
        "summary",
        group="officers",
        notes="current balance = Σ LOAN balances to counterparties name-matched to register "
        f"parties holding a key-management (officer) role; {_S1_NOTE_MOVEMENTS}",
    ),
    15: RowSource(
        None,
        notes="advances to other employees — staff-loan register required (no employee "
        "counterparty attribute exists)",
    ),
}

# ---- Sheet-2: particulars of directors (Section 53) --------------------------
_S2_NOTE = (
    "related-party register: name & address, date appointed and shares held (shareholdings) "
    "of the N-th director (roles director/board_chairman, ordered by appointment date); "
    "full/part-time status and interests held in other companies are not held in the "
    "register — bank supplies"
)
_S2_ROWS = {row: _register("directors", rank=row - 7, notes=_S2_NOTE) for row in range(8, 14)}

# ---- Sheet-3: facilities to firms in which directors are interested ----------
_S3_NOTE = (
    "director-interest (firm) register required — the related-party register does not "
    "record which firms a director is interested in; particulars, amounts, security, "
    "type, balance and date granted must be supplied"
)
_S3_TOTAL_NOTE = "official TOTAL row carries no template formula — bank totals its detail rows"

# ---- Sheet-4: schedule of facilities to directors (Section 43) ---------------
_S4_NOTE = (
    "N-th director from the related-party register (same order as Sheet-2); present "
    "balance = Σ LOAN balances to counterparties name-matched to the director, type of "
    "advance = product name(s), nature of security = crm_collateral_class attribute when "
    "ingested; secured/unsecured/guaranteed split and board-approval date need the "
    "facility's security/approval record — bank supplies"
)
_S4_RATE_NOTE = (
    "rate of interest = the facility's contractual rate when the director has exactly one "
    "LOAN; several facilities carry several rates — bank states them"
)

# ---- Sheet-5: unsecured facilities to officials & other employees ------------
_S5_NOTE = (
    "staff-loan register by employee category (housing / vehicle / other secured / "
    "unsecured: number and amount) required — no employee-category attribute exists"
)
_S5_TOTAL_NOTE = "official total row (C.) carries no template formula — bank totals its rows"

# ---- Sheet-6: advances, credits and guarantees as % of net worth (Section 47) --
_S6_NOTE = (
    "N-th largest customer by on- + off-balance-sheet exposure from canonical positions "
    "(LOAN/INTERBANK_PLACEMENT/SECURITY_HOLDING on-balance; LC_GUARANTEE/COMMITMENT_UNDRAWN "
    "off-balance; connected groups by group_reference; sovereign/BoG/government excluded); "
    "total security = Σ crm_collateral_ghs + crm_guarantee_ghs attributes when ingested"
)
_S6_PCT_NOTE = (
    "percentage to net worth = exposure ÷ BSD2 line 16 Shareholders' Funds (D135) × 100; "
    "secured/unsecured split needs the ingested security value"
)

# ---- Sheet-7: immovable property in satisfaction of debt (Section 12(5)) -----
_S7_NOTE = (
    "property-in-satisfaction-of-debt register required (customer, description, tenure, "
    "acquisition date, advance, values, prior lien, net income) — no canonical entity"
)

# ---- Sheet 8: exposure to relatives of directors / significant shareholders --
_S8_NOTE = (
    "relatives register required — related-party roles cover directors, shareholders and "
    "key management, not their relatives (Sections 43(1)(d), 43(6))"
)


LINES = {
    "BSD11-Sheet-1": grid_lines(
        _FORM,
        "BSD11-Sheet-1",
        rows=range(12, 16),
        value_columns={
            "previous": "B",
            "granted": "C",
            "repaid": "D",
            "written_off": "E",
            "current": "F",
        },
        row_sources=_S1_ROWS,
        code_prefix="BSD11.S1",
    ),
    "BSD11-Sheet-2": grid_lines(
        _FORM,
        "BSD11-Sheet-2",
        rows=range(8, 14),
        value_columns={
            "name": "B",
            "appointed": "C",
            "employment": "D",
            "shares": "E",
            "interests": "F",
        },
        row_sources=_S2_ROWS,
        code_prefix="BSD11.S2",
    ),
    "BSD11-Sheet-3": (
        *grid_lines(
            _FORM,
            "BSD11-Sheet-3",
            rows=range(10, 15),
            value_columns={
                "particulars": "B",
                "secured": "C",
                "unsecured": "D",
                "security": "E",
                "facility_type": "F",
                "balance": "G",
                "granted": "H",
            },
            row_sources={},
            default=RowSource(None, notes=_S3_NOTE),
            code_prefix="BSD11.S3",
        ),
        *grid_lines(
            _FORM,
            "BSD11-Sheet-3",
            rows=(15,),
            value_columns={"secured": "C", "unsecured": "D", "balance": "G"},
            row_sources={},
            default=RowSource(None, notes=_S3_TOTAL_NOTE),
            code_prefix="BSD11.S3.total",
        ),
    ),
    "BSD11-Sheet-4": (
        *grid_lines(
            _FORM,
            "BSD11-Sheet-4",
            rows=range(12, 18),
            value_columns={
                "name": "B",
                "secured": "C",
                "unsecured": "D",
                "guaranteed": "E",
                "security": "F",
                "facility_type": "H",
                "approved": "I",
                "balance": "J",
            },
            row_sources={
                row: _register("directors", rank=row - 11, notes=_S4_NOTE) for row in range(12, 18)
            },
            code_prefix="BSD11.S4",
        ),
        *grid_lines(
            _FORM,
            "BSD11-Sheet-4",
            rows=range(12, 18),
            value_columns={"rate": "G"},
            row_sources={
                row: _register("directors", rank=row - 11, notes=_S4_RATE_NOTE, unscaled=True)
                for row in range(12, 18)
            },
            code_prefix="BSD11.S4.rate",
        ),
    ),
    "BSD11-Sheet-5": grid_lines(
        _FORM,
        "BSD11-Sheet-5",
        rows=(11, 12, 13, 17, 18, 19, 22),
        value_columns={
            "housing_no": "C",
            "housing_amount": "D",
            "vehicle_no": "E",
            "vehicle_amount": "F",
            "other_secured_no": "G",
            "other_secured_amount": "H",
            "unsecured_no": "I",
            "unsecured_amount": "J",
            "total_no": "K",
            "total_amount": "L",
        },
        row_sources={22: RowSource(None, notes=_S5_TOTAL_NOTE)},
        default=RowSource(None, notes=_S5_NOTE),
        code_prefix="BSD11.S5",
    ),
    "BSD11-Sheet-6": (
        *grid_lines(
            _FORM,
            "BSD11-Sheet-6",
            rows=range(8, 18),
            value_columns={
                "name": "B",
                "on_balance": "C",
                "off_balance": "D",
                "total": "E",
                "security": "F",
            },
            row_sources={
                row: _register("large_exposures", rank=row - 7, notes=_S6_NOTE)
                for row in range(8, 18)
            },
            code_prefix="BSD11.S6",
        ),
        *grid_lines(
            _FORM,
            "BSD11-Sheet-6",
            rows=range(8, 18),
            value_columns={"pct_secured": "G", "pct_unsecured": "H", "pct_total": "I"},
            row_sources={
                row: _register("large_exposures", rank=row - 7, notes=_S6_PCT_NOTE, unscaled=True)
                for row in range(8, 18)
            },
            code_prefix="BSD11.S6.pct",
        ),
    ),
    "BSD11-Sheet-7": grid_lines(
        _FORM,
        "BSD11-Sheet-7",
        rows=range(9, 17),
        value_columns={
            "customer": "B",
            "description": "C",
            "tenure": "D",
            "acquired": "E",
            "advance": "F",
            "value": "G",
            "forced_sale_value": "H",
            "prior_lien": "I",
            "net_income": "J",
        },
        row_sources={},
        default=RowSource(None, notes=_S7_NOTE),
        code_prefix="BSD11.S7",
    ),
    "BSD11- Sheet 8": (
        *grid_lines(
            _FORM,
            "BSD11- Sheet 8",
            rows=range(8, 18),
            value_columns={"serial": "A"},
            row_sources={row: _serial(row - 7) for row in range(8, 18)},
            code_prefix="BSD11.S8.serial",
        ),
        *grid_lines(
            _FORM,
            "BSD11- Sheet 8",
            rows=range(8, 18),
            value_columns={
                "customer": "B",
                "relationship": "C",
                "granted": "D",
                "security_type": "E",
                "security_value": "F",
                "expiry": "G",
                "outstanding": "H",
                "classification": "I",
            },
            row_sources={},
            default=RowSource(None, notes=_S8_NOTE),
            code_prefix="BSD11.S8",
        ),
    ),
}
