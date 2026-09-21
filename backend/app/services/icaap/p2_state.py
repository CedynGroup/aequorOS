"""The Pillar 2 half of what a reviewer approved, as one value-based digest.

The review digest has to cover the risk-and-capital assessment as well as the
narrative, or a CRO could approve a report and somebody could then change the
operational-risk add-on without any stage having to look again. This module is
the seam: the workflow asks for one digest and knows nothing about Pillar 2
internals, so P2 can grow without the workflow changing.

``None`` means "this cycle has no Pillar 2 state", which is a real answer — a
cycle whose register is empty has nothing for the digest to cover — and it is
recorded as ``null`` rather than as an empty-string digest, so a later cycle
that DOES have state cannot accidentally hash to the same value.

Value-based, like every other digest in this codebase: the approved revision
NUMBER and its content hash, never the revision row's id.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.workflow import chain
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import (
    IcaapControlExplanation,
    IcaapPillar2Item,
    IcaapPillar2ItemRevision,
    IcaapRequirementReconciliationLine,
    IcaapResourcesReconciliationLine,
)
from app.services.icaap.digests import register_digest


def _amount(value: Decimal | None) -> str | None:
    """A money value as ONE canonical string, whatever its scale (D-067).

    The normalisation itself lives in the shared chain engine
    (``domain/workflow/chain.normalise_amount``) because the BoG filing chain's
    review digest needs exactly the same rule, and a second hand-written
    ``str(value)`` in a digest body is how this bug comes back.
    """
    return chain.normalise_amount(value)


def _items(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[list[Any]]:
    rows = db.scalars(
        select(IcaapPillar2Item).where(
            IcaapPillar2Item.organization_id == access.ctx.organization_id,
            IcaapPillar2Item.cycle_id == cycle.id,
            IcaapPillar2Item.retired_at.is_(None),
        )
    ).all()
    if not rows:
        return []
    digests = {
        (revision.item_id, revision.revision_no): revision.snapshot_sha256
        for revision in db.scalars(
            select(IcaapPillar2ItemRevision).where(
                IcaapPillar2ItemRevision.organization_id == access.ctx.organization_id,
                IcaapPillar2ItemRevision.cycle_id == cycle.id,
            )
        )
    }
    return sorted(
        [
            row.item_key,
            row.method_status,
            row.current_revision_no,
            row.approved_revision_no,
            digests.get((row.id, row.current_revision_no)),
            _amount(row.baseline_amount),
            _amount(row.stressed_amount),
        ]
        for row in rows
    )


def _reconciliation(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, Any]:
    requirement = sorted(
        [row.line_key, row.computed_digest, row.explanation is not None]
        for row in db.scalars(
            select(IcaapRequirementReconciliationLine).where(
                IcaapRequirementReconciliationLine.organization_id == access.ctx.organization_id,
                IcaapRequirementReconciliationLine.cycle_id == cycle.id,
            )
        )
    )
    resources = sorted(
        [row.line_key, _amount(row.internal_amount)]
        for row in db.scalars(
            select(IcaapResourcesReconciliationLine).where(
                IcaapResourcesReconciliationLine.organization_id == access.ctx.organization_id,
                IcaapResourcesReconciliationLine.cycle_id == cycle.id,
            )
        )
    )
    controls = sorted(
        [row.control_code, row.comparison_key, row.values_digest]
        for row in db.scalars(
            select(IcaapControlExplanation).where(
                IcaapControlExplanation.organization_id == access.ctx.organization_id,
                IcaapControlExplanation.cycle_id == cycle.id,
            )
        )
    )
    return {"requirement": requirement, "resources": resources, "controls": controls}


def state_digest(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> str | None:
    """A digest of the Pillar 2 state a decision is being taken on, or ``None``."""
    items = _items(db, access, cycle)
    reconciliation = _reconciliation(db, access, cycle)
    if not items and not any(reconciliation.values()):
        return None
    return register_digest({"schema": "icaap-p2-state-v1", "items": items, **reconciliation})


__all__ = ["state_digest"]
