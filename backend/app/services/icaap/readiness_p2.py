"""What the risk-and-capital half of an ICAAP still owes before it can be frozen.

P1's readiness answers "is the document written and are its figures current".
This answers "is the assessment behind it finished": every framework risk
scored with a reason, every material risk given a treatment and a Pillar 2
figure, the appetite set and not weaker than the regulator's own floor, the
Pillar 2 items computed, current and approved, the reconciliation recomputed
and its differences explained, an independent review on file and every Board
challenge answered.

Each finding is a CODE plus parameters; the sentence is rendered by the
readiness service, which knows the regulator's name (it is data, not a
literal). Severity follows P1's shape: ``blocking`` stops a freeze, ``warning``
is a judgement somebody has already made, ``info`` is context.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap.readiness import ReadinessItem
from app.models.icaap import IcaapCycle
from app.services.icaap import params

#: Calendar structure, not a regulatory value.
MONTHS_PER_YEAR = 12

#: Governed ages a review or a Board approval may reach before it is stale.
AGE_CODES: tuple[str, ...] = (
    "icaap_independent_review_max_months",
    "icaap_review_max_months",
)

#: The sentences the readiness service renders. Every one of them names what to
#: do, because a finding nobody can act on is noise on a checklist.
MESSAGES: Mapping[str, str] = {
    "risk_unassessed": "{risk_title} has not been scored for likelihood and impact.",
    "risk_rationale_missing": (
        "{risk_title} is scored but does not say why it is or is not material."
    ),
    "material_risk_without_treatment": (
        "{risk_title} is material and has no decision on how it is covered."
    ),
    "material_risk_without_pillar2": (
        "{risk_title} is to be quantified and carries no Pillar 2 figure yet."
    ),
    "materiality_thresholds_changed": (
        "{risk_title} was assessed under earlier materiality thresholds. Re-save it to "
        "apply the current ones."
    ),
    "materiality_override_down": (
        "{risk_title} was overridden to not material. The reason is on the record."
    ),
    "material_risk_without_appetite": ("{risk_title} is material and has no risk-appetite entry."),
    "material_risk_qualitative_only": (
        "{risk_title} has only a qualitative appetite statement, with no measured level."
    ),
    "appetite_capacity_weaker_than_regulatory": (
        "{metric_label}: the capacity is weaker than the {regulator} minimum, so the "
        "statement allows a position the rules do not."
    ),
    "appetite_regulatory_reference_missing": (
        "{metric_label} is not assessed against a regulatory floor: no approved value "
        "is configured for {param_code}."
    ),
    "appetite_board_approval_missing": ("{metric_label} has no record of Board approval."),
    "appetite_board_approval_stale": (
        "{metric_label} was last approved by the Board on {approved_on}, which is older "
        "than the review period."
    ),
    "appetite_breach": "{metric_label} is beyond appetite ({status}).",
    "pillar2_item_not_computed": "{component_label} has not been computed yet.",
    "pillar2_item_stale": ("{component_label} was computed from figures that have since changed."),
    "pillar2_item_incomplete": "{component_label} is incomplete: {detail}",
    "pillar2_item_not_computable": "{component_label} cannot be computed yet: {detail}",
    "pillar2_item_not_approved": "{component_label} has not been approved.",
    "pillar2_item_approval_stale": (
        "{component_label} changed after it was approved and needs approving again."
    ),
    "pillar2_interim_method": (
        "{component_label} uses an interim method, which the report labels as such."
    ),
    "pillar2_representative_parameter": (
        "{component_label} rests on a representative calibration ({param_codes}), not a "
        "published benchmark."
    ),
    "pillar2_pending_parameter": (
        "{component_label} rests on figures awaiting stakeholder confirmation ({param_codes})."
    ),
    "table5_like_for_like": (
        "{table5_row} has a figure on one basis only, so the stress column would cover "
        "fewer risks than the current one."
    ),
    "requirement_reconciliation_missing": ("The capital requirement has not been reconciled yet."),
    "requirement_reconciliation_stale": (
        "The capital requirement reconciliation was computed from figures that have since changed."
    ),
    "requirement_reconciliation_unexplained": (
        "{line_label} needs an explanation of the difference."
    ),
    "resources_reconciliation_missing": ("The capital resources have not been reconciled yet."),
    "resources_reconciliation_unexplained": (
        "{line_label} differs from its regulatory amount and needs an explanation."
    ),
    "source_consistency_unexplained": (
        "{comparison_key}: this ICAAP and {comparator} state different figures for the "
        "same risk, with no explanation."
    ),
    # D-015 / P5-DESIGN §1.6 item 6. The stress annex measures interest rate
    # risk with the earlier engine under stressed curves; the current and base
    # figures come from the standardised framework (D-009). The two therefore
    # differ BY METHOD, which is a difference to record rather than a break to
    # reconcile — and calling it a break would train preparers to explain away
    # the control that catches real ones.
    "irrbb_method_difference": (
        "The stress tables measure interest rate risk in the banking book with the "
        "earlier engine, while this ICAAP measures it with the standardised framework. "
        "The two figures differ because the methods differ, not because they disagree. "
        "Say so in the stress section; nothing needs correcting."
    ),
    "allocation_missing": "Internal capital has not been allocated to business units.",
    "audit_review_missing": "No independent review of this ICAAP has been recorded.",
    "audit_review_draft_only": (
        "The independent review is still a draft and has not been finalised."
    ),
    "audit_review_outdated": (
        "The most recent independent review was performed on {performed_on}, which is "
        "older than the review period."
    ),
    "challenge_evidence_missing": ("No Board or committee challenge with a response is on record."),
    "challenge_unanswered": ("Challenge {challenge_no} from {raised_in} has not been answered."),
    "capital_trigger_action_weaker_than_floor": (
        "The plan's action level for {metric_code} sits below the {regulator} minimum."
    ),
    "supervisory_addon_exceeds_icaap": (
        "{table5_row}: the supervisor's add-on is larger than this ICAAP's own figure."
    ),
}


def _item(
    code: str,
    severity: str,
    scope: str,
    ref: str | None,
    **parameters: str,
) -> ReadinessItem:
    return ReadinessItem(
        code=code,
        severity=severity,  # pyright: ignore[reportArgumentType]
        scope=scope,  # pyright: ignore[reportArgumentType]
        ref=ref,
        params=parameters,
    )


def _rehearsal(cycle: IcaapCycle) -> bool:
    return cycle.cycle_kind == "rehearsal"


def _add_months(start: date, months: int) -> date:
    """``start`` plus whole months, clamped to the end of the target month."""
    total = start.month - 1 + months
    year = start.year + total // MONTHS_PER_YEAR
    month = total % MONTHS_PER_YEAR + 1
    day = min(start.day, monthrange(year, month)[1])
    return date(year, month, day)


def _months_old(performed: date, today: date, months: int | None) -> bool:
    if months is None:
        return False
    return _add_months(performed, months) < today


def evaluate_p2(  # noqa: PLR0912, PLR0915 - one branch per readiness rule, each named
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    *,
    today: date,
) -> tuple[ReadinessItem, ...]:
    """Every outstanding thing in the risk-and-capital half of this ICAAP."""
    # Imported here, not at module load: P1's readiness service imports this
    # module, and these services import that one back for the cycle guards.
    from app.services.icaap import (  # noqa: PLC0415
        allocation as allocation_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        appetite as appetite_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        audit_reviews as audit_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        capital_triggers as trigger_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        challenges as challenge_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        pillar2 as pillar2_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        reconciliation as reconciliation_service,
    )
    from app.services.icaap import (  # noqa: PLC0415 - avoid an import cycle
        risks as risks_service,
    )

    items: list[ReadinessItem] = []
    rehearsal = _rehearsal(cycle)
    ages = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date, codes=AGE_CODES)

    # --- risk register ----------------------------------------------------
    register = risks_service.get_register(db, access, cycle.id)
    material_keys: set[str] = set()
    for risk in register.risks:
        if risk.retired_at is not None:
            continue
        title = risk.title
        if risk.materiality_score is None:
            items.append(
                _item("risk_unassessed", "blocking", "cycle", risk.risk_key, risk_title=title)
            )
            continue
        if not risk.materiality_rationale:
            items.append(
                _item(
                    "risk_rationale_missing", "blocking", "cycle", risk.risk_key, risk_title=title
                )
            )
        if not risk.thresholds_current:
            items.append(
                _item(
                    "materiality_thresholds_changed",
                    "warning",
                    "cycle",
                    risk.risk_key,
                    risk_title=title,
                )
            )
        if risk.verdict == "material":
            material_keys.add(risk.risk_key)
            if risk.pillar2_treatment == "undecided":
                items.append(
                    _item(
                        "material_risk_without_treatment",
                        "blocking",
                        "cycle",
                        risk.risk_key,
                        risk_title=title,
                    )
                )
            if risk.pillar2_treatment == "quantified" and not any(
                component.item_id is not None for component in risk.components
            ):
                items.append(
                    _item(
                        "material_risk_without_pillar2",
                        "blocking",
                        "cycle",
                        risk.risk_key,
                        risk_title=title,
                    )
                )
        if risk.verdict_source == "override" and risk.matrix_verdict == "material":
            items.append(
                _item(
                    "materiality_override_down",
                    "warning",
                    "cycle",
                    risk.risk_key,
                    risk_title=title,
                )
            )

    # --- appetite ---------------------------------------------------------
    appetite = appetite_service.get_appetite(db, access, cycle.id)
    covered = {metric.risk_key for metric in appetite.metrics if metric.risk_key}
    quantitative = {
        metric.risk_key
        for metric in appetite.metrics
        if metric.risk_key and metric.measure_kind == "quantitative"
    }
    for risk_key in sorted(material_keys):
        if risk_key not in covered:
            items.append(
                _item(
                    "material_risk_without_appetite",
                    "blocking",
                    "cycle",
                    risk_key,
                    risk_title=risk_key,
                )
            )
        elif risk_key not in quantitative:
            items.append(
                _item(
                    "material_risk_qualitative_only",
                    "warning",
                    "cycle",
                    risk_key,
                    risk_title=risk_key,
                )
            )
    review_months = ages.optional_integer("icaap_review_max_months")
    for metric in appetite.metrics:
        if "capacity_weaker_than_regulatory" in metric.violations:
            items.append(
                _item(
                    "appetite_capacity_weaker_than_regulatory",
                    "blocking",
                    "cycle",
                    metric.metric_key,
                    metric_label=metric.label,
                )
            )
        if metric.reference_missing and metric.regulatory_param_code:
            items.append(
                _item(
                    "appetite_regulatory_reference_missing",
                    "warning" if rehearsal else "blocking",
                    "cycle",
                    metric.metric_key,
                    metric_label=metric.label,
                    param_code=metric.regulatory_param_code,
                )
            )
        if metric.board_approved_on is None:
            items.append(
                _item(
                    "appetite_board_approval_missing",
                    "blocking",
                    "cycle",
                    metric.metric_key,
                    metric_label=metric.label,
                )
            )
        elif _months_old(metric.board_approved_on, today, review_months):
            items.append(
                _item(
                    "appetite_board_approval_stale",
                    "warning",
                    "cycle",
                    metric.metric_key,
                    metric_label=metric.label,
                    approved_on=metric.board_approved_on.isoformat(),
                )
            )
        if metric.evaluation is not None and metric.evaluation.rag in {"amber", "red"}:
            items.append(
                _item(
                    "appetite_breach",
                    "warning",
                    "cycle",
                    metric.metric_key,
                    metric_label=metric.label,
                    status=metric.evaluation.status,
                )
            )

    # --- Pillar 2 ---------------------------------------------------------
    pillar2 = pillar2_service.get_register(db, access, cycle.id)
    for entry in pillar2.items:
        label = entry.component_key
        if entry.method_status == "not_computed":
            items.append(
                _item(
                    "pillar2_item_not_computed",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                )
            )
        elif entry.method_status == "incomplete":
            items.append(
                _item(
                    "pillar2_item_incomplete",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                    detail=entry.status_detail or "",
                )
            )
        elif entry.method_status == "not_computable":
            items.append(
                _item(
                    "pillar2_item_not_computable",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                    detail=entry.status_detail or "",
                )
            )
        if entry.stale:
            items.append(
                _item(
                    "pillar2_item_stale",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                )
            )
        if entry.method_status == "not_capitalised":
            continue
        if entry.approved_revision_no is None:
            items.append(
                _item(
                    "pillar2_item_not_approved",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                )
            )
        elif not entry.approval_current:
            items.append(
                _item(
                    "pillar2_item_approval_stale",
                    "blocking",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                )
            )
        if entry.method_status == "interim_non_sf":
            items.append(
                _item(
                    "pillar2_interim_method",
                    "warning",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                )
            )
        if entry.representative_parameters:
            items.append(
                _item(
                    "pillar2_representative_parameter",
                    "warning",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                    param_codes=", ".join(entry.representative_parameters),
                )
            )
        if entry.pending_parameters:
            items.append(
                _item(
                    "pillar2_pending_parameter",
                    "info",
                    "cycle",
                    entry.item_key,
                    component_label=label,
                    param_codes=", ".join(entry.pending_parameters),
                )
            )
    for finding in pillar2.findings:
        items.append(
            _item(
                finding.code,
                "blocking",
                "cycle",
                finding.ref,
                table5_row=finding.params.get("table5_row", ""),
            )
        )
    items.extend(_consistency_findings(pillar2))

    items.extend(_supervisory_findings(pillar2))

    # --- reconciliation and allocation ------------------------------------
    try:
        reconciliation = reconciliation_service.get_reconciliation(db, access, cycle.id)
    except HTTPException:
        reconciliation = None
    if reconciliation is None or not reconciliation.requirement.lines:
        items.append(_item("requirement_reconciliation_missing", "blocking", "cycle", None))
    else:
        if reconciliation.requirement.stale:
            items.append(_item("requirement_reconciliation_stale", "blocking", "cycle", None))
        for line in reconciliation.requirement.lines:
            if line.explanation_required and not (line.explanation and line.explanation_current):
                items.append(
                    _item(
                        "requirement_reconciliation_unexplained",
                        "blocking",
                        "cycle",
                        line.line_key,
                        line_label=line.label,
                    )
                )
    # Resources are reported independently of the requirement: a bank that has
    # not listed its capital components owes that answer whether or not the
    # requirement has been computed.
    if reconciliation is None or not reconciliation.resources.lines:
        items.append(_item("resources_reconciliation_missing", "blocking", "cycle", None))
    else:
        for line in reconciliation.resources.lines:
            if line.explanation_required and not line.explanation:
                items.append(
                    _item(
                        "resources_reconciliation_unexplained",
                        "blocking",
                        "cycle",
                        line.line_key,
                        line_label=line.label,
                    )
                )
    allocation = allocation_service.get_allocation(db, access, cycle.id)
    if not allocation.cells:
        items.append(_item("allocation_missing", "warning", "cycle", None))

    # --- review and challenge ---------------------------------------------
    reviews = audit_service.list_reviews(db, access, cycle.id)
    finalised = [review for review in reviews.reviews if review.status == "finalised"]
    if not finalised:
        code = "audit_review_draft_only" if reviews.reviews else "audit_review_missing"
        items.append(_item(code, "warning", "cycle", None))
    else:
        review_max = ages.optional_integer("icaap_independent_review_max_months")
        latest = finalised[0]
        if _months_old(latest.performed_on, today, review_max):
            items.append(
                _item(
                    "audit_review_outdated",
                    "warning",
                    "cycle",
                    str(latest.id),
                    performed_on=latest.performed_on.isoformat(),
                )
            )
    challenges = challenge_service.list_challenges(db, access, cycle.id)
    answered_board = [
        challenge
        for challenge in challenges.challenges
        if challenge.raised_in in challenge_service.BOARD_FORUMS and challenge.responses
    ]
    if not answered_board:
        items.append(
            _item(
                "challenge_evidence_missing",
                "warning" if rehearsal else "blocking",
                "cycle",
                None,
            )
        )
    for challenge in challenges.challenges:
        if challenge.open:
            items.append(
                _item(
                    "challenge_unanswered",
                    "blocking",
                    "cycle",
                    str(challenge.id),
                    challenge_no=str(challenge.challenge_no),
                    raised_in=challenge.raised_in,
                )
            )

    # --- capital triggers --------------------------------------------------
    evaluation = trigger_service.evaluate(db, access, cycle.id)
    for result in evaluation.results:
        if "trigger_action_below_floor" in result.findings:
            items.append(
                _item(
                    "capital_trigger_action_weaker_than_floor",
                    "warning",
                    "cycle",
                    result.metric_code,
                    metric_code=result.metric_code,
                )
            )
    return tuple(items)


def _consistency_findings(register) -> list[ReadinessItem]:
    """The internal control's unexplained differences, method differences apart.

    One case is not a break: the stress annex's Pillar 2 IRRBB overlay is the
    earlier engine's economic-value loss under stressed curves, while the
    ICAAP's own figure comes from the standardised framework (D-015, D-009).
    Those two cannot agree and are not meant to, so the control labels the pair
    a METHOD DIFFERENCE and asks the report to say so, instead of demanding an
    explanation that would read as if something were wrong.
    """
    from app.domain.icaap import reconciliation as recon_domain  # noqa: PLC0415 - avoid a cycle
    from app.domain.icaap.pillar2 import irrbb_sf_method as sf_method  # noqa: PLC0415

    method_difference_rows = {
        entry.table5_row
        for entry in register.items
        if entry.method == sf_method.METHOD and entry.table5_row
    }
    out: list[ReadinessItem] = []
    for control in register.consistency:
        if control.status != "inconsistent" or (
            control.explanation and control.explanation_current
        ):
            continue
        if (
            control.basis == recon_domain.BASIS_STRESSED
            and control.comparator == recon_domain.COMPARATOR_STRESS_OVERLAY
            and control.row in method_difference_rows
        ):
            out.append(
                _item(
                    "irrbb_method_difference",
                    "warning",
                    "cycle",
                    control.comparison_key,
                    comparison_key=control.comparison_key,
                    comparator=control.comparator,
                )
            )
            continue
        out.append(
            _item(
                "source_consistency_unexplained",
                "blocking",
                "cycle",
                control.comparison_key,
                comparison_key=control.comparison_key,
                comparator=control.comparator,
            )
        )
    return out


def _supervisory_findings(register) -> list[ReadinessItem]:
    out: list[ReadinessItem] = []
    for control in register.consistency:
        if (
            control.comparator == "supervisory"
            and control.icaap is not None
            and control.other is not None
            and control.other > control.icaap
        ):
            out.append(
                _item(
                    "supervisory_addon_exceeds_icaap",
                    "info",
                    "cycle",
                    control.row,
                    table5_row=control.row,
                )
            )
    return out


def readiness_codes() -> Sequence[str]:
    return tuple(MESSAGES)


__all__ = ["AGE_CODES", "MESSAGES", "evaluate_p2", "readiness_codes"]
