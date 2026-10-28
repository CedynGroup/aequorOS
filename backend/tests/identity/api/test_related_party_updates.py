from __future__ import annotations

from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.presentation import LOCKED_JSON, LOCKED_TEXT
from app.core.key_management.types import KeyStatus, KeyUnavailableError
from app.identity.schemas.institution_profile import (
    InstitutionProfileFullRead,
    RelatedPartyCreate,
    RelatedPartyRead,
    RelatedPartyRoleInput,
)
from app.identity.service import institution_profile
from tests.core import test_bank_field_encryption as fields
from tests.support.helpers import headers

provider = fields.provider
BASE = f"/api/v1/banks/{fields.BANK_A}/related-parties"


@pytest.fixture
def party(db_session: Session, provider: LocalKeyProvider) -> RelatedPartyRead:
    fields.connect(db_session, provider)
    return institution_profile.create_related_party(
        db_session,
        fields.CTX,
        fields.BANK_A,
        RelatedPartyCreate(
            reason="Synthetic supplied-field update",
            party_type="individual",
            full_name=fields.NAME,
            contact={"email": "synthetic@example.test"},
            regulated_jurisdiction="Synthetic jurisdiction",
            roles=[
                RelatedPartyRoleInput(
                    role="director",
                    other_responsibilities="Synthetic duties",
                    term_of_appointment="Three years",
                    icag_registration="Synthetic registration",
                    sitting_allowance=Decimal("10"),
                    travel_allowance=Decimal("20"),
                    annual_fees=Decimal("30"),
                )
            ],
        ),
    )


def protected_storage(db: Session, party_id: UUID) -> list[tuple[object, ...]]:
    statements = (
        "SELECT full_name, contact, full_name_blind_index FROM related_parties WHERE id = :id",
        "SELECT id, other_responsibilities, term_of_appointment, icag_registration "
        "FROM related_party_roles WHERE party_id = :id ORDER BY id",
    )
    rows = [
        cast(tuple[object, ...], tuple(row))
        for statement in statements
        for row in db.execute(
            text(statement).bindparams(bindparam("id", type_=Uuid())), {"id": party_id}
        )
    ]
    db.commit()
    return rows


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_http_locked_shareholding_mutations_preserve_identity_and_scope(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    party: RelatedPartyRead,
    status: KeyStatus,
) -> None:
    owners = [
        institution_profile.create_related_party(
            db_session,
            fields.CTX,
            bank_id,
            RelatedPartyCreate(
                reason="Synthetic shareholding owner",
                party_type="individual",
                full_name="Synthetic UBO",
            ),
        )
        for bank_id in (fields.BANK_A, fields.BANK_B)
    ]
    before = protected_storage(db_session, party.id)
    provider.set_status(fields.KEY_A, status)
    reason = "revoked" if status == KeyStatus.DISABLED else "unavailable"
    payload: dict[str, object] = {
        "reason": "Synthetic numeric holding",
        "share_type": "ordinary",
        "shareholder_rights": "voting",
        "number_of_shares": "100",
        "pct_shareholding": "10",
        "ubo_party_id": str(owners[0].id),
    }
    url = f"{BASE}/{party.id}/shareholdings"
    for target, body in (
        (url, {**payload, "ubo_party_id": str(owners[1].id)}),
        (url.replace(fields.BANK_A, fields.BANK_B), payload),
    ):
        response = db_client.post(target, headers=headers(), json=body)
        assert response.status_code == 404, response.text
    response = db_client.post(url, headers=headers(), json=payload)
    assert response.status_code == 201, response.text
    created = RelatedPartyRead.model_validate_json(response.text)
    assert len(created.shareholdings) == 1
    holding = created.shareholdings[0]
    assert holding.ubo_party_id == owners[0].id
    assert holding.number_of_shares == Decimal("100")
    assert created.full_name == LOCKED_TEXT
    assert created.contact == {LOCKED_JSON: reason}
    assert TypeAdapter(dict[str, str]).validate_json(
        response.headers["X-Aequoros-Protected-Data"]
    ) == {fields.BANK_A: reason}
    update_url = f"{url}/{holding.id}"
    for target, body in (
        (update_url, {**payload, "ubo_party_id": str(owners[1].id)}),
        (update_url.replace(fields.BANK_A, fields.BANK_B), payload),
        (update_url.replace(str(party.id), str(owners[0].id)), payload),
    ):
        response = db_client.put(target, headers=headers(), json=body)
        assert response.status_code == 404, response.text
    response = db_client.put(
        update_url,
        headers=headers(),
        json={**payload, "number_of_shares": "250", "pct_shareholding": "25"},
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    assert saved.full_name == LOCKED_TEXT
    assert saved.contact == {LOCKED_JSON: reason}
    assert saved.roles[0].term_of_appointment == LOCKED_TEXT
    assert saved.shareholdings[0].number_of_shares == Decimal("250")
    assert saved.shareholdings[0].pct_shareholding == Decimal("25")
    assert TypeAdapter(dict[str, str]).validate_json(
        response.headers["X-Aequoros-Protected-Data"]
    ) == {fields.BANK_A: reason}
    assert protected_storage(db_session, party.id) == before
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={"reason": "Refuse protected edit", "full_name": "Changed identity"},
    )
    assert response.status_code == 503, response.text
    provider.set_status(fields.KEY_A, KeyStatus.ACTIVE)
    db_session.expunge_all()
    restored = institution_profile.get_full_profile(db_session, fields.CTX, fields.BANK_A)
    current = next(record for record in restored.related_parties if record.id == party.id)
    assert current.full_name == party.full_name
    assert current.contact == party.contact
    assert current.roles == party.roles
    assert current.shareholdings == saved.shareholdings


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_http_numeric_update_preserves_protected_storage_and_recovers(
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    party: RelatedPartyRead,
    status: KeyStatus,
) -> None:
    before = protected_storage(db_session, party.id)
    provider.set_status(fields.KEY_A, status)
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={
            "reason": "Change only unprotected fields",
            "status": "inactive",
            "regulated_elsewhere": True,
            "regulated_jurisdiction": None,
            "role_updates": [
                {
                    "role_id": str(party.roles[0].id),
                    "appointed_on": "2026-10-01",
                    "sitting_allowance": "45.67",
                    "travel_allowance": None,
                    "annual_fees": "100",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    reason = "revoked" if status == KeyStatus.DISABLED else "unavailable"
    assert TypeAdapter(dict[str, str]).validate_json(
        response.headers["X-Aequoros-Protected-Data"]
    ) == {fields.BANK_A: reason}
    assert saved.full_name == LOCKED_TEXT
    assert saved.contact == {LOCKED_JSON: reason}
    assert saved.roles[0].other_responsibilities == LOCKED_TEXT
    assert saved.roles[0].term_of_appointment == LOCKED_TEXT
    assert saved.roles[0].icag_registration == LOCKED_TEXT
    assert saved.roles[0].id == party.roles[0].id
    assert saved.roles[0].sitting_allowance == Decimal("45.67")
    assert saved.roles[0].travel_allowance is None
    assert saved.status == "inactive"
    assert saved.regulated_elsewhere
    assert saved.regulated_jurisdiction is None
    assert protected_storage(db_session, party.id) == before
    provider.set_status(fields.KEY_A, KeyStatus.ACTIVE)
    response = db_client.get(
        f"/api/v1/banks/{fields.BANK_A}/institution-profile", headers=headers()
    )
    assert response.status_code == 200, response.text
    profile = InstitutionProfileFullRead.model_validate_json(response.text)
    restored = profile.related_parties[0]
    assert restored.full_name == party.full_name
    assert restored.contact == party.contact
    assert restored.roles[0].other_responsibilities == party.roles[0].other_responsibilities
    assert restored.roles[0].term_of_appointment == party.roles[0].term_of_appointment
    assert restored.roles[0].icag_registration == party.roles[0].icag_registration
    assert restored.roles[0].sitting_allowance == Decimal("45.67")
    assert restored.roles[0].annual_fees == Decimal("100")
    assert restored.roles[0].appointed_on is not None
    assert restored.roles[0].appointed_on.isoformat() == "2026-10-01"


@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
@pytest.mark.parametrize(
    "change",
    [
        {"full_name": "Synthetic rename"},
        {"contact": {}},
        {"roles": []},
        {"roles": [{"role": "shareholder"}]},
        {"other_responsibilities": None},
        {"term_of_appointment": None},
        {"icag_registration": None},
        {"term_of_appointment": ""},
    ],
)
def test_http_protected_changes_and_clears_refuse_atomically(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    party: RelatedPartyRead,
    status: KeyStatus,
    change: dict[str, object],
) -> None:
    before = protected_storage(db_session, party.id)
    provider.set_status(fields.KEY_A, status)
    body: dict[str, object] = {"reason": "Refuse protected edit", "status": "inactive"}
    if {"full_name", "contact", "roles"} & change.keys():
        body.update(change)
    else:
        body["role_updates"] = [{"role_id": str(party.roles[0].id), "annual_fees": "999", **change}]
    response = db_client.put(f"{BASE}/{party.id}", headers=headers(), json=body)
    assert response.status_code == 503, response.text
    error = TypeAdapter(dict[str, dict[str, object]]).validate_json(response.text)
    assert (
        error["error"]["message"]
        == KeyUnavailableError("private", revoked=status == KeyStatus.DISABLED).public_message
    )
    assert protected_storage(db_session, party.id) == before
    provider.set_status(fields.KEY_A, KeyStatus.ACTIVE)
    response = db_client.get(
        f"/api/v1/banks/{fields.BANK_A}/institution-profile", headers=headers()
    )
    current = InstitutionProfileFullRead.model_validate_json(response.text).related_parties[0]
    assert current.status == "active"
    assert current.roles[0].annual_fees == Decimal("30")


@pytest.mark.parametrize("missing_index", [False, True])
@pytest.mark.parametrize("status", [KeyStatus.DISABLED, KeyStatus.UNAVAILABLE])
def test_http_protected_clear_checks_current_and_index_prerequisites(  # noqa: PLR0913
    db_client: TestClient,
    db_session: Session,
    provider: LocalKeyProvider,
    party: RelatedPartyRead,
    missing_index: bool,
    status: KeyStatus,
) -> None:
    key = registry.scoped_key(db_session, bank_id=fields.BANK_A, organization_id=fields.ORG_1)
    registry.rotate(db_session, key, fields.NEXT, providers=lambda _name: provider)
    db_session.commit()
    before = protected_storage(db_session, party.id)
    provider.set_status(fields.KEY_A if missing_index else fields.NEXT, status)
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={
            "reason": "Clear protected role field",
            "role_updates": [{"role_id": str(party.roles[0].id), "term_of_appointment": None}],
        },
    )
    assert response.status_code == 503, response.text
    error = TypeAdapter(dict[str, dict[str, object]]).validate_json(response.text)
    assert (
        error["error"]["message"]
        == KeyUnavailableError("private", revoked=status == KeyStatus.DISABLED).public_message
    )
    assert protected_storage(db_session, party.id) == before


def test_http_omitted_roles_retain_ids_and_explicit_roles_replace(
    db_client: TestClient,
    db_session: Session,
    party: RelatedPartyRead,
) -> None:
    before = protected_storage(db_session, party.id)
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={"reason": "Status only", "status": "inactive"},
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    assert saved.full_name == party.full_name
    assert saved.contact == party.contact
    assert saved.roles == party.roles
    assert protected_storage(db_session, party.id) == before
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={
            "reason": "Replace roles",
            "roles": [{"role": "shareholder"}],
        },
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    assert [role.role for role in saved.roles] == ["shareholder"]
    assert saved.roles[0].id != party.roles[0].id
    response = db_client.put(
        f"{BASE}/{party.id}", headers=headers(), json={"reason": "Remove roles", "roles": []}
    )
    assert response.status_code == 200, response.text
    assert RelatedPartyRead.model_validate_json(response.text).roles == []


@pytest.mark.parametrize("foreign_bank", [False, True])
def test_http_role_ids_are_scoped_to_bank_and_party(
    db_client: TestClient,
    db_session: Session,
    party: RelatedPartyRead,
    foreign_bank: bool,
) -> None:
    foreign = institution_profile.create_related_party(
        db_session,
        fields.CTX,
        fields.BANK_B if foreign_bank else fields.BANK_A,
        RelatedPartyCreate(
            reason="Foreign role owner",
            party_type="individual",
            full_name="Foreign synthetic party",
            roles=[RelatedPartyRoleInput(role="director", annual_fees=Decimal("77"))],
        ),
    )
    before = protected_storage(db_session, party.id)
    foreign_before = protected_storage(db_session, foreign.id)
    for role_id in (foreign.roles[0].id, UUID(int=1)):
        response = db_client.put(
            f"{BASE}/{party.id}",
            headers=headers(),
            json={
                "reason": "Foreign role refusal",
                "status": "inactive",
                "role_updates": [
                    {"role_id": str(party.roles[0].id), "annual_fees": "999"},
                    {"role_id": str(role_id), "annual_fees": "999"},
                ],
            },
        )
        assert response.status_code == 404, response.text
        assert protected_storage(db_session, party.id) == before
        assert protected_storage(db_session, foreign.id) == foreign_before
    for bank_id, expected in (
        (fields.BANK_A, party),
        (fields.BANK_B if foreign_bank else fields.BANK_A, foreign),
    ):
        db_session.expunge_all()
        current = institution_profile.get_full_profile(db_session, fields.CTX, bank_id)
        assert (
            next(record for record in current.related_parties if record.id == expected.id).roles
            == expected.roles
        )


@pytest.mark.parametrize(
    "change",
    [
        {"full_name": LOCKED_TEXT},
        {"contact": {LOCKED_JSON: "revoked"}},
        {"roles": [{"role": "director", "term_of_appointment": LOCKED_TEXT}]},
        {"other_responsibilities": LOCKED_TEXT},
    ],
)
def test_http_placeholder_writes_are_rejected(
    db_client: TestClient,
    db_session: Session,
    party: RelatedPartyRead,
    change: dict[str, object],
) -> None:
    before = protected_storage(db_session, party.id)
    body: dict[str, object] = {"reason": "Reject placeholders"}
    if "other_responsibilities" in change:
        body["role_updates"] = [{"role_id": str(party.roles[0].id), **change}]
    else:
        body.update(change)
    response = db_client.put(f"{BASE}/{party.id}", headers=headers(), json=body)
    assert response.status_code == 503, response.text
    assert protected_storage(db_session, party.id) == before


@pytest.mark.parametrize(
    "change",
    [
        {"full_name": None},
        {"contact": None},
        {"party_type": None},
        {"status": None},
        {"regulated_elsewhere": None},
        {"roles": None},
        {"role_updates": None},
        {"roles": [], "role_updates": []},
    ],
)
def test_http_invalid_partial_update_is_unchanged(
    db_client: TestClient,
    db_session: Session,
    party: RelatedPartyRead,
    change: dict[str, object],
) -> None:
    before = protected_storage(db_session, party.id)
    response = db_client.put(
        f"{BASE}/{party.id}", headers=headers(), json={"reason": "Invalid partial update", **change}
    )
    assert response.status_code == 422, response.text
    assert protected_storage(db_session, party.id) == before


def test_http_healthy_role_update_preserves_identity_and_validates_effective_roles(
    db_client: TestClient,
    party: RelatedPartyRead,
) -> None:
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={
            "reason": "Update role without replacement",
            "contact": {},
            "role_updates": [
                {
                    "role_id": str(party.roles[0].id),
                    "role": "ultimate_beneficial_owner",
                    "term_of_appointment": None,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    assert saved.roles[0].id == party.roles[0].id
    assert saved.roles[0].term_of_appointment is None
    assert saved.roles[0].other_responsibilities == party.roles[0].other_responsibilities
    assert saved.roles[0].icag_registration == party.roles[0].icag_registration
    assert saved.contact == {}
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={"reason": "Retained UBO role", "party_type": "legal_entity"},
    )
    assert response.status_code == 409, response.text
    response = db_client.put(
        f"{BASE}/{party.id}",
        headers=headers(),
        json={
            "reason": "Change type and role together",
            "party_type": "legal_entity",
            "role_updates": [{"role_id": str(party.roles[0].id), "role": "shareholder"}],
        },
    )
    assert response.status_code == 200, response.text
    saved = RelatedPartyRead.model_validate_json(response.text)
    assert saved.party_type == "legal_entity"
    assert saved.roles[0].role == "shareholder"
    assert saved.roles[0].id == party.roles[0].id
