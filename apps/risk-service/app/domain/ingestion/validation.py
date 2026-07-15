"""Config-driven validation of translated canonical records.

Validation is a first-class layer: every batch runs through it before
anything persists, and the outcome gates the batch. Rules are configuration,
not code paths — different banks have different data-quality realities during
onboarding, so each rule's severity and parameters are per-institution
(stored alongside the mapping config), while rule *logic* lives here.

Severity semantics (spec §6.2):

- ``INFO``     noted, ingestion proceeds
- ``WARNING``  abnormal, ingestion proceeds, record flagged
- ``ERROR``    the affected records are excluded from calculations
- ``BLOCKER``  the entire batch is rejected; nothing persists as accepted
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.ingestion.contracts import CanonicalRecords

Severity = Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
SEVERITIES: tuple[Severity, ...] = ("INFO", "WARNING", "ERROR", "BLOCKER")
_SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}

RuleCategory = Literal[
    "STRUCTURAL", "BUSINESS_RULES", "BALANCE_RECONCILIATION", "CROSS_SOURCE", "TEMPORAL"
]

# Active ISO 4217 codes. Static by design: a stale ERROR here is cheaper than
# a typo'd currency silently flowing into regulatory calculations.
_ISO_4217_LINES = (
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB",
    "BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP",
    "DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF",
    "IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK",
    "LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN",
    "NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF",
    "SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND",
    "TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER",
    "ZAR ZMW ZWG",
)
ISO_4217 = frozenset(code for line in _ISO_4217_LINES for code in line.split())


class RuleConfig(BaseModel):
    name: str
    severity: Severity
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class ValidationConfig(BaseModel):
    """Per-institution rule configuration; the engine never hard-codes severities."""

    rules: list[RuleConfig] = Field(default_factory=list)


def default_validation_config() -> ValidationConfig:
    return ValidationConfig(
        rules=[
            RuleConfig(name="structural_duplicate_source_references", severity="ERROR"),
            RuleConfig(name="structural_unresolved_references", severity="ERROR"),
            RuleConfig(name="structural_unknown_counterparty", severity="WARNING"),
            RuleConfig(
                name="position_rate_bounds",
                severity="ERROR",
                params={"minimum": "0", "maximum": "1"},
            ),
            RuleConfig(name="currency_iso_4217", severity="ERROR"),
            RuleConfig(name="maturity_not_before_as_of", severity="WARNING"),
            RuleConfig(
                name="gl_subledger_reconciliation",
                severity="BLOCKER",
                params={"tolerance_percent": "0.1"},
            ),
            RuleConfig(
                name="unusual_balance_change",
                severity="WARNING",
                params={"threshold": "0.4"},
            ),
        ]
    )


@dataclass(frozen=True)
class ValidationContext:
    as_of_date: date
    # Prior-generation balances keyed by position source_reference; supplied
    # by the orchestrator when a previous accepted batch exists.
    prior_balances: dict[str, Decimal] | None = None
    # Current-generation canonical references already ingested for this bank,
    # so cross-batch links (counterparty file today, positions tomorrow)
    # resolve instead of flagging.
    known_counterparties: frozenset[str] = frozenset()
    known_products: frozenset[str] = frozenset()
    known_gl_accounts: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Finding:
    rule: str
    category: RuleCategory
    severity: Severity
    detail: str
    entity_type: str | None = None
    source_reference: str | None = None
    source_locator: str | None = None


@dataclass
class ValidationOutcome:
    findings: list[Finding] = field(default_factory=list)
    # (entity_type, source_reference) -> accepted | warning | error | blocked
    record_statuses: dict[tuple[str, str], str] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    overall_status: str = "accepted"

    @property
    def batch_status(self) -> str:
        """The ingestion batch status this outcome dictates."""
        return self.overall_status


def run_validation(
    records: CanonicalRecords,
    config: ValidationConfig,
    context: ValidationContext,
) -> ValidationOutcome:
    outcome = ValidationOutcome()
    for key in _record_keys(records):
        outcome.record_statuses[key] = "accepted"

    for rule in config.rules:
        if not rule.enabled:
            continue
        implementation = _RULES.get(rule.name)
        if implementation is None:
            outcome.findings.append(
                Finding(
                    rule=rule.name,
                    category="STRUCTURAL",
                    severity="WARNING",
                    detail=f"Unknown validation rule {rule.name!r} is configured; skipped.",
                )
            )
            continue
        outcome.findings.extend(implementation(records, rule, context, outcome))

    for finding in outcome.findings:
        if finding.entity_type is None or finding.source_reference is None:
            continue
        key = (finding.entity_type, finding.source_reference)
        current = outcome.record_statuses.get(key, "accepted")
        proposed = _status_for(finding.severity)
        if _STATUS_RANK[proposed] > _STATUS_RANK[current]:
            outcome.record_statuses[key] = proposed

    worst = max(
        (finding.severity for finding in outcome.findings),
        key=lambda severity: _SEVERITY_RANK[severity],
        default="INFO",
    )
    if worst == "BLOCKER":
        outcome.overall_status = "rejected"
        outcome.record_statuses = {key: "blocked" for key in outcome.record_statuses}
    elif any(status != "accepted" for status in outcome.record_statuses.values()) or worst in (
        "WARNING",
        "ERROR",
    ):
        outcome.overall_status = "accepted_with_warnings"
    return outcome


def build_validation_report(
    outcome: ValidationOutcome,
    *,
    records_extracted: int,
    records_translated: int,
) -> dict[str, Any]:
    """The machine-readable report persisted on the batch and shown to operators."""
    statuses = list(outcome.record_statuses.values())
    return {
        "summary": {
            "records_extracted": records_extracted,
            "records_translated": records_translated,
            "records_accepted": statuses.count("accepted"),
            "records_warning": statuses.count("warning"),
            "records_error": statuses.count("error"),
            "records_blocked": statuses.count("blocked"),
            "overall_status": outcome.overall_status.upper(),
        },
        "reconciliation": outcome.reconciliation,
        "failures": [
            {
                "rule": finding.rule,
                "category": finding.category,
                "severity": finding.severity,
                "entity_type": finding.entity_type,
                "source_reference": finding.source_reference,
                "source_locator": finding.source_locator,
                "detail": finding.detail,
            }
            for finding in outcome.findings
        ],
    }


_STATUS_RANK = {"accepted": 0, "warning": 1, "error": 2, "blocked": 3}


def _status_for(severity: Severity) -> str:
    return {"INFO": "accepted", "WARNING": "warning", "ERROR": "error", "BLOCKER": "blocked"}[
        severity
    ]


def _record_keys(records: CanonicalRecords) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    keys.extend(("gl_account", record.source_reference) for record in records.gl_accounts)
    keys.extend(("counterparty", record.source_reference) for record in records.counterparties)
    keys.extend(("product", record.source_reference) for record in records.products)
    keys.extend(("position", record.source_reference) for record in records.positions)
    return keys


def _rule_duplicate_source_references(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    findings: list[Finding] = []
    seen: dict[tuple[str, str], int] = {}
    for key in _record_keys(records):
        seen[key] = seen.get(key, 0) + 1
    for (entity_type, source_reference), count in seen.items():
        if count > 1:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="STRUCTURAL",
                    severity=rule.severity,
                    entity_type=entity_type,
                    source_reference=source_reference,
                    detail=f"source_reference appears {count} times in this batch.",
                )
            )
    return findings


def _rule_unresolved_references(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    products = {record.product_code for record in records.products} | context.known_products
    gl_accounts = {
        record.account_code for record in records.gl_accounts
    } | context.known_gl_accounts
    findings: list[Finding] = []
    for position in records.positions:
        dangling: list[str] = []
        if position.product_code is not None and (
            products and position.product_code not in products
        ):
            dangling.append(f"product {position.product_code!r}")
        if position.gl_account_code is not None and (
            gl_accounts and position.gl_account_code not in gl_accounts
        ):
            dangling.append(f"gl account {position.gl_account_code!r}")
        if dangling:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="STRUCTURAL",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=(
                        f"References not found in this batch or previously "
                        f"ingested data: {', '.join(dangling)}."
                    ),
                )
            )
    return findings


def _rule_unknown_counterparty(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    """Counterparty master gaps are an onboarding reality, so the default
    severity is WARNING: the position still aggregates into the balance
    sheet, while the gap stays visible until the counterparty file lands."""
    known = {
        record.source_reference for record in records.counterparties
    } | context.known_counterparties
    findings: list[Finding] = []
    for position in records.positions:
        reference = position.counterparty_reference
        if reference is not None and known and reference not in known:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="STRUCTURAL",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=(
                        f"Counterparty {reference!r} is not in this batch or "
                        f"previously ingested data."
                    ),
                )
            )
    return findings


def _rule_position_rate_bounds(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    minimum = Decimal(str(rule.params.get("minimum", "0")))
    maximum = Decimal(str(rule.params.get("maximum", "1")))
    findings: list[Finding] = []
    for position in records.positions:
        if position.interest_rate is None:
            continue
        if not minimum <= position.interest_rate <= maximum:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="BUSINESS_RULES",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=(
                        f"interest_rate={position.interest_rate} outside [{minimum}, {maximum}]."
                    ),
                )
            )
    return findings


def _rule_currency_iso_4217(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    extra = {str(code) for code in rule.params.get("additional_currencies", [])}
    allowed = ISO_4217 | extra
    findings: list[Finding] = []
    for position in records.positions:
        if position.currency not in allowed:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="BUSINESS_RULES",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=f"currency {position.currency!r} is not an active ISO 4217 code.",
                )
            )
    return findings


def _rule_maturity_not_before_as_of(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    findings: list[Finding] = []
    for position in records.positions:
        maturity = position.contractual_maturity
        if maturity is not None and maturity < context.as_of_date:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="BUSINESS_RULES",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=(
                        f"contractual_maturity {maturity} is before the "
                        f"as-of date {context.as_of_date}."
                    ),
                )
            )
    return findings


def _rule_gl_subledger_reconciliation(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    tolerance_percent = Decimal(str(rule.params.get("tolerance_percent", "0.1")))
    gl_balances = {
        record.account_code: record.balance
        for record in records.gl_accounts
        if record.balance is not None
    }
    subledger_totals: dict[str, Decimal] = {}
    for position in records.positions:
        code = position.gl_account_code
        if code is None or code not in gl_balances:
            continue
        subledger_totals[code] = subledger_totals.get(code, Decimal(0)) + position.balance

    findings: list[Finding] = []
    accounts_report: dict[str, Any] = {}
    for code, subledger_total in sorted(subledger_totals.items()):
        gl_total = gl_balances[code]
        difference = subledger_total - gl_total
        if gl_total == 0:
            within = difference == 0
            difference_percent = None
        else:
            difference_percent = difference / gl_total * 100
            within = abs(difference_percent) <= tolerance_percent
        accounts_report[code] = {
            "gl_total": str(gl_total),
            "subledger_total": str(subledger_total),
            "difference": str(difference),
            "difference_percent": (
                str(difference_percent) if difference_percent is not None else None
            ),
            "within_tolerance": within,
        }
        if not within:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="BALANCE_RECONCILIATION",
                    severity=rule.severity,
                    entity_type="gl_account",
                    source_reference=code,
                    detail=(
                        f"GL {code}: sub-ledger total {subledger_total} differs from GL "
                        f"balance {gl_total} by {difference} "
                        f"(tolerance {tolerance_percent}%)."
                    ),
                )
            )
    if accounts_report:
        outcome.reconciliation["gl_vs_subledger"] = accounts_report
    return findings


def _rule_unusual_balance_change(
    records: CanonicalRecords,
    rule: RuleConfig,
    context: ValidationContext,
    outcome: ValidationOutcome,
) -> list[Finding]:
    if not context.prior_balances:
        return []
    threshold = Decimal(str(rule.params.get("threshold", "0.4")))
    findings: list[Finding] = []
    for position in records.positions:
        prior = context.prior_balances.get(position.source_reference)
        if prior is None or prior == 0:
            continue
        change = abs(position.balance - prior) / abs(prior)
        if change > threshold:
            findings.append(
                Finding(
                    rule=rule.name,
                    category="TEMPORAL",
                    severity=rule.severity,
                    entity_type="position",
                    source_reference=position.source_reference,
                    source_locator=position.source_locator,
                    detail=(
                        f"balance moved from {prior} to {position.balance} "
                        f"({change:.2%}), above the {threshold:.0%} review threshold."
                    ),
                )
            )
    return findings


_RULES = {
    "structural_duplicate_source_references": _rule_duplicate_source_references,
    "structural_unknown_counterparty": _rule_unknown_counterparty,
    "structural_unresolved_references": _rule_unresolved_references,
    "position_rate_bounds": _rule_position_rate_bounds,
    "currency_iso_4217": _rule_currency_iso_4217,
    "maturity_not_before_as_of": _rule_maturity_not_before_as_of,
    "gl_subledger_reconciliation": _rule_gl_subledger_reconciliation,
    "unusual_balance_change": _rule_unusual_balance_change,
}

RULE_NAMES = tuple(sorted(_RULES))
