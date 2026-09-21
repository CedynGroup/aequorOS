"""Validating an ICAAP filing: the rules that are about the report, not the data.

The generic validator checks that a snapshot is well formed. These rules check
that the FILING is right — that every section the regulator's structure names is
present and says something, that the checklist has been answered, that the
figures are as at the date being reported, that the stress annex is the current
one, and that the stress work meets the governed minima.

Two things separate these from generation notes. They may be ``ERROR``, because
an ICAAP whose Appendix II annex is stale is not "validated with a warning", it
is wrong. And **every number they measure against is read from the snapshot's
own parameter provenance** (D-024) — never a literal here, and never a live
lookup either: the report was measured against the governed values in force
when it was frozen, and a console change afterwards must not silently re-grade
a filed document.

Rules that depend on an annex run only when the framework declared one, so a
jurisdiction without Appendix II is validated honestly rather than failed for
missing something it never had.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.icaap.pillar2 import irrbb as irrbb_interim
from app.domain.icaap.pillar2 import irrbb_sf_method as irrbb_sf
from app.domain.icaap.reconciliation import PARAM_SOURCE_TOLERANCE
from app.domain.icaap.units import HUNDRED, ZERO, amount, ratio
from app.models import RegulatoryPackage
from app.services.regulatory_irr_sf import CODE_MANDATORY_FROM as PARAM_SF_MANDATORY_FROM

RULE_VERSION = "icaap-validation-v1"

METHOD_SF = irrbb_sf.METHOD
#: The two methods that may carry interest rate risk in the banking book. Named
#: rather than matched by prefix so a future third method has to be added here
#: deliberately instead of being swept in by its name.
_IRRBB_METHODS: frozenset[str] = frozenset({METHOD_SF, irrbb_interim.METHOD})

#: How many item ids a finding names before it stops being readable. Editorial.
_NAMED_IN_FINDING = 20

_ERROR = "ERROR"
_WARNING = "WARNING"
_INFO = "INFO"


def _finding(rule: str, severity: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "severity": severity, "detail": detail}


def _icaap(package: RegulatoryPackage) -> dict[str, Any]:
    metadata = package.snapshot.get("metadata") or {}
    block = metadata.get("icaap")
    return block if isinstance(block, dict) else {}


def _entries(block: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [entry for entry in (block.get(key) or []) if isinstance(entry, dict)]


def _parameter(block: dict[str, Any], code: str) -> dict[str, Any] | None:
    for entry in _entries(block, "parameters"):
        if entry.get("param_code") == code:
            return entry
    return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _facts(block: dict[str, Any], block_type: str) -> dict[str, Any]:
    for entry in _entries(block, "blocks"):
        if entry.get("block_type") == block_type:
            facts = entry.get("facts")
            return facts if isinstance(facts, dict) else {}
    return {}


def _fact_value(facts: dict[str, Any], key: str) -> Any:
    raw = facts.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _bound(block: dict[str, Any], block_type: str) -> dict[str, Any] | None:
    for entry in _entries(block, "blocks"):
        if entry.get("block_type") == block_type and entry.get("seq") is not None:
            return entry
    return None


def _requirement_states(block: dict[str, Any]) -> list[tuple[str, str, str, str | None]]:
    out: list[tuple[str, str, str, str | None]] = []
    for section in _entries(block, "sections"):
        for item in section.get("requirements") or []:
            if not isinstance(item, dict):
                continue
            out.append(
                (
                    str(section.get("key")),
                    str(item.get("item_id")),
                    str(item.get("status")),
                    item.get("reason"),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _sections_committed(block: dict[str, Any]) -> list[dict[str, str]]:
    missing = [
        str(section.get("key"))
        for section in _entries(block, "sections")
        if section.get("version_no") is None or not section.get("doc_sha256")
    ]
    if not missing:
        return []
    return [
        _finding(
            "icaap.sections_committed",
            _ERROR,
            f"These sections of the report have no committed text: {', '.join(sorted(missing))}.",
        )
    ]


def _requirements(block: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    open_items = [
        f"{section}/{item}"
        for section, item, status, _reason in _requirement_states(block)
        if status == "open"
    ]
    if open_items:
        findings.append(
            _finding(
                "icaap.requirements",
                _ERROR,
                "These checklist items were never answered: "
                f"{', '.join(sorted(open_items)[:_NAMED_IN_FINDING])}.",
            )
        )
    unexplained = [
        f"{section}/{item}"
        for section, item, status, reason in _requirement_states(block)
        if status == "not_applicable" and not (reason or "").strip()
    ]
    if unexplained:
        findings.append(
            _finding(
                "icaap.requirements",
                _ERROR,
                "These checklist items are marked not applicable with no reason: "
                f"{', '.join(sorted(unexplained)[:_NAMED_IN_FINDING])}.",
            )
        )
    pending = [
        str(section.get("key"))
        for section in _entries(block, "sections")
        if section.get("source_status") == "pending_primary_text"
    ]
    if pending and (block.get("cycle") or {}).get("kind") != "rehearsal":
        findings.append(
            _finding(
                "icaap.requirements",
                _ERROR,
                "These sections are built on a framework whose primary text has not "
                f"been read: {', '.join(sorted(pending))}. The checklist behind them "
                "is incomplete.",
            )
        )
    return findings


def _freeze_attachments(block: dict[str, Any]) -> list[dict[str, str]]:
    present: dict[str, int] = {}
    attributes: dict[str, list[dict[str, Any]]] = {}
    for entry in _entries(block, "attachments"):
        kind = str(entry.get("kind"))
        present[kind] = present.get(kind, 0) + 1
        attributes.setdefault(kind, []).append(entry.get("attributes") or {})
    findings: list[dict[str, str]] = []
    for requirement in _entries(block, "attachment_requirements"):
        if requirement.get("gate") != "freeze" or not requirement.get("applies", True):
            continue
        kind = str(requirement.get("kind"))
        minimum = int(requirement.get("min_count") or 0)
        if present.get(kind, 0) < minimum:
            findings.append(
                _finding(
                    "icaap.freeze_attachments",
                    _ERROR,
                    f"{requirement.get('title') or kind} must accompany this report and "
                    f"is missing ({present.get(kind, 0)} of {minimum}).",
                )
            )
    # REG-ICAAP-071b: a senior management report has to say which functions
    # commented on it, or the reader cannot tell whose view it records.
    for body in attributes.get("senior_management_report", []):
        functions = body.get("commenting_functions")
        if isinstance(functions, list) and functions:
            break
    else:
        if present.get("senior_management_report"):
            findings.append(
                _finding(
                    "icaap.freeze_attachments",
                    _ERROR,
                    "The senior management report does not record which functions "
                    "commented on the ICAAP.",
                )
            )
    return findings


def _as_of(block: dict[str, Any], package: RegulatoryPackage) -> list[dict[str, str]]:
    reporting_date = package.reporting_date.isoformat()
    findings: list[dict[str, str]] = []
    # The absence of the capital position is checked FIRST, because every rule
    # below iterates only over blocks that exist. "The capital block's as-of
    # equals the reporting date" was unenforced when there was no capital block,
    # so a report with no capital figures at all validated more cleanly than one
    # whose figures were a month old (independent audit F2). The freeze gate now
    # refuses that cycle; this refuses the PACKAGE, because a package can also
    # arrive here from a rehearsal, a clone or a family hook, and an ERROR is
    # what stops it being submitted for approval.
    if _bound(block, "capital_position") is None:
        findings.append(
            _finding(
                "icaap.as_of",
                _ERROR,
                "This report states no capital position. An ICAAP report without the "
                "institution's capital adequacy figures cannot be filed.",
            )
        )
    for entry in _entries(block, "blocks"):
        source_as_of = entry.get("source_as_of")
        if entry.get("seq") is None or source_as_of is None:
            continue
        if source_as_of == reporting_date:
            continue
        title = entry.get("title") or entry.get("block_key")
        if entry.get("pin_reason"):
            findings.append(
                _finding(
                    "icaap.as_of",
                    _WARNING,
                    f"{title} is deliberately kept at figures as at {source_as_of} "
                    f"rather than {reporting_date}: {entry['pin_reason']}",
                )
            )
        elif entry.get("block_type") == "capital_position":
            findings.append(
                _finding(
                    "icaap.as_of",
                    _ERROR,
                    f"The capital position in this report is as at {source_as_of}, not "
                    f"the reporting date {reporting_date}.",
                )
            )
        else:
            findings.append(
                _finding(
                    "icaap.as_of",
                    _WARNING,
                    f"{title} shows figures as at {source_as_of} rather than {reporting_date}.",
                )
            )
    return findings


def _annex_current(
    db: Session, block: dict[str, Any], package: RegulatoryPackage
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for annex in _entries(block, "annexes"):
        code = str(annex.get("return_code"))
        current = db.scalar(
            select(RegulatoryPackage)
            .where(
                RegulatoryPackage.organization_id == package.organization_id,
                RegulatoryPackage.bank_id == package.bank_id,
                RegulatoryPackage.return_code == code,
                RegulatoryPackage.reporting_date == package.reporting_date,
                RegulatoryPackage.basis == package.basis,
                RegulatoryPackage.status != "superseded",
            )
            .order_by(RegulatoryPackage.version.desc())
            .limit(1)
        )
        if current is None:
            findings.append(
                _finding(
                    "icaap.annex_current",
                    _ERROR,
                    f"The {code} annex this report carries has been superseded or "
                    "withdrawn and no current version exists.",
                )
            )
        elif current.content_digest != annex.get("content_digest"):
            findings.append(
                _finding(
                    "icaap.annex_current",
                    _ERROR,
                    f"The {code} annex in this report is not the current version "
                    f"(version {current.version} has different content).",
                )
            )
    return findings


def _pillar2_reconciliation(block: dict[str, Any]) -> list[dict[str, str]]:
    entry = _bound(block, "capital_reconciliation")
    if entry is None:
        # Was ``return []`` — an absent reconciliation said nothing while a
        # reconciliation with no coverage figure was an ERROR (independent audit
        # F2). Reconciling internal capital to regulatory capital is what an
        # ICAAP IS; a report that omits it is not a milder version of one that
        # got it wrong.
        return [
            _finding(
                "icaap.pillar2_reconciliation",
                _ERROR,
                "This report carries no reconciliation of internal capital to "
                "regulatory capital.",
            )
        ]
    facts = entry.get("facts") if isinstance(entry.get("facts"), dict) else {}
    coverage = _decimal(_fact_value(facts or {}, "internal_capital_coverage_pct"))
    if coverage is None:
        return [
            _finding(
                "icaap.pillar2_reconciliation",
                _ERROR,
                "The internal-to-regulatory capital reconciliation in this report has "
                "no computed coverage figure.",
            )
        ]
    return []


def _stress_rules(block: dict[str, Any]) -> list[dict[str, str]]:
    """The Appendix II rules. They run only when the framework declares the annex."""
    if not _entries(block, "annexes"):
        return []
    findings: list[dict[str, str]] = []
    appendix = _bound(block, "appendix_ii")
    facts = appendix.get("facts") if appendix and isinstance(appendix.get("facts"), dict) else {}
    facts = facts or {}

    horizon_param = _parameter(block, "icaap_stress_horizon_years_min")
    horizon_min = _decimal(horizon_param.get("value")) if horizon_param else None
    horizon = _decimal(_fact_value(facts, "horizon_years"))
    if horizon_min is None:
        findings.append(
            _finding(
                "icaap.stress.horizon",
                _ERROR,
                "The minimum stress-testing horizon is a governed parameter with no "
                "approved row behind this report, so the horizon cannot be checked.",
            )
        )
    elif horizon is not None and horizon < horizon_min:
        findings.append(
            _finding(
                "icaap.stress.horizon",
                _ERROR,
                f"The stress test covers {horizon} years against a required minimum of "
                f"{horizon_min}.",
            )
        )

    severe_param = _parameter(block, "icaap_stress_severe_scenarios_min")
    severe_min = _decimal(severe_param.get("value")) if severe_param else None
    severe = _decimal(_fact_value(facts, "severe_scenario_count"))
    if severe_min is not None and severe is not None and severe < severe_min:
        findings.append(
            _finding(
                "icaap.stress.severe_scenario",
                _ERROR,
                f"The stress test runs {severe} severe scenarios against a required "
                f"minimum of {severe_min}.",
            )
        )

    if appendix is not None:
        with_actions = _fact_value(facts, "with_management_actions")
        if with_actions is None:
            findings.append(
                _finding(
                    "icaap.stress.management_actions",
                    _ERROR,
                    "The stress results do not say whether management actions were "
                    "modelled (¶67(f)).",
                )
            )
    if _bound(block, "reverse_stress") is None and not _waived(block, "reverse_stress"):
        findings.append(
            _finding(
                "icaap.stress.reverse_stress",
                _ERROR,
                "No reverse stress test is attached to this report and none of its "
                "checklist items records why (¶36).",
            )
        )
    narratives = _bound(block, "stress_narratives")
    if narratives is not None:
        narrative_facts = narratives.get("facts")
        attested = _fact_value(
            narrative_facts if isinstance(narrative_facts, dict) else {}, "attested_on"
        )
        if not attested:
            findings.append(
                _finding(
                    "icaap.stress.board_attestation",
                    _ERROR,
                    "The stress narratives in this report come from a sign-off the "
                    "Board has not attested (¶20).",
                )
            )
    return findings


def _waived(block: dict[str, Any], token: str) -> bool:
    """True when a checklist item mentioning ``token`` was explicitly waived."""
    return any(
        status == "not_applicable" and (reason or "").strip() and token in item
        for _section, item, status, reason in _requirement_states(block)
    )


def _pillar2_items(block: dict[str, Any]) -> list[dict[str, Any]]:
    """The Pillar 2 register rows the frozen report carries."""
    entry = _bound(block, "pillar2_summary")
    payload = entry.get("payload") if entry is not None else None
    raw = payload.get("raw") if isinstance(payload, dict) else None
    register = raw.get("register") if isinstance(raw, dict) else None
    items = register.get("items") if isinstance(register, dict) else None
    if not isinstance(items, list):
        return []
    return [row for row in items if isinstance(row, dict)]


def _governed_date(block: dict[str, Any], code: str) -> date | None:
    """A governed commencement date as the REPORT recorded it (D-024).

    Read from the snapshot's own parameter provenance, never re-resolved: the
    report was measured against the rows in force when it was frozen, and a
    console change afterwards must not silently re-grade a filed document.
    """
    entry = _parameter(block, code)
    if entry is None or not entry.get("resolved"):
        return None
    raw = (entry.get("value_json") or {}).get("date")
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _irrbb_sf_rules(  # noqa: PLR0912 - one branch per named rule, deliberately flat
    block: dict[str, Any], package: RegulatoryPackage
) -> list[dict[str, str]]:
    """The standardised framework's rules (P5-DESIGN §1.6 item 5).

    Four questions, all answered from the report's own contents:

    * from the governed commencement date the framework recorded, is the IRRBB
      Pillar 2 figure the framework's?
    * does the figure the register capitalises equal the figure the bound block
      shows? A report whose narrative table and whose capital line disagree is
      wrong whichever one is right.
    * does the framework's Tier 1 denominator agree with the capital position's,
      within the governed tolerance? A warning: the two are computed at
      different moments and a small difference is not a defect.
    * how much of the result rests on a modelling default or a representative
      calibration? Not a fault — a disclosure, stated in the filed document so
      a reader never has to go looking for it.

    Every rule is silent when the row it would measure against is absent. A
    report that carries no governed commencement date is not graded against an
    invented one (D-024).
    """
    findings: list[dict[str, str]] = []
    facts = _facts(block, "irrbb_sf")
    bound = _bound(block, "irrbb_sf")
    items = _pillar2_items(block)
    irrbb_items = [row for row in items if row.get("method") in _IRRBB_METHODS]
    mandatory_from = _governed_date(block, PARAM_SF_MANDATORY_FROM)
    mandated = mandatory_from is not None and package.reporting_date >= mandatory_from

    if mandated and irrbb_items:
        interim = [row for row in irrbb_items if row.get("method") != METHOD_SF]
        if interim:
            findings.append(
                _finding(
                    "icaap.irrbb_sf.method",
                    _ERROR,
                    "Interest rate risk in the banking book is capitalised from the "
                    f"interim method, which the standardised framework superseded on "
                    f"{mandatory_from.isoformat() if mandatory_from else ''}.",
                )
            )
    sf_items = [row for row in irrbb_items if row.get("method") == METHOD_SF]
    if sf_items and bound is None:
        findings.append(
            _finding(
                "icaap.irrbb_sf.source",
                _ERROR,
                "The interest rate risk capital in this report comes from the "
                "standardised framework, but the report carries no standardised "
                "framework result to read it from.",
            )
        )
    measure = _decimal(_fact_value(facts, "eve_risk_measure"))
    for row in sf_items:
        baseline = _decimal(row.get("baseline"))
        # The register carries the canonical Pillar 2 unit and the run carries
        # the engine's own precision, so they are compared in the unit the
        # register states (D-062) rather than digit for digit.
        if measure is None or baseline is None or amount(baseline) == amount(measure):
            continue
        findings.append(
            _finding(
                "icaap.irrbb_sf.source",
                _ERROR,
                f"The interest rate risk capital in this report is {baseline}, while the "
                f"standardised framework result it cites reports {measure}.",
            )
        )
    if bound is not None:
        findings.extend(_irrbb_sf_tier1(block, facts))
        applied = _decimal(_fact_value(facts, "assumption_defaults_applied"))
        if applied is not None and applied > 0:
            findings.append(
                _finding(
                    "icaap.irrbb_sf.assumptions",
                    _INFO,
                    f"The standardised framework applied modelling defaults to {applied} "
                    "positions whose terms the book did not state. They are listed with "
                    "the result.",
                )
            )
        representative = _decimal(_fact_value(facts, "representative_parameters"))
        if representative is not None and representative > 0:
            findings.append(
                _finding(
                    "icaap.irrbb_sf.assumptions",
                    _WARNING,
                    f"{representative} of the figures behind the standardised framework "
                    "result are representative calibrations rather than published "
                    "supervisory benchmarks.",
                )
            )
    return findings


def _irrbb_sf_tier1(block: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, str]]:
    """Does the framework's Tier 1 denominator agree with the capital position?"""
    sf_tier1 = _decimal(_fact_value(facts, "tier1"))
    capital = _facts(block, "capital_position")
    ratio_pct = _decimal(_fact_value(capital, "tier1_ratio_pct"))
    total_rwa = _decimal(_fact_value(capital, "total_rwa"))
    tolerance = _parameter(block, PARAM_SOURCE_TOLERANCE)
    tolerance_pct = _decimal(tolerance.get("value")) if tolerance else None
    if sf_tier1 is None or ratio_pct is None or total_rwa is None or tolerance_pct is None:
        return []
    implied = ratio_pct * total_rwa / HUNDRED
    scale = max(abs(implied), abs(sf_tier1))
    if scale == ZERO:
        return []
    difference = ratio(abs(implied - sf_tier1) * HUNDRED / scale)
    if difference <= tolerance_pct:
        return []
    return [
        _finding(
            "icaap.irrbb_sf.tier1",
            _WARNING,
            "The Tier 1 capital the standardised framework measured against differs from "
            "the capital position in this report by more than the tolerance this ICAAP "
            f"applies ({difference}% against {tolerance_pct}%).",
        )
    ]


def _framework_and_parameters(block: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    framework = block.get("framework") or {}
    if framework.get("exposure_draft"):
        findings.append(
            _finding(
                "icaap.exposure_draft",
                _INFO,
                f"{framework.get('short_title') or framework.get('code')} is an exposure "
                "draft; the regulator may change the text before it is final.",
            )
        )
    pending = sorted(
        str(entry.get("param_code"))
        for entry in _entries(block, "parameters")
        if entry.get("confirmation_status") == "pending"
    )
    if pending:
        findings.append(
            _finding(
                "icaap.parameters_pending",
                _WARNING,
                "These governed figures are still awaiting stakeholder confirmation: "
                f"{', '.join(pending)}.",
            )
        )
    unresolved = sorted(
        str(entry.get("param_code"))
        for entry in _entries(block, "parameters")
        if not entry.get("resolved")
    )
    if unresolved:
        findings.append(
            _finding(
                "icaap.parameters_pending",
                _ERROR,
                "These governed figures the framework depends on had no approved row "
                f"when this report was frozen: {', '.join(unresolved)}.",
            )
        )
    return findings


def _framework_changed(block: dict[str, Any]) -> list[dict[str, str]]:
    from app.domain.icaap.frameworks import registry  # noqa: PLC0415 - avoid an import cycle

    framework = block.get("framework") or {}
    code, version, digest = framework.get("code"), framework.get("version"), framework.get("digest")
    if not code or not version:
        return []
    try:
        published = registry.get(str(code), str(version))
    except registry.FrameworkNotFound:
        return [
            _finding(
                "icaap.framework_changed",
                _WARNING,
                f"Framework {code} {version} is no longer published. The report remains "
                "authoritative for what it says.",
            )
        ]
    if published.digest != digest:
        return [
            _finding(
                "icaap.framework_changed",
                _WARNING,
                f"Framework {code} {version} has changed since this report was frozen. "
                "The frozen report remains authoritative.",
            )
        ]
    return []


def findings(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    """Every ICAAP rule, in a stable order."""
    block = _icaap(package)
    if not block:
        # A package of this family with no ICAAP block did not come from a
        # freeze. Saying so is the honest finding; validating nothing is not.
        return [
            _finding(
                "icaap.sections_committed",
                _ERROR,
                "This package carries no ICAAP assessment. An ICAAP report exists only "
                "because a workspace cycle was frozen.",
            )
        ]
    return [
        *_sections_committed(block),
        *_requirements(block),
        *_freeze_attachments(block),
        *_as_of(block, package),
        *_annex_current(db, block, package),
        *_pillar2_reconciliation(block),
        *_irrbb_sf_rules(block, package),
        *_stress_rules(block),
        *_framework_and_parameters(block),
        *_framework_changed(block),
    ]


__all__ = ["RULE_VERSION", "findings"]
