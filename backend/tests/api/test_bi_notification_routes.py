"""The threshold-alert and scheduled-report routes, attacked rather than exercised.

Four properties this surface exists to hold, and each is a test that fails loudly
if it stops holding:

* **reachability decides before ownership.** An identity with no relationship to
  an alert gets ``404`` for its id — the object is not enumerable — while an
  identity the alert is addressed to gets ``403`` for the same mutation. Two
  different answers to the same request are what prove the order.
* **owner-only mutation.** Proven against an Org Owner and an account
  administrator, the two identities a shortcut would most plausibly be written
  for, and against a named recipient, who can reach the object and still may not
  change it.
* **creating a subscription confers nothing.** The stored row is checked COLUMN BY
  COLUMN against the set of things a subscription is allowed to hold, so a future
  change that cached the author's decision on the row fails here rather than
  three months later in a delivery.
* **a distribution list is the owner's to see.** A recipient reading a report they
  receive is checked against the serialised body for the other recipients'
  addresses.

Plus: a refused delivery reads as an outcome and not as an error; a verdict needs
the CALLER's own access, including when the caller is the owner; and with
``BI_ENABLED`` unset every one of these paths answers 404 — the feature is not
forbidden, it is not there.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.features import manage_bi_notifications as feature
from app.identity.service import authorization
from app.models import AuditEvent, AuthorizationBinding, Bank, Organization, User
from app.models.bi_notifications import (
    BiAlert,
    BiAlertEvent,
    BiSubscription,
    BiSubscriptionDelivery,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2, headers

BANK_ID = "BK-BINOTI01"
SIBLING_BANK_ID = "BK-BINOTI02"
OTHER_TENANT_BANK = "BK-BINOTI03"
BASE = f"/api/v1/banks/{BANK_ID}/bi"
SIBLING_BASE = f"/api/v1/banks/{SIBLING_BANK_ID}/bi"

#: Credit aggregates — the figure the narrow reader holds.
LOANS = "loans.balance_rc"
#: A liquidity aggregate the narrow reader does NOT hold.
DEPOSITS = "deposits.balance_rc"
#: A capital ratio that declares a governed threshold source, so a governed-limit
#: alert is buildable on it and not on ``LOANS``.
CAR = "engine.car_pct.crd.live"
#: A restricted field: any question naming it is record level, so a subscription
#: over it is never attached.
OBLIGOR = "counterparty.name"

#: Further identities of ORG_1: the person a report is addressed to, an Org Owner
#: who owns nothing here, and an account administrator.
RECIPIENT = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SECOND_RECIPIENT = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OWNER_ROLE_USER = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ACCOUNT_ADMIN = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
STRANGER = UUID("aaaaaaa1-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


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
        name=f"BI notifications {bank_id}",
        short_name="BI notify",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _user(db: Session, user_id: UUID, organization_id: str, *, email: str | None = None) -> User:
    existing = db.get(User, user_id)
    if existing is not None:
        return existing
    user = User(
        id=user_id,
        organization_id=organization_id,
        email=email or f"{user_id}@example.test",
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
        reason="Exercise the BI notification routes.",
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

    ``USER_1`` reads everything and authors every object here. ``RECIPIENT`` and
    ``SECOND_RECIPIENT`` read credit aggregates only. ``OWNER_ROLE_USER`` is an Org
    Owner who can read everything, and ``ACCOUNT_ADMIN`` administers the account —
    the two identities an ownership shortcut would be written for. ``STRANGER``
    reads everything and is on no list, which is what makes their 404 about
    reachability rather than about authority.
    """

    bank = _bank(db_session, BANK_ID, ORG_1)
    _bank(db_session, SIBLING_BANK_ID, ORG_1)
    if db_session.get(Organization, ORG_2) is None:
        db_session.add(Organization(id=ORG_2, name="Other tenant"))
        db_session.flush()
    _bank(db_session, OTHER_TENANT_BANK, ORG_2)
    # ``USER_1`` is seeded by the shared tenant fixture, so its address is
    # whatever that fixture chose; every address these tests NAME belongs to an
    # identity created here.
    _user(db_session, USER_1, ORG_1, email="author@example.test")
    _user(db_session, RECIPIENT, ORG_1, email="treasurer@example.test")
    _user(db_session, SECOND_RECIPIENT, ORG_1, email="cfo@example.test")
    _user(db_session, OWNER_ROLE_USER, ORG_1, email="owner@example.test")
    _user(db_session, ACCOUNT_ADMIN, ORG_1, email="itadmin@example.test")
    _user(db_session, STRANGER, ORG_1, email="stranger@example.test")
    _user(db_session, USER_2, ORG_2, email="foreign@example.test")
    # The hermetic fixture hands every identity an organization-wide all/all
    # sentence. These tests are about what a NARROW reader sees, so this tenant's
    # baseline goes and each grant below is stated. The other tenant's baseline is
    # left alone: its identity is here to be refused at the institution.
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.flush()
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    # The Analyst bundle is what carries ``export``, and a record-level report
    # needs it: nothing is attached, but the author is held to the same sentence
    # the delivery path applies per recipient. ``STRANGER`` deliberately does NOT
    # get it, which is how the refusal below is proven.
    _grant(
        db_session,
        USER_1,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.ANALYST,
    )
    _grant(db_session, STRANGER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    for narrow in (RECIPIENT, SECOND_RECIPIENT):
        _grant(
            db_session, narrow, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED
        )
    # Ownership is two sentences: the Owner bundle administers and carries no
    # ``view``. Both are granted so the ownership tests refuse an identity that CAN
    # read — which is the case a shortcut would be written for.
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
    _grant(
        db_session,
        ACCOUNT_ADMIN,
        module=ModuleScope.ACCOUNT,
        sensitivity=SensitivityScope.RESTRICTED,
        bundle=RoleBundle.ACCOUNT_ADMIN,
    )
    _grant(db_session, ACCOUNT_ADMIN, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    db_session.commit()
    return bank


def _headers(db: Session, user_id: UUID, *, organization_id: str = ORG_1) -> dict[str, str]:
    return headers(
        org_id=organization_id, user_id=user_id, authorization_version=_authv(db, user_id)
    )


# --- request bodies ----------------------------------------------------------------------


def _alert_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Loan book above plan",
        "measure_id": LOANS,
        "direction": "above",
        "threshold_basis": "stated",
        "threshold": "1000000",
        "notify_emails": ["treasurer@example.test"],
        "reason": "The ALCO asked to be told when the book passes the plan.",
    }
    body.update(overrides)
    return body


def _query(*measures: str, dimensions: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "measures": list(measures),
        "dimensions": list(dimensions),
        "time": {"as_of": "2026-08-31"},
    }


def _subscription_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Monday lending pack",
        "query": _query(LOANS),
        "artifact_format": "csv",
        "cadence": "weekly",
        "hour": 7,
        "minute": 30,
        "day_of_week": 1,
        "recipient_emails": ["treasurer@example.test"],
        "reason": "The weekly lending pack the ALCO reads on Monday morning.",
    }
    body.update(overrides)
    return body


def _create_alert(
    client: TestClient, db: Session, *, owner: UUID = USER_1, **overrides: Any
) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/alerts", json=_alert_body(**overrides), headers=_headers(db, owner)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_subscription(
    client: TestClient, db: Session, *, owner: UUID = USER_1, **overrides: Any
) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/subscriptions", json=_subscription_body(**overrides), headers=_headers(db, owner)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- reachability, then ownership --------------------------------------------------------


def test_reachability_decides_before_ownership(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The same request, two identities, two different refusals — in that order.

    A named recipient can SEE the alert, so their attempt to change it is
    ``403``: reachable, not theirs. An identity with no relationship to it gets
    ``404`` for the very same path, because for them the alert does not exist. If
    ownership were consulted first, both would be 403 and the alert's existence
    would be enumerable by anyone in the tenant.
    """

    _ = plane
    created = _create_alert(db_client, db_session)
    path = f"{BASE}/alerts/{created['id']}"
    body = _alert_body(name="Renamed by somebody else")

    reachable = db_client.put(path, json=body, headers=_headers(db_session, RECIPIENT))
    assert reachable.status_code == 403, reachable.text
    assert reachable.json()["error"]["details"]["error_code"] == "bi_notification_owner_only"

    unrelated = db_client.put(path, json=body, headers=_headers(db_session, STRANGER))
    assert unrelated.status_code == 404, unrelated.text
    assert unrelated.json()["error"]["details"]["error_code"] == "bi_alert_not_found"

    # And the read is the same shape: reachable for the recipient, absent for the
    # stranger, who reads everything this institution publishes.
    assert db_client.get(path, headers=_headers(db_session, RECIPIENT)).status_code == 200
    assert db_client.get(path, headers=_headers(db_session, STRANGER)).status_code == 404


def test_only_the_owner_may_change_an_alert(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Not an Org Owner, not an account administrator, not a recipient.

    All three can read the institution's figures; none of them authored this
    alert. The stranger is checked too, and answers 404 rather than 403, which is
    the reachability rule holding under a mutation.
    """

    _ = plane
    created = _create_alert(db_client, db_session)
    path = f"{BASE}/alerts/{created['id']}"
    for actor, expected in (
        (OWNER_ROLE_USER, 404),
        (ACCOUNT_ADMIN, 404),
        (RECIPIENT, 403),
        (STRANGER, 404),
    ):
        stop = db_client.post(
            f"{path}/deactivation",
            json={"reason": "Trying to stop somebody else's alert."},
            headers=_headers(db_session, actor),
        )
        assert stop.status_code == expected, f"{actor}: {stop.text}"
        removed = db_client.delete(path, headers=_headers(db_session, actor))
        assert removed.status_code == expected, f"{actor}: {removed.text}"
    # Nothing moved.
    alert = db_session.get(BiAlert, UUID(created["id"]))
    assert alert is not None
    assert alert.is_active is True


def test_only_the_owner_may_change_a_scheduled_report(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The same rule on the surface that makes the platform mail people."""

    _ = plane
    created = _create_subscription(db_client, db_session)
    path = f"{BASE}/subscriptions/{created['id']}"
    for actor, expected in ((OWNER_ROLE_USER, 404), (ACCOUNT_ADMIN, 404), (RECIPIENT, 403)):
        response = db_client.put(
            path,
            json=_subscription_body(recipient_emails=["itadmin@example.test"]),
            headers=_headers(db_session, actor),
        )
        assert response.status_code == expected, f"{actor}: {response.text}"
    subscription = db_session.get(BiSubscription, UUID(created["id"]))
    assert subscription is not None
    assert subscription.recipient_user_ids == [str(RECIPIENT)]


def test_a_sibling_institutions_alert_is_not_reachable_under_this_bank(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Two banks of one organization share an RLS tenant, so the query is scoped
    to the institution as well — the by-id rule in AGENTS.md."""

    _ = plane
    created = _create_alert(db_client, db_session)
    response = db_client.get(
        f"{SIBLING_BASE}/alerts/{created['id']}", headers=_headers(db_session, USER_1)
    )
    assert response.status_code == 404, response.text
    listed = db_client.get(f"{SIBLING_BASE}/alerts", headers=_headers(db_session, USER_1))
    assert listed.status_code == 200, listed.text
    assert listed.json()["alerts"] == []


def test_a_foreign_tenants_bank_is_refused_at_the_institution(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.get(
        f"/api/v1/banks/{OTHER_TENANT_BANK}/bi/alerts", headers=_headers(db_session, USER_1)
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Bank not found."


# --- the stored definition carries no authority ------------------------------------------

#: Everything a subscription row is allowed to hold. Anything else on the model
#: is a new column somebody added, and the test below makes them say what it is.
_SUBSCRIPTION_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "organization_id",
        "bank_id",
        "name",
        "owner_user_id",
        "query",
        "artifact_format",
        "cadence",
        "hour",
        "minute",
        "day_of_week",
        "day_of_month",
        "recipient_user_ids",
        "is_active",
        "created_at",
        "updated_at",
    }
)


def test_a_stored_subscription_carries_no_resolved_authority(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The row holds a question and a distribution list. Nothing about access.

    The author IS authorized at creation — a principal may not instruct the
    platform to send a question they cannot ask — and that decision must leave no
    trace, because every delivery is authorized and rendered as its own recipient
    at send time. So the row is checked two ways: its column set is exactly the
    list above, and no cell anywhere in it contains the author's binding ids or
    their authorization version.
    """

    _ = plane
    author_bindings = list(
        db_session.scalars(
            select(AuthorizationBinding.id).where(
                AuthorizationBinding.organization_id == ORG_1,
                AuthorizationBinding.principal_user_id == USER_1,
            )
        )
    )
    assert author_bindings, "the author must hold a binding, or this proves nothing"
    created = _create_subscription(db_client, db_session)
    row = db_session.get(BiSubscription, UUID(created["id"]))
    assert row is not None

    columns = {column.name for column in BiSubscription.__table__.columns}
    assert columns == _SUBSCRIPTION_COLUMNS, sorted(columns ^ _SUBSCRIPTION_COLUMNS)

    serialised = repr({name: getattr(row, name) for name in sorted(columns)})
    for binding_id in author_bindings:
        assert str(binding_id) not in serialised
    for forbidden in ("authorization_version", "permission", "binding", "authv", "grant"):
        assert forbidden not in serialised
    # The only identity on the row is the owner and the people it is addressed to.
    assert row.owner_user_id == USER_1
    assert row.recipient_user_ids == [str(RECIPIENT)]


def test_an_alert_row_carries_no_resolved_authority_either(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Same property on the alert: a threshold, a question, and who to tell."""

    _ = plane
    created = _create_alert(db_client, db_session)
    row = db_session.get(BiAlert, UUID(created["id"]))
    assert row is not None
    columns = {column.name for column in BiAlert.__table__.columns}
    expected = {
        "id",
        "organization_id",
        "bank_id",
        "name",
        "measure_id",
        "filters",
        "direction",
        "threshold_basis",
        "threshold",
        "owner_user_id",
        "notify_user_ids",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert columns == expected, sorted(columns ^ expected)
    serialised = repr({name: getattr(row, name) for name in sorted(columns)})
    for forbidden in ("authorization_version", "permission", "binding", "authv"):
        assert forbidden not in serialised


# --- recipients --------------------------------------------------------------------------


def test_a_recipient_must_be_an_active_identity_of_this_tenant(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A foreign tenant's user, an address nobody holds, and an unknown id.

    Each refuses the WHOLE request rather than being dropped: a report that
    quietly delivers to fewer people than its author believes is worse than one
    that refused to be saved.
    """

    _ = plane
    cases: list[tuple[str, dict[str, Any]]] = [
        ("a foreign tenant's user id", {"recipient_user_ids": [str(USER_2)]}),
        ("an address nobody holds", {"recipient_emails": ["nobody@example.test"]}),
        ("an id nobody holds", {"recipient_user_ids": [str(uuid4())]}),
    ]
    for label, override in cases:
        body = _subscription_body(**{"recipient_emails": [], "recipient_user_ids": [], **override})
        response = db_client.post(
            f"{BASE}/subscriptions", json=body, headers=_headers(db_session, USER_1)
        )
        assert response.status_code == 422, f"{label}: {response.text}"
        details = response.json()["error"]["details"]
        assert details["error_code"] == "bi_notification_recipient_unknown", label
    assert db_session.scalars(select(BiSubscription)).all() == []


def test_an_address_and_an_identifier_naming_one_person_count_once(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The cap counts PEOPLE, so resolution de-duplicates before it is applied."""

    _ = plane
    created = _create_subscription(
        db_client,
        db_session,
        recipient_user_ids=[str(RECIPIENT)],
        recipient_emails=["treasurer@example.test", "cfo@example.test"],
    )
    row = db_session.get(BiSubscription, UUID(created["id"]))
    assert row is not None
    assert row.recipient_user_ids == [str(RECIPIENT), str(SECOND_RECIPIENT)]


def test_a_distribution_list_is_the_owners_to_see(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Being on a list is not being told who else is on it.

    Checked against the serialised body, not against a field being ``None``: a
    future read model that added a display name would otherwise reintroduce the
    disclosure silently.
    """

    _ = plane
    created = _create_subscription(
        db_client, db_session, recipient_emails=["treasurer@example.test", "cfo@example.test"]
    )
    path = f"{BASE}/subscriptions/{created['id']}"

    owner_view = db_client.get(path, headers=_headers(db_session, USER_1))
    assert owner_view.status_code == 200, owner_view.text
    owner_payload = owner_view.json()
    assert owner_payload["owned_by_caller"] is True
    assert {person["email"] for person in owner_payload["recipients"]} == {
        "treasurer@example.test",
        "cfo@example.test",
    }

    recipient_view = db_client.get(path, headers=_headers(db_session, RECIPIENT))
    assert recipient_view.status_code == 200, recipient_view.text
    payload = recipient_view.json()
    assert payload["owned_by_caller"] is False
    assert payload["recipients"] == []
    assert payload["recipient_user_ids"] == [str(RECIPIENT)]
    assert "cfo@example.test" not in recipient_view.text
    assert str(SECOND_RECIPIENT) not in recipient_view.text


def test_delivery_history_is_owner_only(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """It names who received a report and which of them was refused one."""

    _ = plane
    created = _create_subscription(db_client, db_session)
    path = f"{BASE}/subscriptions/{created['id']}/deliveries"
    assert db_client.get(path, headers=_headers(db_session, USER_1)).status_code == 200
    refused = db_client.get(path, headers=_headers(db_session, RECIPIENT))
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["error_code"] == "bi_notification_owner_only"


# --- a refused delivery is an outcome, not an error --------------------------------------


def _seed_delivery(
    db: Session, subscription_id: UUID, *, status: str, reason: str | None, recipient: UUID
) -> BiSubscriptionDelivery:
    delivery = BiSubscriptionDelivery(
        organization_id=ORG_1,
        bank_id=BANK_ID,
        subscription_id=subscription_id,
        recipient_user_id=recipient,
        scheduled_for=datetime(2026, 9, 21, 7, 30, tzinfo=UTC),
        trigger="schedule",
        status=status,
        as_of_date=date(2026, 8, 31),
        disclosure_class="summary" if status == "sent" else None,
        delivery_mode="attachment" if status == "sent" else None,
        artifact_format="csv" if status == "sent" else None,
        artifact_sha256="a" * 64 if status == "sent" else None,
        artifact_size_bytes=2048 if status == "sent" else None,
        row_count=12 if status == "sent" else None,
        member_ids=[LOANS] if status == "sent" else [],
        denied_members=[] if status == "sent" else [DEPOSITS],
        reason=reason,
        build_fingerprint="b" * 64 if status == "sent" else None,
        builder_version=1,
        # Both stamps are stated: the row's CHECK refuses a send that precedes its
        # own claim, and the model's default would put ``created_at`` at now.
        created_at=datetime(2026, 9, 21, 7, 30, tzinfo=UTC),
        sent_at=datetime(2026, 9, 21, 7, 31, tzinfo=UTC) if status == "sent" else None,
    )
    db.add(delivery)
    db.flush()
    return delivery


def test_a_refused_delivery_reads_as_an_outcome_and_not_as_a_failure(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A recipient who could not see the figures is not an error to hide.

    The row says what happened in a sentence, carries no figure, no file and no
    row count — because none was ever produced — and is plainly distinguishable
    from the delivery that failed at the relay, which IS something to act on.
    """

    _ = plane
    created = _create_subscription(
        db_client, db_session, recipient_emails=["treasurer@example.test", "cfo@example.test"]
    )
    subscription_id = UUID(created["id"])
    _seed_delivery(db_session, subscription_id, status="sent", reason=None, recipient=RECIPIENT)
    _seed_delivery(
        db_session,
        subscription_id,
        status="denied",
        reason="data_scope_unsupported",
        recipient=SECOND_RECIPIENT,
    )
    db_session.commit()

    response = db_client.get(
        f"{BASE}/subscriptions/{subscription_id}/deliveries", headers=_headers(db_session, USER_1)
    )
    assert response.status_code == 200, response.text
    by_recipient = {row["recipient_user_id"]: row for row in response.json()["deliveries"]}
    refused = by_recipient[str(SECOND_RECIPIENT)]
    assert refused["status"] == "denied"
    assert "Not sent" in refused["detail"]
    assert "access" in refused["detail"]
    assert refused["row_count"] is None
    assert refused["artifact_size_bytes"] is None
    assert refused["delivery_mode"] is None
    assert refused["sent_at"] is None
    assert refused["recipient_email"] == "cfo@example.test"

    sent = by_recipient[str(RECIPIENT)]
    assert sent["status"] == "sent"
    assert sent["detail"] == "Sent."
    assert sent["row_count"] == 12
    assert sent["delivery_mode"] == "attachment"


# --- a verdict is a figure ---------------------------------------------------------------


def _seed_event(db: Session, alert: BiAlert, *, state: str) -> BiAlertEvent:
    event = BiAlertEvent(
        organization_id=ORG_1,
        bank_id=BANK_ID,
        alert_id=alert.id,
        as_of_date=date(2026, 8, 31),
        build_fingerprint="c" * 64,
        state=state,
        observed_value=Decimal("1500000") if state != "not_evaluated" else None,
        threshold_value=Decimal("1000000") if state != "not_evaluated" else None,
        threshold_basis="stated",
        limit_source=None,
        reason=None if state != "not_evaluated" else "no_figure",
        member_ids=[LOANS],
        notified_user_ids=[str(RECIPIENT)],
        withheld_user_ids=[],
        builder_version=1,
    )
    db.add(event)
    db.flush()
    return event


def test_a_verdict_needs_the_callers_own_access_even_when_they_own_the_alert(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """An alert outlives a grant. Its owner keeps the alert and loses the figure.

    The list still carries the alert — its owner has to be able to stop it — with
    no verdict and a sentence saying why, and the events read, whose every row
    states the observed figure, is refused outright.
    """

    _ = plane
    created = _create_alert(db_client, db_session)
    alert = db_session.get(BiAlert, UUID(created["id"]))
    assert alert is not None
    _seed_event(db_session, alert, state="breached")
    db_session.commit()

    # With the grant, the verdict and the figure are both served.
    listed = db_client.get(f"{BASE}/alerts", headers=_headers(db_session, USER_1))
    assert listed.status_code == 200, listed.text
    row = listed.json()["alerts"][0]
    assert row["latest_state"] == "breached"
    assert row["latest_as_of"] == "2026-08-31"
    events = db_client.get(f"{BASE}/alerts/{alert.id}/events", headers=_headers(db_session, USER_1))
    assert events.status_code == 200, events.text
    assert "1500000" in events.text

    # Withdraw every grant the author holds. They still own the alert.
    db_session.execute(
        delete(AuthorizationBinding).where(
            AuthorizationBinding.organization_id == ORG_1,
            AuthorizationBinding.principal_user_id == USER_1,
        )
    )
    db_session.commit()
    authorization.invalidate_user_authorization(
        db_session,
        organization_id=ORG_1,
        user_id=USER_1,
        reason="Every grant this author held was withdrawn.",
    )
    db_session.commit()

    after = db_client.get(f"{BASE}/alerts", headers=_headers(db_session, USER_1))
    assert after.status_code == 200, after.text
    stripped = after.json()["alerts"][0]
    assert stripped["latest_state"] is None
    assert stripped["latest_as_of"] is None
    assert "does not cover the figure" in stripped["latest_detail"]
    assert "1500000" not in after.text

    refused = db_client.get(
        f"{BASE}/alerts/{alert.id}/events", headers=_headers(db_session, USER_1)
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["error_code"] == "bi_authorization_denied"

    # And stopping it still works, which is the point of keeping it visible.
    stop = db_client.post(
        f"{BASE}/alerts/{alert.id}/deactivation",
        json={"reason": "My access changed and this should stop."},
        headers=_headers(db_session, USER_1),
    )
    assert stop.status_code == 200, stop.text
    assert stop.json()["is_active"] is False


def test_an_author_cannot_watch_a_figure_they_cannot_read(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Deny-by-default applies to writing as well as reading."""

    _ = plane
    response = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(measure_id=DEPOSITS, name="Deposits below plan", direction="below"),
        headers=_headers(db_session, RECIPIENT),
    )
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == "bi_authorization_denied"
    assert DEPOSITS in details["denied_members"]
    assert db_session.scalars(select(BiAlert)).all() == []


def test_an_author_cannot_schedule_a_report_they_cannot_read(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.post(
        f"{BASE}/subscriptions",
        json=_subscription_body(query=_query(DEPOSITS), name="Deposits pack"),
        headers=_headers(db_session, RECIPIENT),
    )
    assert response.status_code == 403, response.text
    assert db_session.scalars(select(BiSubscription)).all() == []


# --- the threshold pairing ---------------------------------------------------------------


def test_a_threshold_is_stated_or_governed_and_never_neither(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The refusal names the rule, and nothing reaches the database constraint."""

    _ = plane
    missing = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(threshold_basis="stated", threshold=None),
        headers=_headers(db_session, USER_1),
    )
    assert missing.status_code == 422, missing.text
    assert "a stated threshold needs a value" in missing.text

    both = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(threshold_basis="governed_limit", threshold="5"),
        headers=_headers(db_session, USER_1),
    )
    assert both.status_code == 422, both.text
    assert "governed limit carries no value of its own" in both.text
    assert db_session.scalars(select(BiAlert)).all() == []


def test_a_governed_limit_needs_a_figure_that_has_one(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The pairing refusal the request model cannot make, named at the route.

    ``loans.balance_rc`` declares no governed threshold source, so an alert set to
    be judged against "the limit already governed for this figure" could only ever
    record that there was no line. It is refused before it is written, with the
    error code the interface disables the control on.
    """

    _ = plane
    refused = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(measure_id=LOANS, threshold_basis="governed_limit", threshold=None),
        headers=_headers(db_session, USER_1),
    )
    assert refused.status_code == 422, refused.text
    details = refused.json()["error"]["details"]
    assert details["error_code"] == "bi_alert_governed_limit_unavailable"
    assert "State a number instead." in details["message"]

    accepted = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(
            name="Capital adequacy below its limit",
            measure_id=CAR,
            direction="below",
            threshold_basis="governed_limit",
            threshold=None,
        ),
        headers=_headers(db_session, USER_1),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["threshold"] is None
    assert accepted.json()["threshold_basis"] == "governed_limit"


def test_an_alert_cannot_watch_something_that_is_not_a_figure(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    response = db_client.post(
        f"{BASE}/alerts",
        json=_alert_body(measure_id=OBLIGOR),
        headers=_headers(db_session, USER_1),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_alert_measure_unknown"


# --- the cadence pairing -----------------------------------------------------------------


def test_a_cadence_carries_exactly_the_schedule_it_needs(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Four cadences, and every wrong combination refused before it is stored."""

    _ = plane
    wrong: list[tuple[str, dict[str, Any]]] = [
        (
            "a weekly report with no weekday",
            _subscription_body(cadence="weekly", day_of_week=None),
        ),
        (
            "a daily report carrying a weekday",
            _subscription_body(cadence="daily", day_of_week=1),
        ),
        (
            "a monthly report with no day of month",
            _subscription_body(cadence="monthly", day_of_week=None, day_of_month=None),
        ),
        (
            "an on-new-data report carrying a clock",
            _subscription_body(cadence="on_new_data", day_of_week=None),
        ),
    ]
    for label, body in wrong:
        response = db_client.post(
            f"{BASE}/subscriptions", json=body, headers=_headers(db_session, USER_1)
        )
        assert response.status_code == 422, f"{label}: {response.text}"
    assert db_session.scalars(select(BiSubscription)).all() == []

    created = _create_subscription(
        db_client,
        db_session,
        name="When the figures land",
        cadence="on_new_data",
        hour=None,
        minute=None,
        day_of_week=None,
    )
    assert created["cadence"] == "on_new_data"
    assert created["hour"] is None
    assert created["time_zone"] == "Africa/Accra"


def test_a_record_level_report_says_it_will_never_be_attached(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The classifier decides it from the catalogue, before a row is ever read."""

    _ = plane
    created = _create_subscription(
        db_client,
        db_session,
        name="Lending by obligor",
        query=_query(LOANS, dimensions=(OBLIGOR,)),
    )
    assert created["disclosure_class"] == "record_level"
    assert "never attached" in created["delivery_note"]
    assert "sign-in link" in created["delivery_note"]

    summary = _create_subscription(db_client, db_session, name="Lending total")
    assert summary["disclosure_class"] == "summary"
    assert "as a file" in summary["delivery_note"]


def test_a_record_level_report_needs_the_full_export_sentence(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A reader who may VIEW everything and export nothing may not schedule one.

    The author is held to exactly the sentence each recipient will be held to
    (``exports/policy.permissions_for``): a summary report needs ``view`` alone,
    a record-level one needs ``view`` and ``export``. ``STRANGER`` reads every
    module at every sensitivity and holds no export authority, so the summary
    report is accepted from them and the record-level one is not — which is what
    shows the refusal is about the CLASS and not about the figures.
    """

    _ = plane
    accepted = db_client.post(
        f"{BASE}/subscriptions",
        json=_subscription_body(name="Totals only", recipient_emails=["treasurer@example.test"]),
        headers=_headers(db_session, STRANGER),
    )
    assert accepted.status_code == 201, accepted.text

    refused = db_client.post(
        f"{BASE}/subscriptions",
        json=_subscription_body(
            name="Obligor detail",
            query=_query(LOANS, dimensions=(OBLIGOR,)),
            recipient_emails=["treasurer@example.test"],
        ),
        headers=_headers(db_session, STRANGER),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["error_code"] == "bi_authorization_denied"


# --- the audit trail ---------------------------------------------------------------------


def _events(db: Session, event_type: str) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == ORG_1, AuditEvent.event_type == event_type
            )
        )
    )


def test_every_mutation_is_audited_with_who_what_and_why(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Six mutations, six trails, each naming the actor, the object and the reason.

    The recipient COUNT is recorded rather than the list: the audit trail is read
    by people who are not the owner, so the owner-only rule on a distribution list
    holds there too.
    """

    _ = plane
    alert = _create_alert(db_client, db_session)
    subscription = _create_subscription(db_client, db_session)
    assert (
        db_client.put(
            f"{BASE}/alerts/{alert['id']}",
            json=_alert_body(name="Loan book above the revised plan"),
            headers=_headers(db_session, USER_1),
        ).status_code
        == 200
    )
    assert (
        db_client.post(
            f"{BASE}/subscriptions/{subscription['id']}/deactivation",
            json={"reason": "The ALCO moved to a monthly pack."},
            headers=_headers(db_session, USER_1),
        ).status_code
        == 200
    )
    assert (
        db_client.delete(
            f"{BASE}/alerts/{alert['id']}", headers=_headers(db_session, USER_1)
        ).status_code
        == 204
    )
    assert (
        db_client.delete(
            f"{BASE}/subscriptions/{subscription['id']}", headers=_headers(db_session, USER_1)
        ).status_code
        == 204
    )

    created = _events(db_session, feature.EVENT_ALERT_CREATED)
    assert len(created) == 1
    assert created[0].actor_user_id == USER_1
    assert created[0].entity_type == feature.ENTITY_ALERT
    assert created[0].entity_id == alert["id"]
    assert created[0].details["reason"] == _alert_body()["reason"]
    assert created[0].details["threshold"] == "1000000"
    assert created[0].details["notifies"] == 1
    assert str(RECIPIENT) not in repr(created[0].details)

    updated = _events(db_session, feature.EVENT_ALERT_UPDATED)
    assert len(updated) == 1
    assert updated[0].details["name"] == "Loan book above the revised plan"

    stopped = _events(db_session, feature.EVENT_SUBSCRIPTION_DEACTIVATED)
    assert len(stopped) == 1
    assert stopped[0].details["is_active"] is False
    assert stopped[0].details["reason"] == "The ALCO moved to a monthly pack."
    assert stopped[0].details["recipients"] == 1

    assert len(_events(db_session, feature.EVENT_SUBSCRIPTION_CREATED)) == 1
    assert len(_events(db_session, feature.EVENT_ALERT_DELETED)) == 1
    assert len(_events(db_session, feature.EVENT_SUBSCRIPTION_DELETED)) == 1


def test_deleting_an_alert_takes_its_recorded_verdicts_with_it(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    _ = plane
    created = _create_alert(db_client, db_session)
    alert = db_session.get(BiAlert, UUID(created["id"]))
    assert alert is not None
    _seed_event(db_session, alert, state="breached")
    db_session.commit()
    assert (
        db_client.delete(
            f"{BASE}/alerts/{alert.id}", headers=_headers(db_session, USER_1)
        ).status_code
        == 204
    )
    assert db_session.scalars(select(BiAlertEvent)).all() == []


def test_two_objects_of_one_institution_cannot_share_a_name(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """The unique key answers as a named conflict, never as an integrity error."""

    _ = plane
    _create_alert(db_client, db_session)
    clash = db_client.post(
        f"{BASE}/alerts", json=_alert_body(), headers=_headers(db_session, USER_1)
    )
    assert clash.status_code == 409, clash.text
    assert clash.json()["error"]["details"]["error_code"] == "bi_alert_name_taken"


# --- the deployment flag -----------------------------------------------------------------


def test_with_bi_disabled_every_notification_path_is_absent(
    db_client: TestClient, db_session: Session, plane: Bank
) -> None:
    """Not forbidden — not there. The same answer an unmounted path gives."""

    _ = plane
    get_settings.cache_clear()
    for method, path, body in (
        ("GET", f"{BASE}/alerts", None),
        ("POST", f"{BASE}/alerts", _alert_body()),
        ("GET", f"{BASE}/subscriptions", None),
        ("POST", f"{BASE}/subscriptions", _subscription_body()),
        ("GET", f"{BASE}/alerts/{uuid4()}/events", None),
        ("GET", f"{BASE}/subscriptions/{uuid4()}/deliveries", None),
    ):
        response = db_client.request(method, path, json=body, headers=_headers(db_session, USER_1))
        assert response.status_code == 404, f"{method} {path}: {response.text}"


def test_a_stored_filter_is_part_of_the_question_a_verdict_is_authorized_against(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """A filter is a read, so the figure it narrows is not the question asked.

    The alert watches gross loans — which the narrow recipient may read — NARROWED
    to one named counterparty, which they may not. Authorizing the bare measure
    would admit them to "exposure to this obligor" on the strength of being
    allowed the total, so the stored filter rides into the decision and their
    verdict is withheld.
    """

    _ = plane
    created = _create_alert(
        db_client,
        db_session,
        name="Exposure to one obligor above plan",
        filters=[{"member": OBLIGOR, "op": "eq", "values": ["ACME"]}],
    )
    alert = db_session.get(BiAlert, UUID(created["id"]))
    assert alert is not None
    assert alert.filters == [{"member": OBLIGOR, "op": "eq", "values": ["ACME"]}]
    _seed_event(db_session, alert, state="breached")
    db_session.commit()

    owner = db_client.get(f"{BASE}/alerts", headers=_headers(db_session, USER_1))
    assert owner.status_code == 200, owner.text
    assert owner.json()["alerts"][0]["latest_state"] == "breached"

    recipient = db_client.get(f"{BASE}/alerts", headers=_headers(db_session, RECIPIENT))
    assert recipient.status_code == 200, recipient.text
    row = recipient.json()["alerts"][0]
    assert row["latest_state"] is None
    assert "does not cover the figure" in row["latest_detail"]
    assert "1500000" not in recipient.text

    refused = db_client.get(
        f"{BASE}/alerts/{alert.id}/events", headers=_headers(db_session, RECIPIENT)
    )
    assert refused.status_code == 403, refused.text
    assert OBLIGOR in refused.json()["error"]["details"]["denied_members"]


def test_a_stored_question_that_can_no_longer_be_read_refuses_by_name(
    db_client: TestClient, db_session: Session, plane: Bank, bi_on: None
) -> None:
    """Never a 500, and the report can still be deleted.

    A stored question is written by this application from a validated model, so
    the only way it becomes unreadable is a validator that tightened afterwards.
    That must be a refusal its owner can act on — and ``DELETE`` never reads the
    question, which is what makes acting on it possible.
    """

    _ = plane
    created = _create_subscription(db_client, db_session)
    subscription = db_session.get(BiSubscription, UUID(created["id"]))
    assert subscription is not None
    subscription.query = {"measures": [], "time": {}}
    db_session.commit()

    listed = db_client.get(f"{BASE}/subscriptions", headers=_headers(db_session, USER_1))
    assert listed.status_code == 409, listed.text
    assert listed.json()["error"]["details"]["error_code"] == "bi_subscription_query_unreadable"

    removed = db_client.delete(
        f"{BASE}/subscriptions/{subscription.id}", headers=_headers(db_session, USER_1)
    )
    assert removed.status_code == 204, removed.text
    assert db_session.scalars(select(BiSubscription)).all() == []
