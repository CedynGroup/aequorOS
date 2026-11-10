from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.policy.models.parameter_register import (
    BankOwnedParameterMixin,
    RegulatoryParameterMixin,
)


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


def get_active_params[ParamT: RegulatoryParameterMixin](  # noqa: PLR0913 - complete tenant/bank resolution key
    session: Session,
    organization_id: str,
    jurisdiction_code: str,
    model: type[ParamT],
    as_of_date: date,
    *,
    bank_id: str | None = None,
) -> list[ParamT]:
    """Return parameter rows active on ``as_of_date`` for one tenant and jurisdiction.

    A row is active when ``effective_from <= as_of_date`` and ``effective_to`` is
    either null (open-ended) or strictly greater than ``as_of_date``.
    """
    if issubclass(model, BankOwnedParameterMixin) and not bank_id:
        raise ValueError("Bank-local parameters require bank_id.")
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
    if issubclass(model, BankOwnedParameterMixin):
        statement = statement.where(model.bank_id == bank_id)
    return list(session.scalars(statement))


def prefetch_active_params[ParamT: RegulatoryParameterMixin](  # noqa: PLR0913 - complete tenant/bank resolution key
    session: Session,
    organization_id: str,
    jurisdiction_code: str,
    model: type[ParamT],
    as_of_dates: list[date],
    *,
    bank_id: str | None = None,
) -> PrefetchedActiveParams[ParamT]:
    """Load every generation that can be active on one of ``as_of_dates``.

    The query is tenant- and jurisdiction-scoped and bounded to the requested
    effective-date window. ``active_on`` then applies the exact same date rule
    and ordering as :func:`get_active_params` for each dashboard trend point.
    """
    if issubclass(model, BankOwnedParameterMixin) and not bank_id:
        raise ValueError("Bank-local parameters require bank_id.")
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
    if issubclass(model, BankOwnedParameterMixin):
        statement = statement.where(model.bank_id == bank_id)
    return PrefetchedActiveParams(tuple(session.scalars(statement)))


def get_active_bank_params[ParamT: BankOwnedParameterMixin](  # noqa: PLR0913 - complete tenant/bank resolution key
    session: Session,
    organization_id: str,
    bank_id: str,
    jurisdiction_code: str,
    model: type[ParamT],
    as_of_date: date,
) -> list[ParamT]:
    """Resolve only this bank's effective policy rows; never use the archive."""
    return get_active_params(
        session, organization_id, jurisdiction_code, model, as_of_date, bank_id=bank_id
    )
