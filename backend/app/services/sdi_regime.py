"""The SDI / universal-bank regime boundary, in one place.

An SDI and a universal bank are supervised under **different law** (Act 930 s.29
and the LMTD for the one, the BoG CRD framework for the other), so they have
separate calculation engines by design — the forensic audit of 2026-08-21 is
explicit that those engines must NOT be merged. What was missing was the gate:
some engines are written for one regime only, and nothing stopped the other
regime's tenant from being handed their output.

The gate is not a new opinion about which metric belongs to which class. That
declaration already exists, once, in WS-A's metric authority registry
(``app/domain/authority/registry.py``), where every metric names the
``institution_class`` and ``regime`` it is authoritative under. This module reads
that registry and refuses when a surface has no authority for the tenant's class,
so the boundary can never drift away from the declaration.

Forecasting is the case that motivated it (architecture audit §6, "Open
product/architecture gap"; §10 "Make the forecast engine regime-aware or
explicitly block/label Basel forecast ratios for SDI tenants"). Every metric in
``MetricFamily.FORECAST`` is registered under ``InstitutionClass.BANK`` with
``methodology_id='bank_forecast_projection_run'``: the projection's compliance
outputs are Basel CET1, Tier 1, CAR, leverage, LCR and NSFR, computed from Basel
parameters against Basel floors. None of those is the ratio a specialised
deposit-taking institution is measured on. There is no registered s.29 projection
methodology, so for an SDI the projection is REFUSED rather than produced —
showing a bank's ratios under an SDI's name would be a wrong number, not a
conservative one.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domain.authority.outcomes import (
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    outcome,
)
from app.domain.authority.registry import REGISTRY, InstitutionClass, MetricFamily
from app.models import Bank
from app.services import institution_types, jurisdictions

__all__ = [
    "RegimeNotApplicable",
    "family_has_authority",
    "forecast_regime_applies",
    "institution_class_enum",
    "require_bank_forecast_regime",
    "require_metric_family",
]


class RegimeNotApplicable(HTTPException, NotComputable):
    """A surface has no registered authority for this institution's regime.

    Doubly typed, exactly like ``institution_types.InstitutionTypeUnresolved``:
    ``HTTPException`` (409, this codebase's configured-state conflict code) so an
    API caller gets a precise, actionable message rather than a 500, and
    ``NotComputable`` so a worker or engine boundary that already handles WS-A's
    fail-closed outcomes handles this one identically.
    """

    def __init__(self, detail: OutcomeDetail) -> None:
        NotComputable.__init__(self, detail)
        HTTPException.__init__(self, status_code=status.HTTP_409_CONFLICT, detail=detail.message)


def institution_class_enum(db: Session, bank: Bank) -> InstitutionClass:
    """The tenant's class as the registry's enum. Fail-closed: an unresolved
    licence class raises ``InstitutionTypeUnresolved`` rather than defaulting."""
    return InstitutionClass(institution_types.institution_class(db, bank))


def family_has_authority(klass: InstitutionClass, family: MetricFamily) -> bool:
    """Whether ANY registered metric authority in ``family`` covers ``klass``.

    ``InstitutionClass.ALL`` entries count for every class — that is the
    registry's own encoding of a class-neutral engine (ECL, loan classification).
    """
    return any(entry.metric_family is family for entry in REGISTRY.for_institution_class(klass))


def require_metric_family(
    db: Session,
    bank: Bank,
    family: MetricFamily,
    *,
    surface: str,
    reason: str,
) -> None:
    """Refuse when no registered authority covers ``family`` for this tenant.

    ``surface`` is the user-facing name of the thing being asked for and
    ``reason`` is the plain-English explanation shown to the operator — this is
    production copy, not a developer string.
    """
    klass = institution_class_enum(db, bank)
    if family_has_authority(klass, family):
        return
    type_row = institution_types.get_type(db, bank)
    raise RegimeNotApplicable(
        outcome(
            OutcomeState.POLICY_UNRESOLVED,
            metric_id=f"{family.value}:{surface}",
            reason=reason,
            items=(
                f"bank:{bank.id}",
                f"institution_class:{klass.value}",
                f"metric_family:{family.value}",
            ),
            context={
                "bank_id": bank.id,
                "organization_id": bank.organization_id,
                "institution_type": type_row.type_code,
                "institution_class": type_row.institution_class,
                "capital_regime": type_row.capital_regime,
                "metric_family": family.value,
            },
        )
    )


#: The forecast run's compliance outputs, all of them Basel constructs. Named so
#: a reader can see exactly what is being withheld and why.
BASEL_FORECAST_OUTPUTS: tuple[str, ...] = (
    "car_pct",
    "tier1_ratio_pct",
    "cet1_ratio_pct",
    "lcr_pct",
    "nsfr_pct",
)

#: The statutory capital basis behind each ``institution_types.capital_regime``
#: code, for the refusal message. Ghana-factual content keyed on DATA, so a
#: future jurisdiction adds a row here rather than a branch (CLAUDE.md:
#: jurisdiction is data, never hardcoded country identity).
_CAPITAL_REGIME_BASIS: dict[str, str] = {
    "s29": "the Banks and Specialised Deposit-Taking Institutions Act 2016 (Act 930), s.29",
}


def _forecast_refusal(db: Session, bank: Bank) -> str:
    """The refusal an operator reads. Production copy: it says what is withheld,
    what this institution IS measured on, and why a bank's number is not shown
    in its place."""
    regulator = jurisdictions.regulator_name(db, bank)
    basis = _CAPITAL_REGIME_BASIS.get(institution_types.capital_regime(db, bank))
    capital_sentence = (
        f"its capital floor comes from {basis}"
        if basis
        else "its capital floor comes from the regime its own licence is supervised under"
    )
    return (
        "The five-year projection measures this institution against Basel capital and "
        "liquidity ratios — CET1, Tier 1, capital adequacy, leverage, and the liquidity "
        "coverage and net stable funding ratios. A specialised deposit-taking "
        f"institution is not supervised on any of those: {capital_sentence}, and its "
        f"liquidity from the {regulator} liquidity monitoring tools. No projection "
        "method has been approved for this licence class, so the projection is not "
        "produced. Showing a bank's ratios here would misstate this institution's "
        "position, not merely approximate it."
    )


def forecast_regime_applies(db: Session, bank: Bank) -> bool:
    """Whether the bank-plane forecast is authoritative for this tenant."""
    return family_has_authority(institution_class_enum(db, bank), MetricFamily.FORECAST)


def require_bank_forecast_regime(db: Session, bank: Bank) -> None:
    """Gate every entry point into the bank-plane forecast.

    Raises :class:`RegimeNotApplicable` for a tenant whose class has no
    registered forecast authority (today: every SDI licence class).
    """
    if forecast_regime_applies(db, bank):
        return
    require_metric_family(
        db,
        bank,
        MetricFamily.FORECAST,
        surface="balance_sheet_projection",
        reason=_forecast_refusal(db, bank),
    )
