"""Wire models for saved dashboards and calculated measures.

Everything a client may SEND about a dashboard is a set of questions, and
everything it may send about a calculated measure is one string of formula text.
Neither carries a figure, a table, a column, a route or a permission:

* a dashboard's canvas is ``BiDashboardSpec``, whose widgets are the pack
  schema's own :class:`~app.schemas.bi.BiPackWidget` — the spec says a saved
  dashboard uses the same shape as a certified pack, so an editable copy of a
  pack is literally a copy of its widgets, and there is exactly one widget shape
  in this platform;
* a measure's formula is text that only the SERVER parses
  (``app/domain/bi/expr.py``). A client never sends an AST, a member list, a
  sensitivity or a module: every one of those is derived here from the text, and
  a client-supplied member list would be an authorization walk taking its input
  from the thing it is meant to police.

What a reader is SERVED is the pack surface's own resolved shape
(:class:`~app.schemas.bi.BiPackWidgetRead`), for the same reason: a refused
widget must be a model that cannot carry a title, a caption, a measure or a
figure, and that model already exists. ``BiDashboardRead`` is therefore
``BiPackRead`` with the fields a SAVED dashboard has instead of a pack's
(an owner, a visibility, a version, a badge) and without the ones it does not
(a pack audience, a file version).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.identity.public import SodDecisionRead
from app.models.bi_content import (
    DASHBOARD_CHANGE_NOTE_MAX_LENGTH,
    DASHBOARD_DESCRIPTION_MAX_LENGTH,
    DASHBOARD_TITLE_MAX_LENGTH,
    MEASURE_EXPRESSION_MAX_LENGTH,
    MEASURE_KEY_MAX_LENGTH,
    MEASURE_LABEL_MAX_LENGTH,
    MEASURE_REASON_MAX_LENGTH,
)
from app.schemas.bi import (
    BI_PACK_MAX_WIDGETS,
    BiClosedModel,
    BiLayoutItem,
    BiPackAccess,
    BiPackWidget,
    BiPackWidgetRead,
)

#: The four reachability rules, mirroring
#: ``app/models/bi_content.py::DASHBOARD_VISIBILITIES``.
BiDashboardVisibility = Literal["private", "users", "role", "org"]

#: The three certification levels a surface may badge. ``platform_certified`` is
#: a certified pack (a file in this build); a SAVED dashboard is never one.
BiCertificationBadge = Literal["platform_certified", "bank_certified", "personal"]

#: A calculated measure's promotion state.
BiMeasureState = Literal["personal", "proposed", "bank_certified"]

#: What a calculated measure's number is. Measures only: ``text`` / ``date`` /
#: ``flag`` describe a dimension's values, not a figure.
BiMeasureValueType = Literal["amount", "pct", "fraction", "index", "duration_years", "count"]

#: Which direction is good for the institution.
BiMeasureFavourableDirection = Literal[
    "higher_better", "lower_better", "magnitude_lower_better", "neutral"
]

#: The lexical shape of a calculated measure's id: the catalogue's own
#: snake_case-with-dots shape (``app/domain/bi/expr.py`` accepts exactly this
#: inside a ``[m:…]`` reference), so a measure can be named in a formula. The
#: service additionally refuses a key the catalogue already defines.
MEASURE_KEY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"


class BiDashboardSpec(BiClosedModel):
    """A saved dashboard's canvas: its widgets and where they sit.

    The same two lists a certified pack carries, validated the same way, and
    deliberately WITHOUT a pack's file metadata: a person's dashboard has no
    audience and no semantic version, and carrying those would make a tenant row
    claim to be a published pack.
    """

    widgets: list[BiPackWidget] = Field(min_length=1, max_length=BI_PACK_MAX_WIDGETS)
    layout: list[BiLayoutItem] = Field(min_length=1, max_length=BI_PACK_MAX_WIDGETS)

    @model_validator(mode="after")
    def _every_widget_is_positioned_exactly_once(self) -> BiDashboardSpec:
        widget_ids = [widget.id for widget in self.widgets]
        if len(widget_ids) != len(set(widget_ids)):
            raise ValueError("widget ids must be unique within a dashboard")
        positioned = [item.i for item in self.layout]
        if len(positioned) != len(set(positioned)):
            raise ValueError("a widget may be positioned only once")
        if set(positioned) != set(widget_ids):
            missing = sorted(set(widget_ids) - set(positioned))
            unknown = sorted(set(positioned) - set(widget_ids))
            raise ValueError(
                f"the layout must position every widget and nothing else; "
                f"unpositioned: {missing}, unknown: {unknown}"
            )
        return self


class BiDashboardCreateRequest(BiClosedModel):
    """Save a new dashboard: either an authored canvas or a copy of a pack.

    A copy names the pack and nothing else about its content: the widgets come
    from the file the server holds, never from the request, so a client cannot
    pass off an edited canvas as a copy of a certified dashboard.
    """

    title: str = Field(min_length=1, max_length=DASHBOARD_TITLE_MAX_LENGTH)
    description: str = Field(default="", max_length=DASHBOARD_DESCRIPTION_MAX_LENGTH)
    visibility: BiDashboardVisibility = "private"
    #: Required when ``visibility`` is ``role``, refused otherwise.
    visibility_role: str | None = Field(default=None, max_length=32)
    spec: BiDashboardSpec | None = None
    #: A certified pack key to copy the widgets and layout from.
    from_pack: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _one_source_and_a_role_only_for_role_visibility(self) -> BiDashboardCreateRequest:
        if (self.spec is None) == (self.from_pack is None):
            raise ValueError("name either a canvas of your own or a certified pack to copy")
        if (self.visibility == "role") != (self.visibility_role is not None):
            raise ValueError("a role visibility names exactly one role, and no other one does")
        return self


class BiDashboardUpdateRequest(BiClosedModel):
    """Replace a dashboard's canvas and its reachability, appending a version.

    Complete rather than partial: a canvas is edited as a whole, and a PATCH of
    one widget would leave the append-only history holding fragments that no
    version ever rendered.
    """

    title: str = Field(min_length=1, max_length=DASHBOARD_TITLE_MAX_LENGTH)
    description: str = Field(default="", max_length=DASHBOARD_DESCRIPTION_MAX_LENGTH)
    visibility: BiDashboardVisibility
    visibility_role: str | None = Field(default=None, max_length=32)
    spec: BiDashboardSpec
    #: One line for the history. Optional: a moved widget needs no justification,
    #: and demanding one would make the note a formality rather than a record.
    change_note: str = Field(default="", max_length=DASHBOARD_CHANGE_NOTE_MAX_LENGTH)

    @model_validator(mode="after")
    def _a_role_belongs_only_to_role_visibility(self) -> BiDashboardUpdateRequest:
        if (self.visibility == "role") != (self.visibility_role is not None):
            raise ValueError("a role visibility names exactly one role, and no other one does")
        return self


class BiDashboardShareRequest(BiClosedModel):
    """The complete set of identities a ``users``-visibility dashboard reaches.

    Replacing the whole set rather than adding one at a time makes revocation an
    ordinary save: an identity absent from the list loses reachability in the
    same transaction the rest keep it.
    """

    user_ids: list[UUID] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _no_repeats(self) -> BiDashboardShareRequest:
        if len(self.user_ids) != len(set(self.user_ids)):
            raise ValueError("each identity may be named once")
        return self


class BiDashboardShareRead(BiClosedModel):
    """One identity a dashboard is reachable by."""

    user_id: UUID
    display_name: str | None
    email: str
    shared_at: datetime


class BiDashboardShareListRead(BiClosedModel):
    shares: list[BiDashboardShareRead]


class BiDashboardSummaryRead(BiClosedModel):
    """One dashboard in a list: enough to open it, and no figure at all.

    The widgets are absent on purpose. A list is read by everyone the dashboard
    reaches, and the member ids a widget names are only shown once they have been
    authorized for that reader — which happens when the dashboard is opened.
    """

    id: UUID
    title: str
    description: str
    owner_user_id: UUID
    owner_display_name: str | None
    #: Whether the caller is the owner, and therefore the only identity that may
    #: edit, delete, rename or re-share it.
    owned_by_caller: bool
    visibility: BiDashboardVisibility
    visibility_role: str | None
    badge: BiCertificationBadge
    version: int
    widget_count: int
    source_pack: str | None
    created_at: datetime
    updated_at: datetime


class BiDashboardListRead(BiClosedModel):
    dashboards: list[BiDashboardSummaryRead]


class BiDashboardRead(BiClosedModel):
    """One saved dashboard resolved for one reader and one reporting date.

    Every widget was authorized for THIS reader before it was put in this
    response; a widget they lack a member for is ``restricted`` and carries its
    id and its place on the canvas and nothing else. The owner's own authority
    never enters this decision.
    """

    id: UUID
    title: str
    description: str
    owner_user_id: UUID
    owner_display_name: str | None
    owned_by_caller: bool
    visibility: BiDashboardVisibility
    visibility_role: str | None
    badge: BiCertificationBadge
    version: int
    source_pack: str | None
    #: The reporting date every query in this response was resolved for.
    as_of: date
    access: BiPackAccess
    #: Production copy stating what the reader is looking at, shown as-is.
    message: str
    widgets: list[BiPackWidgetRead]
    #: How many widgets were refused, and how many could have shown a figure. A
    #: count is not a disclosure; a name would be.
    restricted_widgets: int = 0
    readable_widgets: int = 0
    catalogue_version: str
    created_at: datetime
    updated_at: datetime


class BiDashboardVersionRead(BiClosedModel):
    """One entry of a dashboard's history.

    Metadata only: the canvas of an old version is not served here, because a
    reader's access is decided against the CURRENT canvas and an old one would
    have to be re-authorized widget by widget to be shown safely. What the
    history answers is "who changed this, when, and what did they say about it".
    """

    version: int
    title: str
    description: str
    change_note: str
    widget_count: int
    spec_digest: str
    created_by_user_id: UUID
    created_by_display_name: str | None
    created_at: datetime
    #: Whether this is the version being rendered today.
    current: bool


class BiDashboardVersionListRead(BiClosedModel):
    dashboard_id: UUID
    versions: list[BiDashboardVersionRead]


# --- calculated measures -----------------------------------------------------------------


class BiMeasureCreateRequest(BiClosedModel):
    """A new personal calculated measure.

    The request carries no module, no sensitivity and no member list: all three
    are derived on the server from ``expression``, which is the only thing about
    this measure a client is permitted to state.
    """

    measure_key: str = Field(
        min_length=1, max_length=MEASURE_KEY_MAX_LENGTH, pattern=MEASURE_KEY_PATTERN
    )
    label: str = Field(min_length=1, max_length=MEASURE_LABEL_MAX_LENGTH)
    description: str = Field(default="", max_length=DASHBOARD_DESCRIPTION_MAX_LENGTH)
    expression: str = Field(min_length=1, max_length=MEASURE_EXPRESSION_MAX_LENGTH)
    value_type: BiMeasureValueType
    favourable_direction: BiMeasureFavourableDirection = "neutral"


class BiMeasureUpdateRequest(BiClosedModel):
    """Edit a calculated measure. A changed formula drops any certification."""

    label: str = Field(min_length=1, max_length=MEASURE_LABEL_MAX_LENGTH)
    description: str = Field(default="", max_length=DASHBOARD_DESCRIPTION_MAX_LENGTH)
    expression: str = Field(min_length=1, max_length=MEASURE_EXPRESSION_MAX_LENGTH)
    value_type: BiMeasureValueType
    favourable_direction: BiMeasureFavourableDirection = "neutral"


class BiMeasureProposalRequest(BiClosedModel):
    """Propose a personal measure for bank certification (the maker's sentence)."""

    reason: str = Field(min_length=1, max_length=MEASURE_REASON_MAX_LENGTH)


class BiMeasureDecisionRequest(BiClosedModel):
    """Approve or reject a proposed measure (the checker's sentence).

    ``expression_digest`` is what the checker read. The server refuses the
    decision if the formula has moved since — an approver approves a specific
    formula, and a decision taken against a version that no longer exists is not
    a decision about what would be certified.
    """

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=MEASURE_REASON_MAX_LENGTH)
    expression_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class BiMeasureValidationRequest(BiClosedModel):
    """Check a formula without saving it."""

    expression: str = Field(min_length=1, max_length=MEASURE_EXPRESSION_MAX_LENGTH)


class BiMeasureValidationRead(BiClosedModel):
    """What the server makes of a formula, for the editor to show as-is.

    ``valid`` is the server's verdict and the only one: a client that parsed the
    text itself would be guessing at the language the server implements. A
    refusal carries fixed copy plus, at most, a value the language itself made
    well-formed — never an echo of the caller's text.
    """

    valid: bool
    #: Production copy: the named refusal, or a confirmation.
    message: str
    #: 1-based character position the refusal happened at, the way an editor
    #: counts. ``None`` when the formula is accepted.
    position: int | None = None
    #: The figures the formula names, in the order written — empty on a refusal,
    #: and never including one the caller may not read (those are listed in
    #: ``denied_members`` instead and the formula is refused).
    referenced_members: list[str] = Field(default_factory=list)
    referenced_member_labels: list[str] = Field(default_factory=list)
    #: Figures the formula names that this caller's access does not cover.
    denied_members: list[str] = Field(default_factory=list)


class BiMeasureRead(BiClosedModel):
    """One calculated measure, as an identity that may read it sees it.

    A measure is only ever served to a caller whose access covers every figure it
    names, so there is no "restricted" variant of this model: a measure the
    caller cannot compute is absent from the list rather than named in it. The
    formula names catalogue members, and naming a member to someone who may not
    see it is the disclosure the read surface refuses everywhere else.
    """

    id: UUID
    measure_key: str
    label: str
    description: str
    expression: str
    referenced_members: list[str]
    referenced_member_labels: list[str]
    value_type: BiMeasureValueType
    favourable_direction: BiMeasureFavourableDirection
    state: BiMeasureState
    badge: BiCertificationBadge
    owner_user_id: UUID
    owner_display_name: str | None
    #: Whether the caller is the owner, and therefore the only identity that may
    #: edit or delete it.
    owned_by_caller: bool
    created_at: datetime
    updated_at: datetime
    proposed_by_user_id: UUID | None = None
    proposed_by_display_name: str | None = None
    proposed_at: datetime | None = None
    proposal_reason: str | None = None
    approved_by_user_id: UUID | None = None
    approved_by_display_name: str | None = None
    approved_at: datetime | None = None
    approval_reason: str | None = None
    #: The formula that was certified. Equal to ``expression`` while the
    #: certification holds, and absent once an edit has dropped it.
    approved_expression: str | None = None
    #: Whether THIS caller may take the checker's decision on it: the measure is
    #: proposed, they are not the proposer, and their access covers approving
    #: every figure it names. Never a licence in itself — the route decides
    #: again, and the database refuses a self-approval whatever a client believes.
    awaiting_caller_decision: bool = False


class BiMeasureListRead(BiClosedModel):
    """Every calculated measure this caller may read, and what can be done with them."""

    measures: list[BiMeasureRead]
    #: Whether a calculated measure can yet be put on a chart or in a grid. The
    #: formulas are written, reviewed and certified here; evaluating one inside a
    #: query is the query engine's own work and is not built, so this is ``false``
    #: and the client must not offer them as fields.
    available_in_queries: bool
    #: Production copy for that state, shown as-is.
    message: str


class BiMeasureDecisionRead(BiClosedModel):
    """A promotion decision, with the separation-of-duties verdict behind it.

    ``sod_decision`` is the platform's own assignment-time machinery
    (``app/identity/service/grant_administration.py``), reused rather than reimplemented:
    proposer ≠ approver is one policy in this codebase, and a promotion is judged
    by it like any other authority decision.
    """

    measure: BiMeasureRead
    sod_decision: SodDecisionRead
