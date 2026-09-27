"""The wire contract for threshold alerts and scheduled subscriptions.

Every model is CLOSED (``extra="forbid"``, inherited from
``app.schemas.bi.BiClosedModel``): an unrecognised key is a client that thinks it
is configuring something, and accepting it silently is how a "confidential: true"
flag ends up deciding a disclosure question the server is supposed to decide.

The validators here restate the database's own CHECK constraints
(``app/models/bi_notifications.py``) rather than trusting them to be the only
gate, for two reasons that are not redundancy: a 422 naming the field is a better
answer than an integrity error, and the pairing rules — a stated threshold
carries a number, a governed one carries none; a clock cadence carries exactly
the fields it needs — are the shape of the FEATURE and belong where a reader
looks for it.

Three rules are enforced only here, because a JSON column cannot carry them:

* **Recipients are tenant users, capped.** Never a free-text address: an
  arbitrary mailbox is outside every authorization decision the platform can
  make, so a recipient must be a principal the evaluator can answer about. The
  cap is ``app.services.bi.subscriptions.MAX_RECIPIENTS`` and it bounds how long
  one delivery run can occupy a worker.
* **A subscription's query names a window SHAPE, not a date.** ``time.as_of``
  only: a stored ``range`` or ``compare_to`` is an absolute historical window
  that a recurring delivery drifts away from, and there is no "previous period"
  token in ``BiQuery`` to rebind instead. The run supplies the date.
* **A subscription's query asks for no page of its own.** ``limit`` / ``offset``
  are a client's paging concern; a mailed artifact is the whole answer under the
  server's own cap.

Nothing here decides whether an artifact may be ATTACHED. That is
``app/services/bi/exports/policy.py``, read from the catalogue's own sensitivity
declarations — which is exactly why there is no field for it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.bi import BiClosedModel, BiFilter, BiMemberId, BiQuery

#: Mirrors ``models.bi_notifications.ALERT_DIRECTIONS``.
BiAlertDirection = Literal["above", "below"]
#: Mirrors ``models.bi_notifications.ALERT_THRESHOLD_BASES``.
BiAlertThresholdBasis = Literal["stated", "governed_limit"]
#: Mirrors ``models.bi_notifications.ALERT_EVENT_STATES``.
BiAlertState = Literal["breached", "cleared", "not_evaluated"]
#: Mirrors ``models.bi_notifications.SUBSCRIPTION_CADENCES``.
BiSubscriptionCadence = Literal["daily", "weekly", "monthly", "on_new_data"]
#: Mirrors ``services.bi.exports.EXPORT_FORMATS`` and
#: ``models.bi_notifications.SUBSCRIPTION_FORMATS``; parity is asserted by a test.
BiSubscriptionFormat = Literal["csv", "xlsx", "pdf"]
#: Mirrors ``models.bi_notifications.DELIVERY_STATUSES``.
BiDeliveryStatus = Literal["pending", "sent", "denied", "no_data", "failed"]
#: Mirrors ``models.bi_notifications.DELIVERY_MODES``.
BiDeliveryMode = Literal["attachment", "link"]
#: Mirrors ``models.bi_notifications.DELIVERY_TRIGGERS``.
BiDeliveryTrigger = Literal["schedule", "new_data"]

#: The most recipients one subscription may name. Kept in step with
#: ``app.services.bi.subscriptions.MAX_RECIPIENTS`` by a test rather than by an
#: import, so the schema module depends on no service.
BI_SUBSCRIPTION_MAX_RECIPIENTS = 50

#: The most users one alert may notify. Same reasoning as above, and the same
#: number: an alert that fans out to a hundred people is a report, not an alert.
BI_ALERT_MAX_RECIPIENTS = 50

#: How many filters an alert may carry. The alert is a threshold on one figure;
#: this is enough to name a slice and not enough to build a report.
BI_ALERT_MAX_FILTERS = 8


class BiAlertUpsert(BiClosedModel):
    """Create or replace one threshold alert.

    ``threshold`` is the bank's OWN number and is required exactly when
    ``threshold_basis`` is ``stated``. With ``governed_limit`` it must be absent:
    the line then comes from the governed register through
    ``app/services/bi/limits.py``, and a number sent alongside would be a second,
    unauthoritative copy of it.
    """

    name: str = Field(min_length=1, max_length=120)
    measure_id: BiMemberId
    filters: list[BiFilter] = Field(default_factory=list, max_length=BI_ALERT_MAX_FILTERS)
    direction: BiAlertDirection
    threshold_basis: BiAlertThresholdBasis = "stated"
    threshold: Decimal | None = None
    notify_user_ids: list[UUID] = Field(default_factory=list, max_length=BI_ALERT_MAX_RECIPIENTS)
    is_active: bool = True
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _threshold_matches_basis(self) -> BiAlertUpsert:
        if self.threshold_basis == "stated" and self.threshold is None:
            raise ValueError("a stated threshold needs a value")
        if self.threshold_basis == "governed_limit" and self.threshold is not None:
            raise ValueError(
                "a governed limit carries no value of its own; it is resolved from the register"
            )
        return self

    @model_validator(mode="after")
    def _recipients_are_distinct(self) -> BiAlertUpsert:
        if len(set(self.notify_user_ids)) != len(self.notify_user_ids):
            raise ValueError("notify_user_ids must be distinct")
        return self


class BiAlertRead(BiClosedModel):
    """One alert as a surface shows it."""

    id: UUID
    bank_id: str
    name: str
    measure_id: str
    #: The measure's label from the catalogue, so a list needs no second lookup.
    measure_label: str
    filters: list[BiFilter] = Field(default_factory=list)
    direction: BiAlertDirection
    threshold_basis: BiAlertThresholdBasis
    threshold: Decimal | None = None
    owner_user_id: UUID
    notify_user_ids: list[UUID] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: The newest verdict, when there is one.
    latest_state: BiAlertState | None = None
    latest_as_of: date | None = None


class BiAlertEventRead(BiClosedModel):
    """One recorded transition. ``reason`` is set only for ``not_evaluated``."""

    id: UUID
    alert_id: UUID
    as_of_date: date
    state: BiAlertState
    observed_value: Decimal | None = None
    threshold_value: Decimal | None = None
    threshold_basis: BiAlertThresholdBasis
    limit_source: str | None = None
    reason: str | None = None
    #: Production copy for the row, composed by the read feature.
    detail: str
    evaluated_at: datetime


class BiSubscriptionUpsert(BiClosedModel):
    """Create or replace one scheduled subscription.

    The clock fields are read in the INSTITUTION's own time zone, resolved from
    the ``jurisdictions`` registry — ``07:30`` means half past seven where the
    bank is. ``day_of_month`` stops at 28 so a monthly subscription fires in
    every month: a 31st would silently skip most of them.
    """

    name: str = Field(min_length=1, max_length=120)
    query: BiQuery
    artifact_format: BiSubscriptionFormat
    cadence: BiSubscriptionCadence
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    #: ISO-8601 weekday, Monday = 1.
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    recipient_user_ids: list[UUID] = Field(min_length=1, max_length=BI_SUBSCRIPTION_MAX_RECIPIENTS)
    is_active: bool = True
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _schedule_matches_cadence(self) -> BiSubscriptionUpsert:
        clock = (self.hour, self.minute)
        if self.cadence == "on_new_data":
            if any(value is not None for value in (*clock, self.day_of_week, self.day_of_month)):
                raise ValueError("an on_new_data subscription carries no schedule of its own")
            return self
        if any(value is None for value in clock):
            raise ValueError(f"a {self.cadence} subscription needs hour and minute")
        if self.cadence == "weekly" and self.day_of_week is None:
            raise ValueError("a weekly subscription needs day_of_week")
        if self.cadence != "weekly" and self.day_of_week is not None:
            raise ValueError("day_of_week applies only to a weekly subscription")
        if self.cadence == "monthly" and self.day_of_month is None:
            raise ValueError("a monthly subscription needs day_of_month")
        if self.cadence != "monthly" and self.day_of_month is not None:
            raise ValueError("day_of_month applies only to a monthly subscription")
        return self

    @model_validator(mode="after")
    def _query_names_a_window_shape(self) -> BiSubscriptionUpsert:
        if self.query.time.range is not None:
            raise ValueError(
                "a subscription reports one reporting date; the run supplies it, so the "
                "query must not fix a range"
            )
        if self.query.time.compare_to is not None:
            raise ValueError(
                "a subscription cannot fix a comparison date: it would not move with the "
                "reporting date"
            )
        if self.query.limit is not None or self.query.offset:
            raise ValueError("a subscription delivers the whole answer, so it takes no paging")
        return self

    @model_validator(mode="after")
    def _recipients_are_distinct(self) -> BiSubscriptionUpsert:
        if len(set(self.recipient_user_ids)) != len(self.recipient_user_ids):
            raise ValueError("recipient_user_ids must be distinct")
        return self


class BiSubscriptionRead(BiClosedModel):
    """One subscription as a surface shows it."""

    id: UUID
    bank_id: str
    name: str
    query: BiQuery
    artifact_format: BiSubscriptionFormat
    cadence: BiSubscriptionCadence
    hour: int | None = None
    minute: int | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    #: The IANA zone the clock fields are read in, resolved from the
    #: institution's jurisdiction. Shown so "07:30" is never ambiguous.
    time_zone: str
    owner_user_id: UUID
    recipient_user_ids: list[UUID] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: What this subscription WOULD deliver as an attachment, decided by the
    #: export classifier from the query's members. ``record_level`` means every
    #: recipient gets a sign-in link instead.
    disclosure_class: Literal["summary", "record_level"]
    #: Production copy stating that, for the surface.
    delivery_note: str


class BiSubscriptionDeliveryRead(BiClosedModel):
    """One recipient's copy of one run, as the owner's history shows it."""

    id: UUID
    subscription_id: UUID
    recipient_user_id: UUID
    scheduled_for: datetime
    trigger: BiDeliveryTrigger
    status: BiDeliveryStatus
    as_of_date: date | None = None
    disclosure_class: Literal["summary", "record_level"] | None = None
    delivery_mode: BiDeliveryMode | None = None
    artifact_format: BiSubscriptionFormat | None = None
    artifact_size_bytes: int | None = None
    row_count: int | None = None
    reason: str | None = None
    #: Production copy for ``status`` and ``reason``, composed by the read
    #: feature. Never a raw enum on a surface.
    detail: str
    sent_at: datetime | None = None


__all__ = [
    "BI_ALERT_MAX_FILTERS",
    "BI_ALERT_MAX_RECIPIENTS",
    "BI_SUBSCRIPTION_MAX_RECIPIENTS",
    "BiAlertDirection",
    "BiAlertEventRead",
    "BiAlertRead",
    "BiAlertState",
    "BiAlertThresholdBasis",
    "BiAlertUpsert",
    "BiDeliveryMode",
    "BiDeliveryStatus",
    "BiDeliveryTrigger",
    "BiSubscriptionCadence",
    "BiSubscriptionDeliveryRead",
    "BiSubscriptionFormat",
    "BiSubscriptionRead",
    "BiSubscriptionUpsert",
]
