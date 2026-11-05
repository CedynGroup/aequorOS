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

This module's other-suite cases live in ``test_bi_notification_models_integration.py``.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.bi.models.notifications import (
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
    BiSubscription,
    BiSubscriptionDelivery,
)
from app.bi.schemas import notifications as wire
from app.bi.service import alerts as alerts_service
from app.bi.service import exports, subscriptions
from app.bi.service.exports import policy
from app.db.base import Base
from tests.bi.models.test_bi_notification_models_integration import MODELS


def test_every_table_is_declared_and_reachable_through_the_metadata() -> None:
    """``create_all`` builds what the migration must build, and nothing is stranded."""

    assert tuple(model.__tablename__ for model in MODELS) == BI_NOTIFICATION_TABLES
    for name in BI_NOTIFICATION_TABLES:
        assert name in Base.metadata.tables, name
        assert name.startswith("bi_"), name


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
