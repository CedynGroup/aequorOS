"""Tests for test_uuid_generation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import FinancialInstitution, RiskCase
from tests.support.helpers import ORG_1


def test_financial_models_use_uuid7_primary_key_defaults(db_session: Session) -> None:
    risk_case = RiskCase(
        organization_id=ORG_1,
        title="Financial workspace",
        case_type="vendor",
        status="active",
    )
    db_session.add(risk_case)
    db_session.flush()

    institution = FinancialInstitution(
        organization_id=ORG_1,
        case_id=risk_case.id,
        dedupe_key="test:institution:uuid-generation",
        name="Example Bank",
    )
    db_session.add(institution)
    db_session.flush()

    assert risk_case.id.version == 4
    assert institution.id.version == 7
