"""SDI paid-up-capital + statutory-reserve-fund checks (docs/sdi.md §4.2, Phase E).

Control-plane-driven, isolated reads over the capital_structure reference dataset.
``paid_up_min`` is licence-specific (S&L = 15m GHS); missing data yields
NOT-COMPUTABLE, never a false green.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalReferenceRow, IngestionBatch, LineageRecord
from app.services import regulatory_capital
from app.services import sdi_capital_checks as checks
from tests.api.helpers import ORG_1, USER_1

_AS_OF = date(2026, 6, 30)
_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db: Session, *, institution_type: str = "savings_and_loans") -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name=f"{institution_type} tenant",
        short_name="Cap",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type=institution_type,
    )
    db.add(bank)
    db.flush()
    return bank


def _seed_capital_structure(
    db: Session, bank: Bank, rows: list[tuple[str, str]]
) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank.id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=_AS_OF,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="sdi-capital-checks-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    for i, (component, amount) in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=bank.id,
                ingestion_batch_id=batch.id,
                lineage_id=lineage.id,
                dataset_kind="capital_structure",
                as_of_date=_AS_OF,
                row_index=i,
                source_reference=f"CS/{i}",
                payload={"capital_component": component, "amount_ghs": amount, "tier": "CET1"},
            )
        )
    db.flush()


def test_paid_up_below_minimum_flags(db_session: Session) -> None:
    sdi = _bank(db_session)  # savings_and_loans → 15m floor
    _seed_capital_structure(db_session, sdi, [("paid_up_capital", "10000000")])
    result = checks.check_paid_up_capital(db_session, _CTX, sdi, _AS_OF)
    assert result.computable
    assert result.compliant is False
    assert result.actual_ghs == Decimal("10000000")
    assert result.required_ghs == Decimal("15000000")
    assert result.source_citation


def test_paid_up_meets_minimum(db_session: Session) -> None:
    sdi = _bank(db_session)
    _seed_capital_structure(db_session, sdi, [("paid_up_capital", "20000000")])
    result = checks.check_paid_up_capital(db_session, _CTX, sdi, _AS_OF)
    assert result.compliant is True


def test_paid_up_not_computable_without_dataset(db_session: Session) -> None:
    sdi = _bank(db_session)
    result = checks.check_paid_up_capital(db_session, _CTX, sdi, _AS_OF)
    assert result.compliant is None
    assert result.computable is False
    assert result.required_ghs == Decimal("15000000")  # the floor is still known
    assert "not computable" in result.detail.lower()


def test_paid_up_floor_is_licence_specific(db_session: Session) -> None:
    # A rural & community bank floor is 1m, a universal bank 400m — same check,
    # licence-specific value from the control plane.
    rcb = _bank(db_session, institution_type="rural_community_bank")
    _seed_capital_structure(db_session, rcb, [("paid_up_capital", "2000000")])
    result = checks.check_paid_up_capital(db_session, _CTX, rcb, _AS_OF)
    assert result.required_ghs == Decimal("1000000")
    assert result.compliant is True


def test_statutory_reserve_fund_still_building(db_session: Session) -> None:
    sdi = _bank(db_session)
    _seed_capital_structure(
        db_session, sdi, [("paid_up_capital", "20000000"), ("statutory_reserves", "5000000")]
    )
    result = checks.check_statutory_reserve_fund(db_session, _CTX, sdi, _AS_OF)
    assert result.compliant is False  # 5m < 20m paid-up → still building
    assert result.actual_ghs == Decimal("5000000")


def test_statutory_reserve_fund_fully_built(db_session: Session) -> None:
    sdi = _bank(db_session)
    _seed_capital_structure(
        db_session, sdi, [("paid_up_capital", "20000000"), ("statutory_reserves", "25000000")]
    )
    result = checks.check_statutory_reserve_fund(db_session, _CTX, sdi, _AS_OF)
    assert result.compliant is True


# --- P1-6: SDI checks reconciled into live findings + module status ----------


def test_paid_up_shortfall_becomes_a_critical_live_finding(db_session: Session) -> None:
    sdi = _bank(db_session)  # 15m floor
    _seed_capital_structure(db_session, sdi, [("paid_up_capital", "10000000")])  # below floor
    findings, status = regulatory_capital._sdi_capital_live_findings(
        db_session, _CTX, sdi, _AS_OF
    )
    rules = {f.rule_id: f for f in findings}
    assert "sdi_capital.paid_up_capital" in rules
    assert rules["sdi_capital.paid_up_capital"].severity == "critical"
    # A hard licensing floor breach drives the capital module red.
    assert status == "red"


def test_compliant_sdi_capital_emits_no_findings(db_session: Session) -> None:
    sdi = _bank(db_session)
    _seed_capital_structure(
        db_session, sdi, [("paid_up_capital", "20000000"), ("statutory_reserves", "25000000")]
    )
    findings, status = regulatory_capital._sdi_capital_live_findings(
        db_session, _CTX, sdi, _AS_OF
    )
    assert findings == ()
    assert status == "green"


def test_not_computable_check_raises_no_alert(db_session: Session) -> None:
    sdi = _bank(db_session)  # no capital_structure dataset seeded
    findings, status = regulatory_capital._sdi_capital_live_findings(
        db_session, _CTX, sdi, _AS_OF
    )
    # Missing data is not a breach — the s.29 page surfaces it, but no alert fires.
    assert findings == ()
    assert status == "green"
