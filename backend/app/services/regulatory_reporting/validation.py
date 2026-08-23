"""Package validation pipeline (docs/regulatory_reporting.md §5, ``validation.py``).

Three deterministic rules run over the generated snapshot:

1. **Completeness** — every declared (non-optional) section carries rows and
   the snapshot header fields are present. The headline ``totals`` block is one
   of those fields *unless* the snapshot's own authority record declares an
   official template as the authority for the return's derived figures, in
   which case the roll-ups are the template's own cells and a generated totals
   section would be arithmetic the regulator never published
   (``_template_authoritative_rollups``).
2. **Internal consistency** — every section total that declares
   ``equals_sum_of_rows`` cross-foots exactly against its row values.
3. **Prior-period movement** — headline ``totals`` are compared against the
   latest submitted/acknowledged package of the same return at an earlier
   reporting date; swings above 25% are flagged as WARNING, and so is a
   movement out of zero (which has no percentage form). Totals the test could
   not cover — no counterpart in the prior package, or a non-numeric value —
   are listed by name, and the clean-run finding states how many totals were
   actually compared rather than issuing a blanket all-clear (P0-14).

Additionally, generators may record advisory notes at generation time in
``snapshot.metadata.generation_findings`` (for example the Large Exposures
top-100 truncation); the pipeline folds them into the report capped at
INFO/WARNING severity — generator notes can never block validation.

Each finding is ``{rule, severity, detail}`` with severity INFO/WARNING/ERROR.
The report is persisted onto ``validation_report``; a clean run (no ERROR)
flips ``generated -> validated``, otherwise the package stays (or returns to)
``generated`` with the errors listed. ERROR findings block approval requests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage
from app.schemas.regulatory_reporting import RegulatoryPackageRead
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_package_or_404,
    read_package,
)
from app.services.regulatory_reporting.provenance import ReportAuthority

RULE_VERSION = "regulatory-package-validation-v1.3.0"
COMPLETENESS_RULE = "package.sections_complete"
CONSISTENCY_RULE = "package.totals_consistent"
MOVEMENT_RULE = "package.prior_period_movement"
GENERATION_NOTES_RULE = "package.generation_notes"
_GENERATION_NOTE_SEVERITIES = ("INFO", "WARNING")
MOVEMENT_THRESHOLD_PCT = Decimal("25")
_MOVEMENT_STATUSES = ("submitted", "acknowledged")
_VALIDATABLE_STATUSES = ("generated", "validated")
_CONSISTENCY_TOLERANCE = Decimal("0.0001")
_HUNDRED = Decimal("100")

#: Snapshot blocks every package must carry, whatever generator produced it.
_REQUIRED_BLOCKS = ("reporting_date", "institution", "sections")
#: The headline roll-up block. Conditionally required — see
#: ``_template_authoritative_rollups``.
_TOTALS_BLOCK = "totals"
_TEMPLATE_AUTHORITY = ReportAuthority.TEMPLATE_FORMULA.value


def _finding(rule: str, severity: str, detail: str) -> dict[str, str]:
    return {"rule": rule, "severity": severity, "detail": detail}


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _template_authoritative_rollups(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """The snapshot's own declaration that an official template owns its roll-ups.

    Returns the authority record when the snapshot declares it, ``None``
    otherwise. This is the discriminator the completeness rule uses to tell two
    genuinely different things apart:

    * a return whose roll-ups ARE the official template's own formula cells, so
      a separate ``totals`` section would be arithmetic the regulator never
      published — and writing one would break the framework rule that a BoG
      line is never re-implemented or simplified; and
    * a return that should carry headline totals and does not, which is a real
      defect and must keep ERRORing.

    Why the declaration and not the generator name. ``run_validation_rules`` is
    a pure function of one stored snapshot, and that is load-bearing: packages
    are immutable and re-validatable years after they were minted, and
    ``_movement_findings`` reads the *prior* package's snapshot directly, with
    no generator anywhere in reach. Keying the control on
    ``registry.get_definition(package.return_code).generator == "bog_form"``
    would make a sealed package's verdict depend on today's registry — a
    retired code, a renamed generator or a second template-driven generator for
    another jurisdiction would each silently re-arm this exact blocker. The
    snapshot must therefore carry its own evidence, which it already does:
    ``_stamp_provenance`` writes an authority record onto EVERY package, in one
    place, for every family, and ``bog_forms`` declares
    ``ReportAuthority.TEMPLATE_FORMULA`` there through
    ``build_template_provenance``. Reading it adds no field, no schema version
    and no migration, and it makes the reason legible in the sealed JSON an
    examiner reads rather than only in code.

    The declaration is corroborated rather than believed: a bare authority
    string is not enough, the record must also name the committed template it
    is claiming authority for (``template_hash``, the SHA-256 of the layout
    under ``bog_forms/layouts/``). Deliberately NOT corroborated with
    ``formula_cells_evaluated > 0``: six of the official BSD layouts (BSD11,
    BSD14, BSD15A, BSD15B, BSD17, BSD2A) carry no formula cell at all — they
    are published as blank grids — and a return whose official form declares no
    roll-up of its own must not be told to invent one.
    """
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        return None
    if str(provenance.get("authority") or "") != _TEMPLATE_AUTHORITY:
        return None
    if not str(provenance.get("template_hash") or "").strip():
        return None
    return provenance


def _template_rollup_detail(provenance: dict[str, Any]) -> str:
    """The INFO line that records why this snapshot carries no ``totals``.

    The control is not skipped in silence: the report states which official
    workbook stands in place of the totals block, and how much of it the
    formula engine actually evaluated.
    """
    workbook = str(
        provenance.get("official_workbook") or provenance.get("template_id") or "unnamed"
    )
    digest = str(provenance.get("template_hash") or "")
    counted = provenance.get("formula_cells_evaluated")
    evaluated = (
        f" {counted} of the template's own formula cells were evaluated for this return."
        if isinstance(counted, int)
        else ""
    )
    return (
        f"No separate 'totals' block is required: the snapshot's authority record names the "
        f"official workbook '{workbook}' (template digest {digest[:12]}) as the authority for "
        f"every derived figure on this return, so its roll-ups are the template's own cells "
        f"and a generated totals section would be a roll-up the regulator never published."
        f"{evaluated}"
    )


def _completeness_findings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for field in _REQUIRED_BLOCKS:
        if not snapshot.get(field):
            findings.append(
                _finding(
                    COMPLETENESS_RULE,
                    "ERROR",
                    f"The snapshot is missing its '{field}' block.",
                )
            )
    if not snapshot.get(_TOTALS_BLOCK):
        template = _template_authoritative_rollups(snapshot)
        findings.append(
            _finding(COMPLETENESS_RULE, "INFO", _template_rollup_detail(template))
            if template is not None
            else _finding(
                COMPLETENESS_RULE,
                "ERROR",
                "The snapshot is missing its 'totals' block. Headline totals are what the "
                "prior-period movement rule compares across reporting dates; a snapshot may "
                "omit them only by declaring in its provenance block that an official "
                f"template owns this return's roll-ups (authority '{_TEMPLATE_AUTHORITY}' "
                "naming the template digest it evaluated).",
            )
        )
    sections = snapshot.get("sections") or []
    populated = 0
    for section in sections:
        if section.get("rows"):
            populated += 1
            continue
        severity = "INFO" if section.get("optional") else "ERROR"
        qualifier = "optional " if section.get("optional") else ""
        findings.append(
            _finding(
                COMPLETENESS_RULE,
                severity,
                f"The {qualifier}section '{section.get('code')}' has no rows.",
            )
        )
    if not any(finding["severity"] == "ERROR" for finding in findings):
        findings.append(
            _finding(
                COMPLETENESS_RULE,
                "INFO",
                f"All required sections contain data ({populated} of {len(sections)} "
                "sections populated).",
            )
        )
    return findings


def _consistency_findings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    declared = 0
    for section in snapshot.get("sections") or []:
        total = section.get("total")
        if not total or not total.get("equals_sum_of_rows"):
            continue
        declared += 1
        total_value = _decimal_or_none(total.get("value"))
        row_values = [_decimal_or_none(row.get("value")) for row in section.get("rows") or []]
        if total_value is None or any(value is None for value in row_values):
            findings.append(
                _finding(
                    CONSISTENCY_RULE,
                    "ERROR",
                    f"Section '{section.get('code')}' declares a cross-foot total but "
                    "carries non-numeric values.",
                )
            )
            continue
        row_sum = sum((value for value in row_values if value is not None), Decimal("0"))
        if abs(row_sum - total_value) > _CONSISTENCY_TOLERANCE:
            findings.append(
                _finding(
                    CONSISTENCY_RULE,
                    "ERROR",
                    f"Section '{section.get('code')}' total {total_value} does not equal "
                    f"the sum of its rows {row_sum}.",
                )
            )
    if not any(finding["severity"] == "ERROR" for finding in findings):
        findings.append(
            _finding(
                CONSISTENCY_RULE,
                "INFO",
                f"All {declared} declared section totals cross-foot against their rows.",
            )
        )
    return findings


def _prior_package(db: Session, package: RegulatoryPackage) -> RegulatoryPackage | None:
    return db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == package.organization_id,
            RegulatoryPackage.bank_id == package.bank_id,
            RegulatoryPackage.return_code == package.return_code,
            RegulatoryPackage.reporting_date < package.reporting_date,
            RegulatoryPackage.status.in_(_MOVEMENT_STATUSES),
        )
        .order_by(
            RegulatoryPackage.reporting_date.desc(),
            RegulatoryPackage.version.desc(),
        )
        .limit(1)
    )


def _movement_findings(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    prior = _prior_package(db, package)
    if prior is None:
        return [
            _finding(
                MOVEMENT_RULE,
                "INFO",
                "No prior submitted or acknowledged package of this return exists; "
                "the movement check has nothing to compare against.",
            )
        ]
    prior_totals = {
        row.get("code"): _decimal_or_none(row.get("value"))
        for row in prior.snapshot.get("totals") or []
    }
    findings: list[dict[str, str]] = []
    compared = 0
    # Totals the percentage test cannot express, tracked so the clean-run
    # statement can be honest about what it did and did not cover. P0-14: the
    # rule used to `continue` past every one of these and then assert that "no
    # headline total moved more than 25%" — writing a false all-clear into the
    # record an examiner reads. A movement out of zero is the most material
    # movement a return can show; it must never be the one that goes unsaid.
    no_prior: list[str] = []
    non_numeric: list[str] = []
    for row in package.snapshot.get("totals") or []:
        code = str(row.get("code"))
        current = _decimal_or_none(row.get("value"))
        previous = prior_totals.get(code)
        if current is None:
            non_numeric.append(code)
            continue
        if code not in prior_totals or previous is None:
            no_prior.append(code)
            continue
        if previous == 0:
            # 0 -> X has no finite percentage. Report the movement itself at the
            # same severity a >25% swing gets, because that is what it is; only
            # 0 -> 0 is genuinely no movement.
            if current != 0:
                findings.append(
                    _finding(
                        MOVEMENT_RULE,
                        "WARNING",
                        f"'{code}' moved from zero to {current} versus the "
                        f"{prior.reporting_date.isoformat()} package. A movement out of zero "
                        "has no percentage form and is not covered by the "
                        f"{MOVEMENT_THRESHOLD_PCT}% test; it needs explanation.",
                    )
                )
            else:
                compared += 1
            continue
        compared += 1
        movement_pct = abs((current - previous) / previous) * _HUNDRED
        if movement_pct > MOVEMENT_THRESHOLD_PCT:
            findings.append(
                _finding(
                    MOVEMENT_RULE,
                    "WARNING",
                    f"'{code}' moved {movement_pct.quantize(Decimal('0.01'))}% versus the "
                    f"{prior.reporting_date.isoformat()} package "
                    f"({previous} -> {current}); movements above "
                    f"{MOVEMENT_THRESHOLD_PCT}% need explanation.",
                )
            )
    if no_prior:
        findings.append(
            _finding(
                MOVEMENT_RULE,
                "INFO",
                f"{len(no_prior)} headline total(s) have no counterpart in the "
                f"{prior.reporting_date.isoformat()} package and were not compared: "
                f"{', '.join(sorted(no_prior))}.",
            )
        )
    if non_numeric:
        findings.append(
            _finding(
                MOVEMENT_RULE,
                "INFO",
                f"{len(non_numeric)} headline total(s) carry no numeric value and were not "
                f"compared: {', '.join(sorted(non_numeric))}.",
            )
        )
    if not any(finding["severity"] == "WARNING" for finding in findings):
        # Says exactly what was tested. The old wording claimed a clean bill of
        # health over totals it had skipped.
        findings.append(
            _finding(
                MOVEMENT_RULE,
                "INFO",
                f"{compared} headline total(s) were compared against the "
                f"{prior.reporting_date.isoformat()} package and none moved more than "
                f"{MOVEMENT_THRESHOLD_PCT}%."
                + (
                    f" {len(no_prior) + len(non_numeric)} further total(s) could not be "
                    "compared and are listed above."
                    if (no_prior or non_numeric)
                    else ""
                ),
            )
        )
    return findings


def _generation_note_findings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """Fold generator-recorded advisory notes into the report (INFO/WARNING only)."""
    metadata = snapshot.get("metadata") or {}
    raw_findings = metadata.get("generation_findings") or []
    findings: list[dict[str, str]] = []
    for entry in raw_findings:
        if not isinstance(entry, dict):
            continue
        detail = str(entry.get("detail") or "").strip()
        if not detail:
            continue
        severity = str(entry.get("severity") or "INFO").upper()
        if severity not in _GENERATION_NOTE_SEVERITIES:
            severity = "INFO"
        rule = str(entry.get("rule") or GENERATION_NOTES_RULE)
        findings.append(_finding(rule, severity, detail))
    return findings


def run_validation_rules(db: Session, package: RegulatoryPackage) -> list[dict[str, str]]:
    """Pure rule pipeline over one package snapshot; returns ordered findings."""
    return [
        *_completeness_findings(package.snapshot),
        *_consistency_findings(package.snapshot),
        *_movement_findings(db, package),
        *_generation_note_findings(package.snapshot),
    ]


def validate_package(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> RegulatoryPackageRead:
    get_bank_or_404(db, ctx, bank_id)
    package = get_package_or_404(db, ctx, bank_id, package_id)
    if package.status not in _VALIDATABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only generated or validated packages can be validated; this package "
                f"is '{package.status}'."
            ),
        )

    findings = run_validation_rules(db, package)
    error_count = sum(1 for finding in findings if finding["severity"] == "ERROR")
    warning_count = sum(1 for finding in findings if finding["severity"] == "WARNING")
    info_count = sum(1 for finding in findings if finding["severity"] == "INFO")
    passed = error_count == 0
    package.validation_report = {
        "rule_version": RULE_VERSION,
        "validated_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "findings": findings,
    }
    package.status = "validated" if passed else "generated"
    record_event(
        db,
        ctx,
        event_type="regulatory_package.validated",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "return_code": package.return_code,
            "reporting_date": package.reporting_date.isoformat(),
            "version": package.version,
            "passed": passed,
            "error_count": error_count,
            "warning_count": warning_count,
        },
    )
    db.commit()
    return read_package(db, package)
