"""Capital floors for the ICAAP, resolved per date (DV-005).

The ICAAP reads the same effective minima the capital plan does — the board
register clamped tighten-only against the governed control plane — so there is
one definition of "the minimum this institution must hold" rather than an
ICAAP one and a capital one that can disagree at a filing.

Per DATE, because an ICAAP projects several years and a floor that commences
mid-horizon applies to the years after it, not to all of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.schemas.capital_plan import CapitalFloorRead
from app.services import capital_plan, institution_types
from app.services.institution_types import InstitutionTypeUnresolved

#: Everything the ICAAP measures itself against. ``car_min`` is the Pillar 1
#: total-capital minimum used to convert RWA into a capital requirement.
FLOOR_CODES: tuple[str, ...] = ("car_min", "tier1_min", "cet1_min", "leverage_min")


def basel_regime(db: Session, access: IcaapAccess) -> bool:
    """Whether the tiered (CRD) minima apply. ICAAP is banks-only, so they do;
    the guard stays because the licence lookup can legitimately fail."""
    try:
        return institution_types.institution_class(db, access.bank) == "bank"
    except InstitutionTypeUnresolved:
        return False


def capital_floors_by_date(
    db: Session,
    access: IcaapAccess,
    *,
    dates: Sequence[date],
    codes: Sequence[str] = FLOOR_CODES,
) -> dict[date, dict[str, CapitalFloorRead]]:
    """The effective minima at each date, or a typed refusal naming the gap."""
    return capital_plan.capital_floors_by_date(
        db,
        access.ctx,
        access.bank,
        list(dates),
        tuple(codes),
        basel=basel_regime(db, access),
    )


def capital_floors(
    db: Session,
    access: IcaapAccess,
    *,
    as_of: date,
    codes: Sequence[str] = FLOOR_CODES,
) -> dict[str, CapitalFloorRead]:
    return capital_floors_by_date(db, access, dates=[as_of], codes=codes)[as_of]


def car_min_pct(db: Session, access: IcaapAccess, *, as_of: date) -> CapitalFloorRead:
    """The total-capital minimum, or ``missing_parameter`` naming ``car_min``.

    There is no fallback: a Pillar 2 add-on quoted against a capital ratio the
    platform invented would be a number nobody approved (D-024 §4).
    """
    from app.services.icaap import params  # noqa: PLC0415 - avoid an import cycle

    floors = capital_floors(db, access, as_of=as_of, codes=("car_min",))
    floor = floors.get("car_min")
    if floor is None:
        raise params.missing_parameter("car_min")
    return floor


def unavailable_detail(exc: HTTPException) -> dict[str, object]:
    """The typed body of a projection refusal, for embedding in a read."""
    detail = exc.detail
    return detail if isinstance(detail, dict) else {"message": str(detail)}


__all__ = [
    "FLOOR_CODES",
    "basel_regime",
    "capital_floors",
    "capital_floors_by_date",
    "car_min_pct",
    "unavailable_detail",
]
