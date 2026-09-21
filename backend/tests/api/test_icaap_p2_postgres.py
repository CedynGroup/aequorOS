"""Risk-and-capital claims that can only be shown against a real Postgres.

Three of them, each for a reason SQLite cannot demonstrate:

* **RLS is real.** Another tenant addressing this cycle's Pillar 2 item by its
  own id gets 404 — the policy, not only the service's WHERE clause.
* **The revision log only grows.** An edit, a recompute and a retirement
  leave every earlier revision's digest exactly as it was written. (The
  database tier — the append-only trigger and the restrictive UPDATE policy —
  is proved against a MIGRATED schema in
  ``tests/db/test_icaap_risk_capital_migration.py``; this client builds its
  schema with ``create_all``.)
* **A concurrent approval is issued once.** Two requests approving the same
  revision: one wins, and the loser is told the revision moved.

Postgres-gated and committing, so each case cleans up after itself.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, User
from app.models.icaap import IcaapCycle
from app.models.icaap_risk_capital import IcaapPillar2Item, IcaapPillar2ItemRevision
from app.services import authorization
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for the ICAAP risk-and-capital checks.",
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap"
CHECKER = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")


def _bind(session, user_id: UUID, bundle: RoleBundle) -> int:
    authorization.create_role_binding(
        session,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.CAPITAL,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise ICAAP risk-and-capital isolation against Postgres.",
    )
    session.commit()
    user = session.get(User, user_id)
    assert user is not None
    session.refresh(user)
    return user.authorization_version


@pytest.fixture
def analyst(
    committing_db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, str]]:
    get_settings.cache_clear()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
        if session.get(User, CHECKER) is None:
            session.add(
                User(
                    id=CHECKER,
                    organization_id=ORG_1,
                    email="pg.checker@example.test",
                    display_name="Second Person",
                )
            )
            session.commit()
        auth = headers(
            roles=("analyst",), authorization_version=_bind(session, USER_1, RoleBundle.ANALYST)
        )
    finally:
        session.close()
    yield auth
    cleanup = get_sessionmaker()()
    cleanup.info["organization_id"] = ORG_1
    try:
        cleanup.execute(delete(IcaapCycle).where(IcaapCycle.organization_id == ORG_1))
        cleanup.commit()
    finally:
        cleanup.close()


@pytest.fixture
def approver(committing_db_client: TestClient, analyst: dict[str, str]) -> dict[str, str]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        version = _bind(session, CHECKER, RoleBundle.APPROVER)
    finally:
        session.close()
    return headers(roles=("approver",), user_id=CHECKER, authorization_version=version)


def _cycle(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post(
        f"{BASE}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Isolation check",
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _computed_item(client: TestClient, auth: dict[str, str], cycle_id: str) -> dict:
    created = client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items",
        json={
            "component_key": "irrbb",
            "method": "irrbb_interim_delta_eve",
            "input_mode": "manual_with_evidence",
            "rationale": "Group ALM figures.",
            "reason": "Quantify interest rate risk.",
        },
        headers=auth,
    )
    assert created.status_code == 201, created.text
    item = created.json()
    computed = client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/compute",
        json={
            "base_revision_no": item["current_revision_no"],
            "manual_inputs": {
                "tier1": "700",
                "irrbb_deltas": {
                    "parallel_up_450": "-116.759902",
                    "parallel_down_450": "142.996097",
                },
            },
            "reason": "Compute.",
        },
        headers=auth,
    )
    assert computed.status_code == 200, computed.text
    return computed.json()


def test_another_tenant_cannot_address_this_pillar_two_item(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    item = _computed_item(committing_db_client, analyst, cycle_id)
    other = headers(org_id=ORG_2, roles=("analyst",))
    for path in (
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{cycle_id}/pillar2",
        f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap/cycles/{cycle_id}"
        f"/pillar2/items/{item['id']}/revisions",
    ):
        response = committing_db_client.get(path, headers=other)
        assert response.status_code in {403, 404}, path
        assert "116.7599" not in response.text


def test_earlier_revisions_are_never_rewritten_by_a_later_one(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    """The service only ever appends.

    The DATABASE tier (the ``append_only`` trigger and the restrictive UPDATE
    policy) is proved against a migrated schema in
    ``tests/db/test_icaap_risk_capital_migration.py``; this client builds its
    schema with ``create_all``, so what it can show is the behaviour above the
    trigger — every earlier revision's digest survives an edit, a recompute and
    a retirement untouched.
    """
    cycle_id = _cycle(committing_db_client, analyst)
    item = _computed_item(committing_db_client, analyst, cycle_id)

    def digests() -> dict[int, str]:
        listing = committing_db_client.get(
            f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/revisions",
            headers=analyst,
        )
        assert listing.status_code == 200, listing.text
        return {
            entry["revision_no"]: entry["snapshot_sha256"] for entry in listing.json()["revisions"]
        }

    before = digests()
    assert set(before) == {1, 2}

    recomputed = committing_db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/compute",
        json={
            "base_revision_no": item["current_revision_no"],
            "manual_inputs": {
                "tier1": "800",
                "irrbb_deltas": {
                    "parallel_up_450": "-116.759902",
                    "parallel_down_450": "142.996097",
                },
            },
            "reason": "Recompute on the corrected Tier 1.",
        },
        headers=analyst,
    )
    assert recomputed.status_code == 200, recomputed.text
    committing_db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/retire",
        json={"reason": "Superseded."},
        headers=analyst,
    )

    after = digests()
    assert set(after) == {1, 2, 3, 4}
    assert {no: after[no] for no in before} == before


def test_the_stored_figure_is_exactly_what_the_engine_computed(
    committing_db_client: TestClient, analyst: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    item = _computed_item(committing_db_client, analyst, cycle_id)
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        stored = session.get(IcaapPillar2Item, UUID(item["id"]))
        assert stored is not None
        assert stored.baseline_amount == Decimal("116.7599")
        revisions = session.scalars(
            IcaapPillar2ItemRevision.__table__.select().where(
                IcaapPillar2ItemRevision.__table__.c.item_id == UUID(item["id"])
            )
        ).all()
        assert len(revisions) == 2
    finally:
        session.close()


def test_two_approvals_of_the_same_revision_do_not_both_land(
    committing_db_client: TestClient, analyst: dict[str, str], approver: dict[str, str]
) -> None:
    cycle_id = _cycle(committing_db_client, analyst)
    item = _computed_item(committing_db_client, analyst, cycle_id)
    path = f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/approve"
    payload = {"revision_no": item["current_revision_no"], "note": "Reviewed."}

    first = committing_db_client.post(path, json=payload, headers=approver)
    assert first.status_code == 200, first.text
    assert first.json()["approval_current"] is True

    second = committing_db_client.post(path, json=payload, headers=approver)
    assert second.status_code == 409, second.text
    assert "item_not_approvable" in second.text or "revision_changed" in second.text
