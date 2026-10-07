"""The four alert/subscription tables: what the database refuses, and the parity.

Two kinds of check, in the ``test_bi_commentary_model.py`` shape. The declaration
checks pin what ``create_all`` (the hermetic suite and the Playwright stack) and
the migration (a deployment) must both build, including the vocabularies that are
restated in three places — the model tuple, the schema ``Literal`` and the
service — where drift is silent. The enforcement checks insert rows the
application should never write and prove the DATABASE says no, because an
invariant that lives only in a service is one direct write away from being
untrue.

Two of the enforcement checks are the feature's disclosure controls rather than
housekeeping, and they are the reason this file exists at all:

* **a threshold cannot be both stated and governed**, so there is no row shape in
  which "the bank's own number" and "the register's number" could disagree about
  which line an alert is judged against;
* **a second delivery of the same run to the same recipient is refused by the
  database**, which is what makes double-sending a board pack impossible rather
  than merely unlikely.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base, utc_now
from app.models import Bank, User
from app.models.bi_notifications import (
    ALERT_DIRECTIONS,
    ALERT_EVENT_STATES,
    ALERT_NOT_EVALUATED_REASONS,
    ALERT_THRESHOLD_BASES,
    BI_NOTIFICATION_TABLES,
    DELIVERY_MODES,
    DELIVERY_STATUSES,
    DELIVERY_TRIGGERS,
    DISCLOSURE_CLASSES,
    SUBSCRIPTION_CADENCES,
    SUBSCRIPTION_FORMATS,
    BiAlert,
    BiAlertEvent,
    BiSubscription,
    BiSubscriptionDelivery,
)
from app.schemas import bi_notifications as wire
from app.services.bi import alerts as alerts_service
from app.services.bi import exports, subscriptions
from app.services.bi.exports import policy
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

AS_OF = date(2026, 6, 30)
BANK_ID = "BK-NOTIFMD1"
SCHEDULED = datetime(2026, 6, 30, 7, 30, tzinfo=UTC)
FINGERPRINT = "a" * 64

MODELS: tuple[type, ...] = (BiAlert, BiAlertEvent, BiSubscription, BiSubscriptionDelivery)


@pytest.fixture
def bank(db_session: Session) -> Bank:
    existing = db_session.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    row = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="Notification Model Bank",
        short_name="NotifMod",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _alert(**overrides: Any) -> BiAlert:
    values: dict[str, Any] = {
        "id": uuid4(),
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "name": f"Alert {uuid4().hex[:8]}",
        "measure_id": "loans.balance_rc",
        "filters": [],
        "direction": "below",
        "threshold_basis": "stated",
        "threshold": Decimal("13.5"),
        "owner_user_id": USER_1,
        "notify_user_ids": [],
        "is_active": True,
    }
    values.update(overrides)
    return BiAlert(**values)


def _subscription(**overrides: Any) -> BiSubscription:
    values: dict[str, Any] = {
        "id": uuid4(),
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "name": f"Pack {uuid4().hex[:8]}",
        "owner_user_id": USER_1,
        "query": {"measures": ["loans.balance_rc"], "time": {"as_of": AS_OF.isoformat()}},
        "artifact_format": "csv",
        "cadence": "daily",
        "hour": 7,
        "minute": 30,
        "day_of_week": None,
        "day_of_month": None,
        "recipient_user_ids": [str(USER_1)],
        "is_active": True,
    }
    values.update(overrides)
    return BiSubscription(**values)


def _delivery(subscription_id: UUID, **overrides: Any) -> BiSubscriptionDelivery:
    values: dict[str, Any] = {
        "id": uuid4(),
        "organization_id": ORG_1,
        "bank_id": BANK_ID,
        "subscription_id": subscription_id,
        "recipient_user_id": USER_1,
        "scheduled_for": SCHEDULED,
        "trigger": "schedule",
        "status": "pending",
        "as_of_date": AS_OF,
        "disclosure_class": "summary",
        "delivery_mode": None,
        "builder_version": 1,
        "created_at": utc_now(),
    }
    values.update(overrides)
    return BiSubscriptionDelivery(**values)


def _refuses(db: Session, row: object) -> None:
    db.add(row)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --- the declaration ---------------------------------------------------------


def test_every_table_is_declared_and_reachable_through_the_metadata() -> None:
    """``create_all`` builds what the migration must build, and nothing is stranded."""

    assert tuple(model.__tablename__ for model in MODELS) == BI_NOTIFICATION_TABLES
    for name in BI_NOTIFICATION_TABLES:
        assert name in Base.metadata.tables, name
        assert name.startswith("bi_"), name


def test_create_all_on_sqlite_builds_every_table_as_a_plain_table() -> None:
    """No ``postgresql_partition_by``: partitioning is migration-owned (D-011).

    The deliveries table is the only plausible candidate (it grows per recipient
    per run) and it is deliberately NOT partitioned — see the report's
    partitioning argument. Declaring it here would additionally make a Postgres
    ``create_all`` produce a childless parent that rejects every INSERT.
    """

    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    present = set(sa.inspect(engine).get_table_names())
    assert set(BI_NOTIFICATION_TABLES) <= present
    for model in MODELS:
        table = model.__table__
        assert table.dialect_options["postgresql"]["partition_by"] is None, table.name


def test_every_table_carries_both_tenant_keys_and_the_composite_bank_key() -> None:
    """The house rule: ``String(16)`` platform ids with the composite bank FK.

    A plain ``banks.id`` reference would let one tenant's row point at another
    tenant's institution, which is the shape the platform-id epoch exists to
    make impossible.
    """

    for model in MODELS:
        table = model.__table__
        for column in ("organization_id", "bank_id"):
            assert column in table.columns, f"{table.name}.{column}"
            assert isinstance(table.columns[column].type, sa.String)
            assert table.columns[column].type.length == 16
            assert not table.columns[column].nullable
        composites = [
            constraint
            for constraint in table.foreign_key_constraints
            if {element.parent.name for element in constraint.elements}
            == {"bank_id", "organization_id"}
        ]
        assert composites, f"{table.name} has no composite bank foreign key"


def test_no_json_column_is_jsonb() -> None:
    """``sa.JSON`` only: the hermetic suite and the e2e stack build this on SQLite."""

    for model in MODELS:
        for column in model.__table__.columns:
            if isinstance(column.type, sa.JSON):
                assert type(column.type) is sa.JSON, f"{model.__tablename__}.{column.name}"


def test_every_user_reference_is_tenant_scoped() -> None:
    """A principal named by one of these rows must be a user of the SAME tenant.

    Otherwise a subscription could name a sibling tenant's user as a recipient,
    and the delivery would then be authorized against a principal the owning
    organization has no relationship with.
    """

    references = {
        (BiAlert, "owner_user_id"),
        (BiSubscription, "owner_user_id"),
        (BiSubscriptionDelivery, "recipient_user_id"),
    }
    for model, column in references:
        composites = [
            constraint
            for constraint in model.__table__.foreign_key_constraints
            if {element.parent.name for element in constraint.elements}
            == {column, "organization_id"}
        ]
        assert composites, f"{model.__tablename__}.{column} is not tenant-scoped"


# --- vocabulary parity, in both directions -----------------------------------


def test_the_artifact_formats_are_exactly_the_renderers_that_exist() -> None:
    """A format a subscription may ask for and a format ``exports`` can render.

    Imported rather than copied: a fourth renderer must reach the CHECK
    constraint and the wire ``Literal`` in the same change, or a bank configures
    a format nothing produces.
    """

    assert SUBSCRIPTION_FORMATS == exports.EXPORT_FORMATS
    assert set(wire.BiSubscriptionFormat.__args__) == set(SUBSCRIPTION_FORMATS)


def test_the_disclosure_classes_are_the_export_policy_s_own() -> None:
    """The classifier decides attachment vs link, so its classes are the column's."""

    assert set(DISCLOSURE_CLASSES) == {policy.SUMMARY, policy.RECORD_LEVEL}


def test_every_wire_vocabulary_matches_its_column_vocabulary() -> None:
    """The schema ``Literal``s and the CHECK tuples, pinned against each other."""

    pairs = (
        (wire.BiAlertDirection, ALERT_DIRECTIONS),
        (wire.BiAlertThresholdBasis, ALERT_THRESHOLD_BASES),
        (wire.BiAlertState, ALERT_EVENT_STATES),
        (wire.BiSubscriptionCadence, SUBSCRIPTION_CADENCES),
        (wire.BiDeliveryStatus, DELIVERY_STATUSES),
        (wire.BiDeliveryMode, DELIVERY_MODES),
        (wire.BiDeliveryTrigger, DELIVERY_TRIGGERS),
    )
    for literal, vocabulary in pairs:
        assert set(literal.__args__) == set(vocabulary), vocabulary


def test_every_reason_the_alert_service_can_write_is_in_the_column_vocabulary() -> None:
    """A reason the CHECK does not know would fail at the moment it mattered most.

    Derived from the service's own ``REASON_*`` constants, so a new refusal
    branch cannot ship without a vocabulary entry.
    """

    written = {
        value
        for name, value in vars(alerts_service).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert written, "the alerts service declares no reasons — the scan has collapsed"
    assert written <= set(ALERT_NOT_EVALUATED_REASONS), sorted(
        written - set(ALERT_NOT_EVALUATED_REASONS)
    )


def test_the_recipient_cap_is_one_number() -> None:
    """The schema's cap and the run's cap, which its stale window is set against."""

    assert wire.BI_SUBSCRIPTION_MAX_RECIPIENTS == subscriptions.MAX_RECIPIENTS


# --- what the database refuses -----------------------------------------------


def test_a_stated_threshold_must_carry_a_number(db_session: Session, bank: Bank) -> None:
    _refuses(db_session, _alert(threshold_basis="stated", threshold=None))


def test_a_governed_threshold_must_carry_no_number(db_session: Session, bank: Bank) -> None:
    """The control that makes "passed in, never inferred" a row shape.

    A governed alert whose row also held a number would have two candidate lines
    and no rule for which one governs.
    """

    _refuses(db_session, _alert(threshold_basis="governed_limit", threshold=Decimal("13.5")))


def test_a_governed_threshold_with_no_number_is_accepted(db_session: Session, bank: Bank) -> None:
    row = _alert(threshold_basis="governed_limit", threshold=None)
    db_session.add(row)
    db_session.flush()
    assert row.threshold is None


def test_an_unknown_direction_is_refused(db_session: Session, bank: Bank) -> None:
    _refuses(db_session, _alert(direction="sideways"))


def test_an_alert_cannot_be_owned_by_another_tenants_user(db_session: Session, bank: Bank) -> None:
    other = db_session.get(User, USER_2)
    assert other is not None and other.organization_id == ORG_2, (
        "the hermetic fixture's second tenant user is what this test narrows on"
    )
    _refuses(db_session, _alert(owner_user_id=USER_2))


def test_an_unevaluated_alert_event_must_say_why(db_session: Session, bank: Bank) -> None:
    alert = _alert()
    db_session.add(alert)
    db_session.flush()
    _refuses(
        db_session,
        BiAlertEvent(
            id=uuid4(),
            organization_id=ORG_1,
            bank_id=BANK_ID,
            alert_id=alert.id,
            as_of_date=AS_OF,
            build_fingerprint=FINGERPRINT,
            state="not_evaluated",
            observed_value=None,
            threshold_value=None,
            threshold_basis="stated",
            reason=None,
            evaluated_at=utc_now(),
            builder_version=1,
        ),
    )


def test_a_verdict_must_carry_the_figure_and_the_line(db_session: Session, bank: Bank) -> None:
    """A breach with no observed value would be an assertion with no evidence."""

    alert = _alert()
    db_session.add(alert)
    db_session.flush()
    _refuses(
        db_session,
        BiAlertEvent(
            id=uuid4(),
            organization_id=ORG_1,
            bank_id=BANK_ID,
            alert_id=alert.id,
            as_of_date=AS_OF,
            build_fingerprint=FINGERPRINT,
            state="breached",
            observed_value=None,
            threshold_value=Decimal("13.5"),
            threshold_basis="stated",
            reason=None,
            evaluated_at=utc_now(),
            builder_version=1,
        ),
    )


def test_the_same_data_cannot_be_judged_twice_for_one_alert(
    db_session: Session, bank: Bank
) -> None:
    """The alert half of idempotence: ``(alert, as_of, fingerprint)`` is unique.

    Re-evaluating an unchanged mart build therefore cannot write a second row,
    and so cannot send a second notification.
    """

    alert = _alert()
    db_session.add(alert)
    db_session.flush()

    def event(state: str) -> BiAlertEvent:
        return BiAlertEvent(
            id=uuid4(),
            organization_id=ORG_1,
            bank_id=BANK_ID,
            alert_id=alert.id,
            as_of_date=AS_OF,
            build_fingerprint=FINGERPRINT,
            state=state,
            observed_value=Decimal("12"),
            threshold_value=Decimal("13.5"),
            threshold_basis="stated",
            reason=None,
            evaluated_at=utc_now(),
            builder_version=1,
        )

    db_session.add(event("breached"))
    db_session.flush()
    _refuses(db_session, event("cleared"))


def test_an_on_new_data_subscription_carries_no_clock(db_session: Session, bank: Bank) -> None:
    _refuses(db_session, _subscription(cadence="on_new_data", hour=7, minute=30))


def test_a_daily_subscription_needs_an_hour_and_a_minute(db_session: Session, bank: Bank) -> None:
    _refuses(db_session, _subscription(cadence="daily", hour=None, minute=None))


def test_a_weekly_subscription_needs_a_weekday(db_session: Session, bank: Bank) -> None:
    _refuses(db_session, _subscription(cadence="weekly", day_of_week=None))


def test_a_monthly_subscription_cannot_name_a_day_no_month_has(
    db_session: Session, bank: Bank
) -> None:
    """28 is the cap: a 31st would silently skip most months."""

    _refuses(db_session, _subscription(cadence="monthly", day_of_week=None, day_of_month=31))


def test_a_second_delivery_of_one_run_to_one_recipient_is_refused(
    db_session: Session, bank: Bank
) -> None:
    """THE double-send guard, at the database.

    Everything else about idempotence — the coalesce key, the existence check on
    the job, the committed ``pending`` claim — is a fast path. This is the
    guarantee: a duplicate tick, a replayed queue row and a reclaimed job running
    beside the original all collide here, and only one of them sends.
    """

    subscription = _subscription()
    db_session.add(subscription)
    db_session.flush()
    db_session.add(_delivery(subscription.id))
    db_session.flush()
    _refuses(db_session, _delivery(subscription.id))


def test_the_same_recipient_at_a_different_minute_is_a_different_run(
    db_session: Session, bank: Bank
) -> None:
    """The guard must not be so broad that tomorrow's pack is refused as well."""

    subscription = _subscription()
    db_session.add(subscription)
    db_session.flush()
    db_session.add(_delivery(subscription.id))
    db_session.add(
        _delivery(subscription.id, scheduled_for=datetime(2026, 7, 1, 7, 30, tzinfo=UTC))
    )
    db_session.flush()


def test_a_sent_delivery_must_record_when_it_went(db_session: Session, bank: Bank) -> None:
    subscription = _subscription()
    db_session.add(subscription)
    db_session.flush()
    _refuses(
        db_session,
        _delivery(subscription.id, status="sent", delivery_mode=None, sent_at=None),
    )


def test_an_attached_delivery_must_account_for_its_bytes(db_session: Session, bank: Bank) -> None:
    """ "Sent as an attachment" with no digest and no size is an unauditable claim.

    The digest is the only record of WHICH bytes left the platform — the bytes
    themselves are deliberately never stored — so a row that omits it cannot be
    reconciled against anything.
    """

    subscription = _subscription()
    db_session.add(subscription)
    db_session.flush()
    _refuses(
        db_session,
        _delivery(
            subscription.id,
            status="sent",
            delivery_mode="attachment",
            artifact_format="csv",
            artifact_sha256=None,
            artifact_size_bytes=None,
            sent_at=utc_now(),
        ),
    )


def test_a_link_delivery_must_claim_no_bytes(db_session: Session, bank: Bank) -> None:
    subscription = _subscription()
    db_session.add(subscription)
    db_session.flush()
    _refuses(
        db_session,
        _delivery(
            subscription.id,
            status="sent",
            delivery_mode="link",
            artifact_sha256="b" * 64,
            artifact_size_bytes=10,
            sent_at=utc_now(),
        ),
    )
