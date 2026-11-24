"""Small synthetic return definitions, for the gateway's own tests only.

The gateway publishes the AequorOS-aligned catalogue (``app/returns/catalogue``) in
``local``, ``test`` and ``sandbox``; these short stand-ins keep unit and API tests readable.


Every definition here is SYNTHETIC: return codes are real BoG codes, but line structures,
labels and rules are illustrative stand-ins built to the approved design (BSD2 uses the
lines and labels of ``design/Submission.dc.html``; rule counts match
``design/Returns.dc.html``). BoG publishes the real definitions through the return
definitions workflow. Schemas carry ``x-srg-synthetic: true``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pydantic import JsonValue

from app.core.jcs import MAX_SAFE_INTEGER
from app.returns.rules import (
    CompareRule,
    CrossEqualRule,
    NonNegativeRule,
    Rule,
    SumRule,
    VarianceRule,
)

SYNTHETIC_FIRST_REPORTING_DATE = date(2025, 11, 30)


@dataclass(frozen=True)
class LineSpec:
    code: str
    label: str
    total: bool = False


@dataclass(frozen=True)
class SectionSpec:
    """``total`` equals the sum of ``components``."""

    total: str
    components: tuple[str, ...]
    description: str | None = None


@dataclass(frozen=True)
class CrossSpec:
    line: str
    other_return: str
    other_line: str
    tolerance: int = 0


@dataclass(frozen=True)
class CompareSpec:
    left: str
    op: str
    right: str
    description: str


@dataclass(frozen=True)
class Derive:
    """Seed generator hint: ``value = reference × U(low, high)``."""

    reference: str
    low: float
    high: float


@dataclass(frozen=True)
class DefinitionSpec:
    code: str
    name: str
    cadence: str
    applies_to: tuple[str, ...]
    rule_count: int
    lines: tuple[LineSpec, ...]
    sections: tuple[SectionSpec, ...]
    str_count: int
    ari_count: int
    per_lines: tuple[str, ...] = ()
    equalities: tuple[tuple[str, str], ...] = ()
    cross: tuple[CrossSpec, ...] = ()
    extra_ari: tuple[CompareSpec, ...] = ()
    extra_sums: tuple[SectionSpec, ...] = ()
    str_by_section: bool = False
    variance_pct: int = 25
    auto_accept: bool = False
    version: str = "2026.1"
    first_reporting_date: date = SYNTHETIC_FIRST_REPORTING_DATE
    weights: dict[str, float] = field(default_factory=lambda: {})
    derive: dict[str, Derive] = field(default_factory=lambda: {})
    change_summary: str = "Synthetic pilot definition for local, test and sandbox use."

    def label(self, code: str) -> str:
        for line in self.lines:
            if line.code == code:
                return line.label
        return code

    @property
    def line_codes(self) -> list[str]:
        return [line.code for line in self.lines]


def _range(codes: tuple[str, ...]) -> str:
    return codes[0] if len(codes) == 1 else f"{codes[0]} to {codes[-1]}"


def build_rules(spec: DefinitionSpec) -> list[Rule]:
    rid = spec.code
    structure: list[Rule] = []
    per_line = [
        NonNegativeRule(
            id="",
            severity="error",
            kind="non_negative",
            lines=[line.code],
            description=f"{line.label} is zero or positive",
        )
        for line in spec.lines
    ]
    per_section = [
        NonNegativeRule(
            id="",
            severity="error",
            kind="non_negative",
            lines=[*section.components, section.total],
            description=f"Lines {_range(section.components)} and {section.total} are zero or positive",
        )
        for section in _unique_sections(spec.sections)
    ]
    everything = NonNegativeRule(
        id="",
        severity="error",
        kind="non_negative",
        lines=spec.line_codes,
        description="Every reported line is zero or positive",
    )
    candidates = [*per_section, *per_line] if spec.str_by_section else [*per_line, *per_section]
    candidates.append(everything)
    structure = list(candidates[: spec.str_count])

    arithmetic: list[Rule] = [
        CompareRule(
            id="",
            severity="error",
            kind="compare",
            left=c.left,
            op=">=" if c.op == ">=" else "<=" if c.op == "<=" else "==",
            right=c.right,
            description=c.description,
        )
        for c in spec.extra_ari
    ]
    arithmetic += [
        SumRule(
            id="",
            severity="error",
            kind="sum",
            target=s.total,
            terms=list(s.components),
            tolerance=0,
            description=s.description
            or f"{spec.label(s.total)} equals the sum of lines {', '.join(s.components)}",
        )
        for s in (*spec.sections, *spec.extra_sums)
    ]
    arithmetic += [
        CompareRule(
            id="",
            severity="error",
            kind="compare",
            left=a,
            op="==",
            right=b,
            description=f"{spec.label(a)} equals {spec.label(b)}",
        )
        for a, b in spec.equalities
    ]
    for s in spec.sections:
        for component in s.components:
            arithmetic.append(
                CompareRule(
                    id="",
                    severity="error",
                    kind="compare",
                    left=component,
                    op="<=",
                    right=s.total,
                    description=f"{spec.label(component)} does not exceed {spec.label(s.total)}",
                )
            )
    by_total = {s.total: s for s in spec.sections}
    for a, b in spec.equalities:
        for own, other in ((a, b), (b, a)):
            section = by_total.get(own)
            if section is None:
                continue
            for component in section.components:
                arithmetic.append(
                    CompareRule(
                        id="",
                        severity="error",
                        kind="compare",
                        left=component,
                        op="<=",
                        right=other,
                        description=f"{spec.label(component)} does not exceed {spec.label(other)}",
                    )
                )
    arithmetic = arithmetic[: spec.ari_count]

    cross: list[Rule] = [
        CrossEqualRule(
            id="",
            severity="error",
            kind="cross_equal",
            line=c.line,
            other_return=c.other_return,
            other_line=c.other_line,
            tolerance=c.tolerance,
            description=f"{spec.label(c.line)} agrees with {c.other_return} line {c.other_line}",
        )
        for c in spec.cross
    ]
    period: list[Rule] = [
        VarianceRule(
            id="",
            severity="warning",
            kind="variance",
            line=code,
            max_change_pct=spec.variance_pct,
            description=f"{spec.label(code)} moves by no more than {spec.variance_pct}% from the "
            "previous reporting date",
        )
        for code in spec.per_lines
    ]
    if len(structure) != spec.str_count or len(arithmetic) != spec.ari_count:
        raise ValueError(f"{spec.code}: not enough rule candidates for the requested counts")
    numbered: list[Rule] = []
    for group, rules in (("STR", structure), ("ARI", arithmetic), ("XR", cross), ("PER", period)):
        for index, rule in enumerate(rules, start=1):
            numbered.append(rule.model_copy(update={"id": f"{rid}-{group}-{index:03d}"}))
    if len(numbered) != spec.rule_count:
        raise ValueError(f"{spec.code}: built {len(numbered)} rules, expected {spec.rule_count}")
    return numbered


def _unique_sections(sections: tuple[SectionSpec, ...]) -> list[SectionSpec]:
    seen: set[str] = set()
    unique: list[SectionSpec] = []
    for section in sections:
        key = section.total + ":" + ",".join(section.components)
        if key not in seen and all(not c.startswith("~") for c in section.components):
            seen.add(key)
            unique.append(section)
    return unique


def build_schema(spec: DefinitionSpec) -> dict[str, JsonValue]:
    line_properties: dict[str, JsonValue] = {
        line.code: {
            "type": "integer",
            "title": line.label,
            "minimum": -MAX_SAFE_INTEGER,
            "maximum": MAX_SAFE_INTEGER,
        }
        for line in spec.lines
    }
    line_meta: list[JsonValue] = [
        {"code": line.code, "label": line.label, "total": line.total} for line in spec.lines
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:srg:return:{spec.code}:{spec.version}",
        "title": f"{spec.code} {spec.name} (synthetic)",
        "type": "object",
        "additionalProperties": False,
        "required": ["currency", "unit", "lines"],
        "properties": {
            "currency": {"const": "GHS"},
            "unit": {"const": "thousands"},
            "lines": {
                "type": "object",
                "additionalProperties": False,
                "required": list[JsonValue](spec.line_codes),
                "properties": line_properties,
            },
        },
        "x-srg-synthetic": True,
        "x-srg-lines": line_meta,
    }


def _lines(*items: tuple[str, str] | tuple[str, str, bool]) -> tuple[LineSpec, ...]:
    return tuple(LineSpec(item[0], item[1], len(item) == 3 and bool(item[2])) for item in items)


def _sec(total: str, *components: str, description: str | None = None) -> SectionSpec:
    return SectionSpec(total, components, description)


CORE = ("universal_bank", "rural_bank", "savings_and_loans")
UNIVERSAL = ("universal_bank",)

BSD2_ITEMS = (
    ("1.1", "Cash and balances with the central bank"),
    ("1.2", "Due from banks"),
    ("1.3", "Investments"),
    ("1.4", "Loans and advances (net)"),
    ("1.5", "Other assets"),
    ("2.1", "Deposits"),
    ("2.2", "Borrowings"),
    ("2.3", "Other liabilities"),
    ("2.4", "Shareholders’ funds"),
)

DBK_DAILY = DefinitionSpec(
    code="DBK-DAILY",
    name="Daily Bank Return",
    cadence="daily",
    applies_to=CORE,
    rule_count=12,
    lines=_lines(
        ("1.1", "Cash on hand"),
        ("1.2", "Balances with the central bank"),
        ("1.3", "Interbank placements"),
        ("1", "Liquid funds", True),
        ("2.1", "Demand deposits"),
        ("2.2", "Savings deposits"),
        ("2.3", "Time deposits"),
        ("2", "Total deposits", True),
    ),
    sections=(_sec("1", "1.1", "1.2", "1.3"), _sec("2", "2.1", "2.2", "2.3")),
    str_count=6,
    ari_count=6,
    auto_accept=True,
    first_reporting_date=date(2026, 11, 2),
    weights={"1.1": 0.05, "1.2": 0.12, "1.3": 0.08, "2.1": 0.30, "2.2": 0.25, "2.3": 0.20},
)

BSD1 = DefinitionSpec(
    code="BSD1",
    name="Liquidity Reserve Return",
    cadence="weekly",
    applies_to=CORE,
    rule_count=16,
    lines=_lines(
        ("1.1", "Cash in vaults"),
        ("1.2", "Balances with the central bank"),
        ("1", "Reserve assets", True),
        ("2.1", "Demand deposits"),
        ("2.2", "Savings deposits"),
        ("2.3", "Time deposits"),
        ("2", "Deposit base", True),
        ("3", "Required reserve"),
    ),
    sections=(_sec("1", "1.1", "1.2"), _sec("2", "2.1", "2.2", "2.3")),
    extra_ari=(CompareSpec("3", "<=", "2", "Required reserve does not exceed the deposit base"),),
    str_count=8,
    ari_count=8,
    first_reporting_date=date(2026, 10, 30),
    weights={"1.1": 0.05, "1.2": 0.12, "2.1": 0.30, "2.2": 0.25, "2.3": 0.20},
    derive={"3": Derive("2", 0.08, 0.12)},
)

BSD2 = DefinitionSpec(
    code="BSD2",
    name="Statement of Assets and Liabilities",
    cadence="monthly",
    applies_to=CORE,
    rule_count=58,
    lines=_lines(
        *BSD2_ITEMS[:5],
        ("1", "Total assets", True),
        *BSD2_ITEMS[5:],
        ("2", "Total liabilities and equity", True),
    ),
    sections=(_sec("1", "1.1", "1.2", "1.3", "1.4", "1.5"), _sec("2", "2.1", "2.2", "2.3", "2.4")),
    equalities=(("1", "2"),),
    extra_sums=(_sec("1", "2.1", "2.2", "2.3", "2.4"),),
    str_count=12,
    ari_count=22,
    cross=(
        CrossSpec("1.1", "BSD6", "1.1"),
        CrossSpec("1.4", "BSD6", "1.4"),
        CrossSpec("1.5", "BSD6", "1.5"),
        CrossSpec("1", "BSD6", "1"),
        CrossSpec("2.1", "BSD6", "2.1"),
        CrossSpec("2.2", "BSD6", "2.2"),
        CrossSpec("2.3", "BSD6", "2.3"),
        CrossSpec("2.4", "BSD6", "2.4"),
        CrossSpec("2", "BSD6", "2"),
        CrossSpec("1", "BSD6", "2"),
        CrossSpec("2", "BSD6", "1"),
        CrossSpec("1.1", "BSD6", "1.1.1"),
        CrossSpec("2.4", "BSD5A", "1"),
        CrossSpec("1", "BSD5A", "7"),
    ),
    per_lines=("1", "2", "1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3"),
    weights={
        "1.1": 0.10,
        "1.2": 0.07,
        "1.3": 0.29,
        "1.4": 0.50,
        "1.5": 0.04,
        "2.1": 0.77,
        "2.2": 0.05,
        "2.3": 0.045,
        "2.4": 0.135,
    },
)

BSD3A = DefinitionSpec(
    code="BSD3A",
    name="Large Exposures — Advances and Deposits",
    cadence="monthly",
    applies_to=CORE,
    rule_count=20,
    lines=_lines(
        ("1.1", "Advances to the largest borrower"),
        ("1.2", "Advances to the second largest borrower"),
        ("1.3", "Advances to the third largest borrower"),
        ("1.4", "Advances to the fourth largest borrower"),
        ("1.5", "Advances to the fifth largest borrower"),
        ("1", "Five largest advances", True),
        ("2.1", "Deposits of the largest depositor"),
        ("2.2", "Deposits of the second largest depositor"),
        ("2.3", "Deposits of the third largest depositor"),
        ("2.4", "Deposits of the fourth largest depositor"),
        ("2.5", "Deposits of the fifth largest depositor"),
        ("2", "Five largest deposits", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3", "1.4", "1.5"),
        _sec("2", "2.1", "2.2", "2.3", "2.4", "2.5"),
    ),
    str_count=12,
    ari_count=8,
    weights={
        "1.1": 0.030,
        "1.2": 0.025,
        "1.3": 0.02,
        "1.4": 0.018,
        "1.5": 0.015,
        "2.1": 0.04,
        "2.2": 0.03,
        "2.3": 0.025,
        "2.4": 0.02,
        "2.5": 0.018,
    },
)


def bsd5a_spec(version: str = "2026.1") -> DefinitionSpec:
    rwa = ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6"]
    rwa_lines: list[tuple[str, str]] = [
        ("4.1", "Claims on sovereigns"),
        ("4.2", "Claims on banks"),
        ("4.3", "Claims on corporates"),
        ("4.4", "Retail claims"),
        ("4.5", "Other assets"),
        ("4.6", "Off-balance-sheet items"),
    ]
    extended = version != "2026.1"
    if extended:
        rwa.append("4.7")
        rwa_lines.append(("4.7", "Operational risk RWA"))
    return DefinitionSpec(
        code="BSD5A",
        name="Capital Adequacy Return",
        cadence="monthly",
        applies_to=CORE,
        rule_count=46 if extended else 44,
        version=version,
        first_reporting_date=date(2027, 1, 31) if extended else SYNTHETIC_FIRST_REPORTING_DATE,
        change_summary="Adds line 4.7, operational risk RWA"
        if extended
        else "Synthetic pilot definition for local, test and sandbox use.",
        lines=_lines(
            ("1.1", "Paid-up capital"),
            ("1.2", "Statutory reserve"),
            ("1.3", "Retained earnings"),
            ("1.4", "Other reserves"),
            ("1", "Shareholders’ funds", True),
            ("2.1", "Goodwill and intangibles"),
            ("2.2", "Investments in unconsolidated subsidiaries"),
            ("2.3", "Deferred tax assets"),
            ("2", "Regulatory deductions", True),
            ("3.1", "Subordinated term debt"),
            ("3.2", "Revaluation reserves"),
            ("3.3", "General provisions"),
            ("3", "Tier 2 capital", True),
            *rwa_lines,
            ("4", "Total risk-weighted assets", True),
            ("5", "Tier 1 capital", True),
            ("6", "Total regulatory capital", True),
            ("7", "Total assets per balance sheet"),
        ),
        sections=(
            _sec("1", "1.1", "1.2", "1.3", "1.4"),
            _sec("2", "2.1", "2.2", "2.3"),
            _sec("3", "3.1", "3.2", "3.3"),
            _sec("4", *rwa),
            _sec("1", "5", "2"),
            _sec("6", "5", "3"),
        ),
        str_count=23 if extended else 22,
        ari_count=23 if extended else 22,
        weights={
            "1.1": 0.04,
            "1.2": 0.03,
            "1.3": 0.05,
            "1.4": 0.01,
            "2.1": 0.004,
            "2.2": 0.003,
            "2.3": 0.002,
            "3.1": 0.01,
            "3.2": 0.004,
            "3.3": 0.006,
            "4.1": 0.02,
            "4.2": 0.05,
            "4.3": 0.30,
            "4.4": 0.12,
            "4.5": 0.06,
            "4.6": 0.04,
            "4.7": 0.08,
            "7": 1.0,
        },
    )


BSD5A = bsd5a_spec()

_BUCKETS = (("1", "up to 3 months"), ("2", "3 to 12 months"), ("3", "over 1 year"))


def _bsd6_lines() -> tuple[LineSpec, ...]:
    out: list[LineSpec] = []
    for code, label in BSD2_ITEMS:
        for suffix, bucket in _BUCKETS:
            out.append(LineSpec(f"{code}.{suffix}", f"{label}, {bucket}"))
        out.append(LineSpec(code, f"{label}, total", True))
        if code == "1.5":
            out.append(LineSpec("1", "Total assets", True))
    out.append(LineSpec("2", "Total liabilities and equity", True))
    return tuple(out)


BSD6 = DefinitionSpec(
    code="BSD6",
    name="Maturity Analysis of Assets and Liabilities",
    cadence="monthly",
    applies_to=CORE,
    rule_count=40,
    lines=_bsd6_lines(),
    sections=(
        *(_sec(code, f"{code}.1", f"{code}.2", f"{code}.3") for code, _ in BSD2_ITEMS),
        _sec("1", "1.1", "1.2", "1.3", "1.4", "1.5"),
        _sec("2", "2.1", "2.2", "2.3", "2.4"),
    ),
    equalities=(("1", "2"),),
    str_by_section=True,
    str_count=11,
    ari_count=21,
    per_lines=("1", "2", "1.1", "1.3", "1.4", "2.1", "2.2", "2.4"),
)

BSD7A = DefinitionSpec(
    code="BSD7A",
    name="Current Year Results",
    cadence="monthly",
    applies_to=CORE,
    rule_count=26,
    lines=_lines(
        ("1.1", "Interest income on loans and advances"),
        ("1.2", "Interest income on investments"),
        ("1.3", "Interest income on placements"),
        ("1", "Total interest income", True),
        ("2.1", "Interest expense on deposits"),
        ("2.2", "Interest expense on borrowings"),
        ("2", "Total interest expense", True),
        ("3.1", "Fees and commissions"),
        ("3.2", "Trading income"),
        ("3.3", "Other income"),
        ("3", "Total non-interest income", True),
        ("4.1", "Staff costs"),
        ("4.2", "Premises and equipment"),
        ("4.3", "Other operating expenses"),
        ("4", "Total operating expenses", True),
        ("5", "Impairment charges"),
        ("6", "Total income", True),
        ("7", "Total expenses and charges", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3"),
        _sec("2", "2.1", "2.2"),
        _sec("3", "3.1", "3.2", "3.3"),
        _sec("4", "4.1", "4.2", "4.3"),
        _sec("6", "1", "3"),
        _sec("7", "2", "4", "5"),
    ),
    str_count=12,
    ari_count=14,
    weights={
        "1.1": 0.060,
        "1.2": 0.025,
        "1.3": 0.005,
        "2.1": 0.025,
        "2.2": 0.004,
        "3.1": 0.012,
        "3.2": 0.006,
        "3.3": 0.002,
        "4.1": 0.020,
        "4.2": 0.008,
        "4.3": 0.010,
        "5": 0.006,
    },
)

BSD8 = DefinitionSpec(
    code="BSD8",
    name="Advances Subject to Adverse Classification",
    cadence="monthly",
    applies_to=CORE,
    rule_count=24,
    lines=_lines(
        ("1.1", "Current"),
        ("1.2", "Other loans especially mentioned"),
        ("1.3", "Substandard"),
        ("1.4", "Doubtful"),
        ("1.5", "Loss"),
        ("1", "Gross advances", True),
        ("2.1", "Specific provisions on substandard advances"),
        ("2.2", "Specific provisions on doubtful advances"),
        ("2.3", "Specific provisions on loss advances"),
        ("2", "Total specific provisions", True),
        ("3", "Total classified advances", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3", "1.4", "1.5"),
        _sec("2", "2.1", "2.2", "2.3"),
        _sec("3", "1.3", "1.4", "1.5"),
    ),
    extra_ari=(
        CompareSpec(
            "2.1", "<=", "1.3", "Provisions on substandard advances do not exceed those advances"
        ),
        CompareSpec(
            "2.2", "<=", "1.4", "Provisions on doubtful advances do not exceed those advances"
        ),
        CompareSpec("2.3", "<=", "1.5", "Provisions on loss advances do not exceed those advances"),
        CompareSpec("2", "<=", "3", "Total specific provisions do not exceed classified advances"),
        CompareSpec("3", "<=", "1", "Classified advances do not exceed gross advances"),
    ),
    str_count=11,
    ari_count=13,
    weights={"1.1": 0.44, "1.2": 0.02, "1.3": 0.015, "1.4": 0.01, "1.5": 0.008},
    derive={
        "2.1": Derive("1.3", 0.20, 0.30),
        "2.2": Derive("1.4", 0.50, 0.60),
        "2.3": Derive("1.5", 0.90, 1.0),
    },
)

BSD13 = DefinitionSpec(
    code="BSD13",
    name="Net Open Position",
    cadence="monthly",
    applies_to=CORE,
    rule_count=18,
    lines=_lines(
        ("1.1", "Long positions — US dollar"),
        ("1.2", "Long positions — euro"),
        ("1.3", "Long positions — pound sterling"),
        ("1.4", "Long positions — other currencies"),
        ("1", "Total long positions", True),
        ("2.1", "Short positions — US dollar"),
        ("2.2", "Short positions — euro"),
        ("2.3", "Short positions — pound sterling"),
        ("2.4", "Short positions — other currencies"),
        ("2", "Total short positions", True),
        ("3", "Tier 1 capital (reference)"),
    ),
    sections=(_sec("1", "1.1", "1.2", "1.3", "1.4"), _sec("2", "2.1", "2.2", "2.3", "2.4")),
    str_count=10,
    ari_count=8,
    weights={
        "1.1": 0.04,
        "1.2": 0.01,
        "1.3": 0.006,
        "1.4": 0.003,
        "2.1": 0.035,
        "2.2": 0.009,
        "2.3": 0.005,
        "2.4": 0.002,
        "3": 0.10,
    },
)

CAR_RWA = DefinitionSpec(
    code="CAR-RWA",
    name="Capital Adequacy (CAR & RWA)",
    cadence="monthly",
    applies_to=CORE,
    rule_count=62,
    lines=_lines(
        ("1.1", "Paid-up ordinary shares"),
        ("1.2", "Share premium"),
        ("1.3", "Retained earnings"),
        ("1.4", "Statutory reserve"),
        ("1.5", "Other disclosed reserves"),
        ("1", "Common Equity Tier 1 before deductions", True),
        ("2.1", "Goodwill and other intangibles"),
        ("2.2", "Deferred tax assets"),
        ("2.3", "Investments in other financial institutions"),
        ("2.4", "Shortfall of provisions to expected losses"),
        ("2", "Regulatory deductions", True),
        ("3", "Common Equity Tier 1 capital", True),
        ("4.1", "Additional Tier 1 instruments"),
        ("4.2", "Share premium on Additional Tier 1"),
        ("4", "Additional Tier 1 capital", True),
        ("5", "Tier 1 capital", True),
        ("6.1", "Subordinated debt"),
        ("6.2", "General provisions (booked)"),
        ("6.3", "Revaluation reserves"),
        ("6", "Tier 2 capital", True),
        ("7", "Total regulatory capital", True),
        ("8.1", "Claims on sovereigns"),
        ("8.2", "Claims on public sector entities"),
        ("8.3", "Claims on banks"),
        ("8.4", "Claims on corporates"),
        ("8.5", "Retail claims"),
        ("8.6", "Residential mortgages"),
        ("8.7", "Past due exposures"),
        ("8.8", "Other assets"),
        ("8.9", "Off-balance-sheet exposures"),
        ("8", "Credit risk RWA", True),
        ("9.1", "Interest rate risk"),
        ("9.2", "Foreign exchange risk"),
        ("9.3", "Equity risk"),
        ("9", "Market risk RWA", True),
        ("10", "Operational risk RWA"),
        ("11", "Total risk-weighted assets", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3", "1.4", "1.5"),
        _sec("2", "2.1", "2.2", "2.3", "2.4"),
        _sec("1", "3", "2"),
        _sec("4", "4.1", "4.2"),
        _sec("5", "3", "4"),
        _sec("6", "6.1", "6.2", "6.3"),
        _sec("7", "5", "6"),
        _sec("8", "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9"),
        _sec("9", "9.1", "9.2", "9.3"),
        _sec("11", "8", "9", "10"),
    ),
    extra_ari=(
        CompareSpec("7", ">=", "5", "Total regulatory capital is at least Tier 1 capital"),
        CompareSpec("5", ">=", "3", "Tier 1 capital is at least Common Equity Tier 1 capital"),
    ),
    first_reporting_date=date(2026, 9, 30),
    str_count=20,
    ari_count=30,
    per_lines=("11", "7", "5", "3", "8", "9", "10", "1", "2", "6", "4", "1.1"),
    weights={
        "1.1": 0.040,
        "1.2": 0.010,
        "1.3": 0.050,
        "1.4": 0.030,
        "1.5": 0.005,
        "2.1": 0.003,
        "2.2": 0.002,
        "2.3": 0.002,
        "2.4": 0.001,
        "4.1": 0.008,
        "4.2": 0.001,
        "6.1": 0.010,
        "6.2": 0.006,
        "6.3": 0.004,
        "8.1": 0.02,
        "8.2": 0.03,
        "8.3": 0.05,
        "8.4": 0.25,
        "8.5": 0.10,
        "8.6": 0.04,
        "8.7": 0.02,
        "8.8": 0.05,
        "8.9": 0.04,
        "9.1": 0.02,
        "9.2": 0.015,
        "9.3": 0.005,
        "10": 0.08,
    },
)

LMT = DefinitionSpec(
    code="LMT",
    name="Liquidity Monitoring Tools",
    cadence="monthly",
    applies_to=CORE,
    rule_count=71,
    lines=_lines(
        ("1.1", "Level 1 assets"),
        ("1.2", "Level 2A assets"),
        ("1.3", "Level 2B assets"),
        ("1", "Total stock of high-quality liquid assets", True),
        ("2.1", "Retail deposits — stable"),
        ("2.2", "Retail deposits — less stable"),
        ("2.3", "Unsecured wholesale funding"),
        ("2.4", "Secured funding"),
        ("2.5", "Committed credit and liquidity facilities"),
        ("2.6", "Other contractual outflows"),
        ("2", "Total cash outflows over 30 days", True),
        ("3.1", "Inflows from performing loans"),
        ("3.2", "Interbank inflows"),
        ("3.3", "Other contractual inflows"),
        ("3", "Total cash inflows over 30 days", True),
        ("4.1", "Level 1 assets — local currency"),
        ("4.2", "Level 1 assets — foreign currency"),
        ("4.3", "Level 2A assets — local currency"),
        ("4.4", "Level 2A assets — foreign currency"),
        ("4.5", "Level 2B assets — local currency"),
        ("4.6", "Level 2B assets — foreign currency"),
        ("4", "Total high-quality liquid assets by currency", True),
        ("5.1", "Ten largest depositors"),
        ("5.2", "Ten largest wholesale counterparties"),
        ("5.3", "Other funding sources"),
        ("5", "Total funding", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3"),
        _sec("2", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"),
        _sec("3", "3.1", "3.2", "3.3"),
        _sec("1.1", "4.1", "4.2", description="Table 4 Level 1 assets reconcile to Table 1"),
        _sec("1.2", "4.3", "4.4", description="Table 4 Level 2A assets reconcile to Table 1"),
        _sec("1.3", "4.5", "4.6", description="Table 4 Level 2B assets reconcile to Table 1"),
        _sec("4", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6"),
        _sec("5", "5.1", "5.2", "5.3"),
    ),
    extra_ari=(CompareSpec("4", "==", "1", "Table 4 total reconciles to the Table 1 total"),),
    first_reporting_date=date(2026, 9, 30),
    str_count=26,
    ari_count=30,
    per_lines=(
        "1",
        "2",
        "3",
        "4",
        "5",
        "1.1",
        "1.2",
        "1.3",
        "2.1",
        "2.2",
        "2.3",
        "2.4",
        "2.5",
        "3.1",
        "3.2",
    ),
    weights={
        "1.1": 0.12,
        "1.2": 0.04,
        "1.3": 0.01,
        "2.1": 0.03,
        "2.2": 0.05,
        "2.3": 0.06,
        "2.4": 0.01,
        "2.5": 0.02,
        "2.6": 0.01,
        "3.1": 0.03,
        "3.2": 0.02,
        "3.3": 0.01,
        "5.1": 0.15,
        "5.2": 0.10,
        "5.3": 0.55,
    },
)

LE_MONTHLY = DefinitionSpec(
    code="LE-MONTHLY",
    name="Large Exposures",
    cadence="monthly",
    applies_to=UNIVERSAL,
    rule_count=33,
    lines=_lines(
        *((f"1.{n}", f"Exposure to connected group {n}") for n in range(1, 11)),
        ("1", "Ten largest exposures", True),
        ("2", "Tier 1 capital"),
    ),
    sections=(_sec("1", *(f"1.{n}" for n in range(1, 11))),),
    extra_ari=tuple(
        CompareSpec(
            f"1.{n}",
            "<=",
            "2",
            f"Exposure to connected group {n} does not exceed the "
            "reported Tier 1 capital (unit check)",
        )
        for n in range(1, 11)
    ),
    str_count=12,
    ari_count=21,
    weights={**{f"1.{n}": 0.03 - n * 0.002 for n in range(1, 11)}, "2": 0.10},
)

NPL_MONTHLY = DefinitionSpec(
    code="NPL-MONTHLY",
    name="Monthly Non-Performing Loans",
    cadence="monthly",
    applies_to=CORE,
    rule_count=31,
    lines=_lines(
        ("1.1", "Gross loans — corporate"),
        ("1.2", "Gross loans — small and medium enterprises"),
        ("1.3", "Gross loans — retail"),
        ("1.4", "Gross loans — other"),
        ("1", "Gross loans", True),
        ("2.1", "Non-performing loans — corporate"),
        ("2.2", "Non-performing loans — small and medium enterprises"),
        ("2.3", "Non-performing loans — retail"),
        ("2.4", "Non-performing loans — other"),
        ("2", "Total non-performing loans", True),
        ("3.1", "Provisions held — corporate"),
        ("3.2", "Provisions held — small and medium enterprises"),
        ("3.3", "Provisions held — retail"),
        ("3.4", "Provisions held — other"),
        ("3", "Total provisions held", True),
    ),
    sections=(
        _sec("1", "1.1", "1.2", "1.3", "1.4"),
        _sec("2", "2.1", "2.2", "2.3", "2.4"),
        _sec("3", "3.1", "3.2", "3.3", "3.4"),
    ),
    extra_ari=(
        *(
            CompareSpec(
                f"2.{n}",
                "<=",
                f"1.{n}",
                "Non-performing loans do not exceed gross loans in the segment",
            )
            for n in range(1, 5)
        ),
        CompareSpec("2", "<=", "1", "Total non-performing loans do not exceed gross loans"),
    ),
    str_count=15,
    ari_count=16,
    weights={"1.1": 0.20, "1.2": 0.12, "1.3": 0.15, "1.4": 0.03},
    derive={
        **{f"2.{n}": Derive(f"1.{n}", 0.03, 0.15) for n in range(1, 5)},
        **{f"3.{n}": Derive(f"2.{n}", 0.40, 0.90) for n in range(1, 5)},
    },
)

_IRR = (
    ("1", "up to 1 month"),
    ("2", "1 to 3 months"),
    ("3", "3 to 6 months"),
    ("4", "6 to 12 months"),
    ("5", "1 to 5 years"),
    ("6", "over 5 years"),
)

IRRBB_PILOT = DefinitionSpec(
    code="IRRBB-PILOT",
    name="IRRBB Pilot Return",
    cadence="quarterly",
    applies_to=UNIVERSAL,
    rule_count=27,
    lines=(
        *(LineSpec(f"1.{n}", f"Rate-sensitive assets, {label}") for n, label in _IRR),
        LineSpec("1", "Total rate-sensitive assets", True),
        *(LineSpec(f"2.{n}", f"Rate-sensitive liabilities, {label}") for n, label in _IRR),
        LineSpec("2", "Total rate-sensitive liabilities", True),
    ),
    sections=(_sec("1", *(f"1.{n}" for n, _ in _IRR)), _sec("2", *(f"2.{n}" for n, _ in _IRR))),
    str_count=14,
    ari_count=13,
    first_reporting_date=date(2026, 6, 30),
    weights={f"{side}.{n}": 0.05 for side in ("1", "2") for n, _ in _IRR},
)

ICAAP_REPORT = DefinitionSpec(
    code="ICAAP-REPORT",
    name="ICAAP Report",
    cadence="annual",
    applies_to=UNIVERSAL,
    rule_count=9,
    lines=_lines(
        ("1.1", "Credit risk capital"),
        ("1.2", "Market risk capital"),
        ("1.3", "Operational risk capital"),
        ("1.4", "Concentration risk capital"),
        ("1.5", "Interest rate risk in the banking book capital"),
        ("1.6", "Other material risks capital"),
        ("1", "Total internal capital requirement", True),
        ("2", "Available capital resources"),
    ),
    sections=(_sec("1", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6"),),
    str_count=8,
    ari_count=1,
    first_reporting_date=date(2025, 12, 31),
    weights={
        "1.1": 0.05,
        "1.2": 0.01,
        "1.3": 0.01,
        "1.4": 0.005,
        "1.5": 0.005,
        "1.6": 0.003,
        "2": 0.12,
    },
)

SYNTHETIC_DEFINITIONS: tuple[DefinitionSpec, ...] = (
    DBK_DAILY,
    BSD1,
    BSD2,
    BSD3A,
    BSD5A,
    BSD6,
    BSD7A,
    BSD8,
    BSD13,
    CAR_RWA,
    LMT,
    LE_MONTHLY,
    NPL_MONTHLY,
    IRRBB_PILOT,
    ICAAP_REPORT,
)


def spec_for(code: str) -> DefinitionSpec | None:
    for spec in SYNTHETIC_DEFINITIONS:
        if spec.code == code:
            return spec
    return None
