"""Freezing an ICAAP: the checks before, and the one transaction that seals it.

Freeze is the moment a workspace becomes a filing. Two properties make it
trustworthy, and everything in this module serves one of them.

**Every refusal names its reason.** ``freeze_preflight`` is served read-only as
its own endpoint, so a preparer sees the complete list before pressing anything
and each entry says what to do about it. A freeze that failed with "not ready"
would send somebody hunting through seven screens.

**It is one transaction.** The package row, the copied evidence, the cycle's
seal and the recorded decision land together or not at all. Nothing is written
to object storage here (the exports come later), so there is no half-committed
state to reconcile: a failure leaves no package, no superseded predecessor, and
the cycle still in review. The test that proves it makes the attachment copy
raise AFTER the package has been flushed.

**Who may freeze is a maker-checker question (D-030, DV-011).** Freezing makes
the actor the package's ``generated_by``, and a package's generator may sign as
preparer but never as approver or for the Board. If the Board Risk Committee's
approval froze the cycle automatically, the BRC chair would become the
generator and lose the signature slot they were appointed to fill. So freeze is
an explicit act, refused to anyone who reviewed or approved the round — the
authority condition lives in ``workflow.freeze_authority`` and is re-checked
here under the row lock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.domain.icaap import workflow as workflow_domain
from app.domain.icaap.frameworks.schema import Framework
from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle, IcaapStageDecision
from app.schemas.icaap import (
    IcaapCycleSummaryRead,
    IcaapFreezeCreate,
    IcaapFreezeRead,
    IcaapPackageSummaryRead,
    IcaapPreflightItemRead,
    IcaapPreflightRead,
)
from app.services.audit import record_event
from app.services.icaap import (
    guards,
    pillar2_irrbb_hook,
    readiness,
    snapshot,
    workflow,
)
from app.services.icaap import parameters as icaap_parameters

#: When an ICAAP report first becomes a filable return, as a governed date
#: (D-032). Never a literal: BoG's commencement is BoG's answer, the row ships
#: ``pending``, and staff confirm it in the operator console.
FIRST_AS_OF_PARAM = "icaap_report_first_as_of_date"

#: Which return code each kind of cycle becomes. A ¶74 update is a different
#: return from the annual report, and neither is invented here: the codes come
#: from the framework's own filing block.
#: How many section keys a refusal names before it becomes unreadable. Editorial.
_NAMED_IN_REFUSAL = 5
#: The ``regulatory_packages.notes`` column length.
_NOTES_MAX = 2000

_PURPOSE_FOR_KIND: dict[str, str] = {
    "annual": "annual",
    "material_change": "update",
    "regulator_request": "update",
}


def _item(
    code: str,
    message: str,
    *,
    severity: str = "blocking",
    scope: str = "cycle",
    ref: str | None = None,
) -> IcaapPreflightItemRead:
    return IcaapPreflightItemRead(
        code=code,
        severity=severity,  # pyright: ignore[reportArgumentType]
        scope=scope,  # pyright: ignore[reportArgumentType]
        ref=ref,
        message=message,
    )


def return_code_for(framework: Framework, cycle: IcaapCycle) -> str | None:
    """The return this cycle becomes, or ``None`` when the framework files none."""
    if framework.filing is None:
        return None
    purpose = _PURPOSE_FOR_KIND.get(cycle.cycle_kind)
    if purpose is None:
        return None
    return framework.filing.return_code_for(purpose)


def _governed_first_as_of(db: Session, access: IcaapAccess) -> tuple[date | None, str | None]:
    """The governed commencement date, or a refusal code naming the parameter."""
    # The ICAAP plane's own non-recording door (D-078 residual): resolving the
    # commencement date to decide whether a report may be frozen is dispatch,
    # not calculation, and it must not enter any run's provenance.
    resolved = icaap_parameters.try_resolve(db, access.bank, FIRST_AS_OF_PARAM)
    if resolved is None:
        return None, "missing"
    body = resolved.value_json or {}
    raw = body.get("date")
    if not isinstance(raw, str):
        return None, "malformed"
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, "malformed"


def _prior_package(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, return_code: str
) -> RegulatoryPackage | None:
    return db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == access.ctx.organization_id,
            RegulatoryPackage.bank_id == access.bank.id,
            RegulatoryPackage.return_code == return_code,
            RegulatoryPackage.reporting_date == cycle.as_of_date,
            RegulatoryPackage.basis == cycle.basis,
            RegulatoryPackage.status != "superseded",
        )
        .order_by(RegulatoryPackage.version.desc())
        .limit(1)
    )


def _annex_items(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, framework: Framework
) -> list[IcaapPreflightItemRead]:
    """Every annex the framework says is filed inside this report must be current.

    An annex is not a separate filing — Appendix II travels with the ICAAP — so
    a missing one is a report with a hole in it, and a stale one is a report
    whose stress tables disagree with its own narrative.
    """
    if framework.filing is None:
        return []
    items: list[IcaapPreflightItemRead] = []
    blocks = {
        block.block_type: binding for block, binding in snapshot.live_blocks(db, access, cycle)
    }
    for annex in framework.filing.annexes:
        if not annex.required:
            continue
        package = db.scalar(
            select(RegulatoryPackage)
            .where(
                RegulatoryPackage.organization_id == access.ctx.organization_id,
                RegulatoryPackage.bank_id == access.bank.id,
                RegulatoryPackage.return_code == annex.return_code,
                RegulatoryPackage.reporting_date == cycle.as_of_date,
                RegulatoryPackage.basis == cycle.basis,
                RegulatoryPackage.status != "superseded",
            )
            .order_by(RegulatoryPackage.version.desc())
            .limit(1)
        )
        if package is None:
            items.append(
                _item(
                    "annex_missing",
                    f"{annex.return_code} is filed inside this report and has not been "
                    f"generated for {cycle.as_of_date.isoformat()}.",
                    ref=annex.return_code,
                )
            )
            continue
        binding = blocks.get("appendix_ii")
        pinned = (binding.source_ref or {}).get("content_digest") if binding is not None else None
        if pinned is not None and pinned != package.content_digest:
            items.append(
                _item(
                    "annex_stale",
                    f"The stress tables in this report were taken from an earlier version "
                    f"of {annex.return_code}. Refresh the stress block before freezing.",
                    ref=annex.return_code,
                )
            )
    return items


def freeze_preflight(  # noqa: PLR0912 - one branch per named refusal, deliberately flat
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycle,
    *,
    review_digest: str | None = None,
) -> IcaapPreflightRead:
    """Everything between this cycle and a sealed filing, each with its reason."""
    framework, digest_matches = guards.framework_for(cycle)
    state = workflow.load_state(db, access, cycle)
    items: list[IcaapPreflightItemRead] = []

    if cycle.cycle_kind == "rehearsal":
        # INFORMATIONAL, not blocking (D-068). A rehearsal runs the full
        # lifecycle — freeze, signature, submission — into a package marked
        # `is_rehearsal` that can never be filed. At `blocking` this said the
        # rehearsal could not be frozen, which is the behaviour the ruling
        # rejected and which migration 202609190062 exists to permit.
        items.append(
            _item(
                "rehearsal_not_fileable",
                "This is a rehearsal. It goes through the full process but is never "
                "filed and never satisfies a filing obligation.",
                severity="info",
            )
        )
    if cycle.frozen_at is not None or cycle.status not in {"in_review"}:
        items.append(
            _item(
                "cycle_not_awaiting_freeze",
                "Only an ICAAP whose approval stage has approved can be frozen.",
            )
        )
    elif not state.awaiting_freeze:
        items.append(
            _item(
                "cycle_not_awaiting_freeze",
                "The approval stage has not approved this ICAAP yet.",
            )
        )
    if not digest_matches:
        items.append(
            _item(
                "framework_digest_mismatch",
                "The framework text has changed since this ICAAP was started. Move it "
                "onto the new version before freezing.",
            )
        )
    if framework.filing is None or return_code_for(framework, cycle) is None:
        items.append(
            _item(
                "filing_not_available_for_framework",
                f"{framework.short_title} is published for reference only; the platform "
                "cannot file this assessment to that regulator yet.",
            )
        )
    if cycle.cycle_kind != "rehearsal":
        pending = [
            section.key
            for section in framework.sections
            if section.source_status == "pending_primary_text"
        ]
        if pending:
            items.append(
                _item(
                    "framework_pending_primary_text",
                    "Some sections of this framework are still awaiting the regulator's "
                    "own text, so the checklist behind them is incomplete. A report "
                    "filed against them would claim a completeness it does not have.",
                    ref=",".join(sorted(pending)[:_NAMED_IN_REFUSAL]),
                )
            )

    # P1 + P2 readiness, unchanged and unduplicated: the list the preparer has
    # been working through IS the list that blocks the freeze.
    report = readiness.get_readiness(db, access, cycle.id)
    items.extend(
        _item(entry.code, entry.message, severity=entry.severity, scope=entry.scope, ref=entry.ref)
        for entry in report.items
        if entry.severity == "blocking"
    )

    if review_digest is not None and review_digest != state.review_digest:
        items.append(
            _item(
                "review_basis_changed",
                "The report changed since this page was loaded. Reload it and freeze again.",
            )
        )
    freeze_seq = state.freeze_seq
    if freeze_seq is not None:
        approved = [
            row
            for row in state.decisions
            if row.stage_seq == freeze_seq
            and row.decision == "approved"
            and row.round >= workflow_domain.reset_round_for(state.facts, freeze_seq)
        ]
        if approved and approved[-1].review_digest != state.review_digest:
            items.append(
                _item(
                    "review_basis_changed",
                    "The report has changed since the approval stage approved it. It has "
                    "to be reviewed again before it can be frozen.",
                )
            )

    # The same rule readiness already ran, re-run here as an independent gate:
    # a freeze that sealed a superseded IRRBB method because readiness happened
    # to be quiet would file a number the supervisor no longer accepts. It is
    # reported ONCE — the preparer has been working through this finding on the
    # checklist, and printing it twice under one code reads as two problems.
    interim = pillar2_irrbb_hook.interim_blocking(db, access, cycle)
    if interim is not None and not any(entry.code == interim for entry in items):
        items.append(
            _item(
                interim,
                "The interest rate risk in the banking book figure in this report comes "
                "from the interim method, which the standardised framework has "
                "superseded for this reporting date.",
            )
        )

    items.extend(_annex_items(db, access, cycle, framework))

    if cycle.cycle_kind != "rehearsal":
        first_as_of, problem = _governed_first_as_of(db, access)
        if problem is not None:
            items.append(
                _item(
                    "missing_parameter",
                    "The date from which an ICAAP report is filable is a governed "
                    f"parameter ({FIRST_AS_OF_PARAM}) with no usable approved row for "
                    "this institution. Staff configure it in the operator console.",
                    ref=FIRST_AS_OF_PARAM,
                )
            )
        elif first_as_of is not None and cycle.as_of_date < first_as_of:
            items.append(
                _item(
                    "return_not_yet_effective",
                    f"An ICAAP report is filable from {first_as_of.isoformat()}. This "
                    f"assessment is as at {cycle.as_of_date.isoformat()}.",
                )
            )

    return_code = return_code_for(framework, cycle)
    if return_code is not None:
        prior = _prior_package(db, access, cycle, return_code)
        if prior is not None and prior.status == "submitted":
            items.append(
                _item(
                    "prior_filing_with_regulator",
                    "The current version of this return is with the regulator. Wait for "
                    "the outcome, or request a resubmission, before freezing another.",
                    ref=str(prior.id),
                )
            )
    blocking = [entry for entry in items if entry.severity == "blocking"]
    return IcaapPreflightRead(
        cycle_id=cycle.id,
        ready=not blocking,
        review_digest=state.review_digest,
        items=items,
    )


def get_preflight(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapPreflightRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    return freeze_preflight(db, access, cycle)


# ---------------------------------------------------------------------------
# The package-plane seams (owned by WS-B)
# ---------------------------------------------------------------------------


def _definition(return_code: str) -> Any:
    from app.services.regulatory_reporting.registry import REGISTRY  # noqa: PLC0415 - avoid a cycle

    return REGISTRY.get(return_code)


def resolve_period(db: Session, access: IcaapAccess, cycle: IcaapCycle, return_code: str) -> Any:
    """The computed fact snapshot AS OF the assessment date — exact, or refuse.

    ``get_snapshot_for_reporting_date`` is the single authority every other
    return uses, and it is exact for every cadence: a book from an earlier date
    is not this date's position. The refusal names the date required and the
    nearest earlier one, and never substitutes it.
    """
    from app.services.regulatory_reporting.common import (  # noqa: PLC0415 - avoid a cycle
        get_snapshot_for_reporting_date,
    )

    definition = _definition(return_code)
    return get_snapshot_for_reporting_date(
        db,
        access.ctx,
        access.bank,
        cycle.as_of_date,
        return_code=return_code,
        frequency=None if definition is None else definition.frequency,
    )


def _assert_reconciled(db: Session, access: IcaapAccess, cycle: IcaapCycle, period: Any) -> None:
    """A filing built on a book that does not balance is refused, naming the gap."""
    from app.services import filing_reconciliation  # noqa: PLC0415 - avoid a cycle

    filing_reconciliation.assert_filing_reconciled(
        db,
        access.ctx,
        access.bank,
        as_of=period.period_end,
        period_id=period.id,
        purpose="package_generation",
    )


def _generate(  # noqa: PLR0913 - the mint key is its named parts
    db: Session,
    access: IcaapAccess,
    *,
    return_code: str,
    reporting_date: date,
    basis: str,
    notes: str,
    build: Callable[[], Any],
) -> RegulatoryPackage:
    """Mint the package WITHOUT committing, so the freeze stays one transaction.

    Named rather than inlined because it is the seam the package plane owns:
    ``generate_frozen_package`` runs the institution-class and jurisdiction
    gates AND the withdrawn-evidence gate, stamps the authority record, versions
    and supersedes, computes both digests and writes the audit event, and
    returns the flushed row instead of committing it.

    Which gate sits on which side of this seam is the thing that has gone wrong
    twice (D-069, and the architecture audit's M1). The dividing line is: a gate
    that is about the FILING belongs at the mint, so the next freeze-minted
    family inherits it; a gate that is about the CYCLE — the review chain, the
    framework's readiness — belongs here. The reporting-period and
    reconciliation gates are held above by ``freeze_cycle`` because they need
    the period the cycle resolved; either owner may hold a gate, NEITHER may
    drop one.
    """
    from app.services.regulatory_reporting import generation  # noqa: PLC0415 - avoid a cycle

    return generation.generate_frozen_package(
        db,
        access.ctx,
        access.bank,
        return_code=return_code,
        reporting_date=reporting_date,
        build=build,
        basis=basis,
        notes=notes,
        commit=False,
    )


def _copy_attachments(
    db: Session, access: IcaapAccess, cycle: IcaapCycle, package: RegulatoryPackage
) -> None:
    """Attach the freeze-gate evidence to the package, in the same transaction."""
    from app.services.regulatory_reporting import attachments  # noqa: PLC0415 - avoid a cycle

    attachments.copy_freeze_attachments(db, access.ctx, package, cycle_id=cycle.id)


# ---------------------------------------------------------------------------
# The transaction
# ---------------------------------------------------------------------------


def freeze_cycle(
    db: Session, access: IcaapAccess, cycle_id: UUID, payload: IcaapFreezeCreate
) -> IcaapFreezeRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    framework = guards.require_framework(cycle)
    state = workflow.load_state(db, access, cycle)
    actor = guards.actor_id(access)
    reviewers = workflow_domain.checkers(state.facts, current_round=cycle.round, exclude_seq=None)
    if str(actor) in reviewers:
        raise guards.conflict(
            "maker_checker",
            "You reviewed or approved this ICAAP. Freezing it would record you as the "
            "person who produced the filing, which would bar you from signing it as "
            "approver or for the Board. A preparer freezes it.",
        )
    preflight = freeze_preflight(db, access, cycle, review_digest=payload.review_digest)
    blocking = [entry for entry in preflight.items if entry.severity == "blocking"]
    if blocking:
        raise guards.conflict(
            "freeze_blocked",
            "This ICAAP is not ready to be frozen.",
            items=[entry.model_dump(mode="json") for entry in blocking],
        )
    return_code = return_code_for(framework, cycle)
    if return_code is None:  # pragma: no cover - preflight refuses first
        raise guards.conflict(
            "filing_not_available_for_framework",
            "This framework declares no return for the platform to file.",
        )
    freeze_seq = state.freeze_seq
    freeze_stage = None if freeze_seq is None else state.stage(freeze_seq)
    if freeze_stage is None:  # pragma: no cover - the chain validator forbids it
        raise guards.conflict("stage_chain_invalid", "This review chain has no freeze stage.")

    signer = workflow.signer_display(db, access)
    # Outside the try: a refusal here is a precondition failure with its own
    # named 409 (``no_computed_position`` / ``filing_reconciliation``), and
    # nothing has been written yet for a rollback to undo.
    period = resolve_period(db, access, cycle, return_code)
    _assert_reconciled(db, access, cycle, period)
    definition = _definition(return_code)
    try:
        package = _generate(
            db,
            access,
            return_code=return_code,
            reporting_date=cycle.as_of_date,
            basis=cycle.basis,
            notes=(
                f"Frozen from ICAAP cycle '{cycle.title}', round {cycle.round}. {payload.reason}"
            )[:_NOTES_MAX],
            build=snapshot.builder(
                db,
                access,
                cycle,
                review_digest=payload.review_digest,
                period=period,
                definition=definition,
            ),
        )
        _copy_attachments(db, access, cycle, package)
        # ONE update from the unsealed in_review row: the model's
        # ``sealed_has_package`` CHECK and the governed-row guard both require
        # status and package_id to move together.
        cycle.status = "frozen"
        cycle.package_id = package.id
        cycle.frozen_at = utc_now()
        db.add(
            IcaapStageDecision(
                organization_id=cycle.organization_id,
                bank_id=cycle.bank_id,
                cycle_id=cycle.id,
                stage_seq=freeze_stage.seq,
                stage_key=freeze_stage.stage_key,
                round=cycle.round,
                decision="frozen",
                review_digest=payload.review_digest,
                package_id=package.id,
                comment=payload.reason,
                decided_by=actor,
                decided_by_name=signer[0],
                officer_title=signer[1],
            )
        )
        record_event(
            db,
            access.ctx,
            event_type="icaap.cycle.frozen",
            entity_type="icaap_cycle",
            entity_id=cycle.id,
            details={
                "package_id": str(package.id),
                "return_code": package.return_code,
                "version": package.version,
                "content_digest": package.content_digest,
                "review_digest": payload.review_digest,
                "round": cycle.round,
                "reason": payload.reason,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(cycle)
    db.refresh(package)
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    summary = cycles_service.read(db, access, cycle)
    return IcaapFreezeRead(
        cycle=IcaapCycleSummaryRead.model_validate(
            summary.model_dump(include=set(IcaapCycleSummaryRead.model_fields))
        ),
        package=package_summary(package),
    )


def package_summary(package: RegulatoryPackage) -> IcaapPackageSummaryRead:
    return IcaapPackageSummaryRead(
        id=package.id,
        return_code=package.return_code,
        return_family=package.return_family,
        reporting_date=package.reporting_date,
        basis=package.basis,
        status=package.status,
        version=package.version,
        content_digest=package.content_digest,
        generated_at=package.generated_at,
    )


__all__ = [
    "FIRST_AS_OF_PARAM",
    "freeze_cycle",
    "freeze_preflight",
    "get_preflight",
    "package_summary",
    "return_code_for",
]
