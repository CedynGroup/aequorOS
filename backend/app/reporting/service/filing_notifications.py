"""Who hears about a return as it moves, and what they are told (backend/docs/notifications.md).

The filing chain and the regulator channels call these functions at the moment
something changes; each one decides the recipients from AUTHORITY, never from a
scalar role, and publishes one catalogue event:

==============================  ===============================================
Moment                          Who is told
==============================  ===============================================
return reaches a stage          everyone who may decide that stage now: the
(sent for approval, handed on)  stage permission at this bank, maker-checker
                                clean, officer title matching
a stage approves                the preparer and earlier officers this round
a stage sends back              the officers who held the target stage before,
                                or everyone who may decide it
filed with the regulator        every officer who touched the return
regulator receipt / decision /  every officer who touched the return, plus
query                           everyone who may file it (the Validator stage)
==============================  ===============================================

The person who caused the change is never emailed about their own act.
Everything is published inside the caller's transaction, so a decision that
rolls back notifies nobody. Regulator names come from jurisdiction data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ConditionCheck, ConditionKind
from app.core.return_family_access import gate_for
from app.identity.public import Bank, User
from app.notifications.public import Action, Fact, Message, catalog, publish
from app.policy.public import jurisdictions
from app.reporting.models.filing_workflow import PackageStageDecision, PackageWorkflowStage
from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service import family_access
from app.reporting.service.registry import get_definition

#: Comments shown in an email are clipped by the renderer; this bounds what is read.
_COMMENT_KEYS = ("comments", "message", "reason", "detail")


# ---------------------------------------------------------------------------
# Shared message parts
# ---------------------------------------------------------------------------


def human_date(value: date) -> str:
    return f"{value.day} {value:%B %Y}"


def return_label(package: RegulatoryPackage) -> str:
    """``CAR-RWA for 30 June 2026``."""
    return f"{package.return_code} for {human_date(package.reporting_date)}"


def return_path(package: RegulatoryPackage) -> str:
    return (
        f"/submissions/returns?code={package.return_code}&date={package.reporting_date.isoformat()}"
    )


def _bank(db: Session, package: RegulatoryPackage) -> Bank | None:
    return db.get(Bank, package.bank_id)


def _facts(
    db: Session, package: RegulatoryPackage, *extra: Fact, bank: Bank | None = None
) -> tuple[Fact, ...]:
    definition = get_definition(package.return_code)
    institution = bank or _bank(db, package)
    facts = [
        Fact(
            "Return",
            package.return_code
            if definition is None
            else f"{package.return_code} — {definition.title}",
        ),
        Fact("Reporting date", human_date(package.reporting_date)),
        Fact("Version", str(package.version)),
    ]
    if institution is not None:
        facts.append(Fact("Institution", institution.name))
    facts.extend(extra)
    return tuple(facts)


def _person(db: Session, user_id: UUID | None) -> str:
    if user_id is None:
        return "AequorOS"
    user = db.get(User, user_id)
    if user is None:
        return "A colleague"
    return user.display_name or user.email


def publish_for_return(  # noqa: PLR0913 - the publish envelope for one return
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    event: catalog.EventDefinition,
    message: Message,
    recipients: Iterable[UUID],
    dedupe_key: str | None = None,
) -> int:
    """Publish one return's notice; returns how many people it reached."""
    gate = gate_for(package.return_family)
    return len(
        publish(
            db,
            ctx,
            event=event,
            message=message,
            recipients=recipients,
            # The same locator the existing package notices use: the return
            # family's own read gate, which every officer the chain reaches holds.
            module=gate.module,
            sensitivity=gate.sensitivity,
            bank_id=package.bank_id,
            entity_type="regulatory_package",
            entity_id=package.id,
            dedupe_key=dedupe_key,
            exclude=() if ctx.actor_user_id is None else (ctx.actor_user_id,),
        )
    )


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------


def stage_holders(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    stage: PackageWorkflowStage,
    *,
    maker_checker: bool = True,
) -> list[UUID]:
    """Everyone who could take this stage's decision right now.

    The same tests the decision route applies, asked of each active human in
    the organisation: a binding granting the stage's permission over this
    bank's return plane, a matching officer title when the stage names titles,
    and (``maker_checker``) a clean maker-checker record for this round.
    Evaluated quietly — this is a question, not an enforcement.

    ``maker_checker=False`` is for news ABOUT a filed return (the regulator
    answered): whoever may file the return should hear what the regulator
    said, even if they prepared it.
    """
    from app.reporting.service.filing_workflow import chain  # noqa: PLC0415 - cycle

    bank = _bank(db, package)
    if bank is None:
        return []
    state = chain.load_state(db, ctx, package) if maker_checker else None
    permission = chain.stage_permission(stage)
    officer_titles: list[object] = list(stage.officer_titles or ())
    titles = [str(entry).strip().casefold() for entry in officer_titles]
    holders: list[UUID] = []
    for user in db.scalars(
        select(User).where(
            User.organization_id == ctx.organization_id,
            User.is_active.is_(True),
        )
    ):
        if user.auth_provider == "service":
            continue
        if titles and (user.job_title or "").strip().casefold() not in titles:
            continue
        if state is not None and chain.decision_blocker_for(state, stage.seq, user.id) is not None:
            continue
        nominee = replace(
            ctx,
            actor_user_id=user.id,
            roles=(user.role,),
            authorization_version=user.authorization_version,
            impersonation_context=None,
            actor_operator=None,
            examiner_bank_id=None,
        )
        verdict = family_access.chain_decision_verdict(
            db,
            nominee,
            bank,
            package,
            permission,
            surface="notification_recipients",
            # The maker-checker condition the decision route supplies: already
            # established above (or deliberately waived for regulator news).
            conditions=(
                ConditionCheck(
                    kind=ConditionKind.MAKER_CHECKER,
                    passed=True,
                    reason="recipient neither prepared nor already decided this return",
                ),
            ),
            record=False,
        )
        if verdict.allowed:
            holders.append(user.id)
    return holders


def _decisions(db: Session, package: RegulatoryPackage) -> list[PackageStageDecision]:
    return list(
        db.scalars(
            select(PackageStageDecision)
            .where(
                PackageStageDecision.organization_id == package.organization_id,
                PackageStageDecision.package_id == package.id,
            )
            .order_by(PackageStageDecision.created_at)
        )
    )


def participants(db: Session, package: RegulatoryPackage) -> list[UUID]:
    """Everyone who worked on this return: the preparer and every officer who decided."""
    people: list[UUID] = [package.generated_by]
    people.extend(row.decided_by for row in _decisions(db, package))
    return list(dict.fromkeys(people))


def _stage(db: Session, package: RegulatoryPackage, seq: int | None) -> PackageWorkflowStage | None:
    if seq is None:
        return None
    return db.scalar(
        select(PackageWorkflowStage).where(
            PackageWorkflowStage.organization_id == package.organization_id,
            PackageWorkflowStage.package_id == package.id,
            PackageWorkflowStage.seq == seq,
        )
    )


def _transmit_stage(db: Session, package: RegulatoryPackage) -> PackageWorkflowStage | None:
    return db.scalar(
        select(PackageWorkflowStage).where(
            PackageWorkflowStage.organization_id == package.organization_id,
            PackageWorkflowStage.package_id == package.id,
            PackageWorkflowStage.transmit_on_approve.is_(True),
        )
    )


# ---------------------------------------------------------------------------
# The internal chain
# ---------------------------------------------------------------------------


def stage_entered(
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    note: str | None = None,
) -> None:
    """The return now waits at its current stage: tell everyone who can take it."""
    stage = _stage(db, package, package.current_stage_seq)
    if stage is None or stage.decision_kind == "prepare":
        return
    actor = _person(db, ctx.actor_user_id)
    label = return_label(package)
    publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_REVIEW_REQUESTED,
        message=Message(
            title=f"{label} is waiting for {stage.title}",
            summary=(
                f"{actor} sent version {package.version} of {label} to {stage.title}. "
                f"You can take this stage's decision."
            ),
            facts=_facts(db, package, Fact("Stage", stage.title), Fact("Sent by", actor)),
            quote=note,
            quote_label=f"Note from {actor}",
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=stage_holders(db, ctx, package, stage),
    )
    from app.reporting.service import review_reminders  # noqa: PLC0415 - imports this module

    review_reminders.schedule_for_stage(db, package.organization_id)


def stage_approved(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    stage: PackageWorkflowStage,
    *,
    comment: str | None = None,
) -> None:
    """A stage approved: tell the preparer and the officers before this stage."""
    actor = _person(db, ctx.actor_user_id)
    label = return_label(package)
    earlier = [
        row.decided_by
        for row in _decisions(db, package)
        if row.round == package.workflow_round and row.stage_seq < stage.seq
    ]
    final = stage.transmit_on_approve
    publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_STAGE_APPROVED,
        message=Message(
            title=f"{label} was approved at {stage.title}",
            summary=(
                f"{actor} approved version {package.version} of {label} at {stage.title}."
                + (
                    " It is ready to be filed with the regulator."
                    if final
                    else " It moves on once they send it to the next stage."
                )
            ),
            facts=_facts(db, package, Fact("Stage", stage.title), Fact("Approved by", actor)),
            quote=comment,
            quote_label=f"Comment from {actor}",
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=[package.generated_by, *earlier],
    )


def sent_back(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    from_stage: PackageWorkflowStage,
    target_seq: int,
    comment: str | None,
) -> None:
    """A stage returned the return: tell whoever must pick it up again."""
    target = _stage(db, package, target_seq)
    if target is None:
        return
    actor = _person(db, ctx.actor_user_id)
    label = return_label(package)
    previous = [row.decided_by for row in _decisions(db, package) if row.stage_seq == target_seq]
    if target.decision_kind == "prepare":
        previous.append(package.generated_by)
    recipients = previous or stage_holders(db, ctx, package, target)
    publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_SENT_BACK,
        message=Message(
            title=f"{label} was sent back to {target.title}",
            summary=(
                f"{actor} sent {label} back from {from_stage.title} to {target.title}. "
                "Address the comments below, then send it on again."
            ),
            facts=_facts(
                db,
                package,
                Fact("Sent back from", from_stage.title),
                Fact("Now with", target.title),
                Fact("Sent back by", actor),
            ),
            quote=comment,
            quote_label=f"Why {actor} sent it back",
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=recipients,
    )


#: How each filing channel is described to a person. Worded for any
#: jurisdiction: the regulator's own system name is never assumed.
CHANNEL_LABELS: dict[str, str] = {
    "orass_api": "the regulator's online returns system",
    "orass_sandbox": "the regulator's test system",
    "reporting_gateway": "the AequorOS Reporting Gateway",
    "email": "email (fallback while the regulator's system is down)",
    "manual": "outside AequorOS (recorded by hand)",
}


def submission_recorded(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    channel: str,
    reference: str | None,
    detail: dict[str, Any] | None,
) -> None:
    """A submission was recorded: announce the filing and, with a receipt, the receipt."""
    filed(db, ctx, package, channel=channel, reference=reference)
    receipt = (detail or {}).get("receipt")
    if channel == "reporting_gateway" and isinstance(receipt, dict) and reference:
        regulator_received(db, ctx, package, reference=reference)


def filed(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    channel: str,
    reference: str | None,
) -> None:
    """The return was transmitted: tell everyone who worked on it."""
    channel_label = CHANNEL_LABELS.get(channel, channel.replace("_", " "))
    bank = _bank(db, package)
    regulator = jurisdictions.regulator_name(db, bank) if bank is not None else "the regulator"
    actor = _person(db, ctx.actor_user_id)
    label = return_label(package)
    extra = [Fact("Filed by", actor), Fact("Sent through", channel_label)]
    if reference:
        extra.append(Fact("Reference", reference))
    publish_for_return(
        db,
        ctx,
        package,
        event=catalog.FILING_FILED,
        message=Message(
            title=f"{label} was filed with {regulator}",
            summary=(
                f"{actor} filed version {package.version} of {label} with {regulator}. "
                "You will hear here when they respond."
            ),
            facts=_facts(db, package, *extra, bank=bank),
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=participants(db, package),
    )


# ---------------------------------------------------------------------------
# The regulator
# ---------------------------------------------------------------------------


def _regulator_audience(db: Session, ctx: TenantContext, package: RegulatoryPackage) -> list[UUID]:
    people = participants(db, package)
    transmit = _transmit_stage(db, package)
    if transmit is not None:
        people.extend(stage_holders(db, ctx, package, transmit, maker_checker=False))
    return list(dict.fromkeys(people))


def regulator_comment(detail: dict[str, Any] | None) -> str | None:
    for key in _COMMENT_KEYS:
        value = (detail or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    validation = (detail or {}).get("validation")
    if isinstance(validation, dict):
        issues = validation.get("issues")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(issues, list) and issues:
            lines = [
                str(issue.get("message") if isinstance(issue, dict) else issue)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                for issue in issues[:10]  # pyright: ignore[reportUnknownVariableType]
            ]
            return "\n".join(f"• {line}" for line in lines)
    return None


def regulator_received(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    reference: str,
) -> None:
    bank = _bank(db, package)
    regulator = jurisdictions.regulator_name(db, bank) if bank is not None else "The regulator"
    label = return_label(package)
    publish_for_return(
        db,
        ctx,
        package,
        event=catalog.REGULATOR_RECEIVED,
        message=Message(
            title=f"{regulator} received {label}",
            summary=(
                f"{regulator} issued receipt {reference} for {label} and is validating it. "
                "You will hear here when it is accepted or sent back."
            ),
            facts=_facts(db, package, Fact("Receipt", reference), bank=bank),
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=_regulator_audience(db, ctx, package),
        dedupe_key=f"{package.id}:{reference}",
    )


_DECISION_EVENTS = {
    "acknowledged": catalog.REGULATOR_ACCEPTED,
    "rejected": catalog.REGULATOR_SENT_BACK,
    "declined": catalog.REGULATOR_DECLINED,
}


def regulator_decided(  # noqa: PLR0913 - one notice, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    event: str,
    reference: str | None,
    detail: dict[str, Any] | None,
) -> None:
    """The regulator accepted, sent back or declined a filed return."""
    definition = _DECISION_EVENTS.get(event)
    if definition is None:
        return
    bank = _bank(db, package)
    regulator = jurisdictions.regulator_name(db, bank) if bank is not None else "The regulator"
    label = return_label(package)
    extra = [Fact("Reference", reference)] if reference else []
    if event == "acknowledged":
        title = f"{regulator} accepted {label}"
        summary = (
            f"{regulator} accepted version {package.version} of {label}. Nothing more is needed."
        )
    elif event == "rejected":
        title = f"{regulator} sent {label} back for correction"
        summary = (
            f"{regulator} returned version {package.version} of {label} for correction. "
            "Prepare a corrected version and file it again."
        )
    else:
        title = f"{regulator} declined {label}"
        summary = (
            f"{regulator} refused version {package.version} of {label}. "
            "Contact your supervisor at the regulator before filing again."
        )
    publish_for_return(
        db,
        ctx,
        package,
        event=definition,
        message=Message(
            title=title,
            summary=summary,
            facts=_facts(db, package, *extra, bank=bank),
            quote=regulator_comment(detail),
            quote_label=f"Comments from {regulator}",
            action=Action(f"Open {package.return_code}", return_path(package)),
        ),
        recipients=_regulator_audience(db, ctx, package),
        dedupe_key=f"{package.id}:{event}:{reference or ''}",
    )


def regulator_query(  # noqa: PLR0913 - one query, spelled out
    db: Session,
    ctx: TenantContext,
    package: RegulatoryPackage,
    *,
    number: str,
    subject: str | None,
    question: str | None,
    due: date | None,
) -> int:
    """The regulator asked a question about a filed return. Returns people newly told."""
    bank = _bank(db, package)
    regulator = jurisdictions.regulator_name(db, bank) if bank is not None else "The regulator"
    label = return_label(package)
    extra = [Fact("Query", number)]
    if subject:
        extra.append(Fact("Subject", subject))
    if due is not None:
        extra.append(Fact("Response due", human_date(due)))
    return publish_for_return(
        db,
        ctx,
        package,
        event=catalog.REGULATOR_QUERY,
        message=Message(
            title=f"{regulator} raised a query on {label}",
            summary=f"{regulator} asked a question about {label} that needs a response.",
            facts=_facts(db, package, *extra, bank=bank),
            quote=question,
            quote_label=f"Question from {regulator}",
            action=Action("Answer the query", "/submissions/queries"),
        ),
        recipients=_regulator_audience(db, ctx, package),
        dedupe_key=f"{package.id}:query:{number}",
    )


def recipients_for_review(
    db: Session, ctx: TenantContext, package: RegulatoryPackage
) -> Sequence[UUID]:
    """Who the current stage waits on (the escalation scan's reminder audience)."""
    stage = _stage(db, package, package.current_stage_seq)
    if stage is None:
        return []
    return stage_holders(db, ctx, package, stage)


__all__ = [
    "CHANNEL_LABELS",
    "filed",
    "human_date",
    "participants",
    "publish_for_return",
    "recipients_for_review",
    "regulator_comment",
    "regulator_decided",
    "regulator_query",
    "regulator_received",
    "return_label",
    "return_path",
    "sent_back",
    "stage_approved",
    "stage_entered",
    "stage_holders",
    "submission_recorded",
]
