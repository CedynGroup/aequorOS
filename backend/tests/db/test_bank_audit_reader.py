"""Execute bank audit reads with a real NOBYPASSRLS API login."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, RoleBundle
from app.core.tenancy import TenantContext
from app.identity.service import audit_log
from app.models import AuditEvent, Bank, OperatorAuditLog, User
from tests.db.test_database_roles import administrator, role_banks, role_engines, role_schema
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, ORG_2, USER_1

__all__ = ["administrator", "role_banks", "role_engines", "role_schema"]
pytestmark = [
    pytest.mark.committing_db,
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required"),
]
NOW = datetime(2026, 10, 12, 10, tzinfo=UTC)
SIBLING = "BK-AUDDB002"


@pytest.fixture(scope="module")
def evidence(administrator: Engine, role_banks: dict[str, str]) -> dict[str, str]:
    with Session(administrator) as db:
        db.add(
            Bank(
                id=SIBLING,
                organization_id=ORG_1,
                name="Sibling audit fixture",
                short_name="Sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="bank",
                institution_type="universal_bank",
            )
        )
        db.add(User(id=USER_1, organization_id=ORG_1, email="audit.officer@example.test"))
        db.flush()
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=role_banks[ORG_1],
            user_id=USER_1,
            bundle=RoleBundle.SECURITY_OFFICER,
            module=ModuleScope.AUDIT,
        )
        foreign = AuditEvent(
            organization_id=ORG_2,
            event_type="foreign.probe",
            details={"bank_id": role_banks[ORG_2]},
            created_at=NOW,
        )
        own = AuditEvent(
            organization_id=ORG_1,
            actor_user_id=USER_1,
            event_type="audit.probe",
            entity_type="bank",
            entity_id=role_banks[ORG_1],
            details={"bank_id": role_banks[ORG_1]},
            created_at=NOW,
        )
        staff = OperatorAuditLog(
            operator_email="staff@example.test",
            auth_mode="dev",
            target_org=ORG_1,
            action="inspector.probe",
            created_at=NOW,
            detail={"bank_id": role_banks[ORG_1], "reason": "Synthetic probe"},
        )
        db.add(foreign)
        db.flush()
        db.add_all([own, staff])
        db.flush()
        sibling = AuditEvent(
            organization_id=ORG_1,
            event_type="sibling.probe",
            details={"bank_id": SIBLING},
            created_at=NOW,
        )
        db.add(sibling)
        db.add(
            OperatorAuditLog(
                operator_email="staff@example.test",
                auth_mode="dev",
                target_org=ORG_1,
                action="inspector.sibling",
                created_at=NOW,
                detail={"bank_id": SIBLING},
            )
        )
        db.commit()
        return {
            "own": str(own.id),
            "foreign": str(foreign.id),
            "staff": str(staff.id),
            "sibling": str(sibling.id),
        }


@pytest.fixture
def api_connection(
    role_engines: dict[str, Engine], evidence: dict[str, str], role_banks: dict[str, str]
) -> Iterator[Connection]:
    with role_engines["aqs_api"].connect() as connection, connection.begin():
        connection.execute(
            text(
                "SELECT set_config('app.organization_id', :org, true), "
                "set_config('app.audit_bank_id', :bank, true)"
            ),
            {"org": ORG_1, "bank": role_banks[ORG_1]},
        )
        yield connection


def test_api_reads_verified_events_without_global_chain_access(
    api_connection: Connection, evidence: dict[str, str], role_banks: dict[str, str]
) -> None:
    assert not api_connection.scalar(
        text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    )
    with Session(api_connection, join_transaction_mode="create_savepoint") as db:
        db.info["organization_id"] = ORG_1
        response = audit_log.read_log(
            db,
            TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1),
            role_banks[ORG_1],
            audit_log.Filters(end=NOW.replace(hour=11)),
        )
        assert {str(event.id) for event in response.events} == {evidence["own"], evidence["staff"]}
        assert all(event.integrity == "verified" and event.sequence for event in response.events)
    # read_log committed its savepoint: re-establish only transaction-local context.
    api_connection.execute(
        text("SELECT set_config('app.audit_bank_id', :bank, true)"), {"bank": role_banks[ORG_1]}
    )
    ids = {
        str(value)
        for value in TypeAdapter(list[UUID]).validate_python(
            api_connection.execute(text("SELECT event_id FROM audit_chain_entries")).scalars().all()
        )
    }
    assert evidence["own"] in ids
    assert evidence["foreign"] not in ids and evidence["sibling"] not in ids
    assert api_connection.scalar(text("SELECT count(*) FROM operator_audit_log")) == 1
    with pytest.raises(DBAPIError, match="permission denied"), api_connection.begin_nested():
        api_connection.execute(text("SELECT count(*) FROM audit_chain_heads"))
    assert not api_connection.scalar(
        text("SELECT has_schema_privilege('aequoros_audit_owner',current_schema(),'CREATE')")
    )


def test_api_cannot_read_staff_events_for_another_bank(
    api_connection: Connection, role_banks: dict[str, str]
) -> None:
    api_connection.execute(
        text("SELECT set_config('app.audit_bank_id', :bank, true)"), {"bank": role_banks[ORG_2]}
    )
    assert api_connection.scalar(text("SELECT count(*) FROM operator_audit_log")) == 0
    assert api_connection.scalar(text("SELECT count(*) FROM audit_chain_entries")) == 0


@pytest.mark.parametrize("damage", ["payload", "predecessor", "missing_entry"])
def test_privileged_tampering_is_reported_as_failed(
    administrator: Engine, evidence: dict[str, str], role_banks: dict[str, str], damage: str
) -> None:
    with administrator.connect() as connection, connection.begin() as transaction:
        if damage == "payload":
            connection.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
            connection.execute(
                text("UPDATE audit_events SET event_type='tampered.probe' WHERE id=:id"),
                {"id": evidence["own"]},
            )
            connection.execute(text("ALTER TABLE audit_events ENABLE TRIGGER USER"))
        else:
            connection.execute(text("ALTER TABLE audit_chain_entries DISABLE TRIGGER USER"))
            connection.execute(
                text("DELETE FROM audit_chain_entries WHERE event_id=:id"),
                {"id": evidence["foreign" if damage == "predecessor" else "own"]},
            )
            connection.execute(text("ALTER TABLE audit_chain_entries ENABLE TRIGGER USER"))
        connection.execute(text("SET LOCAL ROLE aqs_api"))
        connection.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
        with Session(connection, join_transaction_mode="create_savepoint") as db:
            db.info["organization_id"] = ORG_1
            response = audit_log.read_log(
                db,
                TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1),
                role_banks[ORG_1],
                audit_log.Filters(end=NOW.replace(hour=11)),
            )
            own = next(event for event in response.events if str(event.id) == evidence["own"])
            assert own.integrity == "failed"
        transaction.rollback()
