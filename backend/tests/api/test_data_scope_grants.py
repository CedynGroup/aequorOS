"""Branch and region scopes end to end: composed, refused, audited, projected.

``docs/bi.md`` §Phase 4 puts a scope control in the Members composer, fed by
``GET /organization/institutions/{id}/branches``. Four things are checked here
because each fails silently if it is wrong: the scope is part of the ONE
indivisible binding (so it enters the authority sentence, the audit and the
duplicate rule), an unusable scope is refused at the boundary with a sentence
rather than by the database CHECK, the branch feed reads the CANONICAL register
for exactly one bank, and ``/auth/me`` projects the slice each capability
actually reads.
"""

from __future__ import annotations

from datetime import date
from typing import Any, get_args
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    User,
)
from app.schemas.authorization import GrantableRoleBundle
from app.services import authorization, grant_administration
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, headers

GRANTEE = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
BANK_A = "BK-DSGRNT01"
BANK_B = "BK-DSGRNT02"
AS_OF = date(2026, 8, 31)
EARLIER = date(2026, 7, 31)

#: Bank A's register, using the CANONICAL field spellings.
_BANK_A_REGISTER: tuple[dict[str, Any], ...] = (
    {
        "business_unit_id": "ACC-001",
        "business_unit_name": "Accra Main",
        "region": "Greater Accra",
    },
    {"business_unit_id": "TEM-002", "business_unit_name": "Tema", "region": "Greater Accra"},
    # No region declared: it must come back as unknown rather than guessed from
    # anything else on the row.
    {"business_unit_id": "KUM-003", "business_unit_name": "Kumasi Central"},
)
#: Bank B's register, using the DOCUMENTED ALIAS spellings from
#: docs/API_INTEGRATION.md §3.5. A bank already pushing these cannot be corrected
#: at the boundary, so the read has to resolve them.
_BANK_B_REGISTER: tuple[dict[str, Any], ...] = (
    {"unit_id": "SIB-900", "name": "Sibling Branch", "region": "Northern"},
)


def _session() -> Session:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    return session


def _owner_headers() -> dict[str, str]:
    return headers(roles=("account_admin",), authorization_version=2)


def _grantee_headers(version: int) -> dict[str, str]:
    return headers(user_id=GRANTEE, roles=("viewer",), authorization_version=version)


def _register(db: Session, bank_id: str, rows: tuple[dict[str, Any], ...], as_of: date) -> None:
    batch = IngestionBatch(
        organization_id=ORG_1,
        bank_id=bank_id,
        source_system="EXCEL_CSV",
        adapter_version="1.0",
        extraction_mode="full",
        status="accepted",
        as_of_date=as_of,
    )
    db.add(batch)
    db.flush()
    lineage = LineageRecord(
        organization_id=ORG_1,
        ingestion_batch_id=batch.id,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref="data-scope-test",
        input_lineage_ids=[],
    )
    db.add(lineage)
    db.flush()
    for index, payload in enumerate(rows):
        db.add(
            CanonicalReferenceRow(
                organization_id=ORG_1,
                bank_id=bank_id,
                ingestion_batch_id=batch.id,
                as_of_date=as_of,
                dataset_kind="business_units",
                row_index=index,
                payload=payload,
                source_reference=f"fixture#business_units!{index}",
                lineage_id=lineage.id,
            )
        )
    db.flush()


def _seed() -> None:
    with _session() as db:
        owner = db.get(User, USER_1)
        assert owner is not None
        owner.role = "account_admin"
        db.add(
            User(
                id=GRANTEE,
                organization_id=ORG_1,
                email="kofi.branch@example.test",
                display_name="Kofi Branch",
                role="viewer",
            )
        )
        db.add_all(
            [
                Bank(
                    id=BANK_A,
                    organization_id=ORG_1,
                    name="Aequor Bank Ghana",
                    short_name="Aequor Ghana",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                ),
                Bank(
                    id=BANK_B,
                    organization_id=ORG_1,
                    name="Aequor Rural Bank",
                    short_name="Aequor Rural",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="rural_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                ),
            ]
        )
        db.flush()
        # An earlier as-of for the same bank, superseded by AS_OF below: the
        # reader must take the latest register, not the union of every push.
        _register(
            db,
            BANK_A,
            ({"business_unit_id": "OLD-000", "business_unit_name": "Closed Branch"},),
            EARLIER,
        )
        _register(db, BANK_A, _BANK_A_REGISTER, AS_OF)
        _register(db, BANK_B, _BANK_B_REGISTER, AS_OF)
        db.commit()
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=owner.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ORG_OWNER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
            reason="explicit owner authority for data-scope tests",
        )


@pytest.fixture
def scope_client(db_client: TestClient) -> TestClient:
    _seed()
    return db_client


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "principal_user_id": str(GRANTEE),
        "role_bundle": "analyst",
        "institution_scope": "institution",
        "institution_id": BANK_A,
        "module_scope": "credit",
        "sensitivity_scope": "confidential",
        "reason_category": "other",
        "reason_detail": "Branch manager scope approved by the Head of Retail",
    }
    payload.update(overrides)
    return payload


def _reviewed(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    preview = client.post(
        "/api/v1/authorization/bindings/preview", headers=_owner_headers(), json=payload
    )
    assert preview.status_code == 200, preview.text
    return {**payload, "expected_authority_sentence": preview.json()["authority_sentence"]}


# --- the branch feed ----------------------------------------------------------


def test_the_branch_feed_returns_declared_branches_and_regions(scope_client: TestClient) -> None:
    response = scope_client.get(
        f"/api/v1/organization/institutions/{BANK_A}/branches", headers=_owner_headers()
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_id"] == BANK_A
    assert [(entry["code"], entry["name"], entry["region"]) for entry in body["branches"]] == [
        ("ACC-001", "Accra Main", "Greater Accra"),
        ("KUM-003", "Kumasi Central", None),
        ("TEM-002", "Tema", "Greater Accra"),
    ]
    assert body["regions"] == ["Greater Accra"], "regions are the DECLARED ones, de-duped"
    assert "OLD-000" not in {entry["code"] for entry in body["branches"]}, (
        "a superseded earlier register must not be unioned into the latest one"
    )
    assert [(option["kind"], option["label"]) for option in body["scope_options"]] == [
        ("all", "Whole institution"),
        ("branch", "Selected branches"),
        ("region", "Selected regions"),
    ]


def test_the_branch_feed_resolves_the_documented_field_aliases(scope_client: TestClient) -> None:
    """``unit_id``/``name`` are in the field and cannot be corrected on push."""
    response = scope_client.get(
        f"/api/v1/organization/institutions/{BANK_B}/branches", headers=_owner_headers()
    )

    assert response.status_code == 200, response.text
    assert [(entry["code"], entry["name"]) for entry in response.json()["branches"]] == [
        ("SIB-900", "Sibling Branch")
    ]


def test_the_branch_feed_never_shows_a_sibling_banks_branches(scope_client: TestClient) -> None:
    """Two banks of one organization share an RLS tenant, so the query scopes."""
    bank_a = scope_client.get(
        f"/api/v1/organization/institutions/{BANK_A}/branches", headers=_owner_headers()
    ).json()
    bank_b = scope_client.get(
        f"/api/v1/organization/institutions/{BANK_B}/branches", headers=_owner_headers()
    ).json()

    assert "SIB-900" not in {entry["code"] for entry in bank_a["branches"]}
    assert "Northern" not in bank_a["regions"]
    assert "ACC-001" not in {entry["code"] for entry in bank_b["branches"]}


def test_a_bank_with_no_register_returns_an_honest_empty_shape(scope_client: TestClient) -> None:
    with _session() as db:
        db.add(
            Bank(
                id="BK-DSGRNT03",
                organization_id=ORG_1,
                name="Aequor Newly Onboarded",
                short_name="Aequor New",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        db.commit()

    response = scope_client.get(
        "/api/v1/organization/institutions/BK-DSGRNT03/branches", headers=_owner_headers()
    )

    assert response.status_code == 200, response.text
    assert response.json()["branches"] == []
    assert response.json()["regions"] == []
    assert response.json()["scope_options"], "the control still has its choices"


def test_the_branch_feed_is_owner_gated_like_the_institution_directory(
    scope_client: TestClient,
) -> None:
    """The same account-plane authority, not a weaker one."""
    directory = scope_client.get("/api/v1/organization/institutions", headers=_grantee_headers(1))
    branches = scope_client.get(
        f"/api/v1/organization/institutions/{BANK_A}/branches", headers=_grantee_headers(1)
    )

    assert directory.status_code == 403, directory.text
    assert branches.status_code == directory.status_code, branches.text


def test_the_branch_feed_404s_for_an_institution_outside_the_tenant(
    scope_client: TestClient,
) -> None:
    response = scope_client.get(
        "/api/v1/organization/institutions/BK-NOTMINE1/branches", headers=_owner_headers()
    )

    assert response.status_code == 404, response.text


# --- the composer -------------------------------------------------------------


def test_a_branch_scoped_grant_carries_its_scope_through_sentence_and_audit(
    scope_client: TestClient,
) -> None:
    payload = _payload(data_scope_kind="branch", data_scope_values=["TEM-002", "ACC-001"])
    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, payload),
    )

    assert created.status_code == 201, created.text
    binding = created.json()["binding"]
    assert binding["data_scope_kind"] == "branch"
    assert binding["data_scope_values"] == ["ACC-001", "TEM-002"], "normalised before storage"
    assert binding["data_scope_label"] == "Selected branches: ACC-001, TEM-002"
    assert binding["authority_sentence"] == (
        "Kofi Branch is an Analyst in Credit for Aequor Bank Ghana, covering "
        "Confidential data, limited to branches ACC-001 and TEM-002."
    )

    with _session() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_granted",
                AuditEvent.entity_id == binding["id"],
            )
        )
        assert audit is not None
        assert audit.details["scope"]["data_scope_kind"] == "branch"
        assert audit.details["scope"]["data_scope_values"] == ["ACC-001", "TEM-002"]


def test_a_region_scoped_grant_reads_as_one_plain_sentence(scope_client: TestClient) -> None:
    payload = _payload(data_scope_kind="region", data_scope_values=["Greater Accra"])
    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, payload),
    )

    assert created.status_code == 201, created.text
    assert created.json()["binding"]["authority_sentence"].endswith(
        "limited to the Greater Accra region."
    )
    assert created.json()["binding"]["data_scope_label"] == "Selected regions: Greater Accra"


def test_a_whole_institution_sentence_is_unchanged_by_this_phase(
    scope_client: TestClient,
) -> None:
    """Every grant written before data scopes meant the whole institution."""
    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, _payload()),
    )

    assert created.status_code == 201, created.text
    assert created.json()["binding"]["authority_sentence"] == (
        "Kofi Branch is an Analyst in Credit for Aequor Bank Ghana, covering Confidential data."
    )
    assert created.json()["binding"]["data_scope_label"] == "Whole institution"


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        (
            {"data_scope_kind": "all", "data_scope_values": ["ACC-001"]},
            "Whole-institution access covers every branch",
        ),
        (
            {"data_scope_kind": "branch", "data_scope_values": []},
            "Select at least one branch or region",
        ),
        (
            {"data_scope_kind": "branch", "data_scope_values": ["   "]},
            "Select at least one branch or region",
        ),
        (
            {"data_scope_kind": "branch", "data_scope_values": ["B" * 121]},
            "at most 120 characters",
        ),
        (
            {
                "institution_scope": "organization",
                "institution_id": None,
                "data_scope_kind": "region",
                "data_scope_values": ["Ashanti"],
            },
            "belong to one institution",
        ),
        ({"data_scope_kind": "mixed", "data_scope_values": ["ACC-001"]}, "data_scope_kind"),
        ({"data_scope_kind": "none", "data_scope_values": []}, "data_scope_kind"),
    ],
)
def test_the_composer_refuses_an_unusable_scope_at_the_boundary(
    scope_client: TestClient,
    overrides: dict[str, Any],
    expected_fragment: str,
) -> None:
    """422 with a sentence, never an IntegrityError from the database CHECK."""
    response = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json={**_payload(**overrides), "expected_authority_sentence": "unreviewed"},
    )

    assert response.status_code == 422, response.text
    assert expected_fragment in response.text


def test_two_scopes_of_one_role_are_two_grants_and_a_repeat_is_a_duplicate(
    scope_client: TestClient,
) -> None:
    first = _payload(data_scope_kind="branch", data_scope_values=["ACC-001"])
    second = _payload(data_scope_kind="branch", data_scope_values=["TEM-002"])

    created_first = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, first),
    )
    created_second = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, second),
    )
    # The same set, differently spelt and ordered: one grant, not a second.
    repeat = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(
            scope_client,
            _payload(data_scope_kind="branch", data_scope_values=[" ACC-001 ", "ACC-001"]),
        ),
    )

    assert created_first.status_code == 201, created_first.text
    assert created_second.status_code == 201, created_second.text
    assert repeat.status_code == 409, repeat.text
    details = repeat.json()["error"]["details"]
    assert details["existing_binding_id"] == created_first.json()["binding"]["id"]


def test_a_human_cannot_be_granted_the_machine_feed_bundle(scope_client: TestClient) -> None:
    """``bi_reader`` is machine-only; the composer never offers it."""
    response = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json={**_payload(role_bundle="bi_reader"), "expected_authority_sentence": "unreviewed"},
    )

    assert response.status_code == 422, response.text
    assert "bi_reader" not in get_args(GrantableRoleBundle), (
        "the Members composer's public vocabulary must never offer a machine bundle"
    )

    # And the service refuses it even when the schema is bypassed entirely.
    with _session() as db:
        with pytest.raises(grant_administration.GrantAdministrationError, match="not grantable"):
            grant_administration.validate_public_grant(
                RoleBundle.BI_READER,
                authorization.BindingScope(
                    InstitutionScope.INSTITUTION,
                    BANK_A,
                    ModuleScope.CREDIT,
                    SensitivityScope.CONFIDENTIAL,
                ),
            )
        with pytest.raises(authorization.AuthorizationInvariantError, match="machine bundle"):
            authorization.create_role_binding(
                db,
                organization_id=ORG_1,
                principal_user_id=GRANTEE,
                principal_type=PrincipalType.HUMAN,
                role_bundle=RoleBundle.BI_READER,
                scope=authorization.BindingScope(
                    InstitutionScope.INSTITUTION,
                    BANK_A,
                    ModuleScope.CREDIT,
                    SensitivityScope.CONFIDENTIAL,
                ),
                grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
                reason="a human must never hold the feed credential",
            )


def test_a_machine_principal_accepts_the_feed_bundle(scope_client: TestClient) -> None:
    with _session() as db:
        service = User(
            id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            organization_id=ORG_1,
            email="feed.reader@service.aequoros.invalid",
            role="viewer",
            auth_provider="service",
        )
        db.add(service)
        db.commit()
        binding = authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=service.id,
            principal_type=PrincipalType.MACHINE,
            role_bundle=RoleBundle.BI_READER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                BANK_A,
                ModuleScope.RISK,
                SensitivityScope.AGGREGATED,
            ),
            grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
            reason="analytics feed credential",
        )

        assert binding.role_bundle == "bi_reader"
        assert binding.data_scope_kind == "all"
        assert binding.data_scope_values is None

        # Its lifecycle belongs to its key, so Members cannot cut it loose.
        with pytest.raises(grant_administration.GrantAdministrationError, match="integration key"):
            grant_administration.revoke_scoped_grant(
                db,
                organization_id=ORG_1,
                binding_id=binding.id,
                actor_user_id=USER_1,
                reason="attempt to revoke a machine binding from Members",
            )


# --- the /auth/me projection --------------------------------------------------


def _capability(body: dict[str, Any], institution_id: str, module: str) -> dict[str, Any]:
    rows = [
        item
        for item in body["effective_authority"]["institution_capabilities"]
        if item["institution_id"] == institution_id
    ]
    assert rows, f"no projected capabilities for {institution_id}"
    matches = [cap for cap in rows[0]["capabilities"] if cap["module"] == module]
    assert matches, f"no projected {module} capability for {institution_id}"
    return matches[0]


def test_auth_me_projects_the_slice_each_capability_actually_reads(
    scope_client: TestClient,
) -> None:
    """Per (institution, module, sensitivity, permission), not per institution.

    The grantee holds Credit by branch and Liquidity institution-wide on the SAME
    bank, so one scope per institution would have to be wrong about one of them.
    """
    for payload in (
        _payload(data_scope_kind="branch", data_scope_values=["ACC-001"]),
        _payload(module_scope="liq"),
    ):
        created = scope_client.post(
            "/api/v1/authorization/bindings",
            headers=_owner_headers(),
            json=_reviewed(scope_client, payload),
        )
        assert created.status_code == 201, created.text

    with _session() as db:
        grantee = db.get(User, GRANTEE)
        assert grantee is not None
        version = grantee.authorization_version

    me = scope_client.get("/api/v1/auth/me", headers=_grantee_headers(version))

    assert me.status_code == 200, me.text
    credit = _capability(me.json(), BANK_A, "credit")
    liquidity = _capability(me.json(), BANK_A, "liq")
    assert credit["data_scope"] == {
        "kind": "branch",
        "branches": ["ACC-001"],
        "regions": [],
    }
    assert liquidity["data_scope"] == {"kind": "all", "branches": [], "regions": []}


def test_a_whole_institution_principal_projects_the_whole_institution(
    scope_client: TestClient,
) -> None:
    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(scope_client, _payload()),
    )
    assert created.status_code == 201, created.text
    with _session() as db:
        grantee = db.get(User, GRANTEE)
        assert grantee is not None
        version = grantee.authorization_version

    me = scope_client.get("/api/v1/auth/me", headers=_grantee_headers(version))

    assert me.status_code == 200, me.text
    capability = _capability(me.json(), BANK_A, "credit")
    assert capability["data_scope"]["kind"] == "all"
    assert capability["data_scope"]["branches"] == []


def test_granting_and_revoking_a_scope_bumps_authv_and_ends_the_session(
    scope_client: TestClient,
) -> None:
    """The ``/auth/me`` cache key includes ``authv``, so a scope change must bump it.

    Without the bump a widened or narrowed slice would be invisible until the
    access token expired, and the dashboard would keep serving the cached
    projection under the old key.
    """
    with _session() as db:
        grantee = db.get(User, GRANTEE)
        assert grantee is not None
        before = grantee.authorization_version
    stale = _grantee_headers(before)

    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(
            scope_client, _payload(data_scope_kind="branch", data_scope_values=["ACC-001"])
        ),
    )
    assert created.status_code == 201, created.text

    with _session() as db:
        grantee = db.get(User, GRANTEE)
        assert grantee is not None
        after_grant = grantee.authorization_version
    assert after_grant > before, "a data-scope grant must invalidate the grantee's authority"
    assert scope_client.get("/api/v1/auth/me", headers=stale).status_code == 401

    revoked = scope_client.post(
        f"/api/v1/authorization/bindings/{created.json()['binding']['id']}/revoke",
        headers=_owner_headers(),
        json={"reason": "branch manager moved to another region"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data_scope_kind"] == "branch"
    assert revoked.json()["data_scope_values"] == ["ACC-001"]

    with _session() as db:
        grantee = db.get(User, GRANTEE)
        assert grantee is not None
        after_revoke = grantee.authorization_version
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "authorization.binding_revoked",
                AuditEvent.entity_id == created.json()["binding"]["id"],
            )
        )
        assert audit is not None
        assert audit.details["scope"]["data_scope_values"] == ["ACC-001"]
        assert "limited to branch ACC-001" in audit.details["authority_sentence"]
    assert after_revoke > after_grant


def test_the_scope_is_stored_on_the_one_indivisible_row(scope_client: TestClient) -> None:
    created = scope_client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(),
        json=_reviewed(
            scope_client,
            _payload(data_scope_kind="branch", data_scope_values=["ACC-001", "TEM-002"]),
        ),
    )
    assert created.status_code == 201, created.text

    with _session() as db:
        rows = list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.role_bundle == RoleBundle.ANALYST.value,
                )
            )
        )

    assert len(rows) == 1, "two branches are one grant, never one row per branch"
    assert rows[0].data_scope_values == ["ACC-001", "TEM-002"]


def test_baseline_membership_cannot_carry_a_narrow_data_scope(scope_client: TestClient) -> None:
    """Membership is system-managed lifecycle evidence, never a data grant.

    The baseline row exists so activation and deactivation have somewhere to be
    recorded; the evaluator must never read it as access. A branch scope on it
    would make it look like one, so ``create_role_binding``'s baseline invariant
    refuses the scope alongside every other dimension it already pins.
    """
    with (
        _session() as db,
        pytest.raises(authorization.AuthorizationInvariantError, match="baseline membership"),
    ):
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=GRANTEE,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.MEMBER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.RESTRICTED,
                DataScope.BRANCH,
                ("ACC-001",),
            ),
            grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test-suite"),
            reason="a baseline membership row must not slice the book",
        )


@pytest.mark.parametrize(
    "module", [module for module in ModuleScope if module is not ModuleScope.CREDIT]
)
@pytest.mark.parametrize("kind", ["branch", "region"])
def test_narrowing_is_credit_only_on_preview_and_issue(
    scope_client: TestClient, module: ModuleScope, kind: str
) -> None:
    payload = _payload(
        module_scope=module.value, data_scope_kind=kind, data_scope_values=["ACC-001"]
    )
    for path in ("/preview", ""):
        response = scope_client.post(
            f"/api/v1/authorization/bindings{path}",
            headers=_owner_headers(),
            json=payload if path else {**payload, "expected_authority_sentence": "unreviewed"},
        )
        assert response.status_code == 422, response.text
        assert "only for Credit" in response.text
    with _session() as db:
        assert not list(
            db.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.principal_user_id == GRANTEE,
                    AuthorizationBinding.data_scope_kind != "all",
                )
            )
        )
        with pytest.raises(grant_administration.GrantAdministrationError, match="only for Credit"):
            grant_administration.validate_public_grant(
                RoleBundle.VIEWER,
                authorization.BindingScope(
                    InstitutionScope.INSTITUTION,
                    BANK_A,
                    module,
                    SensitivityScope.ALL,
                    DataScope(kind),
                    ("ACC-001",),
                ),
            )
