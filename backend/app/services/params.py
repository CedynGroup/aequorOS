from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryParameterMixin


def get_active_params[ParamT: RegulatoryParameterMixin](
    session: Session,
    organization_id: UUID,
    jurisdiction_code: str,
    model: type[ParamT],
    as_of_date: date,
) -> list[ParamT]:
    """Return parameter rows active on ``as_of_date`` for one tenant and jurisdiction.

    A row is active when ``effective_from <= as_of_date`` and ``effective_to`` is
    either null (open-ended) or strictly greater than ``as_of_date``.
    """
    statement = (
        select(model)
        .where(
            model.organization_id == organization_id,
            model.jurisdiction_code == jurisdiction_code,
            model.effective_from <= as_of_date,
            or_(model.effective_to.is_(None), model.effective_to > as_of_date),
        )
        .order_by(model.effective_from, model.id)
    )
    return list(session.scalars(statement))
