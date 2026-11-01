"""Persisted reconciliation proposals, authenticated four-eyes approval and revocation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import TenantContext
from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.data_engine.schemas.reconciliation import (
    ReconciliationExceptionCreate,
    ReconciliationExceptionRead,
)
from app.data_engine.service import reconciliation
from app.db.session import get_sessionmaker
from app.models import AuditEvent, Bank, CurrentFinancialFact, Job, ReconciliationException, User
from tests.data_engine.api.test_ingestion import seed_bank
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

CHECKER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PROPOSAL = {
    "reason": "Known custody-feed gap, quantified and bounded.",
    "max_gap_fraction": "0.002",
    "effective_from": "2026-06-01",
    "effective_to": "2026-09-30",
}


def _setup(client: TestClient, *, maker_can_approve: bool = False) -> str:
    bank_id = seed_bank(client)
    with get_sessionmaker()() as db:
        db.add(
            User(
                id=CHECKER,
                organization_id=ORG_1,
                email="checker@bank.test",
                display_name="Fixture checker",
                is_active=True,
            )
        )
        db.flush()
        db.add(
            CurrentFinancialFact(
                organization_id=ORG_1,
                bank_id=bank_id,
                source_as_of_date=date(2026, 6, 30),
                source_generation=1,
                fact_group="balance_sheet",
                category="cash_vault",
                amount=Decimal("1"),
                currency="GHS",
            )
        )
        for user in (CHECKER, USER_1) if maker_can_approve else (CHECKER,):
            grant_institution_authority(
                db,
                organization_id=ORG_1,
                bank_id=bank_id,
                user_id=user,
                bundle=RoleBundle.APPROVER,
                module=ModuleScope.DATA,
                sensitivity=SensitivityScope.CONFIDENTIAL,
            )
        db.commit()
    return f"/api/v1/banks/{bank_id}/reconciliation/exceptions"


def _counts() -> tuple[int, ...]:
    with get_sessionmaker()() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model)) or 0
            for model in (ReconciliationException, AuditEvent, Job)
        )


def _propose(client: TestClient, url: str) -> str:
    result = client.post(url, headers=headers(), json=PROPOSAL)
    assert result.status_code == 201, result.text
    row = result.json()
    assert row["status"] == "pending"
    assert row["requested_by"] == str(USER_1)
    assert row["approved_by"] is None
    assert row["approved_by_user_id"] is None
    assert row["approval_timestamp"] is None
    return str(row["id"])


def test_proposal_is_not_effective_until_separate_authenticated_approval(
    db_client: TestClient,
) -> None:
    url = _setup(db_client)
    before = _counts()
    row_id = _propose(db_client, url)
    assert _counts()[2] == before[2], "A pending proposal must not enqueue a refresh."
    listed = db_client.get(url, headers=headers(), params={"as_of": "2026-06-30"}).json()
    assert listed["active_exception_id"] != row_id
    approved = db_client.post(
        f"{url}/{row_id}/approve",
        headers=headers(user_id=CHECKER, roles=("viewer",)),
        json={"approved_by": "Head of Finance"},
    )
    assert approved.status_code == 200, approved.text
    row = approved.json()
    assert row["status"] == "approved"
    assert row["requested_by"] == str(USER_1)
    assert row["approved_by_user_id"] == str(CHECKER)
    assert row["approval_timestamp"] is not None
    assert _counts()[2] == before[2] + 1
    before_repeat = _counts()
    assert (
        db_client.post(
            f"{url}/{row_id}/approve",
            headers=headers(user_id=CHECKER),
            json={"approved_by": "A second label"},
        ).status_code
        == 409
    )
    assert _counts() == before_repeat
    revoked = db_client.post(
        f"{url}/{row_id}/revoke",
        headers=headers(user_id=CHECKER),
        json={"revoked_by": "Head of Finance", "reason": "Feed corrected."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    assert revoked.json()["requested_by"] == str(USER_1)
    assert revoked.json()["approved_by_user_id"] == str(CHECKER)


def test_self_approval_cannot_be_disguised_with_another_label(db_client: TestClient) -> None:
    url = _setup(db_client, maker_can_approve=True)
    row_id = _propose(db_client, url)
    before = _counts()
    result = db_client.post(
        f"{url}/{row_id}/approve",
        headers=headers(),
        json={"approved_by": "An entirely different officer"},
    )
    assert result.status_code == 404, result.text
    assert _counts() == before
    with get_sessionmaker()() as db:
        row = db.get(ReconciliationException, UUID(row_id))
        assert row is not None and row.status == "pending"
        assert row.approval_timestamp is None


@pytest.mark.parametrize("field", ["requested_by", "approved_by_user_id", "approved_by", "status"])
def test_proposal_refuses_client_authority_fields(db_client: TestClient, field: str) -> None:
    url = _setup(db_client)
    before = _counts()
    result = db_client.post(url, headers=headers(), json={**PROPOSAL, field: str(CHECKER)})
    assert result.status_code == 422, result.text
    assert _counts() == before


def test_approval_refuses_spoofed_checker_and_requires_approve(db_client: TestClient) -> None:
    url = _setup(db_client)
    row_id = _propose(db_client, url)
    before = _counts()
    result = db_client.post(
        f"{url}/{row_id}/approve",
        headers=headers(roles=("approver",)),
        json={"approved_by": "CRO"},
    )
    assert result.status_code == 404, result.text
    spoof = db_client.post(
        f"{url}/{row_id}/approve",
        headers=headers(user_id=CHECKER),
        json={"approved_by": "CRO", "approved_by_user_id": str(USER_1)},
    )
    assert spoof.status_code == 422, spoof.text
    assert _counts() == before


@pytest.mark.parametrize(
    "change",
    [
        {"reason": "   "},
        {"max_gap_fraction": "0"},
        {"effective_to": "2000-01-01"},
    ],
)
def test_invalid_proposal_has_no_side_effects(
    db_client: TestClient, change: dict[str, str]
) -> None:
    url = _setup(db_client)
    before = _counts()
    result = db_client.post(url, headers=headers(), json={**PROPOSAL, **change})
    assert result.status_code in {409, 422}, result.text
    assert _counts() == before


def test_another_tenant_cannot_see_propose_or_approve(db_client: TestClient) -> None:
    url = _setup(db_client)
    row_id = _propose(db_client, url)
    before = _counts()
    assert db_client.get(url, headers=headers(org_id=ORG_2)).status_code == 404
    assert db_client.post(url, headers=headers(org_id=ORG_2), json=PROPOSAL).status_code == 404
    assert (
        db_client.post(
            f"{url}/{row_id}/approve",
            headers=headers(org_id=ORG_2),
            json={"approved_by": "Other tenant"},
        ).status_code
        == 404
    )
    assert _counts() == before


@pytest.mark.parametrize("entry_point", ["api", "service"])
@pytest.mark.parametrize("ceiling", ["0.0000001", "0.0500006", "0.050000000000000000000000000001"])
def test_unrepresentable_proposal_has_no_effects(
    db_client: TestClient, entry_point: str, ceiling: str
) -> None:
    url = _setup(db_client)
    before = _counts()
    if entry_point == "api":
        result = db_client.post(
            url, headers=headers(), json={**PROPOSAL, "max_gap_fraction": ceiling}
        )
        assert result.status_code == 422, result.text
    else:
        with get_sessionmaker()() as db:
            bank = db.get(Bank, url.split("/")[4])
            assert bank is not None
            with pytest.raises(reconciliation.ReconciliationExceptionError):
                reconciliation.propose_exception(
                    db,
                    TenantContext(
                        organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1
                    ),
                    bank,
                    reason=PROPOSAL["reason"],
                    max_gap_fraction=Decimal(ceiling),
                    effective_from=date(2026, 6, 1),
                )
            db.commit()
    assert _counts() == before


@pytest.mark.parametrize("entry_point", ["api", "service"])
@pytest.mark.parametrize("ceiling", ["0.000001", "0.050000", "0.0500000", "0.999999", "1.0000000"])
def test_representable_ceiling_matches_storage_and_audit(
    db_client: TestClient, entry_point: str, ceiling: str
) -> None:
    url = _setup(db_client)
    before = _counts()
    if entry_point == "api":
        result = db_client.post(
            url, headers=headers(), json={**PROPOSAL, "max_gap_fraction": ceiling}
        )
        assert result.status_code == 201, result.text
        parsed = ReconciliationExceptionRead.model_validate_json(result.content)
        row_id = parsed.id
        assert parsed.max_gap_fraction == Decimal(ceiling)
    else:
        with get_sessionmaker()() as db:
            bank = db.get(Bank, url.split("/")[4])
            assert bank is not None
            row = reconciliation.propose_exception(
                db,
                TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1),
                bank,
                reason=PROPOSAL["reason"],
                max_gap_fraction=Decimal(ceiling),
                effective_from=date(2026, 6, 1),
            )
            row_id = row.id
            db.commit()
    assert _counts() == (before[0] + 1, before[1] + 1, before[2])
    with get_sessionmaker()() as db:
        stored = db.get(ReconciliationException, row_id)
        assert stored is not None
        assert stored.max_gap_fraction == Decimal(ceiling)
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == reconciliation.RECONCILIATION_PROPOSED_EVENT
            )
        )
        assert audit is not None
        recorded_ceiling = cast("object", audit.details["max_gap_fraction"])
        assert isinstance(recorded_ceiling, str)
        assert Decimal(recorded_ceiling) == stored.max_gap_fraction
        assert stored.status == "pending"


@given(micros=st.integers(min_value=1, max_value=1_000_000), zeros=st.integers(0, 40))
def test_proposal_ceiling_precision_preserves_equivalent_decimals(micros: int, zeros: int) -> None:
    ceiling = Decimal(micros).scaleb(-6)
    equivalent = f"{ceiling:.6f}" + "0" * zeros
    parsed = ReconciliationExceptionCreate.model_validate(
        {**PROPOSAL, "max_gap_fraction": equivalent}
    )
    assert parsed.max_gap_fraction == ceiling
    with pytest.raises(ValidationError):
        ReconciliationExceptionCreate.model_validate(
            {**PROPOSAL, "max_gap_fraction": f"{ceiling:.6f}" + "0" * zeros + "1"}
        )
