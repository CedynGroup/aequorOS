"""By-id lookups under ``/banks/{bank_id}/system-of-record`` are bank-scoped.

Two banks of ONE organization share an RLS tenant, so the row-level policy
cannot tell them apart; the route has to. A declaration or withdrawal that
belongs to a sibling bank must resolve, under this bank's path, to the same
404 a cross-tenant probe gets — never a 403 or a 409 that would confirm the
sibling row exists or reveal its state — and the refusal must leave the
sibling's row, the audit log and the job queue untouched.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuditEvent, Bank, CanonicalWithdrawal, Job, SystemOfRecordDeclaration
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_system_of_record import (
    APPROVER_USER,
    FIXTURE_SOURCE,
    REGISTER_URL,
    SECOND_SOURCE,
    WITHDRAWALS_URL,
    _seed,
)
from tests.factories.canonical import FIXTURE_AS_OF

SIBLING_BANK_ID = "BK-SOR00002"

#: The proposer of every seeded declaration: a different person from the
#: approver, so four-eyes never masks the bank-scope refusal under test.
PROPOSER = "analyst@bank.test"
APPROVAL = {"approved_by": "cro@bank.test"}
REVOCATION = {"revoked_by": "cro@bank.test", "reason": "The sign-off named the wrong system."}
REVERSAL = {"reversed_by": "cro@bank.test", "reason": "The withdrawal retired the wrong book."}


def _approver() -> dict[str, str]:
    return headers(user_id=APPROVER_USER, roles=("approver",))


def _seed_banks(client: TestClient) -> str:
    """The sample bank with a duplicated LOAN book, plus a sibling in the same org."""
    bank_id = _seed(client, duplicate=True)
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="Sibling Bank Ltd",
                short_name="Sibling Bank",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()
    return bank_id


def _seed_declaration(bank_id: str, *, status: str, effective_from: date) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        now = utc_now()
        row = SystemOfRecordDeclaration(
            organization_id=ORG_1,
            bank_id=bank_id,
            position_type="LOAN",
            source_system=FIXTURE_SOURCE,
            effective_from=effective_from,
            source_citation="IT sign-off ITSO-2026-014",
            rationale="Core banking is the book of record for lending.",
            status=status,
            proposed_by=PROPOSER,
            proposed_by_user_id=USER_1,
            proposed_at=now,
            approved_by="cfo@bank.test" if status == "approved" else None,
            approved_at=now if status == "approved" else None,
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _seed_withdrawal(bank_id: str, *, status: str) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        now = utc_now()
        row = CanonicalWithdrawal(
            organization_id=ORG_1,
            bank_id=bank_id,
            source_system=SECOND_SOURCE,
            as_of_date=FIXTURE_AS_OF,
            entity="position",
            position_type="LOAN",
            reason="Duplicate of the declared LOAN book of record.",
            status=status,
            requested_by=PROPOSER,
            requested_by_user_id=USER_1,
            requested_at=now,
            approved_by="cfo@bank.test" if status == "applied" else None,
            approved_at=now if status == "applied" else None,
            withdrawal_batch_id=uuid4() if status == "applied" else None,
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _row_state(model: type[Any], row_id: UUID) -> dict[str, Any]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        row = session.get(model, row_id)
        assert row is not None
        return {column.key: getattr(row, column.key) for column in model.__table__.columns}
    finally:
        session.close()


def _side_effect_counts(entity_id: UUID) -> tuple[int, int]:
    """Audit events naming the object, and queued jobs for the tenant."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        events = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.entity_id == str(entity_id))
        )
        jobs = session.scalar(
            select(func.count()).select_from(Job).where(Job.organization_id == ORG_1)
        )
        return int(events or 0), int(jobs or 0)
    finally:
        session.close()


def _not_found_shape(response, row_id: UUID) -> dict[str, Any]:  # noqa: ANN001 - httpx.Response
    """The 404 error, minus its request id and with the caller's own id blanked."""
    assert response.status_code == 404, response.text
    error = dict(response.json()["error"])
    error.pop("request_id", None)
    return {key: str(value).replace(str(row_id), "{id}") for key, value in error.items()}


def _assert_refused_without_side_effects(
    response,  # noqa: ANN001 - httpx.Response
    model: type[Any],
    row_id: UUID,
    before: dict[str, Any],
    counts: tuple[int, int],
) -> dict[str, Any]:
    """A 404 that names nothing but the id the caller sent and changed nothing."""
    shape = _not_found_shape(response, row_id)
    assert SIBLING_BANK_ID not in response.text
    assert _row_state(model, row_id) == before
    assert _side_effect_counts(row_id) == counts
    return shape


def test_a_sibling_banks_declaration_cannot_be_approved_or_revoked_under_this_bank(
    db_client: TestClient,
) -> None:
    bank_id = _seed_banks(db_client)
    draft = _seed_declaration(SIBLING_BANK_ID, status="draft", effective_from=date(2026, 1, 1))
    approved = _seed_declaration(
        SIBLING_BANK_ID, status="approved", effective_from=date(2025, 1, 1)
    )
    draft_before = _row_state(SystemOfRecordDeclaration, draft)
    approved_before = _row_state(SystemOfRecordDeclaration, approved)
    draft_counts = _side_effect_counts(draft)
    approved_counts = _side_effect_counts(approved)

    refused_approve = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{draft}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    foreign_shape = _assert_refused_without_side_effects(
        refused_approve, SystemOfRecordDeclaration, draft, draft_before, draft_counts
    )

    refused_revoke = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{approved}/revoke",
        headers=_approver(),
        json=REVOCATION,
    )
    _assert_refused_without_side_effects(
        refused_revoke, SystemOfRecordDeclaration, approved, approved_before, approved_counts
    )

    # A foreign id is indistinguishable from an id that does not exist at all.
    unknown_id = uuid4()
    unknown = db_client.post(
        f"{REGISTER_URL.format(bank_id=bank_id)}/{unknown_id}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    assert _not_found_shape(unknown, unknown_id) == foreign_shape

    # Positive control: the owning bank's path still approves and revokes them.
    owner_approve = db_client.post(
        f"{REGISTER_URL.format(bank_id=SIBLING_BANK_ID)}/{draft}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    assert owner_approve.status_code == 200, owner_approve.text
    assert owner_approve.json()["status"] == "approved"
    assert owner_approve.json()["bank_id"] == SIBLING_BANK_ID
    owner_revoke = db_client.post(
        f"{REGISTER_URL.format(bank_id=SIBLING_BANK_ID)}/{approved}/revoke",
        headers=_approver(),
        json=REVOCATION,
    )
    assert owner_revoke.status_code == 200, owner_revoke.text
    assert owner_revoke.json()["revoked_at"] is not None
    assert _side_effect_counts(draft)[0] == draft_counts[0] + 1
    assert _side_effect_counts(approved)[0] == approved_counts[0] + 1


def test_a_sibling_banks_withdrawal_cannot_be_approved_or_reversed_under_this_bank(
    db_client: TestClient,
) -> None:
    bank_id = _seed_banks(db_client)
    pending = _seed_withdrawal(SIBLING_BANK_ID, status="pending")
    applied = _seed_withdrawal(SIBLING_BANK_ID, status="applied")
    pending_before = _row_state(CanonicalWithdrawal, pending)
    applied_before = _row_state(CanonicalWithdrawal, applied)
    pending_counts = _side_effect_counts(pending)
    applied_counts = _side_effect_counts(applied)

    refused_approve = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{pending}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    _assert_refused_without_side_effects(
        refused_approve, CanonicalWithdrawal, pending, pending_before, pending_counts
    )

    # An applied sibling withdrawal is 404 too — not the 409 its state would earn
    # under its own bank, which would reveal that state.
    refused_reapprove = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{applied}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    _assert_refused_without_side_effects(
        refused_reapprove, CanonicalWithdrawal, applied, applied_before, applied_counts
    )
    refused_reverse = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=bank_id)}/{applied}/reverse",
        headers=_approver(),
        json=REVERSAL,
    )
    _assert_refused_without_side_effects(
        refused_reverse, CanonicalWithdrawal, applied, applied_before, applied_counts
    )

    # Positive control: under its own bank the applied withdrawal's state is
    # what refuses re-approval.
    owner_reapprove = db_client.post(
        f"{WITHDRAWALS_URL.format(bank_id=SIBLING_BANK_ID)}/{applied}/approve",
        headers=_approver(),
        json=APPROVAL,
    )
    assert owner_reapprove.status_code == 409, owner_reapprove.text


def test_a_withdrawal_cannot_cite_a_sibling_banks_declaration(db_client: TestClient) -> None:
    bank_id = _seed_banks(db_client)
    foreign = _seed_declaration(SIBLING_BANK_ID, status="approved", effective_from=date(2026, 1, 1))
    own = _seed_declaration(bank_id, status="approved", effective_from=date(2026, 1, 1))
    foreign_counts = _side_effect_counts(foreign)
    request = {
        "entity": "position",
        "source_system": SECOND_SOURCE,
        "as_of_date": FIXTURE_AS_OF.isoformat(),
        "reason": "Duplicate of the declared LOAN book of record.",
        "requested_by": PROPOSER,
        "position_type": "LOAN",
    }
    analyst = headers(user_id=USER_1, roles=("analyst",))

    refused = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=analyst,
        json={**request, "declaration_id": str(foreign)},
    )
    assert refused.status_code == 404, refused.text
    assert _side_effect_counts(foreign) == foreign_counts
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        assert session.scalar(select(func.count()).select_from(CanonicalWithdrawal)) == 0
    finally:
        session.close()

    # Positive control: the bank's own declaration is accepted as evidence.
    accepted = db_client.post(
        WITHDRAWALS_URL.format(bank_id=bank_id),
        headers=analyst,
        json={**request, "declaration_id": str(own)},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["declaration_id"] == str(own)
