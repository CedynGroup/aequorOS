"""Shared-register audit projections execute with the hermetic database."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, RoleBundle
from app.core.tenancy import TenantContext
from app.identity.service.audit_log import Filters, read_log
from app.models import Bank
from app.operator.services.operator_views import get_tenant_activity
from app.services.audit import record_event
from tests.conftest import (
    ApiTestDatabase,
    build_test_database,
    rollback_sessionmaker_lifecycle,
    seed_demo_tenants,
    sqlite_database_url,
)
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, ORG_2, USER_1


@pytest.fixture(scope="module")
def projection_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ApiTestDatabase]:
    with build_test_database(
        sqlite_database_url(tmp_path_factory, "shared-register-projection"), seed=seed_demo_tenants
    ) as database:
        yield database


@pytest.fixture
def projection_session(
    projection_database: ApiTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Session]:
    with rollback_sessionmaker_lifecycle(projection_database, monkeypatch) as maker, maker() as db:
        yield db


@pytest.mark.parametrize(
    "subject",
    [
        "param_ecl_assumption",
        "param_crm_haircut",
        "param_concentration_limit",
        "param_credit_threshold",
        "param_liquidity_threshold",
        "param_liquidity_haircut",
    ],
)
@pytest.mark.parametrize("staff", [False, True])
def test_shared_register_bank_projections(
    projection_session: Session, subject: str, staff: bool
) -> None:
    db = projection_session
    source, sibling, jurisdiction, klass, foreign = (
        "BK-SHARED001",
        "BK-SHARED002",
        "BK-SHARED003",
        "BK-SHARED004",
        "BK-SHARED005",
    )
    for bank_id, org, country, currency, kind in (
        (source, ORG_1, "GH", "GHS", "universal_bank"),
        (sibling, ORG_1, "GH", "GHS", "universal_bank"),
        (jurisdiction, ORG_1, "NG", "NGN", "universal_bank"),
        (klass, ORG_1, "GH", "GHS", "savings_and_loans"),
        (foreign, ORG_2, "GH", "GHS", "universal_bank"),
    ):
        db.add(
            Bank(
                id=bank_id,
                organization_id=org,
                name="Audit scope fixture",
                short_name="Audit",
                currency=currency,
                jurisdiction_code=country,
                license_type=kind,
                institution_type=kind,
            )
        )
    db.flush()
    for bank_id in (source, sibling, jurisdiction, klass):
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=bank_id,
            user_id=USER_1,
            bundle=RoleBundle.SECURITY_OFFICER,
            module=ModuleScope.AUDIT,
        )
    ctx = TenantContext(ORG_1, actor_user_id=USER_1, authorization_version=1)
    writer = TenantContext(
        ORG_1,
        actor_user_id=None if staff else USER_1,
        actor_operator="private@example.test" if staff else None,
        impersonation_context=str(UUID(int=1)) if staff else None,
    )
    details: dict[str, JsonValue] = {
        "reason": "Private reason",
        "approved_by": "Private approver",
        "support_reference": "PRIVATE-REF",
        "before": {"value": "20"},
        "after": {"value": "25"},
        "effective_from": "2026-10-01",
    }
    if subject == "param_liquidity_threshold":
        details["institution_class"] = "bank"
    original = record_event(
        db,
        writer,
        event_type="shared.changed",
        entity_type=subject,
        entity_id=source,
        details=details,
    )
    record_event(
        db,
        TenantContext(ORG_2),
        event_type="shared.changed",
        entity_type=subject,
        entity_id=foreign,
        details=details,
    )
    record_event(
        db,
        ctx,
        event_type="shared.changed",
        entity_type=subject,
        entity_id=source,
        details={**details, "bank_id": jurisdiction},
    )
    db.flush()
    original_id = original.id
    expected_banks: set[str] = {source, sibling} | (
        {klass} if subject != "param_liquidity_threshold" else set()
    )
    activity = get_tenant_activity(db, ORG_1, 100)
    assert {
        item.bank_id for item in activity.items if item.summary.startswith("shared.changed")
    } == expected_banks
    for bank_id in (source, sibling, jurisdiction, klass):
        page = read_log(db, ctx, bank_id, Filters(event_type="shared.changed"))
        if bank_id not in expected_banks:
            assert page.events == []
            continue
        assert len(page.events) == 1
        entry = page.events[0]
        assert entry.id == original_id and entry.target_id == source
        assert entry.before == {"value": "20"}
        assert entry.after == {"value": "25"} and entry.details["bank_id"] == source
        if bank_id == source:
            assert entry.actor == ("private@example.test" if staff else str(USER_1))
            assert entry.reason == "Private reason" and entry.support_reference == "PRIVATE-REF"
            assert entry.details["approved_by"] == "Private approver"
        else:
            assert entry.actor is None and entry.reason is None and entry.support_reference is None
            assert entry.details == {
                "bank_id": source,
                "before": {"value": "20"},
                "after": {"value": "25"},
                "effective_from": "2026-10-01",
                **({"institution_class": "bank"} if subject == "param_liquidity_threshold" else {}),
            }
        filtered = read_log(
            db,
            ctx,
            bank_id,
            Filters(
                event_type="shared.changed", actor="private@example.test" if staff else str(USER_1)
            ),
        )
        assert {entry.id for entry in filtered.events} == (
            {original_id} if bank_id == source else set()
        )
