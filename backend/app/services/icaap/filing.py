"""The Filing view: one answer to "can this ICAAP be sent, and if not, why not?".

The signatures, the documents and the submission gate each live in the generic
regulatory plane, and each answers its own question well. What a preparer needs
is the three of them in one sentence, computed the same way the submission gate
computes them — so the screen and the server cannot disagree about whether the
report is ready.

That is the whole contract here: this module RE-USES ``ensure_submittable``
rather than re-implementing its rules, catches the refusal, and turns it into a
list of blockers with the same codes. A second copy of the rules would be a
screen that says "ready" about a submission the server will refuse.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.models import RegulatoryPackage
from app.models.icaap import IcaapCycle
from app.schemas.icaap import (
    IcaapFilingAttachmentRead,
    IcaapFilingRead,
    IcaapFilingSlotRead,
    IcaapPreflightItemRead,
)
from app.services.icaap import freeze, guards


def _blocker(code: str, message: str) -> IcaapPreflightItemRead:
    return IcaapPreflightItemRead(
        code=code, severity="blocking", scope="cycle", ref=None, message=message
    )


def _detail(exc: HTTPException) -> tuple[str, str]:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("error_code") or "not_submittable"), str(
            detail.get("message") or detail
        )
    return "not_submittable", str(detail)


def _slots(
    db: Session, access: IcaapAccess, package: RegulatoryPackage, framework_filing: Any
) -> tuple[list[IcaapFilingSlotRead], str]:
    from app.services.attestation import workflow as attestation  # noqa: PLC0415 - avoid a cycle

    policy = attestation.package_policy(db, access.ctx, package)
    signatures = {
        row.signing_role: row for row in attestation.current_signatures(db, access.ctx, package)
    }
    order = [slot.role for slot in policy.slots]
    slots: list[IcaapFilingSlotRead] = []
    for index, slot in enumerate(policy.slots):
        signed = signatures.get(slot.role)
        earlier_unsigned = [role for role in order[:index] if role not in signatures]
        slots.append(
            IcaapFilingSlotRead(
                role=slot.role,
                required=slot.min_count > 0,
                line=(
                    framework_filing.attestation.line_for(slot.role)
                    if framework_filing is not None
                    else None
                ),
                statement=(
                    framework_filing.attestation.statement_for(slot.role)
                    if framework_filing is not None
                    else None
                ),
                signed_by=None if signed is None else signed.signer_display_name,
                signed_at=None if signed is None else signed.declared_at,
                blocked_by=(
                    None
                    if signed is not None or not (policy.ordered_slots and earlier_unsigned)
                    else earlier_unsigned[-1]
                ),
            )
        )
    return slots, package.attestation_state


def _attachments(
    db: Session, access: IcaapAccess, package: RegulatoryPackage
) -> list[IcaapFilingAttachmentRead]:
    from app.services.attestation import workflow as attestation  # noqa: PLC0415 - avoid a cycle
    from app.services.regulatory_reporting import attachments  # noqa: PLC0415 - avoid a cycle

    policy = attestation.package_policy(db, access.ctx, package)
    return [
        IcaapFilingAttachmentRead(
            kind=row.kind,
            title=row.title,
            gate=row.gate,
            min_count=row.required_count,
            present=row.active_count,
            applies=True,
            satisfied=row.satisfied,
        )
        for row in attachments.requirement_rows(db, access.ctx, package, policy)
    ]


def _package(db: Session, cycle: IcaapCycle) -> RegulatoryPackage | None:
    return None if cycle.package_id is None else db.get(RegulatoryPackage, cycle.package_id)


def get_filing(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapFilingRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    package = _package(db, cycle)
    if package is None:
        return IcaapFilingRead(
            cycle_id=cycle.id,
            package=None,
            attestation_state="not_started",
            slots=[],
            attachments=[],
            submittable=False,
            blockers=[
                _blocker(
                    "cycle_not_frozen",
                    "This ICAAP has not been frozen yet, so there is nothing to file.",
                )
            ],
        )
    slots, state = _slots(db, access, package, framework.filing)
    blockers: list[IcaapPreflightItemRead] = []
    if package.status in {"submitted", "acknowledged"}:
        submittable = False
    else:
        from app.services.attestation import workflow as attestation  # noqa: PLC0415

        try:
            attestation.ensure_submittable(db, access.ctx, package)
            submittable = True
        except HTTPException as exc:
            # Every refusal on this path is an HTTPException, including the
            # typed AttestationConflict. Anything else is a programming error
            # and propagates as a 500: reporting a bug to a bank as "your
            # filing is blocked" would send them looking for a document.
            code, message = _detail(exc)
            blockers.append(_blocker(code, message))
            submittable = False
    return IcaapFilingRead(
        cycle_id=cycle.id,
        package=freeze.package_summary(package),
        attestation_state=state,
        slots=slots,
        attachments=_attachments(db, access, package),
        submittable=submittable,
        blockers=blockers,
    )


__all__ = ["get_filing"]
