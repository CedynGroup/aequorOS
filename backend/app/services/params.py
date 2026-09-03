from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.regulatory import RegulatoryParameterMixin


@dataclass(frozen=True)
class PrefetchedActiveParams[ParamT: RegulatoryParameterMixin]:
    """Effective-dated tenant rows loaded once for a bounded date window."""

    rows: tuple[ParamT, ...]

    def active_on(self, as_of_date: date) -> list[ParamT]:
        """Mirror :func:`get_active_params` without another database round trip."""
        return [
            row
            for row in self.rows
            if row.effective_from <= as_of_date
            and (row.effective_to is None or row.effective_to > as_of_date)
        ]


def get_active_params[ParamT: RegulatoryParameterMixin](
    session: Session,
    organization_id: str,
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


def prefetch_active_params[ParamT: RegulatoryParameterMixin](
    session: Session,
    organization_id: str,
    jurisdiction_code: str,
    model: type[ParamT],
    as_of_dates: list[date],
) -> PrefetchedActiveParams[ParamT]:
    """Load every generation that can be active on one of ``as_of_dates``.

    The query is tenant- and jurisdiction-scoped and bounded to the requested
    effective-date window. ``active_on`` then applies the exact same date rule
    and ordering as :func:`get_active_params` for each dashboard trend point.
    """
    if not as_of_dates:
        return PrefetchedActiveParams(())
    first, last = min(as_of_dates), max(as_of_dates)
    statement = (
        select(model)
        .where(
            model.organization_id == organization_id,
            model.jurisdiction_code == jurisdiction_code,
            model.effective_from <= last,
            or_(model.effective_to.is_(None), model.effective_to > first),
        )
        .order_by(model.effective_from, model.id)
    )
    return PrefetchedActiveParams(tuple(session.scalars(statement)))
