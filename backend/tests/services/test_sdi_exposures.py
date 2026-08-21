"""SDI exposure limits — single-obligor + large-exposure fire on breach, with
class-aware thresholds from the control plane (docs/sdi.md §4.3, Phase F).

An SDI's per-obligor large-exposure limit is 15% of NOF vs a bank's 20%; the
single-obligor limit is 25% for both (Act 930 s.62(1)). The same 18%-of-NOF
exposure therefore breaches for an SDI but not for a bank — proving the limit is
resolved for the institution class, not hardcoded.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, RelatedParty
from app.services.regulatory_reporting.le_generation import (
    _append_aggregate_exposure_finding,  # pyright: ignore[reportPrivateUsage]
    _append_exposure_limit_findings,  # pyright: ignore[reportPrivateUsage]
    _append_related_party_finding,  # pyright: ignore[reportPrivateUsage]
    _Entity,  # pyright: ignore[reportPrivateUsage]
)
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)
_NOF = Decimal("1000")


def _bank(db: Session, *, institution_type: str) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Exp",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _entity(name: str, exposure: str) -> _Entity:
    return _Entity(
        key=name, name=name, connection="group", tin="", exempt=False, drawn=Decimal(exposure)
    )


def _codes(findings: list[dict[str, str]]) -> list[str]:
    return [f["rule"] for f in findings]


def test_sdi_large_exposure_breach_fires_but_bank_within_limit_does_not(
    db_session: Session,
) -> None:
    # 180 / 1000 = 18% of NOF: over the SDI 15% large-exposure limit, under the
    # bank 20% limit, and under the 25% single-obligor limit for both.
    entities = [_entity("Acme Group", "180")]

    sdi = _bank(db_session, institution_type="savings_and_loans")
    sdi_findings: list[dict[str, str]] = []
    _append_exposure_limit_findings(db_session, sdi, _AS_OF, entities, _NOF, sdi_findings)
    assert _codes(sdi_findings) == ["le.large_exposure_limit_exceeded"]
    assert "15" in sdi_findings[0]["detail"]

    bank = _bank(db_session, institution_type="universal_bank")
    bank_findings: list[dict[str, str]] = []
    _append_exposure_limit_findings(db_session, bank, _AS_OF, entities, _NOF, bank_findings)
    assert bank_findings == []  # 18% is within the bank's 20% limit


def test_single_obligor_and_large_exposure_both_fire_above_25pct(db_session: Session) -> None:
    # 300 / 1000 = 30%: breaches the 25% single-obligor limit AND the large-
    # exposure limit for either class.
    entities = [_entity("Mega Group", "300")]
    bank = _bank(db_session, institution_type="universal_bank")
    findings: list[dict[str, str]] = []
    _append_exposure_limit_findings(db_session, bank, _AS_OF, entities, _NOF, findings)
    assert set(_codes(findings)) == {
        "le.single_obligor_limit_exceeded",
        "le.large_exposure_limit_exceeded",
    }


def test_exposure_within_limits_raises_no_finding(db_session: Session) -> None:
    entities = [_entity("Small Co", "100")]  # 10% of NOF
    sdi = _bank(db_session, institution_type="savings_and_loans")
    findings: list[dict[str, str]] = []
    _append_exposure_limit_findings(db_session, sdi, _AS_OF, entities, _NOF, findings)
    assert findings == []


def test_aggregate_cap_fires_advisory_when_larges_exceed_cap(db_session: Session) -> None:
    # The seeded cap is 8x NOF, confirmation_status=pending → an ADVISORY (INFO)
    # finding, not a hard breach. NOF 1000 → cap 8000; larges summing 9000 breach.
    sdi = _bank(db_session, institution_type="savings_and_loans")
    larges = [_entity("A", "3000"), _entity("B", "3000"), _entity("C", "3000")]
    findings: list[dict[str, str]] = []
    _append_aggregate_exposure_finding(db_session, sdi, _AS_OF, larges, _NOF, findings)
    assert _codes(findings) == ["le.aggregate_large_exposure_cap_exceeded"]
    assert findings[0]["severity"] == "INFO"  # pending value → advisory


def test_aggregate_cap_silent_within_cap(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    larges = [_entity("A", "500")]  # 500 well under the 8000 cap
    findings: list[dict[str, str]] = []
    _append_aggregate_exposure_finding(db_session, sdi, _AS_OF, larges, _NOF, findings)
    assert findings == []


def test_related_party_lending_exposure_advisory(db_session: Session) -> None:
    """Related-party lending exposure is aggregated by matching the RelatedParty
    register to the exposure book and compared to the (pending) limit — advisory
    while the value is unconfirmed (docs/sdi.md §4.3)."""
    sdi = _bank(db_session, institution_type="savings_and_loans")
    db_session.add(
        RelatedParty(
            organization_id=ORG_1,
            bank_id=sdi.id,
            party_type="legal_entity",
            full_name="Acme Group",
        )
    )
    db_session.flush()
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    # Acme (a related party) at 300/1000 = 30% of NOF > the 25% related-party limit.
    entities = [_entity("Acme Group", "300"), _entity("Unrelated Co", "100")]
    findings: list[dict[str, str]] = []
    _append_related_party_finding(db_session, ctx, sdi, _AS_OF, entities, _NOF, findings)
    assert _codes(findings) == ["le.related_party_limit_exceeded"]
    assert findings[0]["severity"] == "INFO"  # limit value pending → advisory


def test_related_party_check_skipped_without_register(db_session: Session) -> None:
    sdi = _bank(db_session, institution_type="savings_and_loans")
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    entities = [_entity("Some Co", "900")]  # large, but no related-party register
    findings: list[dict[str, str]] = []
    _append_related_party_finding(db_session, ctx, sdi, _AS_OF, entities, _NOF, findings)
    assert findings == []
