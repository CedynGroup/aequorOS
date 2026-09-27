"""Saved dashboards and calculated measures — the BI content a TENANT authors.

``app/models/bi.py`` holds what the BUILDER writes: marts, dimensions, control
tables. This module holds the other half of the ``bi_*`` plane — the four tables
a user of the bank fills in. Nothing here is a figure: a saved dashboard is a
set of QUESTIONS and a calculated measure is a FORMULA, so every row is authored
text plus catalogue member ids, and not one column holds a number read from the
book.

That distinction is the whole security design of these tables, and it is worth
stating before the columns:

**Sharing never shares data.** A ``bi_dashboards`` row grants REACHABILITY —
whether a second person may open the document. It grants no authority over the
figures inside it. Every widget is re-authorized for whoever opens it, through
the same ``authorize_query`` decision the read routes use
(``app/services/bi/content.py``), so a viewer who lacks a member is served the
refusal marker and never the owner's number. A share row that carried authority
would be a cross-user breach inside one tenant, which is why the share tables
store no permission, no bundle and no scope at all: a share is a name, and the
authority is always the reader's own bindings.

**Only the owner may edit or delete.** Not an account administrator, not an Org
Owner. ``owner_user_id`` is the whole rule and the service compares against it
directly; there is deliberately no "administer someone's dashboard" path,
because the document is that person's working note, not a governed institution
record.

**A calculated measure's approval is frozen at the expression it approved.**
``bi_measures`` carries the proposal and the approval as separate, complete
sentences (who, when, why, and the digest of the exact formula), and
``ck_bi_measures_promotion_separation`` refuses a row whose approver is its
proposer — so maker-checker for a promotion holds in the DATABASE even if a
service path is ever wrong about it. An edit returns the measure to
``personal``: an approver approved a formula, not a name.

House rules shared with ``app/models/bi.py``: ``organization_id`` and
``bank_id`` are ``String(16)`` platform ids with the composite foreign key to
``banks``; JSON columns are ``sa.JSON``, never JSONB, because the hermetic suite
and the Playwright stack build this schema with ``create_all`` on SQLite; CHECK
constraints derive from the tuples below so the model and the database can never
disagree about a vocabulary; and row-level security lives in the migration, like
every other tenant table. Nothing here is partitioned — these tables are small
by construction (a person's dashboards), and the one append-only table among
them is guarded the way ``attestation_signatures`` is rather than the way
``bi_query_log`` is (see :class:`BiDashboardVersion`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# --- vocabularies (the CHECK constraints derive from these) -------------------

#: Who may reach a saved dashboard. Four distinct reachability rules, and none
#: of them is an authority over a figure:
#:
#: * ``private`` — the owner alone;
#: * ``users`` — the owner plus the named ``bi_dashboard_shares`` grantees;
#: * ``role`` — the owner plus every identity holding an effective
#:   ``authorization_bindings`` row of :attr:`BiDashboard.visibility_role` whose
#:   institution coverage includes this institution. It is NOT the scalar
#:   ``users.role`` column: authority is the stored binding everywhere else in
#:   this platform and reachability reads the same rows;
#: * ``org`` — every identity of THIS organization with institution coverage of
#:   this institution. "Organization" is the tenant, so ``org`` can never cross a
#:   tenant boundary: the row itself is tenant-scoped and RLS-forced, and the
#:   reachability query is organization-filtered besides.
DASHBOARD_VISIBILITIES: tuple[str, ...] = ("private", "users", "role", "org")

#: How a surface badges where a dashboard's authority comes from. Three levels
#: exist in the product, and only two of them can ever be a ROW here:
#: "Platform-certified" belongs to a certified pack, which is a FILE in this
#: build (``app/domain/bi/packs``) and is never edited in place — an edit
#: produces a copy, and the copy is the copier's. So a stored dashboard is
#: ``personal`` or, once a bank has a governance path for certifying one of its
#: own, ``bank_certified``.
DASHBOARD_BADGES: tuple[str, ...] = ("platform_certified", "bank_certified", "personal")

#: The badges a ``bi_dashboards`` row may hold. ``platform_certified`` is absent
#: by construction, not by omission: a pack is a file, and a row that claimed to
#: be platform-certified would be a tenant asserting the platform's authority
#: over its own content.
STORED_DASHBOARD_BADGES: tuple[str, ...] = ("bank_certified", "personal")

#: A calculated measure's promotion state. ``personal`` is one person's formula;
#: ``proposed`` has a complete maker sentence awaiting a checker; ``bank_certified``
#: has both, and its :attr:`BiMeasure.approved_expression` is the formula that was
#: approved — frozen there so a later edit cannot inherit the approval.
MEASURE_STATES: tuple[str, ...] = ("personal", "proposed", "bank_certified")

#: What a calculated measure's number IS, mirroring
#: ``app/domain/bi/catalogue/members.py::NUMERIC_VALUE_TYPES`` without importing
#: it (``app.models`` stays free of ``app.domain.bi``, the rule
#: ``app/models/bi.py`` already follows for the target register). A measure is a
#: figure, so ``text``/``date``/``flag`` — which describe a dimension's values —
#: are not here. ``tests/models/test_bi_content_models.py`` pins the parity in
#: both directions.
MEASURE_VALUE_TYPES: tuple[str, ...] = (
    "amount",
    "pct",
    "fraction",
    "index",
    "duration_years",
    "count",
)

#: Which direction is good for the institution, mirroring
#: ``members.FavourableDirection``. A calculated measure has to declare one or a
#: client cannot colour its movement, and guessing would tell a banker that a
#: rising cost ratio is good news.
MEASURE_FAVOURABLE_DIRECTIONS: tuple[str, ...] = (
    "higher_better",
    "lower_better",
    "magnitude_lower_better",
    "neutral",
)

#: Longest formula a row may hold, mirroring ``app/domain/bi/expr.py``'s own cap
#: (``MAX_EXPRESSION_LENGTH``). Stated as a column length so the database
#: refuses what the parser would refuse, and pinned to the parser by the model
#: test rather than being a second opinion about how long a formula may be.
MEASURE_EXPRESSION_MAX_LENGTH = 2_000

#: Widest reasonable authored strings. A title is one line of a canvas header; a
#: change note is one line of history; a reason is a governance sentence.
DASHBOARD_TITLE_MAX_LENGTH = 120
DASHBOARD_DESCRIPTION_MAX_LENGTH = 400
DASHBOARD_CHANGE_NOTE_MAX_LENGTH = 280
MEASURE_KEY_MAX_LENGTH = 64
MEASURE_LABEL_MAX_LENGTH = 120
MEASURE_REASON_MAX_LENGTH = 400


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _bank_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["bank_id", "organization_id"],
        ["banks.id", "banks.organization_id"],
        name=f"fk_{table}_bank",
    )


def _user_fk(table: str, column: str, *, label: str) -> ForeignKeyConstraint:
    """A tenant-composite user reference: a row cannot name another tenant's user."""

    return ForeignKeyConstraint(
        [column, "organization_id"],
        ["users.id", "users.organization_id"],
        name=f"fk_{table}_{label}",
    )


def _dashboard_fk(table: str) -> ForeignKeyConstraint:
    """A child of one dashboard OF THE SAME TENANT, deleted with it.

    ``ON DELETE CASCADE`` is what makes "only the owner may delete" a complete
    act: the versions and the shares go with the document. It is also why the
    versions table blocks UPDATE but not DELETE — see :class:`BiDashboardVersion`.
    """

    return ForeignKeyConstraint(
        ["dashboard_id", "organization_id"],
        ["bi_dashboards.id", "bi_dashboards.organization_id"],
        ondelete="CASCADE",
        name=f"fk_{table}_dashboard",
    )


class _TenantKeys:
    """The tenant and institution every row of this module belongs to."""

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)


class BiDashboard(UuidV4PrimaryKeyMixin, _TenantKeys, TimestampMixin, Base):
    """One saved dashboard: who owns it, who may reach it, and which version is live.

    The CANVAS is not here. Every layout this dashboard has ever had is a
    :class:`BiDashboardVersion` row, and :attr:`current_version` names the live
    one — so there is exactly one copy of any given spec and a history that
    cannot be rewritten by an UPDATE to a "current spec" column. ``title`` and
    ``description`` are duplicated onto each version for the same reason: the
    history has to be complete on its own, or a restored version would come back
    with today's name.
    """

    __tablename__ = "bi_dashboards"
    __table_args__ = (
        CheckConstraint(
            f"visibility IN ({_values(DASHBOARD_VISIBILITIES)})",
            name="ck_bi_dashboards_visibility",
        ),
        CheckConstraint(
            f"badge IN ({_values(STORED_DASHBOARD_BADGES)})", name="ck_bi_dashboards_badge"
        ),
        # A role visibility without a role would be reachable by nobody but the
        # owner while claiming to be shared; a role on any other visibility is a
        # rule nothing reads.
        CheckConstraint(
            "(visibility = 'role') = (visibility_role IS NOT NULL)",
            name="ck_bi_dashboards_visibility_role",
        ),
        CheckConstraint("current_version >= 1", name="ck_bi_dashboards_current_version"),
        _bank_fk("bi_dashboards"),
        _user_fk("bi_dashboards", "owner_user_id", label="owner"),
        # The composite target every child's tenant-scoped foreign key needs.
        UniqueConstraint("id", "organization_id", name="uq_bi_dashboards_id_organization"),
        Index("ix_bi_dashboards_org_bank_owner", "organization_id", "bank_id", "owner_user_id"),
        Index("ix_bi_dashboards_org_bank_visibility", "organization_id", "bank_id", "visibility"),
    )

    #: The one identity that may edit, delete, share or rename this dashboard.
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(DASHBOARD_TITLE_MAX_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        String(DASHBOARD_DESCRIPTION_MAX_LENGTH), nullable=False, server_default=sql_text("''")
    )
    visibility: Mapped[str] = mapped_column(String(8), nullable=False)
    #: The ``authorization_bindings.role_bundle`` a ``role`` visibility names.
    #: Held as text rather than as an enum column so a new bundle needs no
    #: migration here; the service validates it against ``RoleBundle``.
    visibility_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    badge: Mapped[str] = mapped_column(String(20), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The certified pack this dashboard was copied from, when it was. A pack id
    #: is a closed platform catalogue key (``app.domain.bi.packs``), never a
    #: tenant object, so it is carried as its key and not as a foreign key.
    source_pack: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BiDashboardVersion(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """One layout a dashboard has had, appended and never altered.

    Append-only in the ``attestation_signatures`` tier rather than the
    ``audit_events`` tier (attestation spec §9 D1): UPDATE is blocked three ways
    — a row trigger, the revoked privilege and a RESTRICTIVE policy — while
    DELETE stays reachable through the parent's ``ON DELETE CASCADE``, because
    deleting a dashboard has to be a complete act and the owner is entitled to
    delete their own document. What the guard buys is the property the spec
    asks for: a dashboard's history cannot be REWRITTEN. A version can only ever
    be added, and the only way a version disappears is with the dashboard it
    belongs to.
    """

    __tablename__ = "bi_dashboard_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_bi_dashboard_versions_version"),
        _bank_fk("bi_dashboard_versions"),
        _dashboard_fk("bi_dashboard_versions"),
        _user_fk("bi_dashboard_versions", "created_by_user_id", label="author"),
        UniqueConstraint(
            "organization_id",
            "dashboard_id",
            "version",
            name="uq_bi_dashboard_versions_dashboard_version",
        ),
        Index(
            "ix_bi_dashboard_versions_org_dashboard_version",
            "organization_id",
            "dashboard_id",
            "version",
        ),
    )

    dashboard_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(DASHBOARD_TITLE_MAX_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        String(DASHBOARD_DESCRIPTION_MAX_LENGTH), nullable=False, server_default=sql_text("''")
    )
    #: The canvas: ``{"widgets": [...], "layout": [...]}`` validated as
    #: ``app/schemas/bi_content.py::BiDashboardSpec``, whose widgets are the pack
    #: schema's own ``BiPackWidget``. Stored as the model dumped it, so a copy of
    #: a certified pack is literally a copy of that pack's widgets.
    spec: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    #: Value-based digest of ``spec`` (``app/services/bi/content.py``), so two
    #: versions can be compared without re-reading the JSON and an unchanged
    #: save can be recognised as one.
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str] = mapped_column(
        String(DASHBOARD_CHANGE_NOTE_MAX_LENGTH), nullable=False, server_default=sql_text("''")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BiDashboardShare(UuidV4PrimaryKeyMixin, _TenantKeys, Base):
    """One named identity a ``users``-visibility dashboard is reachable by.

    It carries no permission, no bundle and no scope, and that absence is the
    point: this row says "this person may open this document", never "this
    person may see what is in it". The figures are decided by the reader's own
    bindings, every time the document is opened.
    """

    __tablename__ = "bi_dashboard_shares"
    __table_args__ = (
        _bank_fk("bi_dashboard_shares"),
        _dashboard_fk("bi_dashboard_shares"),
        _user_fk("bi_dashboard_shares", "grantee_user_id", label="grantee"),
        UniqueConstraint(
            "organization_id",
            "dashboard_id",
            "grantee_user_id",
            name="uq_bi_dashboard_shares_dashboard_grantee",
        ),
        Index(
            "ix_bi_dashboard_shares_org_grantee", "organization_id", "grantee_user_id", "bank_id"
        ),
    )

    dashboard_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    grantee_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    #: The owner who shared it. Kept beside the grantee so the audit trail on the
    #: row itself answers "who let them in" without a join to ``audit_events``.
    shared_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BiMeasure(UuidV4PrimaryKeyMixin, _TenantKeys, TimestampMixin, Base):
    """One calculated measure: a formula over catalogue figures, and its promotion.

    ``expression`` is the source text a person wrote. It is parsed on the SERVER
    (``app/domain/bi/expr.py``) every time it matters and the ids it names are
    never trusted from a client: ``referenced_members`` here is a recorded COPY
    of the server's own walk, kept so the row is auditable and listable, and the
    authorization walk re-derives the ids from ``expression`` rather than reading
    it — a cached list is a thing that can go stale, and a stale list on the
    authorization path is a figure read without a binding.

    The promotion is two complete sentences. The maker's is
    ``proposed_by_user_id`` / ``proposed_at`` / ``proposed_expression_digest`` /
    ``proposal_reason``; the checker's is ``approved_by_user_id`` /
    ``approved_at`` / ``approved_expression`` / ``approved_expression_digest`` /
    ``approval_reason``. ``approved_expression`` holds the TEXT, not just the
    digest, because what a checker certified for the whole institution must be
    readable afterwards without reconstructing it from an audit event.
    """

    __tablename__ = "bi_measures"
    __table_args__ = (
        CheckConstraint(f"state IN ({_values(MEASURE_STATES)})", name="ck_bi_measures_state"),
        CheckConstraint(
            f"value_type IN ({_values(MEASURE_VALUE_TYPES)})", name="ck_bi_measures_value_type"
        ),
        CheckConstraint(
            f"favourable_direction IN ({_values(MEASURE_FAVOURABLE_DIRECTIONS)})",
            name="ck_bi_measures_favourable_direction",
        ),
        # A state past ``personal`` must carry the whole maker sentence. A
        # half-recorded proposal is a promotion nobody can be held to.
        CheckConstraint(
            "state = 'personal' OR ("
            "proposed_by_user_id IS NOT NULL AND proposed_at IS NOT NULL "
            "AND proposed_expression_digest IS NOT NULL AND proposal_reason IS NOT NULL)",
            name="ck_bi_measures_proposal_complete",
        ),
        # The approval and the certified state are one fact, both ways: a
        # certified row without an approver would be a self-certification, and an
        # approval on a row that is not certified would be an approval of nothing.
        CheckConstraint(
            "(state = 'bank_certified' AND approved_by_user_id IS NOT NULL "
            "AND approved_at IS NOT NULL AND approved_expression IS NOT NULL "
            "AND approved_expression_digest IS NOT NULL AND approval_reason IS NOT NULL) "
            "OR (state <> 'bank_certified' AND approved_by_user_id IS NULL "
            "AND approved_at IS NULL AND approved_expression IS NULL "
            "AND approved_expression_digest IS NULL AND approval_reason IS NULL)",
            name="ck_bi_measures_approval_complete",
        ),
        # Maker-checker in the DATABASE. The service refuses this through the
        # platform's own separation-of-duties machinery and audits the decision,
        # but a promotion is the act that makes one person's formula the
        # institution's, so the constraint is stated where no code path can be
        # wrong about it.
        CheckConstraint(
            "approved_by_user_id IS NULL OR proposed_by_user_id IS NULL "
            "OR approved_by_user_id <> proposed_by_user_id",
            name="ck_bi_measures_promotion_separation",
        ),
        # The approval is frozen at a formula: if it is the CURRENT formula the
        # two digests agree, and an edit is what breaks that — which is why an
        # edit must return the row to ``personal`` rather than carry the approval
        # forward. The constraint makes the inconsistent row unstorable.
        CheckConstraint(
            "state <> 'bank_certified' OR approved_expression_digest = expression_digest",
            name="ck_bi_measures_certified_matches_expression",
        ),
        _bank_fk("bi_measures"),
        _user_fk("bi_measures", "owner_user_id", label="owner"),
        UniqueConstraint(
            "organization_id", "bank_id", "measure_key", name="uq_bi_measures_bank_key"
        ),
        Index("ix_bi_measures_org_bank_state", "organization_id", "bank_id", "state"),
        Index("ix_bi_measures_org_bank_owner", "organization_id", "bank_id", "owner_user_id"),
    )

    #: The id this measure is referenced by, in the catalogue's own lexical shape
    #: and namespaced so it can never collide with a catalogue member (the
    #: service refuses a key the catalogue already knows).
    measure_key: Mapped[str] = mapped_column(String(MEASURE_KEY_MAX_LENGTH), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(MEASURE_LABEL_MAX_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(
        String(DASHBOARD_DESCRIPTION_MAX_LENGTH), nullable=False, server_default=sql_text("''")
    )
    expression: Mapped[str] = mapped_column(String(MEASURE_EXPRESSION_MAX_LENGTH), nullable=False)
    expression_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The server's own ``expr.referenced_members`` walk, recorded. Never the
    #: authority for a read (see the class docstring).
    referenced_members: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    favourable_direction: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)

    proposed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposed_expression_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_reason: Mapped[str | None] = mapped_column(
        String(MEASURE_REASON_MAX_LENGTH), nullable=True
    )

    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The formula that was certified, verbatim. Frozen here at approval.
    approved_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_expression_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(
        String(MEASURE_REASON_MAX_LENGTH), nullable=True
    )


#: The tenant-authored ``bi_*`` tables, in creation order (parents first). The
#: migration and the hermetic model test both read this, so a table added to one
#: cannot be forgotten by the other — the same contract ``app/models/bi.py``'s
#: ``BI_TABLES`` holds for the builder's tables.
BI_CONTENT_TABLES: tuple[str, ...] = (
    BiDashboard.__tablename__,
    BiDashboardVersion.__tablename__,
    BiDashboardShare.__tablename__,
    BiMeasure.__tablename__,
)

#: The one table among them that is append-only, and the tier it is guarded at:
#: UPDATE blocked, DELETE reachable through the parent's CASCADE.
BI_CONTENT_UNALTERABLE_TABLES: tuple[str, ...] = (BiDashboardVersion.__tablename__,)
