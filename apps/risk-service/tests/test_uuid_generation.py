from __future__ import annotations

from datetime import UTC, datetime
from uuid import RFC_4122, UUID

from sqlalchemy.orm import Session

from app.core.ids import new_uuid4, new_uuid7
from app.models import FinancialInstitution, RiskCase
from tests.api.helpers import ORG_1


def uuid7_timestamp_ms(value: UUID) -> int:
    return value.int >> 80


def test_uuid_helpers_generate_expected_versions() -> None:
    before_ms = int(datetime.now(UTC).timestamp() * 1000)
    uuid4_value = new_uuid4()
    uuid7_value = new_uuid7()
    after_ms = int(datetime.now(UTC).timestamp() * 1000)

    assert uuid4_value.version == 4
    assert uuid4_value.variant == RFC_4122
    assert uuid7_value.version == 7
    assert uuid7_value.variant == RFC_4122
    assert before_ms <= uuid7_timestamp_ms(uuid7_value) <= after_ms


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
        name="Example Bank",
    )
    db_session.add(institution)
    db_session.flush()

    assert risk_case.id.version == 4
    assert institution.id.version == 7
