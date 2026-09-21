"""Shared ICAAP guards: what exists, who may see it, and what may still change.

Every ICAAP read goes through :func:`get_cycle_or_404`, which is the single
place the examiner rule lives. An impersonated examiner sees only cycles that
were frozen at some point, because the workspace before a freeze is a bank's
working draft and a supervisor reading it would be reading unapproved figures
the Board has never seen. Routing a new read around this function is how that
property would quietly be lost, so P3 must keep using it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.domain.icaap.frameworks import registry
from app.domain.icaap.frameworks.schema import Framework
from app.models import Bank
from app.models.icaap import IcaapCycle
from app.services import institution_types
from app.services.filing_workflow.errors import conflict, not_found, unprocessable

#: The only cycles an impersonated examiner may read: ones that were frozen, so
#: the text and figures are the ones a Board approved.
EXAMINER_VISIBLE_STATUSES: frozenset[str] = frozenset(
    {"frozen", "board_approved", "submitted", "acknowledged", "superseded"}
)
#: Text and figures lock at submission, so every stage of the review reads the
#: same digest. P3 deliberately does NOT add ``in_review`` here.
EDITABLE_STATUSES: frozenset[str] = frozenset({"draft", "returned"})
#: Evidence is different. A reviewer asking for the Board minutes is the normal
#: way of getting them, and forcing a send-back to attach a file would make the
#: review trail worse rather than better — so uploads and withdrawals stay open
#: while the cycle is under review. The review digest deliberately excludes
#: attachments for the same reason (``domain/icaap/review_digest.py``); the
#: freeze gate checks them separately and at that point they are sealed.
ATTACHABLE_STATUSES: frozenset[str] = frozenset({"draft", "in_review", "returned"})

# ``conflict``, ``unprocessable`` and ``not_found`` now live in
# ``services/filing_workflow/errors.py``: the shared chain engine raises them
# and cannot import the ICAAP. They are re-exported here so every existing
# ``guards.conflict(...)`` call site is unchanged.


def actor_id(access: IcaapAccess) -> UUID:
    """The tenant user behind a mutation.

    Impersonated sessions never reach a mutation dependency, so this is a
    last-line invariant rather than a user-facing path.
    """
    if access.ctx.actor_user_id is None:  # pragma: no cover - refused upstream
        raise conflict("actor_required", "This action requires a signed-in user.")
    return access.ctx.actor_user_id


def require_bank_class(db: Session, bank: Bank) -> None:
    """ICAAP is a banks-and-financial-holding-companies regime.

    A deposit-taking institution outside it gets a 404 rather than a 403: the
    surface does not exist for that licence class, and saying "forbidden" would
    imply it might. An unresolvable licence propagates its own 409 unchanged —
    fail-closed beats guessing.
    """
    if institution_types.institution_class(db, bank) != "bank":
        not_found()


def require_examiner(ctx: TenantContext) -> None:
    """The impersonated-read branch: staff provenance and no blocking condition."""
    from app.services import authorization as authorization_service  # noqa: PLC0415

    if ctx.actor_operator is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ICAAP access requires an active scoped binding.",
        )
    failed = [
        check for check in authorization_service.request_wide_condition_checks() if not check.passed
    ]
    if failed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ICAAP access requires an active scoped binding.",
        )


def get_cycle_or_404(
    db: Session, access: IcaapAccess, cycle_id: UUID, *, for_update: bool = False
) -> IcaapCycle:
    """One cycle of this tenant's institution, or 404.

    ``for_update`` takes a shared lock (FOR SHARE), so concurrent section saves
    do not block each other while P3's freeze — which takes FOR UPDATE — still
    serialises against every one of them.
    """
    statement = select(IcaapCycle).where(
        IcaapCycle.id == cycle_id,
        IcaapCycle.organization_id == access.ctx.organization_id,
        IcaapCycle.bank_id == access.bank.id,
    )
    if access.examiner:
        statement = statement.where(
            IcaapCycle.frozen_at.is_not(None),
            IcaapCycle.status.in_(sorted(EXAMINER_VISIBLE_STATUSES)),
        )
    if for_update:
        statement = statement.with_for_update(read=True)
    cycle = db.scalar(statement)
    if cycle is None:
        not_found()
    return cycle


def require_editable(cycle: IcaapCycle) -> None:
    """Refuse a write to a cycle that is sealed, archived or under review."""
    if cycle.status in EDITABLE_STATUSES:
        return
    if cycle.frozen_at is not None or cycle.status in EXAMINER_VISIBLE_STATUSES:
        raise conflict(
            "cycle_sealed",
            "This ICAAP has been frozen. Its text and figures are the ones the Board "
            "approved and cannot be edited; start a new round to make changes.",
            status=cycle.status,
        )
    raise conflict(
        "cycle_locked",
        "This ICAAP cannot be edited in its current state.",
        status=cycle.status,
    )


def require_attachable(cycle: IcaapCycle) -> None:
    """Refuse an evidence change to a cycle that is sealed or retired.

    Wider than :func:`require_editable` by exactly one status, ``in_review``,
    and the refusal messages are the same ones — so a preparer who tries to
    attach to a frozen ICAAP reads the same sentence as one who tries to edit
    its text.
    """
    if cycle.status in ATTACHABLE_STATUSES and cycle.frozen_at is None:
        return
    require_editable(cycle)


def framework_for(cycle: IcaapCycle) -> tuple[Framework, bool]:
    """The pinned framework and whether its content still matches the pin.

    A mismatch means a published version was edited in place — the one thing the
    registry forbids. Readiness reports it as blocking rather than letting the
    bank's checklist change under a cycle that has already been reviewed.
    """
    from app.services.icaap import frameworks  # noqa: PLC0415 - avoid an import cycle

    frameworks.sync_extra_roots()
    framework = registry.get(cycle.framework_code, cycle.framework_version)
    return framework, framework.digest == cycle.framework_sha256


def require_framework(cycle: IcaapCycle) -> Framework:
    framework, _matches = framework_for(cycle)
    return framework


__all__ = [
    "ATTACHABLE_STATUSES",
    "EDITABLE_STATUSES",
    "EXAMINER_VISIBLE_STATUSES",
    "actor_id",
    "conflict",
    "framework_for",
    "get_cycle_or_404",
    "not_found",
    "require_attachable",
    "require_bank_class",
    "require_editable",
    "require_examiner",
    "require_framework",
    "unprocessable",
]
