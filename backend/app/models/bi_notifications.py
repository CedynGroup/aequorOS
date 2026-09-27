"""Threshold alerts and scheduled subscriptions: the four ``bi_*`` control tables.

``docs/bi.md`` §Phase 3 names them — ``bi_alerts``, ``bi_alert_events``,
``bi_subscriptions``, ``bi_subscription_deliveries`` — and they are the DISPATCH
plane's own state, exactly like the marts: every figure they hold is a COPY of
something the BI query path already served, under an authorization decision the
row records.

Four properties are structural rather than stylistic, because each one is a
disclosure control:

* **A threshold is PASSED IN, never inferred.** ``bi_alerts.threshold_basis``
  says where the line came from in one word: ``stated`` is the bank's own
  number, carried in ``threshold``; ``governed_limit`` carries NO number at all
  and is resolved at evaluation time through ``app/services/bi/limits.py`` — the
  one door to a register. A CHECK makes the two mutually exclusive, so there is
  no row shape in which a limit could be read off data.
* **A delivery is per RECIPIENT.** ``bi_subscription_deliveries`` is keyed by
  ``(subscription, recipient, scheduled_for)`` and carries that recipient's own
  authorization outcome (``member_ids`` / ``denied_members`` / ``reason``). The
  unique constraint on those three columns is the double-send guard: a tick that
  offers the same run twice cannot produce a second email, because the second
  insert is refused by the database rather than by a code path anyone can
  forget.
* **What was disclosed is recorded; what was disclosed is not KEPT.**
  ``disclosure_class`` and ``delivery_mode`` say whether an artifact was
  attached or a sign-in link was sent, and ``artifact_sha256`` /
  ``artifact_size_bytes`` / ``row_count`` prove which bytes were mailed — but
  the bytes themselves are never stored. A mailed pack is already outside the
  platform's control; a second copy in the database would widen that, not
  narrow it.
* **An absence is a positive statement.** Every "nothing happened" row carries a
  reason (``bi_alert_events.reason``, ``bi_subscription_deliveries.reason``), so
  a silent alert and a refused delivery read as decisions rather than as
  missing rows.

House rules shared with ``app/models/bi.py``: vocabularies are module tuples the
CHECK constraints derive from, so the model and the database cannot disagree;
``organization_id`` / ``bank_id`` are ``String(16)`` platform ids with the
composite foreign key to ``banks``; JSON columns are ``sa.JSON`` and never
JSONB, because the hermetic suite and the Playwright stack build this schema
with ``create_all`` on SQLite. Row-level security lives in the migration, like
every other tenant table, and the Postgres census
(``tests/db/test_tenant_rls_completeness.py``) is what finds a table that ships
without it.

**Why this is a separate module, and what that obliges.** ``app/models/bi.py``
declares the mart contract and its hand-written ``BI_TABLES`` tuple, which the
foundation migration and four Postgres parity suites iterate. These four tables
belong to the same ``bi_`` write family (``tests/architecture/
test_bi_plane_boundary.py`` derives what BI may write from the ``bi_``-prefixed
tables of ``Base.metadata``), so they must be admitted to ``BI_TABLES`` in the
same change that lands their migration, or the mart module's table list and the
metadata stop agreeing. The exact edit is named in
``.ai/bi_recon/t16_alerts_subs_report.md`` §"Required DDL".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

# --- vocabularies (the CHECK constraints derive from these) -------------------

#: Which side of the threshold is the breach. There is deliberately no
#: ``crosses`` or ``changes_by``: both need a second observation to mean
#: anything, and a threshold that depends on history is a measure, not a limit.
#: The measure's own ``favourable_direction`` is NOT used to pick the side — an
#: alert says what the bank wants to be told about, and a bank may legitimately
#: want to hear that a ratio has risen ABOVE a level it considers comfortable.
ALERT_DIRECTIONS: tuple[str, ...] = ("above", "below")

#: Where the line came from. ``stated`` is the bank's own number, held on the
#: row; ``governed_limit`` holds no number and is resolved per evaluation
#: through ``app/services/bi/limits.py``, which refuses rather than converting
#: when a register value and a measure are in different units. Nothing here ever
#: derives a threshold from data (``docs/bi.md`` §Phase 3; D-173).
ALERT_THRESHOLD_BASES: tuple[str, ...] = ("stated", "governed_limit")

#: ``bi_alert_events.state``. A row is written on a TRANSITION only, so a book
#: that stays in breach for a month produces one row and one notification
#: rather than thirty (D-176). ``not_evaluated`` is the third outcome and is the
#: reason this vocabulary is not a boolean: an alert the platform could not
#: evaluate — authority withdrawn, no governed limit, no figure for the date —
#: must read as unevaluated and never as "within limit".
ALERT_EVENT_STATES: tuple[str, ...] = ("breached", "cleared", "not_evaluated")

#: Why an evaluation produced no verdict. Stable strings: they reach the
#: operator surface and the owner's alert history, never a bank-facing figure.
ALERT_NOT_EVALUATED_REASONS: tuple[str, ...] = (
    # The owner's authority no longer admits the measure (or they are inactive).
    "authorization_revoked",
    # ``threshold_basis='governed_limit'`` and the resolver refused: no register
    # row, a unit it will not convert, a scope it cannot resolve.
    "no_governed_limit",
    # The mart holds no figure for the measure on this date.
    "no_figure",
    # The catalogue no longer publishes the measure this alert names.
    "unknown_member",
    # Compilation or execution refused (a timeout, a member the compiler will
    # not serve at this grain).
    "query_refused",
    # Phase 4: the owner's bindings admit only part of the institution, and an
    # institution-level threshold over a partial book is not the same question.
    "data_scope_unsupported",
)

#: How often a subscription runs. ``on_new_data`` is not a clock at all: it
#: fires from a completed mart build (``docs/bi.md`` §Phase 3), which is why the
#: four clock columns must all be NULL for it.
SUBSCRIPTION_CADENCES: tuple[str, ...] = ("daily", "weekly", "monthly", "on_new_data")

#: The artifact a subscription asks for. Parity with
#: ``app/services/bi/exports.EXPORT_FORMATS`` is asserted by
#: ``tests/models/test_bi_notification_models.py`` rather than imported: a model
#: module must not import a service.
SUBSCRIPTION_FORMATS: tuple[str, ...] = ("csv", "xlsx", "pdf")

#: What produced this delivery run.
DELIVERY_TRIGGERS: tuple[str, ...] = ("schedule", "new_data")

#: ``bi_subscription_deliveries.status``. ``pending`` is a CLAIM, committed
#: before anything is sent, so a crash mid-send leaves an unsent row rather than
#: a second email (D-180: at-most-once, deliberately). ``denied`` is this
#: recipient's own authorization outcome, ``no_data`` is a date the mart cannot
#: answer, ``failed`` is the relay.
DELIVERY_STATUSES: tuple[str, ...] = ("pending", "sent", "denied", "no_data", "failed")

#: Whether the content travelled or only a pointer to it. Decided by
#: ``app/services/bi/exports/policy.py`` — the classifier the interactive
#: exports already use — and never by a flag on a request: only an aggregated
#: (``summary``) artifact may be attached, and everything above that gets a
#: sign-in link (D-178).
DELIVERY_MODES: tuple[str, ...] = ("attachment", "link")

#: ``bi_subscription_deliveries.disclosure_class``: the export policy's own two
#: classes, mirrored here for the CHECK. Parity asserted, not imported.
DISCLOSURE_CLASSES: tuple[str, ...] = ("summary", "record_level")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _bank_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
    )


def _user_fk(column: str) -> ForeignKeyConstraint:
    """A tenant user, proven to be a user of THIS tenant.

    The composite key is the whole point: a plain ``users.id`` reference would
    let one tenant's subscription name another tenant's user as a recipient, and
    the delivery would then be authorized against a principal the owning
    organization has no relationship with.
    """
    return ForeignKeyConstraint([column, "organization_id"], ["users.id", "users.organization_id"])


class _TenantKeys:
    """The two platform-id columns every table here carries."""

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)


class BiAlert(UuidV4PrimaryKeyMixin, _TenantKeys, TimestampMixin, Base):
    """One threshold the bank has asked to be told about, on one measure.

    ``filters`` is a list of ``BiFilter`` payloads (the wire shape), so an alert
    can be about a slice — "loan balance where sector = X" — and the filter is
    walked by ``authorize_query`` exactly as a projection is. ``notify_user_ids``
    are the tenant users to tell; each one's own authority is evaluated before
    any figure reaches them, so this list is a distribution list and never a
    grant.
    """

    __tablename__ = "bi_alerts"
    __table_args__ = (
        CheckConstraint(
            f"direction IN ({_values(ALERT_DIRECTIONS)})", name="ck_bi_alerts_direction"
        ),
        CheckConstraint(
            f"threshold_basis IN ({_values(ALERT_THRESHOLD_BASES)})",
            name="ck_bi_alerts_threshold_basis",
        ),
        # The rule that makes "a threshold is passed in, never inferred"
        # unrepresentable otherwise: a stated basis carries a number, a governed
        # basis carries none and is resolved through the limit resolver.
        CheckConstraint(
            "(threshold_basis = 'stated' AND threshold IS NOT NULL) "
            "OR (threshold_basis = 'governed_limit' AND threshold IS NULL)",
            name="ck_bi_alerts_threshold_matches_basis",
        ),
        _bank_fk(),
        _user_fk("owner_user_id"),
        UniqueConstraint("id", "organization_id", name="uq_bi_alerts_id_org"),
        UniqueConstraint("organization_id", "bank_id", "name", name="uq_bi_alerts_bank_name"),
        Index("ix_bi_alerts_org_bank_active", "organization_id", "bank_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: A catalogue measure id; the column is the schema's own bound
    #: (``MEMBER_ID_MAX_LENGTH``).
    measure_id: Mapped[str] = mapped_column(String(120), nullable=False)
    filters: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    threshold_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The bank's own number, in the measure's own ``value_type``. NULL exactly
    #: when the basis is ``governed_limit``.
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    notify_user_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )


class BiAlertEvent(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """One transition of one alert, with the evidence for it.

    ``build_fingerprint`` is the mart build the evaluation read, and it is part
    of the unique key: re-evaluating the same data cannot write a second row,
    and therefore cannot send a second notification. That is the alert half of
    the idempotence property (the subscription half is the delivery key).
    """

    __tablename__ = "bi_alert_events"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({_values(ALERT_EVENT_STATES)})", name="ck_bi_alert_events_state"
        ),
        CheckConstraint(
            f"reason IS NULL OR reason IN ({_values(ALERT_NOT_EVALUATED_REASONS)})",
            name="ck_bi_alert_events_reason",
        ),
        # An unevaluated alert must say why, and a verdict must carry the figure
        # and the line it was judged against. Neither half may be silent.
        CheckConstraint(
            "(state = 'not_evaluated' AND reason IS NOT NULL "
            "AND observed_value IS NULL AND threshold_value IS NULL) "
            "OR (state <> 'not_evaluated' AND reason IS NULL "
            "AND observed_value IS NOT NULL AND threshold_value IS NOT NULL)",
            name="ck_bi_alert_events_verdict_is_complete",
        ),
        CheckConstraint("length(build_fingerprint) = 64", name="ck_bi_alert_events_fingerprint"),
        _bank_fk(),
        ForeignKeyConstraint(
            ["alert_id", "organization_id"], ["bi_alerts.id", "bi_alerts.organization_id"]
        ),
        UniqueConstraint(
            "organization_id",
            "alert_id",
            "as_of_date",
            "build_fingerprint",
            name="uq_bi_alert_events_alert_as_of_fingerprint",
        ),
        Index(
            "ix_bi_alert_events_org_alert_evaluated", "organization_id", "alert_id", "as_of_date"
        ),
        Index(
            "ix_bi_alert_events_org_bank_evaluated", "organization_id", "bank_id", "evaluated_at"
        ),
    )

    alert_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    build_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6), nullable=True)
    #: Copied from the alert, so the event says which authority drew the line
    #: even after the alert is edited.
    threshold_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The governed limit's own attribution (citation, or the board's approval
    #: evidence) as ``limits.GovernedLimit.source`` states it. NULL for a
    #: ``stated`` threshold, which needs no attribution beyond the bank itself.
    limit_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Member IDS only — never a filter value, never a row (the ``bi_query_log``
    #: rule).
    member_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: Who was told, and who was not because their OWN authority did not admit
    #: the measure. Both are recorded: a withheld notification is a decision.
    notified_user_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    withheld_user_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    builder_version: Mapped[int] = mapped_column(Integer, nullable=False)


class BiSubscription(UuidV4PrimaryKeyMixin, _TenantKeys, TimestampMixin, Base):
    """A recurring delivery of one query to a list of tenant users.

    The clock columns are read in the INSTITUTION's own time zone, resolved from
    the ``jurisdictions`` registry through ``banks.jurisdiction_code`` — never a
    literal, and never the server's. ``day_of_month`` is capped at 28 because a
    31st would silently skip most months, which reads as a broken subscription
    rather than as a calendar the product chose.
    """

    __tablename__ = "bi_subscriptions"
    __table_args__ = (
        CheckConstraint(
            f"cadence IN ({_values(SUBSCRIPTION_CADENCES)})", name="ck_bi_subscriptions_cadence"
        ),
        CheckConstraint(
            f"artifact_format IN ({_values(SUBSCRIPTION_FORMATS)})",
            name="ck_bi_subscriptions_artifact_format",
        ),
        CheckConstraint(
            "hour IS NULL OR (hour >= 0 AND hour <= 23)", name="ck_bi_subscriptions_hour"
        ),
        CheckConstraint(
            "minute IS NULL OR (minute >= 0 AND minute <= 59)", name="ck_bi_subscriptions_minute"
        ),
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 1 AND day_of_week <= 7)",
            name="ck_bi_subscriptions_day_of_week",
        ),
        CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 28)",
            name="ck_bi_subscriptions_day_of_month",
        ),
        # One CHECK per cadence, so an unschedulable row cannot exist: a clock
        # cadence carries exactly the fields it needs and no others, and
        # ``on_new_data`` carries none at all.
        CheckConstraint(
            "(cadence = 'on_new_data' AND hour IS NULL AND minute IS NULL "
            "AND day_of_week IS NULL AND day_of_month IS NULL) "
            "OR (cadence = 'daily' AND hour IS NOT NULL AND minute IS NOT NULL "
            "AND day_of_week IS NULL AND day_of_month IS NULL) "
            "OR (cadence = 'weekly' AND hour IS NOT NULL AND minute IS NOT NULL "
            "AND day_of_week IS NOT NULL AND day_of_month IS NULL) "
            "OR (cadence = 'monthly' AND hour IS NOT NULL AND minute IS NOT NULL "
            "AND day_of_week IS NULL AND day_of_month IS NOT NULL)",
            name="ck_bi_subscriptions_schedule_matches_cadence",
        ),
        _bank_fk(),
        _user_fk("owner_user_id"),
        UniqueConstraint("id", "organization_id", name="uq_bi_subscriptions_id_org"),
        UniqueConstraint(
            "organization_id", "bank_id", "name", name="uq_bi_subscriptions_bank_name"
        ),
        Index("ix_bi_subscriptions_org_active_cadence", "organization_id", "is_active", "cadence"),
        Index("ix_bi_subscriptions_org_bank_active", "organization_id", "bank_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: A ``BiQuery`` payload as authored. The reporting date is REBOUND at
    #: delivery time (D-179): a date frozen at authoring would mail the same
    #: month for ever.
    query: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    artifact_format: Mapped[str] = mapped_column(String(8), nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    hour: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    minute: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: ISO-8601 weekday, Monday = 1.
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    #: Tenant user ids. Never a free-text address: an arbitrary mailbox is
    #: outside every authorization decision the platform can make, so a
    #: recipient must be a principal the evaluator can answer about.
    recipient_user_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true"), nullable=False
    )


class BiSubscriptionDelivery(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """ONE recipient's copy of ONE run — the row that makes a resend impossible.

    ``scheduled_for`` is the exact minute the run was due (for ``on_new_data``,
    the build's completion minute), and the unique key over
    ``(organization, subscription, recipient, scheduled_for)`` is what a second
    tick collides with. The status starts at ``pending`` and is COMMITTED before
    anything is sent, so the failure mode of a crashed worker is an unsent
    delivery that says so, never a duplicate that nobody can recall.
    """

    __tablename__ = "bi_subscription_deliveries"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_values(DELIVERY_STATUSES)})", name="ck_bi_subscription_deliveries_status"
        ),
        CheckConstraint(
            f"trigger IN ({_values(DELIVERY_TRIGGERS)})",
            name="ck_bi_subscription_deliveries_trigger",
        ),
        CheckConstraint(
            f"disclosure_class IS NULL OR disclosure_class IN ({_values(DISCLOSURE_CLASSES)})",
            name="ck_bi_subscription_deliveries_disclosure_class",
        ),
        CheckConstraint(
            f"delivery_mode IS NULL OR delivery_mode IN ({_values(DELIVERY_MODES)})",
            name="ck_bi_subscription_deliveries_delivery_mode",
        ),
        CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL",
            name="ck_bi_subscription_deliveries_sent_at",
        ),
        CheckConstraint(
            "sent_at IS NULL OR sent_at >= created_at",
            name="ck_bi_subscription_deliveries_sent_after_created",
        ),
        # An attached artifact must account for its bytes; a link must not claim
        # any. This is what stops "sent as a link" from covering a file that in
        # fact left the building.
        CheckConstraint(
            "(delivery_mode = 'attachment' AND artifact_sha256 IS NOT NULL "
            "AND artifact_size_bytes IS NOT NULL) "
            "OR (delivery_mode <> 'attachment' AND artifact_sha256 IS NULL "
            "AND artifact_size_bytes IS NULL) "
            "OR delivery_mode IS NULL",
            name="ck_bi_subscription_deliveries_artifact_accounted",
        ),
        CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_bi_subscription_deliveries_size",
        ),
        CheckConstraint(
            "row_count IS NULL OR row_count >= 0", name="ck_bi_subscription_deliveries_row_count"
        ),
        _bank_fk(),
        ForeignKeyConstraint(
            ["subscription_id", "organization_id"],
            ["bi_subscriptions.id", "bi_subscriptions.organization_id"],
        ),
        _user_fk("recipient_user_id"),
        UniqueConstraint(
            "organization_id",
            "subscription_id",
            "recipient_user_id",
            "scheduled_for",
            name="uq_bi_subscription_deliveries_run_recipient",
        ),
        Index(
            "ix_bi_subscription_deliveries_org_subscription_scheduled",
            "organization_id",
            "subscription_id",
            "scheduled_for",
        ),
        Index(
            "ix_bi_subscription_deliveries_org_bank_created",
            "organization_id",
            "bank_id",
            "created_at",
        ),
    )

    subscription_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    recipient_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The exact minute the run was due. Part of the unique key, so it is the
    #: identity of the run and not merely a timestamp.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The reporting date delivered. NULL while the run has not resolved one
    #: (a refusal before the mart was read).
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    disclosure_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    delivery_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    artifact_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    #: Which bytes were mailed — not the bytes. See the module docstring.
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    denied_members: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    #: Why this recipient got nothing, or less than the whole thing. A stable
    #: string, never a figure and never a filter value.
    reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    build_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    builder_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


#: Every table this module declares, in creation order. The migration and the
#: hermetic model test both read it, so a table added to one cannot be forgotten
#: by the other — the ``app/models/bi.py::BI_TABLES`` idiom.
BI_NOTIFICATION_TABLES: tuple[str, ...] = (
    BiAlert.__tablename__,
    BiAlertEvent.__tablename__,
    BiSubscription.__tablename__,
    BiSubscriptionDelivery.__tablename__,
)

__all__ = [
    "ALERT_DIRECTIONS",
    "ALERT_EVENT_STATES",
    "ALERT_NOT_EVALUATED_REASONS",
    "ALERT_THRESHOLD_BASES",
    "BI_NOTIFICATION_TABLES",
    "DELIVERY_MODES",
    "DELIVERY_STATUSES",
    "DELIVERY_TRIGGERS",
    "DISCLOSURE_CLASSES",
    "SUBSCRIPTION_CADENCES",
    "SUBSCRIPTION_FORMATS",
    "BiAlert",
    "BiAlertEvent",
    "BiSubscription",
    "BiSubscriptionDelivery",
]
