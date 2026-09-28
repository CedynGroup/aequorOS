"""The dashboard and measure routes, attacked rather than exercised.

The central test in this file is the one proving that a shared dashboard does not
carry its owner's authority:
a dashboard is built by a principal who can read everything, shared to one who
can read almost nothing, and the narrow reader's response is checked for the
absence of the owner's figures, the owner's member ids and even the refused
widget's TITLE — by searching the serialised body, not by asserting a field is
None. A share that leaked the owner's authority would be a cross-user breach
inside one tenant, so the property is proven against the bytes.

Around it:

* the four visibility rules, each reachable by exactly the identities it names;
* only the owner may edit, delete or re-share — proven against an Org Owner and
  an account administrator, the two identities most likely to be given a
  shortcut later;
* a foreign tenant's dashboard id is ``404 Bank not found.`` at the institution,
  and a sibling institution's id is 404 at the object;
* the promotion: a proposer cannot approve their own measure, a second person
  with approval authority can, the approved formula is frozen, and an edit
  afterwards drops the certification;
* with ``BI_ENABLED`` unset every one of these paths answers 404 — the feature is
  not forbidden, it is not there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import MUTATION_ROLE_DEPENDENCY_NAMES
from app.core.authorization import (
    MACHINE_ROLE_BUNDLES,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
    principal_bundle_compatible,
)
from app.core.config import get_settings
from app.models import AuditEvent, AuthorizationBinding, Bank, Organization, User
from app.models.bi import BiQueryLog
from app.services import authorization
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

BANK_ID = "BK-BICAPI01"
SIBLING_BANK_ID = "BK-BICAPI02"
OTHER_TENANT_BANK = "BK-BICAPI03"
BASE = f"/api/v1/banks/{BANK_ID}/bi"
AS_OF = "2026-08-31"

#: Two more identities of ORG_1: the narrow reader a dashboard is shared with,
#: and an Org Owner who is not the dashboard's owner.
VIEWER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
OWNER_ROLE_USER = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")

LOANS = "loans.balance_rc"
DEPOSITS = "deposits.balance_rc"
OBLIGOR = "counterparty.name"

#: The refused widget's own title. It must not appear anywhere in a narrow
#: reader's response: "you may not see funding concentration" tells them the
#: institution tracks it.
SECRET_TITLE = "Deposit funding by obligor"


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bank(db: Session, bank_id: str, organization_id: str) -> Bank:
    existing = db.get(Bank, bank_id)
    if existing is not None:
        return existing
    bank = Bank(
        id=bank_id,
        organization_id=organization_id,
        name=f"BI content {bank_id}",
        short_name="BI content",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _user(db: Session, user_id: UUID, organization_id: str) -> User:
    existing = db.get(User, user_id)
    if existing is not None:
        return existing
    user = User(
        id=user_id,
        organization_id=organization_id,
        email=f"{user_id}@example.test",
        display_name=f"Person {str(user_id)[:4]}",
    )
    db.add(user)
    db.flush()
    return user


def _grant(
    db: Session,
    user_id: UUID,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    bundle: RoleBundle = RoleBundle.VIEWER,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(InstitutionScope.ORGANIZATION, None, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the BI content routes.",
        commit=False,
    )
    db.flush()


def _authv(db: Session, user_id: UUID) -> int:
    user = db.get(User, user_id)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


@pytest.fixture
def plane(db_session: Session) -> Bank:
    """One institution, a sibling, a foreign tenant, and exactly these grants.

    ``USER_1`` reads everything, ``VIEWER`` reads credit aggregates only, and
    ``OWNER_ROLE_USER`` is an Org Owner — the identity a shortcut would most
    plausibly be written for.
    """

    bank = _bank(db_session, BANK_ID, ORG_1)
    _bank(db_session, SIBLING_BANK_ID, ORG_1)
    if db_session.get(Organization, ORG_2) is None:
        db_session.add(Organization(id=ORG_2, name="Other tenant"))
        db_session.flush()
    _bank(db_session, OTHER_TENANT_BANK, ORG_2)
    _user(db_session, USER_1, ORG_1)
    _user(db_session, VIEWER, ORG_1)
    _user(db_session, OWNER_ROLE_USER, ORG_1)
    _user(db_session, USER_2, ORG_2)
    # The hermetic fixture hands every identity an organization-wide all/all
    # sentence. These tests are about what a NARROW reader sees, so this tenant's
    # baseline goes and each grant below is stated. The other tenant's baseline is
    # left alone: its identity is here to be refused at the institution, not to
    # read anything.
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.flush()
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    # Ownership is two sentences: the Owner bundle administers and carries no
    # ``view``, so without a separate viewer row an Org Owner sees no figure at
    # all (the founder's production lockout). Both are granted here, so the
    # ownership tests below refuse an identity that CAN read — which is the case
    # a shortcut would be written for.
    _grant(
        db_session,
        OWNER_ROLE_USER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.ORG_OWNER,
    )
    _grant(
        db_session,
        OWNER_ROLE_USER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.VIEWER,
    )
    db_session.commit()
    return bank


def _headers(db: Session, user_id: UUID, *, organization_id: str = ORG_1) -> dict[str, str]:
    return headers(
        org_id=organization_id, user_id=user_id, authorization_version=_authv(db, user_id)
    )


def _widget(widget_id: str, title: str, *measures: str, dimensions: tuple[str, ...] = ()) -> Any:
    return {
        "id": widget_id,
        "kind": "kpi",
        "title": title,
        "query": {
            "measures": list(measures),
            "dimensions": list(dimensions),
            "window": "as_of",
        },
    }


def _spec(*widgets: Any) -> dict[str, Any]:
    return {
        "widgets": list(widgets),
        "layout": [
            {"i": widget["id"], "x": 0, "y": index, "w": 4, "h": 4}
            for index, widget in enumerate(widgets)
        ],
    }


MIXED_SPEC = _spec(
    _widget("loans", "Loan book", LOANS),
    _widget("funding", SECRET_TITLE, DEPOSITS, dimensions=(OBLIGOR,)),
)


def _create(  # noqa: PLR0913 - one dashboard, stated explicitly
    client: TestClient,
    db: Session,
    *,
    visibility: str = "private",
    role: str | None = None,
    spec: dict[str, Any] | None = None,
    owner: UUID = USER_1,
    title: str = "Funding and lending",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "visibility": visibility,
        "spec": spec if spec is not None else MIXED_SPEC,
    }
    if role is not None:
        body["visibility_role"] = role
    response = client.post(f"{BASE}/dashboards", json=body, headers=_headers(db, owner))
    assert response.status_code == 201, response.text
    return response.json()


# --- the central property ----------------------------------------------------------------


def test_a_shared_dashboard_does_not_carry_its_owners_authority(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The one test this feature exists to pass.

    The owner reads every module at every sensitivity and saves a canvas with a
    credit widget and an obligor-level funding widget. It is shared to an identity
    holding credit aggregates only. That identity opens it and must receive:

    * the credit widget, with its query;
    * the funding widget as ``restricted``, with its id and geometry and NOTHING
      else — checked by searching the whole serialised body for the refused
      member ids and for the widget's own title, because a field that is merely
      ``None`` today could be populated by a future renderer.
    """

    _ = plane
    created = _create(db_client, db_session, visibility="users")
    share = db_client.put(
        f"{BASE}/dashboards/{created['id']}/shares",
        json={"user_ids": [str(VIEWER)]},
        headers=_headers(db_session, USER_1),
    )
    assert share.status_code == 200, share.text

    owner_view = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, USER_1)
    )
    viewer_view = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, VIEWER)
    )
    assert owner_view.status_code == 200, owner_view.text
    assert viewer_view.status_code == 200, viewer_view.text

    owner_payload = owner_view.json()
    assert owner_payload["restricted_widgets"] == 0
    assert all(widget["access"] == "granted" for widget in owner_payload["widgets"])
    assert SECRET_TITLE in owner_view.text

    payload = viewer_view.json()
    assert payload["restricted_widgets"] == 1
    assert payload["readable_widgets"] == 2
    refused = next(widget for widget in payload["widgets"] if widget["access"] == "restricted")
    granted = next(widget for widget in payload["widgets"] if widget["access"] == "granted")
    assert refused["id"] == "funding"
    assert refused["layout"]["w"] == 4
    assert refused["query"] is None
    assert refused["title"] is None
    assert refused["kind"] is None
    assert granted["id"] == "loans"
    assert granted["query"]["measures"] == [LOANS]

    body = viewer_view.text
    assert SECRET_TITLE not in body
    assert DEPOSITS not in body
    assert OBLIGOR not in body
    # The count is the only thing said about the refusal, and the message is
    # production copy rather than an enumeration.
    assert "does not cover some of the figures" in payload["message"]


def test_the_refused_members_reach_the_log_and_not_the_reader(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Where an operator sees which grant is missing: the append-only query log."""

    _ = plane
    created = _create(db_client, db_session, visibility="org")
    db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, VIEWER)
    )

    row = db_session.scalars(
        select(BiQueryLog)
        .where(BiQueryLog.principal_user_id == VIEWER, BiQueryLog.bank_id == BANK_ID)
        .order_by(BiQueryLog.queried_at.desc())
    ).first()
    assert row is not None
    assert row.decision == "allowed"
    assert DEPOSITS in row.denied_members
    assert LOANS in row.member_ids


# --- the four visibility rules -----------------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "role", "reachable"),
    [
        ("private", None, False),
        ("users", None, True),
        ("role", RoleBundle.VIEWER.value, True),
        ("org", None, True),
    ],
)
def test_each_visibility_rule_decides_who_may_open_it(  # noqa: PLR0913 - one case per rule
    db_client: TestClient,
    db_session: Session,
    plane: Bank,
    bi_on: None,
    visibility: str,
    role: str | None,
    reachable: bool,
) -> None:
    """Four rules, four answers, for the same second identity.

    ``VIEWER`` holds an organization-wide ``viewer`` binding over this
    institution, so the ``role`` case reaches them and the ``private`` case does
    not; the ``users`` case reaches them only because they are named.
    """

    _ = plane
    created = _create(db_client, db_session, visibility=visibility, role=role)
    if visibility == "users":
        db_client.put(
            f"{BASE}/dashboards/{created['id']}/shares",
            json={"user_ids": [str(VIEWER)]},
            headers=_headers(db_session, USER_1),
        )

    response = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, VIEWER)
    )
    listed = db_client.get(f"{BASE}/dashboards", headers=_headers(db_session, VIEWER))

    assert response.status_code == (200 if reachable else 404)
    ids = {dashboard["id"] for dashboard in listed.json()["dashboards"]}
    assert (created["id"] in ids) is reachable


def test_role_sharing_does_not_reach_a_holder_of_another_role(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create(db_client, db_session, visibility="role", role=RoleBundle.ANALYST.value)

    response = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, VIEWER)
    )

    assert response.status_code == 404


def test_organization_sharing_does_not_cross_a_tenant(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """``org`` is the tenant. The other tenant's identity cannot even name the bank."""

    _ = plane
    created = _create(db_client, db_session, visibility="org")

    same_path = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}",
        headers=_headers(db_session, USER_2, organization_id=ORG_2),
    )
    own_bank = db_client.get(
        f"/api/v1/banks/{OTHER_TENANT_BANK}/bi/dashboards/{created['id']}?as_of={AS_OF}",
        headers=_headers(db_session, USER_2, organization_id=ORG_2),
    )

    assert same_path.status_code == 404
    assert same_path.json()["error"]["message"] == "Bank not found."
    assert own_bank.status_code == 404


def test_a_sibling_institution_of_the_same_tenant_does_not_hold_it(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Two banks of one organization share an RLS tenant, so the by-id lookup is
    bank-scoped as well — organization scoping alone cannot isolate their children."""

    _ = plane
    created = _create(db_client, db_session, visibility="org")

    response = db_client.get(
        f"/api/v1/banks/{SIBLING_BANK_ID}/bi/dashboards/{created['id']}?as_of={AS_OF}",
        headers=_headers(db_session, USER_1),
    )

    assert response.status_code == 404


# --- ownership ---------------------------------------------------------------------------


def test_an_org_owner_may_not_edit_or_delete_someone_elses_dashboard(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Only the owner. Not an Org Owner — the identity a shortcut is written for.

    The Org Owner can OPEN it (it is shared organization-wide) and is refused with
    403 rather than 404, because they have already been shown that it exists.
    """

    _ = plane
    created = _create(db_client, db_session, visibility="org")
    owner_headers = _headers(db_session, OWNER_ROLE_USER)

    opened = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=owner_headers
    )
    edited = db_client.put(
        f"{BASE}/dashboards/{created['id']}",
        json={
            "title": "Renamed by the Org Owner",
            "visibility": "org",
            "spec": _spec(_widget("loans", "Loan book", LOANS)),
        },
        headers=owner_headers,
    )
    deleted = db_client.delete(f"{BASE}/dashboards/{created['id']}", headers=owner_headers)
    shared = db_client.put(
        f"{BASE}/dashboards/{created['id']}/shares",
        json={"user_ids": [str(OWNER_ROLE_USER)]},
        headers=owner_headers,
    )

    assert opened.status_code == 200
    assert edited.status_code == 403
    assert edited.json()["error"]["details"]["error_code"] == "bi_content_owner_only"
    assert deleted.status_code == 403
    assert shared.status_code == 403


def test_the_owner_may_edit_and_every_save_appends_a_version(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create(db_client, db_session, visibility="private")

    edited = db_client.put(
        f"{BASE}/dashboards/{created['id']}",
        json={
            "title": "Lending only",
            "visibility": "private",
            "spec": _spec(_widget("loans", "Loan book", LOANS)),
            "change_note": "Dropped the funding tile",
        },
        headers=_headers(db_session, USER_1),
    )
    history = db_client.get(
        f"{BASE}/dashboards/{created['id']}/versions", headers=_headers(db_session, USER_1)
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    versions = history.json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert versions[0]["current"] is True
    assert versions[0]["change_note"] == "Dropped the funding tile"
    assert versions[1]["title"] == "Funding and lending"
    assert versions[1]["widget_count"] == 2


def test_deleting_a_dashboard_removes_it_and_is_audited(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create(db_client, db_session, visibility="private")

    deleted = db_client.delete(
        f"{BASE}/dashboards/{created['id']}", headers=_headers(db_session, USER_1)
    )
    gone = db_client.get(
        f"{BASE}/dashboards/{created['id']}?as_of={AS_OF}", headers=_headers(db_session, USER_1)
    )

    assert deleted.status_code == 204
    assert gone.status_code == 404
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == created["id"])
    ).all()
    assert {event.event_type for event in events} == {
        "bi.dashboard.created",
        "bi.dashboard.deleted",
    }


def test_the_share_list_is_the_owners_alone(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Telling one reader who else can open a document is not part of opening it."""

    _ = plane
    created = _create(db_client, db_session, visibility="users")
    db_client.put(
        f"{BASE}/dashboards/{created['id']}/shares",
        json={"user_ids": [str(VIEWER)]},
        headers=_headers(db_session, USER_1),
    )

    owner_list = db_client.get(
        f"{BASE}/dashboards/{created['id']}/shares", headers=_headers(db_session, USER_1)
    )
    viewer_list = db_client.get(
        f"{BASE}/dashboards/{created['id']}/shares", headers=_headers(db_session, VIEWER)
    )

    assert owner_list.status_code == 200
    assert [share["user_id"] for share in owner_list.json()["shares"]] == [str(VIEWER)]
    assert viewer_list.status_code == 403


def test_an_author_cannot_save_a_question_they_may_not_ask(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Deny-by-default applies to the save, in the read surface's own envelope."""

    _ = plane
    response = db_client.post(
        f"{BASE}/dashboards",
        json={"title": "Funding", "visibility": "private", "spec": MIXED_SPEC},
        headers=_headers(db_session, VIEWER),
    )

    assert response.status_code == 403
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "bi_authorization_denied"
    assert DEPOSITS in detail["denied_members"]
    assert detail["denied_member_labels"]


def test_a_widget_the_query_engine_would_refuse_is_refused_at_save(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.post(
        f"{BASE}/dashboards",
        json={
            "title": "Broken",
            "visibility": "private",
            "spec": _spec(_widget("wrong", "Loans by event", LOANS, dimensions=("event.type",))),
        },
        headers=_headers(db_session, USER_1),
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["error_code"] == "bi_dashboard_widget_refused"


def test_a_copy_of_a_certified_pack_is_personal(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A curated pack is never edited in place: an edit produces a copy."""

    _ = plane
    from app.domain.bi.packs import pack_ids  # noqa: PLC0415 - fixture data

    response = db_client.post(
        f"{BASE}/dashboards",
        json={"title": "My board view", "visibility": "private", "from_pack": pack_ids()[0]},
        headers=_headers(db_session, USER_1),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["badge"] == "personal"
    assert payload["source_pack"] == pack_ids()[0]
    assert payload["widget_count"] > 0


def test_an_unknown_pack_is_not_found(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.post(
        f"{BASE}/dashboards",
        json={"title": "Copy", "visibility": "private", "from_pack": "not_a_pack"},
        headers=_headers(db_session, USER_1),
    )

    assert response.status_code == 404


# --- calculated measures -----------------------------------------------------------------


def _create_measure(
    client: TestClient,
    db: Session,
    *,
    owner: UUID = USER_1,
    key: str = "custom.loans_to_deposits",
    expression: str = f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/measures",
        json={
            "measure_key": key,
            "label": "Loans to deposits",
            "expression": expression,
            "value_type": "fraction",
            "favourable_direction": "neutral",
        },
        headers=_headers(db, owner),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_formula_is_validated_on_the_server(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The verdict, the position and the figures all come from the server's parse."""

    _ = plane
    good = db_client.post(
        f"{BASE}/measures/validation",
        json={"expression": f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])"},
        headers=_headers(db_session, USER_1),
    )
    malformed = db_client.post(
        f"{BASE}/measures/validation",
        json={"expression": f"[m:{LOANS}] + <script>"},
        headers=_headers(db_session, USER_1),
    )
    refused = db_client.post(
        f"{BASE}/measures/validation",
        json={"expression": f"[m:{DEPOSITS}] * 2"},
        headers=_headers(db_session, VIEWER),
    )

    assert good.json() == {
        "valid": True,
        "message": "This formula is valid.",
        "position": None,
        "referenced_members": [LOANS, DEPOSITS],
        "referenced_member_labels": good.json()["referenced_member_labels"],
        "denied_members": [],
    }
    assert malformed.json()["valid"] is False
    assert malformed.json()["position"] is not None
    assert "script" not in malformed.text
    assert refused.json()["valid"] is False
    assert refused.json()["denied_members"] == [DEPOSITS]


def test_a_measure_may_not_name_a_figure_its_author_cannot_read(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.post(
        f"{BASE}/measures",
        json={
            "measure_key": "custom.sneaky",
            "label": "Sneaky",
            "expression": f"[m:{DEPOSITS}] * 2",
            "value_type": "amount",
        },
        headers=_headers(db_session, VIEWER),
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"]["denied_members"] == [DEPOSITS]


def test_a_measures_state_is_the_servers_and_a_client_cannot_send_one(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The request model is closed: a state, a module or a member list is refused."""

    _ = plane
    response = db_client.post(
        f"{BASE}/measures",
        json={
            "measure_key": "custom.claimed",
            "label": "Claimed",
            "expression": f"[m:{LOANS}] * 2",
            "value_type": "amount",
            "state": "bank_certified",
            "referenced_members": [LOANS],
        },
        headers=_headers(db_session, USER_1),
    )

    assert response.status_code == 422


def test_the_measure_list_says_a_certified_measure_can_be_charted(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The copy tracks the capability, and the capability now exists.

    This asserted the opposite until the authorization walk learned to expand a
    calculated measure into the figures its text names. Until then a calculated id
    was refused as unknown BEFORE a query was compiled, so the compiler's arm was
    real and unreachable, and a surface offering the field would have been a worse
    lie than one saying "not yet". The route-level proof that it is now reachable
    is ``test_a_certified_measure_can_be_charted_through_the_query_route``; this
    test exists so the copy cannot drift back to a claim that is no longer true.
    """

    _ = plane
    _create_measure(db_client, db_session)

    listed = db_client.get(f"{BASE}/measures", headers=_headers(db_session, USER_1))

    payload = listed.json()
    assert payload["available_in_queries"] is True
    assert "cannot yet" not in payload["message"]
    assert [measure["measure_key"] for measure in payload["measures"]] == [
        "custom.loans_to_deposits"
    ]
    assert payload["measures"][0]["badge"] == "personal"


def test_a_certified_measure_is_accepted_on_a_saved_dashboard(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Audit A9-04. The canvas rule must admit what the institution certified.

    `check_canvas_shape`'s `certified_measures` defaults to EMPTY so a caller that
    forgets it refuses every calculated measure rather than admitting one
    unchecked. That default is right; omitting it at the only production call site
    was not. The effect was a bank-certified measure refused on every saved canvas,
    with production copy telling the author to certify what was already certified.

    Both directions, because admitting everything would also pass the first half:
    a CERTIFIED formula is accepted, and a PERSONAL one is still refused — a saved
    dashboard is a document other people open, and a personal formula on one turns
    a share into a way to make someone else compute the owner's arithmetic under
    the owner's label.
    """

    _grant(
        db_session,
        OWNER_ROLE_USER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    _create_measure(db_client, db_session, key="custom.personal_only")
    certified = _create_measure(db_client, db_session, key="custom.certified_one")
    db_client.post(
        f"{BASE}/measures/{certified['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    decided = db_client.post(
        f"{BASE}/measures/{certified['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed against the register",
            "expression_digest": _digest(db_client, db_session, certified["id"]),
        },
        headers=_headers(db_session, OWNER_ROLE_USER),
    )
    assert decided.status_code == 200, decided.text

    def _canvas(measure_key: str) -> dict[str, Any]:
        return {
            "title": f"Canvas for {measure_key}",
            "description": "A9-04",
            "visibility": "private",
            "spec": _spec(_widget("w1", "A figure", measure_key)),
        }

    accepted = db_client.post(
        f"{BASE}/dashboards",
        json=_canvas("custom.certified_one"),
        headers=_headers(db_session, USER_1),
    )
    assert accepted.status_code == 201, accepted.text

    refused = db_client.post(
        f"{BASE}/dashboards",
        json=_canvas("custom.personal_only"),
        headers=_headers(db_session, USER_1),
    )
    assert refused.status_code == 422, refused.text
    assert "bi_dashboard_widget_refused" in refused.text


def test_a_certified_measure_can_be_charted_through_the_query_route(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The reachability proof: a certified formula answers through `POST /bi/query`.

    This is the acceptance test for the whole calculated-measure feature. The
    compiler could evaluate a formula for some time while no read route could
    reach that arm, because the authorization walk resolved every measure id
    against the static catalogue and refused a calculated one as unknown before a
    query was compiled. A unit test of the compiler cannot see that: the refusal
    happened a layer above it. So this goes through the route.
    """

    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    decided = db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed against the register",
            "expression_digest": _digest(db_client, db_session, created["id"]),
        },
        headers=_headers(db_session, VIEWER),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["measure"]["state"] == "bank_certified"

    answered = db_client.post(
        f"{BASE}/query",
        json={
            "measures": ["custom.loans_to_deposits"],
            "dimensions": [],
            "time": {"as_of": AS_OF},
        },
        headers=_headers(db_session, USER_1),
    )

    assert answered.status_code == 200, answered.text
    body = answered.json()
    ids = [column["id"] for column in body["columns"]]
    assert "custom.loans_to_deposits" in ids, ids
    # A value or an honest absence, never a fabricated zero. The fixture's book
    # decides which; what must never happen is a 422 for an unknown member.
    assert body["rows"], body


def test_charting_a_formula_grants_a_reader_nothing_they_did_not_hold(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The other half, and the one that matters more.

    A formula is authorized as the FIGURES ITS TEXT NAMES, expanded from the
    server's own parse of the approved expression. If it were authorized as itself,
    a certified measure would be a way to reach a figure through a name nobody
    evaluated.

    The approver has to be broad to approve, so the narrow reader is a third
    identity: ``VIEWER``, which the neighbouring test already establishes cannot
    even see this measure in its list.
    """

    _grant(
        db_session,
        OWNER_ROLE_USER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    decided = db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed against the register",
            "expression_digest": _digest(db_client, db_session, created["id"]),
        },
        headers=_headers(db_session, OWNER_ROLE_USER),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["measure"]["state"] == "bank_certified"

    refused = db_client.post(
        f"{BASE}/query",
        json={
            "measures": ["custom.loans_to_deposits"],
            "dimensions": [],
            "time": {"as_of": AS_OF},
        },
        headers=_headers(db_session, VIEWER),
    )

    assert refused.status_code == 403, refused.text
    body = refused.text
    # The refusal must name a FIGURE the reader could not read, so an operator
    # knows what to grant. Naming only the formula would tell them nothing.
    assert LOANS in body or DEPOSITS in body, body


def test_a_measure_a_reader_cannot_compute_is_absent_from_their_list(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create_measure(db_client, db_session)

    listed = db_client.get(f"{BASE}/measures", headers=_headers(db_session, VIEWER))
    fetched = db_client.get(
        f"{BASE}/measures/{created['id']}", headers=_headers(db_session, VIEWER)
    )

    assert listed.json()["measures"] == []
    assert fetched.status_code == 404
    assert DEPOSITS not in listed.text


def test_the_proposer_cannot_certify_their_own_measure(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Maker-checker, refused with the separation-of-duties verdict attached."""

    _ = plane
    created = _create_measure(db_client, db_session)
    proposed = db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    assert proposed.status_code == 200, proposed.text

    decided = db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Looks right to me",
            "expression_digest": proposed.json()["expression"]
            and _digest(db_client, db_session, created["id"]),
        },
        headers=_headers(db_session, USER_1),
    )

    assert decided.status_code == 409
    detail = decided.json()["error"]["details"]
    assert detail["error_code"] == "bi_measure_promotion_refused"
    assert detail["sod_decision"]["outcome"] == "block"
    assert detail["sod_decision"]["findings"]


def _digest(client: TestClient, db: Session, measure_id: str) -> str:
    """The stored formula's digest, as a checker would have read it."""

    from app.services.bi import content  # noqa: PLC0415 - one value, read the same way

    fetched = client.get(f"{BASE}/measures/{measure_id}", headers=_headers(db, USER_1))
    return content.expression_digest(fetched.json()["expression"])


def test_a_second_person_with_approval_authority_certifies_it(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """And the approved formula is frozen: the response carries the text approved."""

    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )

    decided = db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed against the register",
            "expression_digest": _digest(db_client, db_session, created["id"]),
        },
        headers=_headers(db_session, VIEWER),
    )

    assert decided.status_code == 200, decided.text
    payload = decided.json()
    assert payload["sod_decision"]["outcome"] == "allow"
    measure = payload["measure"]
    assert measure["state"] == "bank_certified"
    assert measure["badge"] == "bank_certified"
    assert measure["approved_expression"] == measure["expression"]
    assert measure["approved_by_user_id"] == str(VIEWER)
    assert measure["proposed_by_user_id"] == str(USER_1)

    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == created["id"])
    ).all()
    assert "bi.measure.certified" in {event.event_type for event in events}


def test_a_certified_measure_is_the_institutions_and_an_edit_drops_it(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """An approver approved a formula, not a name."""

    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed against the register",
            "expression_digest": _digest(db_client, db_session, created["id"]),
        },
        headers=_headers(db_session, VIEWER),
    )

    # Certified, so the second identity now reads it as part of the institution's
    # vocabulary rather than as someone else's draft.
    listed = db_client.get(f"{BASE}/measures", headers=_headers(db_session, VIEWER))
    assert [measure["state"] for measure in listed.json()["measures"]] == ["bank_certified"]

    edited = db_client.put(
        f"{BASE}/measures/{created['id']}",
        json={
            "label": "Deposits to loans",
            "expression": f"SAFE_DIV([m:{DEPOSITS}], [m:{LOANS}])",
            "value_type": "fraction",
        },
        headers=_headers(db_session, USER_1),
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["state"] == "personal"
    assert edited.json()["approved_expression"] is None


def test_a_decision_against_a_moved_formula_is_refused(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    db_session.commit()
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )

    decided = db_client.post(
        f"{BASE}/measures/{created['id']}/decision",
        json={
            "decision": "approve",
            "reason": "Reviewed",
            "expression_digest": "0" * 64,
        },
        headers=_headers(db_session, VIEWER),
    )

    assert decided.status_code == 409
    assert decided.json()["error"]["details"]["error_code"] == "bi_measure_expression_moved"


def test_only_the_owner_may_edit_or_delete_a_measure(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create_measure(db_client, db_session)
    db_client.post(
        f"{BASE}/measures/{created['id']}/proposal",
        json={"reason": "For the board pack"},
        headers=_headers(db_session, USER_1),
    )
    owner_headers = _headers(db_session, OWNER_ROLE_USER)

    edited = db_client.put(
        f"{BASE}/measures/{created['id']}",
        json={
            "label": "Mine now",
            "expression": f"[m:{LOANS}] * 2",
            "value_type": "amount",
        },
        headers=owner_headers,
    )
    deleted = db_client.delete(f"{BASE}/measures/{created['id']}", headers=owner_headers)

    assert edited.status_code == 403
    assert deleted.status_code == 403


# --- the deployment flag -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/dashboards", None),
        ("POST", "/dashboards", {"title": "x", "visibility": "private", "spec": MIXED_SPEC}),
        ("GET", "/dashboards/11111111-1111-4111-8111-111111111111", None),
        ("GET", "/measures", None),
        (
            "POST",
            "/measures",
            {
                "measure_key": "custom.x",
                "label": "x",
                "expression": f"[m:{LOANS}] * 2",
                "value_type": "amount",
            },
        ),
        ("POST", "/measures/validation", {"expression": f"[m:{LOANS}] * 2"}),
    ],
)
def test_without_the_deployment_flag_every_path_is_absent(  # noqa: PLR0913 - one case per path
    db_client: TestClient,
    db_session: Session,
    plane: Bank,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """No ``bi_on`` fixture: the feature is not forbidden, it is not there."""

    _ = plane
    response = db_client.request(
        method,
        f"{BASE}{path}?as_of={AS_OF}",
        content=None if body is None else json.dumps(body),
        headers={**_headers(db_session, USER_1), "Content-Type": "application/json"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Not found."


# --- the guard sweep ---------------------------------------------------------------------


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


def test_every_content_mutation_is_credited_as_guarded(db_client: TestClient) -> None:
    """D-027: the route-table sweeps classify by dependency NAME.

    A POST or PUT that resolved only ``Tenant`` is what
    ``tests/api/test_impersonation_boundary.py`` convicts, so every unsafe route
    of this surface has to name ``require_bi_read`` (the interactive-human
    admission) and ``resolve_tenant_bank`` (the cross-tenant 404). Asserted over
    the live registry rather than a count, so a route added here cannot escape it.
    """

    assert "require_bi_read" in MUTATION_ROLE_DEPENDENCY_NAMES
    app = db_client.app
    assert isinstance(app, FastAPI)
    unsafe = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and ("/bi/dashboards" in route.path or "/bi/measures" in route.path)
        and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(unsafe) == 10, [route.path for route in unsafe]
    for route in unsafe:
        names = _dependency_names(route)
        assert "require_bi_read" in names, route.path
        assert "resolve_tenant_bank" in names, route.path
        assert "require_bi_enabled" in names, route.path


def test_a_dashboard_cannot_be_shared_with_a_machine_only_role(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A share whose audience no human can join reaches nobody and says it worked.

    ``_validate_visibility`` admitted every ``RoleBundle`` value, which quietly
    included the machine bundles. Sharing with ``integration_writer`` — and, once
    the Power BI feed landed, ``bi_reader`` — therefore succeeded while the
    audience was necessarily empty: no human can hold either, and a machine
    principal cannot reach a dashboard route at all. The owner was told the share
    worked, which is the same shape as a grant that lies, and the refusal message
    already claimed to be about what "this organization can hold".

    Both directions are asserted, because refusing the machine bundles is only
    correct if the human ones still work. Every human bundle is walked rather
    than a sample, so a bundle added later is covered the day it lands.
    """

    assert MACHINE_ROLE_BUNDLES, "there are no machine bundles, so this proves nothing"

    for bundle in MACHINE_ROLE_BUNDLES:
        refused = db_client.post(
            f"{BASE}/dashboards",
            json={
                "title": f"Shared with {bundle.value}",
                "visibility": "role",
                "visibility_role": bundle.value,
                "spec": MIXED_SPEC,
            },
            headers=_headers(db_session, USER_1),
        )
        # 409 ``bi_dashboard_refused``: ``BiContentError`` is how this module
        # states a refusal, and the share is a conflict with what roles exist
        # rather than a malformed request body.
        assert refused.status_code == 409, (bundle.value, refused.text)
        assert refused.json()["error"]["details"]["error_code"] == "bi_dashboard_refused"

        # The message must not name the bundle: a caller learning that
        # ``bi_reader`` exists learns that a machine read surface exists.
        assert bundle.value not in refused.text, refused.text

    # The positive half. Without it, a change that refused EVERY role would pass.
    human_bundles = [
        bundle for bundle in RoleBundle if principal_bundle_compatible(PrincipalType.HUMAN, bundle)
    ]
    assert len(human_bundles) >= 5, human_bundles
    for bundle in human_bundles:
        created = _create(
            db_client,
            db_session,
            visibility="role",
            role=bundle.value,
            title=f"Shared with {bundle.value}",
        )
        assert created["visibility_role"] == bundle.value, created
