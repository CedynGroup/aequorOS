"""The filing review-chain templates are governed writes: exact REG authority.

Proposing, editing and submitting the bank's filing chain are configuration
duties (``reg/configure/restricted`` on the exact bank); deciding a proposed
chain is an approval duty (``reg/approve/restricted`` on the exact bank) that the
evaluator refuses to whoever proposed it, and reading the chain needs
``reg/view/restricted`` on the exact bank. Every refusal lands before any row is
written or audit event recorded, a bank outside the caller's coverage stays an
opaque 404, and the writes answer with write-only acknowledgements — a
configure-only officer never reads stored chain content through a write.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import AuditEvent, Bank, FilingWorkflowTemplate, User
from app.reporting.service.configure_access import REG_DENIAL_DETAIL
from app.reporting.service.filing_workflow import templates as filing_templates
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.authorization_fixtures import (
    current_authorization_version,
    open_tenant_session,
    suspend_all_bindings,
)
from tests.support.helpers import ORG_1, USER_1, error_envelope, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
TEMPLATES_URL = f"{BASE}/filing-workflow-templates"
SIBLING = "BK-FILETPLS1"
CHECKER_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CHECKER_EMAIL = "filing-chain-checker@example.test"

STAGES: list[dict[str, Any]] = [
    {"seq": 1, "stage_key": "preparer", "title": "Preparer", "decision_kind": "prepare"},
    {
        "seq": 2,
        "stage_key": "validator",
        "title": "Validator",
        "decision_kind": "approve",
        "transmit_on_approve": True,
    },
]
REASON = "Review chain for the institution's governance."
ACK_FIELDS = {"id", "bank_id", "version", "status", "created_at", "updated_at", "submitted_at"}


@pytest.fixture(autouse=True)
def book_without_authority(db_client: TestClient) -> Iterator[None]:
    """The canonical bank, one sibling bank, and a second officer to decide with."""
    _ = db_client
    with get_sessionmaker()() as db:
        materialize_canonical_test_book(db)
        db.add(
            Bank(
                id=SIBLING,
                organization_id=ORG_1,
                name="Filing template sibling fixture",
                short_name="FileSibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type="universal_bank",
            )
        )
        db.add(
            User(
                id=CHECKER_ID,
                organization_id=ORG_1,
                email=CHECKER_EMAIL,
                display_name="Filing chain checker",
                role="viewer",
                auth_provider="password",
            )
        )
        db.commit()
    yield


@pytest.fixture(autouse=True)
def only_explicit_grants() -> Iterator[None]:
    """Suspend the demo tenant's seeded all-module viewer binding so each case
    holds exactly the grants it creates."""
    with open_tenant_session(ORG_1) as db:
        suspend_all_bindings(
            db,
            organization_id=ORG_1,
            user_id=USER_1,
            reason="suspend every active fixture binding",
        )
    yield


def _grant(  # noqa: PLR0913 - one binding, every scope dimension named
    user_id: UUID,
    bundle: RoleBundle,
    *,
    module: ModuleScope = ModuleScope.REGULATORY,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    bank_id: str = SAMPLE_BANK_ID,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
) -> None:
    """One exact binding. Grants bump the principal's authorization version, so
    build request headers fresh with :func:`_auth` after every grant."""
    with get_sessionmaker()() as db:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope,
                None if institution_scope is InstitutionScope.ORGANIZATION else bank_id,
                module,
                sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "filing-template-test"),
            reason="filing review-chain authority regression",
        )
        db.commit()


def _version(user_id: UUID) -> int:
    with open_tenant_session(ORG_1) as db:
        return current_authorization_version(db, organization_id=ORG_1, user_id=user_id)


def _auth(user_id: UUID) -> dict[str, str]:
    # Scalar roles ("admin") must grant nothing by themselves: only the explicit
    # scoped binding may authorize these routes. The version is read at call
    # time because every grant and suspension bumps it.
    return headers(
        org_id=ORG_1, user_id=user_id, roles=("admin",), authorization_version=_version(user_id)
    )


def _counts() -> tuple[int, int]:
    with get_sessionmaker()() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.organization_id == ORG_1)
            )
            or 0,
            db.scalar(
                select(func.count())
                .select_from(FilingWorkflowTemplate)
                .where(FilingWorkflowTemplate.organization_id == ORG_1)
            )
            or 0,
        )


def _propose(client: TestClient, auth: dict[str, str]) -> Any:
    return client.post(TEMPLATES_URL, headers=auth, json={"stages": STAGES, "reason": REASON})


def _submit(client: TestClient, auth: dict[str, str], template_id: UUID) -> Any:
    return client.post(
        f"{TEMPLATES_URL}/{template_id}/submit", headers=auth, json={"reason": REASON}
    )


def _pending_chain(client: TestClient, auth: dict[str, str]) -> UUID:
    proposed = _propose(client, auth)
    assert proposed.status_code == 201, proposed.text
    template_id = UUID(proposed.json()["id"])
    submitted = _submit(client, auth, template_id)
    assert submitted.status_code == 200, submitted.text
    return template_id


# --- the three writes ---------------------------------------------------------


def test_template_writes_succeed_with_exact_configure_and_acknowledge_only(
    db_client: TestClient,
) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)

    proposed = _propose(db_client, configured)
    assert proposed.status_code == 201, proposed.text
    ack = proposed.json()
    assert set(ack) == ACK_FIELDS
    assert ack["bank_id"] == SAMPLE_BANK_ID
    assert ack["version"] == 1
    assert ack["status"] == "draft"
    assert ack["submitted_at"] is None
    # Write-only: the stored chain (stages, proposer, reason) is never echoed.
    assert "stages" not in ack and "proposed_by" not in ack and "reason" not in ack

    template_id = UUID(ack["id"])
    updated = db_client.patch(
        f"{TEMPLATES_URL}/{template_id}",
        headers=configured,
        json={"stages": STAGES, "reason": "Tightened review chain for the institution."},
    )
    assert updated.status_code == 200, updated.text
    update_ack = updated.json()
    assert set(update_ack) == ACK_FIELDS
    assert update_ack["status"] == "draft"
    assert update_ack["submitted_at"] is None

    submitted = _submit(db_client, configured, template_id)
    assert submitted.status_code == 200, submitted.text
    submit_ack = submitted.json()
    assert set(submit_ack) == ACK_FIELDS
    assert submit_ack["status"] == "pending_approval"
    assert submit_ack["submitted_at"] is not None

    with get_sessionmaker()() as db:
        row = db.get(FilingWorkflowTemplate, template_id)
        assert row is not None
        assert row.status == "pending_approval"


def test_template_writes_denied_without_configure(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY)
    before = _counts()
    actor = _auth(USER_1)

    for method, url, payload in (
        ("post", TEMPLATES_URL, {"stages": STAGES, "reason": REASON}),
        ("patch", f"{TEMPLATES_URL}/{uuid4()}", {"stages": STAGES, "reason": REASON}),
        ("post", f"{TEMPLATES_URL}/{uuid4()}/submit", {"reason": REASON}),
    ):
        denied = getattr(db_client, method)(url, headers=actor, json=payload)
        assert denied.status_code == 403, (method, denied.text)
        assert error_envelope(denied)["message"] == REG_DENIAL_DETAIL
    assert _counts() == before


def test_sibling_bank_configure_does_not_authorize(db_client: TestClient) -> None:
    # Coverage of the home bank (a view binding) plus a configurator binding on
    # the sibling only: the exact-bank configure sentence is still missing.
    _grant(USER_1, RoleBundle.VIEWER)
    _grant(USER_1, RoleBundle.CONFIGURATOR, bank_id=SIBLING)
    before = _counts()
    actor = _auth(USER_1)

    for method, url, payload in (
        ("post", TEMPLATES_URL, {"stages": STAGES, "reason": REASON}),
        ("patch", f"{TEMPLATES_URL}/{uuid4()}", {"stages": STAGES, "reason": REASON}),
        ("post", f"{TEMPLATES_URL}/{uuid4()}/submit", {"reason": REASON}),
    ):
        denied = getattr(db_client, method)(url, headers=actor, json=payload)
        assert denied.status_code == 403, (method, denied.text)
        assert error_envelope(denied)["message"] == REG_DENIAL_DETAIL
    assert _counts() == before


def test_out_of_coverage_bank_is_hidden(db_client: TestClient) -> None:
    before = _counts()
    hidden = _propose(db_client, _auth(USER_1))
    assert hidden.status_code == 404, hidden.text
    assert error_envelope(hidden)["message"] == "Bank not found."
    other = headers(org_id="OR-1S000002", roles=("admin",))
    for method, url, payload in (
        ("post", TEMPLATES_URL, {"stages": STAGES, "reason": REASON}),
        ("patch", f"{TEMPLATES_URL}/{uuid4()}", {"stages": STAGES, "reason": REASON}),
        ("post", f"{TEMPLATES_URL}/{uuid4()}/submit", {"reason": REASON}),
    ):
        foreign = getattr(db_client, method)(url, headers=other, json=payload)
        assert foreign.status_code == 404, (method, foreign.text)
    assert _counts() == before


def test_impersonated_session_cannot_write_templates(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    before = _counts()
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=ORG_1,
        act_operator="ops@aequoros.com",
        session_id="11111111-2222-4333-8444-555555555555",
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    refused = db_client.post(
        TEMPLATES_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"stages": STAGES, "reason": REASON},
    )
    assert refused.status_code == 403, refused.text
    assert _counts() == before


# --- the list read ------------------------------------------------------------


def test_list_requires_exact_reg_view(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    assert _propose(db_client, configured).status_code == 201

    _grant(USER_1, RoleBundle.VIEWER)
    listed = db_client.get(TEMPLATES_URL, headers=_auth(USER_1))
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["templates"]) == 1

    # A second officer whose only binding is in another module is refused; a
    # sibling-bank view does not carry either.
    _grant(CHECKER_ID, RoleBundle.ANALYST, module=ModuleScope.LIQUIDITY)
    denied = db_client.get(TEMPLATES_URL, headers=_auth(CHECKER_ID))
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == REG_DENIAL_DETAIL

    _grant(CHECKER_ID, RoleBundle.VIEWER, bank_id=SIBLING)
    sibling_only = db_client.get(TEMPLATES_URL, headers=_auth(CHECKER_ID))
    assert sibling_only.status_code == 403, sibling_only.text

    with open_tenant_session(ORG_1) as db:
        suspend_all_bindings(
            db, organization_id=ORG_1, user_id=USER_1, reason="hide the bank entirely"
        )
    hidden = db_client.get(TEMPLATES_URL, headers=_auth(USER_1))
    assert hidden.status_code == 404, hidden.text
    assert error_envelope(hidden)["message"] == "Bank not found."


# --- the decision -------------------------------------------------------------


def test_decide_allowed_for_a_different_officer_with_exact_approve(
    db_client: TestClient,
) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    template_id = _pending_chain(db_client, configured)

    _grant(CHECKER_ID, RoleBundle.APPROVER)
    checker_auth = _auth(CHECKER_ID)
    decided = db_client.post(
        f"{TEMPLATES_URL}/{template_id}/decision",
        headers=checker_auth,
        json={"decision": "approved", "reason": "Approved by the second officer."},
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    # The decider holds restricted view, so the decided row comes back.
    assert body["status"] == "approved"
    assert body["decided_by"] == str(CHECKER_ID)
    with get_sessionmaker()() as db:
        row = db.get(FilingWorkflowTemplate, template_id)
        assert row is not None
        assert row.status == "approved"
        assert row.decided_by == CHECKER_ID

    # A rejection moves a fresh chain the same way.
    second_id = _pending_chain(db_client, configured)
    rejected = db_client.post(
        f"{TEMPLATES_URL}/{second_id}/decision",
        headers=checker_auth,
        json={"decision": "rejected", "reason": "Sent back for a narrower chain."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"


def test_proposer_cannot_decide_even_with_approve(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    _grant(USER_1, RoleBundle.APPROVER)
    configured = _auth(USER_1)
    template_id = _pending_chain(db_client, configured)
    before = _counts()

    refused = db_client.post(
        f"{TEMPLATES_URL}/{template_id}/decision",
        headers=_auth(USER_1),
        json={"decision": "approved", "reason": "The proposer may not approve."},
    )
    assert refused.status_code == 403, refused.text
    assert error_envelope(refused)["message"] == REG_DENIAL_DETAIL
    with get_sessionmaker()() as db:
        row = db.get(FilingWorkflowTemplate, template_id)
        assert row is not None
        assert row.status == "pending_approval"
        assert row.decided_by is None
    assert _counts() == before


def test_decide_denied_without_approve(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    template_id = _pending_chain(db_client, configured)
    before = _counts()

    # A reader (view only) is not an approver.
    _grant(CHECKER_ID, RoleBundle.VIEWER)
    viewer_auth = _auth(CHECKER_ID)
    denied = db_client.post(
        f"{TEMPLATES_URL}/{template_id}/decision",
        headers=viewer_auth,
        json={"decision": "approved", "reason": "View authority decides nothing."},
    )
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == REG_DENIAL_DETAIL
    assert _counts() == before


def test_sibling_bank_approve_does_not_authorize(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    template_id = _pending_chain(db_client, configured)
    before = _counts()

    # Coverage of the home bank (view) plus an approve binding on the sibling
    # only: the exact-bank approve sentence is still missing.
    _grant(CHECKER_ID, RoleBundle.VIEWER)
    _grant(CHECKER_ID, RoleBundle.APPROVER, bank_id=SIBLING)
    denied = db_client.post(
        f"{TEMPLATES_URL}/{template_id}/decision",
        headers=_auth(CHECKER_ID),
        json={"decision": "approved", "reason": "Sibling authority stops at its bank."},
    )
    assert denied.status_code == 403, denied.text
    assert error_envelope(denied)["message"] == REG_DENIAL_DETAIL
    with get_sessionmaker()() as db:
        row = db.get(FilingWorkflowTemplate, template_id)
        assert row is not None and row.status == "pending_approval"
    assert _counts() == before


def test_out_of_coverage_decide_is_hidden(db_client: TestClient) -> None:
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    _pending_chain(db_client, configured)
    before = _counts()

    _grant(CHECKER_ID, RoleBundle.APPROVER)
    checker_auth = _auth(CHECKER_ID)
    missing = db_client.post(
        f"{TEMPLATES_URL}/{uuid4()}/decision",
        headers=checker_auth,
        json={"decision": "approved", "reason": "An unknown chain stays unknown."},
    )
    # The scoped gate admits the officer; the unknown object answers 404.
    assert missing.status_code == 404, missing.text

    with open_tenant_session(ORG_1) as db:
        suspend_all_bindings(
            db, organization_id=ORG_1, user_id=USER_1, reason="hide the bank entirely"
        )
    hidden = db_client.post(
        f"{TEMPLATES_URL}/{uuid4()}/decision",
        headers=_auth(USER_1),
        json={"decision": "approved", "reason": "An invisible bank stays invisible."},
    )
    assert hidden.status_code == 404, hidden.text
    assert error_envelope(hidden)["message"] == "Bank not found."
    assert _counts() == before


def test_decided_row_requires_view_otherwise_acknowledgement(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed projection: a decider the evaluator cannot grant view to
    receives the write-only acknowledgement, never the stored chain."""
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    first_id = _pending_chain(db_client, configured)
    _grant(CHECKER_ID, RoleBundle.APPROVER)
    checker_auth = _auth(CHECKER_ID)

    allowed = db_client.post(
        f"{TEMPLATES_URL}/{first_id}/decision",
        headers=checker_auth,
        json={"decision": "approved", "reason": "Approved with view authority."},
    )
    assert allowed.status_code == 200, allowed.text
    assert "proposed_by" in allowed.json()

    second_id = _pending_chain(db_client, configured)

    from app.identity import public as identity_public  # noqa: PLC0415

    real = identity_public.evaluate_bank_permission

    def view_denied(*args: Any, **kwargs: Any) -> Any:
        decision = real(*args, **kwargs)
        permission = kwargs.get("permission")
        if (
            decision is not None
            and decision.allowed
            and permission is not None
            and permission.value == "view"
        ):
            return None
        return decision

    monkeypatch.setattr(filing_templates, "evaluate_bank_permission", view_denied)
    try:
        acked = db_client.post(
            f"{TEMPLATES_URL}/{second_id}/decision",
            headers=checker_auth,
            json={"decision": "approved", "reason": "Approved without view authority."},
        )
    finally:
        monkeypatch.undo()
    assert acked.status_code == 200, acked.text
    body = acked.json()
    assert set(body) == ACK_FIELDS
    assert body["status"] == "approved"
    assert "proposed_by" not in body and "stages" not in body and "reason" not in body


def test_database_check_forbids_self_approval(db_client: TestClient) -> None:
    """The four-eyes rule also lives in the database, not only in the service."""
    _grant(USER_1, RoleBundle.CONFIGURATOR)
    configured = _auth(USER_1)
    template_id = _pending_chain(db_client, configured)
    with get_sessionmaker()() as db:
        row = db.get(FilingWorkflowTemplate, template_id)
        assert row is not None
        row.decided_by = row.proposed_by
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
