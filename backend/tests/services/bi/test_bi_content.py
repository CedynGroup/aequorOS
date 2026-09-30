"""The rules behind saved dashboards and calculated measures.

Each test here is a property the product depends on, not a shape the code happens
to produce:

* **a shared dashboard carries no authority.** ``resolve_widgets`` is not given
  the owner and cannot consult them: the same canvas resolves to figures for a
  broad reader and to refusal markers for a narrow one, and the refused widget
  carries no query at all.
* **the four visibility rules are four rules.** Private reaches nobody else;
  named sharing reaches exactly the named; role sharing reaches the holders of
  that binding over this institution; organization sharing reaches this tenant's
  identities with institution coverage and — structurally — nobody outside the
  tenant.
* **a formula is the server's to read.** The stored ``referenced_members`` column
  is a recorded copy for audit, and the authorization walk re-derives the ids from
  the TEXT: a doctored column cannot widen what a measure discloses.
* **a promotion is maker-checker, and the approval is frozen at a formula.**
  Proposer ≠ approver through the platform's own policy types; an edit after
  approval clears the certification; a decision against a moved formula refuses.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.domain.bi.catalogue import catalogue
from app.models import AuthorizationBinding, Bank, Organization, User
from app.models.bi_content import BiDashboardShare
from app.schemas.bi import BiLayoutItem, BiPackQuery, BiPackWidget
from app.schemas.bi_content import BiDashboardSpec
from app.services import authorization, grant_administration
from app.services.bi import content
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

BANK_ID = "BK-BICSVC01"
OTHER_TENANT_BANK = "BK-BICSVC02"
AS_OF = date(2026, 8, 31)
SURFACE = content.DASHBOARD_SURFACE

#: A second and third identity of ORG_1: the reader a dashboard is shared WITH,
#: and one it is not.
VIEWER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
STRANGER = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

#: Credit figures and liquidity figures sit in different modules, and an obligor
#: name is ``restricted`` where a loan total is ``aggregated`` — so these three
#: members are three different authorization sentences.
LOANS = "loans.balance_rc"
DEPOSITS = "deposits.balance_rc"
OBLIGOR = "counterparty.name"


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


def _grant(  # noqa: PLR0913 - one complete binding, stated explicitly
    db: Session,
    user_id: UUID,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    bundle: RoleBundle = RoleBundle.VIEWER,
    institution: InstitutionScope = InstitutionScope.ORGANIZATION,
    organization_id: str = ORG_1,
    bank_id: str = BANK_ID,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=organization_id,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            institution,
            bank_id if institution is InstitutionScope.INSTITUTION else None,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the BI content surface.",
        commit=False,
    )
    db.flush()


def _ctx(user_id: UUID, *, organization_id: str = ORG_1) -> TenantContext:
    return TenantContext(
        organization_id=organization_id, actor_user_id=user_id, authorization_version=1
    )


@pytest.fixture
def plane(db_session: Session) -> Iterator[Bank]:
    """One institution, three identities, and no grants but the ones a test makes."""

    bank = _bank(db_session, BANK_ID, ORG_1)
    if db_session.get(Organization, ORG_2) is None:
        db_session.add(Organization(id=ORG_2, name="Other tenant"))
        db_session.flush()
    _bank(db_session, OTHER_TENANT_BANK, ORG_2)
    _user(db_session, USER_1, ORG_1)
    _user(db_session, VIEWER, ORG_1)
    _user(db_session, STRANGER, ORG_1)
    _user(db_session, USER_2, ORG_2)
    # The hermetic fixture hands every identity an organization-wide all/all
    # sentence. These tests are about what a NARROW reader sees, so the baseline
    # goes and each test states the access it means.
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id.in_([ORG_1, ORG_2]))
    )
    db_session.flush()
    yield bank


def _widget(widget_id: str, *measures: str, dimensions: tuple[str, ...] = ()) -> BiPackWidget:
    return BiPackWidget(
        id=widget_id,
        kind="kpi",
        title=f"Widget {widget_id}",
        query=BiPackQuery(measures=list(measures), dimensions=list(dimensions), window="as_of"),
    )


def _spec(*widgets: BiPackWidget) -> BiDashboardSpec:
    return BiDashboardSpec(
        widgets=list(widgets),
        layout=[
            BiLayoutItem(i=widget.id, x=0, y=index, w=4, h=4)
            for index, widget in enumerate(widgets)
        ],
    )


def _authority(db: Session, bank: Bank, user_id: UUID, *, organization_id: str = ORG_1):
    return content.ViewerAuthority(
        db=db,
        ctx=_ctx(user_id, organization_id=organization_id),
        bank=bank,
        cat=catalogue(),
        surface=SURFACE,
    )


# --- sharing never shares data -----------------------------------------------------------


def test_one_canvas_resolves_differently_for_two_readers(db_session: Session, plane: Bank) -> None:
    """The property the whole feature rests on.

    One canvas, two readers. The broad reader is served both widgets with their
    queries; the narrow reader is served the credit widget and REFUSED the
    liquidity one — and the refusal carries no query, so there is nothing in it to
    turn into a figure. Nothing about the owner is an input to the resolution: the
    function is not given one.
    """

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    spec = _spec(_widget("loans", LOANS), _widget("deposits", DEPOSITS))

    owner_view = content.resolve_widgets(
        spec, as_of=AS_OF, authority=_authority(db_session, plane, USER_1)
    )
    viewer_view = content.resolve_widgets(
        spec, as_of=AS_OF, authority=_authority(db_session, plane, VIEWER)
    )

    assert [resolved.granted for resolved in owner_view.widgets] == [True, True]
    assert all(resolved.query is not None for resolved in owner_view.widgets)
    assert owner_view.denied_members == ()

    granted = {resolved.widget.id for resolved in viewer_view.widgets if resolved.granted}
    refused = {resolved.widget.id for resolved in viewer_view.widgets if not resolved.granted}
    assert granted == {"loans"}
    assert refused == {"deposits"}
    assert DEPOSITS in viewer_view.denied_members
    refused_widget = next(r for r in viewer_view.widgets if not r.granted)
    assert refused_widget.query is None


def test_a_refused_reader_is_told_nothing_by_the_resolution(
    db_session: Session, plane: Bank
) -> None:
    """A dashboard of nothing but refused widgets says so, and names nothing.

    ``access`` is ``restricted`` only when every figure-bearing widget was refused,
    and the denied member ids are on the CANVAS result — bound for the append-only
    log — never on a widget the response is built from.
    """

    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    spec = _spec(_widget("deposits", DEPOSITS), _widget("obligors", LOANS, dimensions=(OBLIGOR,)))

    canvas = content.resolve_widgets(
        spec, as_of=AS_OF, authority=_authority(db_session, plane, VIEWER)
    )

    assert canvas.access == "restricted"
    assert canvas.restricted_widgets == canvas.readable_widgets == 2
    assert canvas.served_members == ()
    assert set(canvas.denied_members) >= {DEPOSITS, OBLIGOR}
    assert all(resolved.query is None for resolved in canvas.widgets)


def test_an_obligor_name_is_refused_to_an_aggregate_reader(
    db_session: Session, plane: Bank
) -> None:
    """Sensitivity is exact, never a ladder.

    A credit/aggregated grant covers the loan total and not the obligor NAME, so a
    widget grouping by it is refused even though its measure is allowed — the
    dimension is walked with the same weight as the measure.
    """

    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    spec = _spec(_widget("obligors", LOANS, dimensions=(OBLIGOR,)))

    canvas = content.resolve_widgets(
        spec, as_of=AS_OF, authority=_authority(db_session, plane, VIEWER)
    )

    assert canvas.restricted_widgets == 1
    assert OBLIGOR in canvas.denied_members


def test_a_panel_widget_is_not_refused_by_this_plane(db_session: Session, plane: Bank) -> None:
    """A widget with no query of its own carries its own authorization sentence.

    An embedded platform surface is authorized when the client fetches it, so this
    resolution neither grants nor refuses it; treating it as refused would hide a
    surface the reader may well hold.
    """

    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    spec = BiDashboardSpec(
        widgets=[
            BiPackWidget(
                id="calendar", kind="panel", title="Filing calendar", panel="return_calendar"
            )
        ],
        layout=[BiLayoutItem(i="calendar", x=0, y=0, w=4, h=4)],
    )

    canvas = content.resolve_widgets(
        spec, as_of=AS_OF, authority=_authority(db_session, plane, VIEWER)
    )

    assert canvas.widgets[0].granted is True
    assert canvas.readable_widgets == 0
    assert canvas.access == "granted"


def test_the_author_cannot_save_a_question_they_may_not_ask(
    db_session: Session, plane: Bank
) -> None:
    """Deny-by-default applies to writing, which keeps the refusal marker a
    property of sharing rather than something an author produces for themselves."""

    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    spec = _spec(_widget("deposits", DEPOSITS))

    with pytest.raises(content.MembersDenied) as refusal:
        content.authorize_canvas(db_session, _ctx(VIEWER), plane, spec, surface=SURFACE)

    assert DEPOSITS in refusal.value.denied_members


# --- the four visibility rules -----------------------------------------------------------


def _dashboard(db: Session, *, visibility: str, role: str | None = None, owner: UUID = USER_1):
    dashboard, _ = content.create_dashboard(
        db,
        organization_id=ORG_1,
        bank_id=BANK_ID,
        owner_user_id=owner,
        title="Funding",
        description="",
        visibility=visibility,
        visibility_role=role,
        spec=_spec(_widget("loans", LOANS)),
        source_pack=None,
    )
    db.flush()
    return dashboard


def test_private_reaches_its_owner_and_nobody_else(db_session: Session, plane: Bank) -> None:
    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    dashboard = _dashboard(db_session, visibility="private")

    assert content.reaches(db_session, dashboard, viewer_id=USER_1) is True
    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is False


def test_named_sharing_reaches_exactly_the_named(db_session: Session, plane: Bank) -> None:
    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, STRANGER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    dashboard = _dashboard(db_session, visibility="users")

    content.set_shares(db_session, dashboard, actor_user_id=USER_1, user_ids=[VIEWER])

    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is True
    assert content.reaches(db_session, dashboard, viewer_id=STRANGER) is False


def test_removing_a_name_removes_the_reach(db_session: Session, plane: Bank) -> None:
    """Revocation is an ordinary save: the absent name loses reachability at once."""

    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    dashboard = _dashboard(db_session, visibility="users")
    content.set_shares(db_session, dashboard, actor_user_id=USER_1, user_ids=[VIEWER])

    added, removed = content.set_shares(db_session, dashboard, actor_user_id=USER_1, user_ids=[])

    assert added == ()
    assert removed == (VIEWER,)
    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is False


def test_role_sharing_reaches_the_holders_of_that_role(db_session: Session, plane: Bank) -> None:
    """And it reads the stored binding, never ``users.role``."""

    _ = plane
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
        bundle=RoleBundle.ANALYST,
    )
    _grant(
        db_session,
        STRANGER,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
        bundle=RoleBundle.VIEWER,
    )
    dashboard = _dashboard(db_session, visibility="role", role=RoleBundle.ANALYST.value)

    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is True
    assert content.reaches(db_session, dashboard, viewer_id=STRANGER) is False


def test_role_sharing_does_not_reach_another_institutions_holder(
    db_session: Session, plane: Bank
) -> None:
    """Coverage of THIS institution, not of the role in the abstract."""

    sibling = _bank(db_session, "BK-BICSVC03", ORG_1)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
        bundle=RoleBundle.ANALYST,
        institution=InstitutionScope.INSTITUTION,
        bank_id=sibling.id,
    )
    _ = plane
    dashboard = _dashboard(db_session, visibility="role", role=RoleBundle.ANALYST.value)

    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is False


def test_organization_sharing_reaches_the_tenant_and_stops_there(
    db_session: Session, plane: Bank
) -> None:
    """``org`` means this organization: an identity of another tenant is not in it.

    Structurally rather than by a check: the row is tenant-scoped, the reachability
    query is organization-filtered, and a foreign identity holds no binding in this
    organization to be found by it.
    """

    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    dashboard = _dashboard(db_session, visibility="org")

    assert content.reaches(db_session, dashboard, viewer_id=VIEWER) is True
    # USER_2 belongs to ORG_2 and holds nothing here.
    assert content.reaches(db_session, dashboard, viewer_id=USER_2) is False


def test_organization_sharing_does_not_reach_an_identity_with_no_coverage(
    db_session: Session, plane: Bank
) -> None:
    """An identity of the tenant with no institution coverage reaches nothing here."""

    _ = plane
    dashboard = _dashboard(db_session, visibility="org")

    assert content.reaches(db_session, dashboard, viewer_id=STRANGER) is False


def test_the_list_applies_the_same_four_rules(db_session: Session, plane: Bank) -> None:
    """The list is the same rules asked once, not a second set of them."""

    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    private = _dashboard(db_session, visibility="private")
    org_wide = _dashboard(db_session, visibility="org")
    named = _dashboard(db_session, visibility="users")
    content.set_shares(db_session, named, actor_user_id=USER_1, user_ids=[VIEWER])

    reachable = content.reachable_dashboards(
        db_session, organization_id=ORG_1, bank_id=BANK_ID, viewer_id=VIEWER
    )

    ids = {dashboard.id for dashboard in reachable}
    assert org_wide.id in ids
    assert named.id in ids
    assert private.id not in ids


def test_narrowing_the_visibility_drops_the_names(db_session: Session, plane: Bank) -> None:
    """A share row no visibility reads is a permission nobody can see."""

    _ = plane
    _grant(db_session, VIEWER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    dashboard = _dashboard(db_session, visibility="users")
    content.set_shares(db_session, dashboard, actor_user_id=USER_1, user_ids=[VIEWER])

    content.update_dashboard(
        db_session,
        dashboard,
        actor_user_id=USER_1,
        title="Funding",
        description="",
        visibility="private",
        visibility_role=None,
        spec=_spec(_widget("loans", LOANS)),
        change_note="Keeping this to myself",
    )

    assert content.shares(db_session, dashboard) == ()
    assert (
        db_session.scalars(
            BiDashboardShare.__table__.select().with_only_columns(BiDashboardShare.__table__.c.id)
        ).all()
        == []
    )


# --- ownership ---------------------------------------------------------------------------


def test_only_the_owner_may_edit_or_share(db_session: Session, plane: Bank) -> None:
    """Not an administrator and not an Org Owner: there is no override branch."""

    _ = plane
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.ORG_OWNER,
    )
    dashboard = _dashboard(db_session, visibility="org")

    with pytest.raises(content.NotTheOwner):
        content.update_dashboard(
            db_session,
            dashboard,
            actor_user_id=VIEWER,
            title="Renamed by someone else",
            description="",
            visibility="org",
            visibility_role=None,
            spec=_spec(_widget("loans", LOANS)),
            change_note="",
        )
    with pytest.raises(content.NotTheOwner):
        content.set_shares(db_session, dashboard, actor_user_id=VIEWER, user_ids=[STRANGER])


def test_every_save_appends_a_version(db_session: Session, plane: Bank) -> None:
    """The history extends; it is never rewritten."""

    _ = plane
    dashboard = _dashboard(db_session, visibility="private")
    first = content.current_version(db_session, dashboard)

    content.update_dashboard(
        db_session,
        dashboard,
        actor_user_id=USER_1,
        title="Funding and liquidity",
        description="",
        visibility="private",
        visibility_role=None,
        spec=_spec(_widget("loans", LOANS), _widget("second", LOANS)),
        change_note="Added a second tile",
    )

    history = content.versions(db_session, dashboard)
    assert [row.version for row in history] == [2, 1]
    assert dashboard.current_version == 2
    # The first version still holds what it held: the title it was saved under and
    # its own canvas digest.
    assert history[-1].spec_digest == first.spec_digest
    assert history[-1].title == "Funding"
    assert history[0].change_note == "Added a second tile"


def test_a_cross_tenant_dashboard_does_not_exist(db_session: Session, plane: Bank) -> None:
    """Two banks of one organization share an RLS tenant, so the lookup is
    institution-scoped as well — and a foreign tenant's id is simply absent."""

    _ = plane
    sibling = _bank(db_session, "BK-BICSVC04", ORG_1)
    dashboard = _dashboard(db_session, visibility="org")

    with pytest.raises(content.DashboardNotFound):
        content.load_dashboard(
            db_session,
            organization_id=ORG_1,
            bank_id=sibling.id,
            dashboard_id=dashboard.id,
            viewer_id=USER_1,
        )
    with pytest.raises(content.DashboardNotFound):
        content.load_dashboard(
            db_session,
            organization_id=ORG_2,
            bank_id=BANK_ID,
            dashboard_id=dashboard.id,
            viewer_id=USER_1,
        )


def test_a_copy_of_a_certified_pack_is_a_copy(db_session: Session, plane: Bank) -> None:
    """A pack is never edited in place; an edit produces a copy, and it is personal."""

    _ = plane
    from app.domain.bi.packs import pack_ids, packs  # noqa: PLC0415 - fixture data

    pack_id = pack_ids()[0]
    original = next(spec for spec in packs() if spec.id == pack_id)
    spec, description = content.spec_from_pack(pack_id)

    assert [widget.model_dump() for widget in spec.widgets] == [
        widget.model_dump() for widget in original.widgets
    ]
    assert description == original.description

    dashboard, _ = content.create_dashboard(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_ID,
        owner_user_id=USER_1,
        title="My copy",
        description=description,
        visibility="private",
        visibility_role=None,
        spec=spec,
        source_pack=pack_id,
    )
    assert dashboard.badge == "personal"
    assert dashboard.source_pack == pack_id


def test_an_unknown_pack_is_refused(db_session: Session, plane: Bank) -> None:
    _ = db_session, plane
    with pytest.raises(content.PackNotAvailable):
        content.spec_from_pack("not_a_pack")


def test_the_canvas_digest_is_value_based(db_session: Session, plane: Bank) -> None:
    """Two equal canvases hash alike; a changed one does not."""

    _ = db_session, plane
    one = _spec(_widget("loans", LOANS))
    same = _spec(_widget("loans", LOANS))
    other = _spec(_widget("loans", DEPOSITS))

    assert content.spec_digest(one) == content.spec_digest(same)
    assert content.spec_digest(one) != content.spec_digest(other)


def test_a_widget_the_query_engine_would_refuse_is_refused_at_save(
    db_session: Session, plane: Bank
) -> None:
    """Saved so a person does not build a dashboard and find a broken tile later."""

    _ = db_session, plane
    with pytest.raises(content.CanvasRefused):
        # ``event.type`` is a credit/aggregated dimension like the loan total
        # itself, so nothing about ACCESS refuses this: it is refused because a
        # position measure cannot be broken down by an event attribute.
        content.check_canvas_shape(_spec(_widget("wrong", LOANS, dimensions=("event.type",))))


# --- calculated measures -----------------------------------------------------------------


def test_a_formula_may_only_name_figures_the_author_can_read(
    db_session: Session, plane: Bank
) -> None:
    """The authorization walk, fed by the server's own parse of the text."""

    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    with pytest.raises(content.MembersDenied) as refusal:
        content.compile_expression(
            db_session,
            _ctx(VIEWER),
            plane,
            f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
            surface=SURFACE,
        )

    assert DEPOSITS in refusal.value.denied_members


def test_the_walk_finds_every_figure_a_formula_names(db_session: Session, plane: Bank) -> None:
    """Completeness of ``referenced_members`` is what the walk is FOR."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    compiled = content.compile_expression(
        db_session,
        _ctx(USER_1),
        plane,
        f"IF([m:{LOANS}] > 0, SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}]), 0)",
        surface=SURFACE,
    )

    assert set(compiled.referenced_members) == {LOANS, DEPOSITS}
    assert compiled.digest == content.expression_digest(compiled.source)


def test_a_dimension_is_not_a_figure(db_session: Session, plane: Bank) -> None:
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    with pytest.raises(content.ExpressionRefused):
        content.compile_expression(
            db_session, _ctx(USER_1), plane, f"[m:{OBLIGOR}] + 1", surface=SURFACE
        )


def test_an_unknown_figure_is_refused_by_name(db_session: Session, plane: Bank) -> None:
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    with pytest.raises(content.ExpressionRefused) as refusal:
        content.compile_expression(
            db_session, _ctx(USER_1), plane, "[m:loans.invented_total] * 2", surface=SURFACE
        )

    assert "loans.invented_total" in str(refusal.value)


def test_a_formula_naming_no_figure_is_refused(db_session: Session, plane: Bank) -> None:
    """A "calculated measure" of constants computes nothing about the book."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    with pytest.raises(content.ExpressionRefused):
        content.compile_expression(db_session, _ctx(USER_1), plane, "1 + 1", surface=SURFACE)


def test_malformed_text_is_refused_with_a_position_and_no_echo(
    db_session: Session, plane: Bank
) -> None:
    """Hostile input: named refusal, a position, and none of the caller's text back."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    hostile = f"[m:{LOANS}] + <script>alert(1)</script>"

    with pytest.raises(content.ExpressionRefused) as refusal:
        content.compile_expression(db_session, _ctx(USER_1), plane, hostile, surface=SURFACE)

    assert refusal.value.position is not None
    assert "script" not in str(refusal.value)


def test_one_calculated_measure_cannot_be_written_in_terms_of_another(
    db_session: Session, plane: Bank
) -> None:
    """Refused by NAME rather than as an unknown figure: there IS such a measure,
    it just cannot be nested, and saying "no such figure" would be false."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    compiled = content.compile_expression(
        db_session, _ctx(USER_1), plane, f"[m:{LOANS}] * 2", surface=SURFACE
    )
    content.create_measure(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_ID,
        owner_user_id=USER_1,
        measure_key="custom.double_loans",
        label="Double loans",
        description="",
        compiled=compiled,
        value_type="amount",
        favourable_direction="neutral",
    )

    with pytest.raises(content.ExpressionRefused) as refusal:
        content.compile_expression(
            db_session, _ctx(USER_1), plane, "[m:custom.double_loans] + 1", surface=SURFACE
        )

    assert "calculated measure" in str(refusal.value)


def test_a_measure_may_not_shadow_a_platform_figure(db_session: Session, plane: Bank) -> None:
    """One id must mean one figure, or a formula naming it reads whichever the
    resolver consulted first."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    compiled = content.compile_expression(
        db_session, _ctx(USER_1), plane, f"[m:{LOANS}] * 2", surface=SURFACE
    )

    with pytest.raises(content.MeasureKeyUnavailable):
        content.create_measure(
            db_session,
            organization_id=ORG_1,
            bank_id=BANK_ID,
            owner_user_id=USER_1,
            measure_key=LOANS,
            label="Shadow",
            description="",
            compiled=compiled,
            value_type="amount",
            favourable_direction="neutral",
        )


def _measure(db: Session, bank: Bank, *, owner: UUID = USER_1, key: str = "custom.ratio"):
    compiled = content.compile_expression(
        db,
        _ctx(owner),
        bank,
        f"SAFE_DIV([m:{LOANS}], [m:{DEPOSITS}])",
        surface=SURFACE,
    )
    return content.create_measure(
        db,
        organization_id=ORG_1,
        bank_id=BANK_ID,
        owner_user_id=owner,
        measure_key=key,
        label="Loans to deposits",
        description="",
        compiled=compiled,
        value_type="fraction",
        favourable_direction="neutral",
    )


def test_a_doctored_member_column_does_not_widen_what_a_measure_discloses(
    db_session: Session, plane: Bank
) -> None:
    """The authorization walk re-derives the ids from the TEXT.

    The stored ``referenced_members`` column is a recorded copy for audit. If it
    were the authority, rewriting it to a figure the reader may see would make the
    measure readable while its formula still names one they may not — a figure read
    without a binding. So the column is doctored here and the answer must not
    change.
    """

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    measure = _measure(db_session, plane)

    measure.referenced_members = [LOANS]
    db_session.flush()

    assert content.readable(db_session, _ctx(VIEWER), plane, measure, surface=SURFACE) is False
    assert content.readable(db_session, _ctx(USER_1), plane, measure, surface=SURFACE) is True


def test_a_measure_a_reader_cannot_compute_is_absent_from_their_list(
    db_session: Session, plane: Bank
) -> None:
    """Absent rather than restricted: the label can describe the refused figure."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, VIEWER, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")

    visible = content.readable_measures(
        db_session, _ctx(VIEWER), plane, viewer_id=VIEWER, surface=SURFACE
    )

    assert visible == ()


def test_another_persons_draft_is_theirs_alone(db_session: Session, plane: Bank) -> None:
    """A personal measure is not part of the institution's vocabulary."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(db_session, VIEWER, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _measure(db_session, plane, owner=USER_1)

    assert (
        content.readable_measures(
            db_session, _ctx(VIEWER), plane, viewer_id=VIEWER, surface=SURFACE
        )
        == ()
    )
    assert (
        len(
            content.readable_measures(
                db_session, _ctx(USER_1), plane, viewer_id=USER_1, surface=SURFACE
            )
        )
        == 1
    )


# --- promotion ---------------------------------------------------------------------------


def test_the_proposer_may_not_be_the_approver(db_session: Session, plane: Bank) -> None:
    """Maker-checker, through the platform's own policy types rather than a boolean."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")

    with pytest.raises(grant_administration.SodPolicyBlocked) as refusal:
        content.decide_promotion(
            db_session,
            _ctx(USER_1),
            plane,
            measure,
            actor_user_id=USER_1,
            decision="approve",
            reason="Looks right to me",
            expression_digest_reviewed=measure.expression_digest,
            surface=SURFACE,
        )

    decision = refusal.value.decision
    assert decision.outcome is grant_administration.SodOutcome.BLOCK
    assert decision.findings
    assert measure.state == "proposed"


def test_a_second_person_with_approval_authority_certifies_it(
    db_session: Session, plane: Bank
) -> None:
    """And the approved formula is frozen on the row, with the whole sentence."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")

    certified, sod = content.decide_promotion(
        db_session,
        _ctx(VIEWER),
        plane,
        measure,
        actor_user_id=VIEWER,
        decision="approve",
        reason="Reviewed against the register",
        expression_digest_reviewed=measure.expression_digest,
        surface=SURFACE,
    )

    assert sod.outcome is grant_administration.SodOutcome.ALLOW
    assert certified.state == "bank_certified"
    assert certified.approved_by_user_id == VIEWER
    assert certified.proposed_by_user_id == USER_1
    assert certified.approved_expression == certified.expression
    assert certified.approved_expression_digest == certified.expression_digest
    assert certified.approval_reason == "Reviewed against the register"
    assert content.badge_for(certified) == "bank_certified"


def test_certifying_needs_approval_authority_not_merely_view(
    db_session: Session, plane: Bank
) -> None:
    """Certifying a measure for the institution is an approval, so it needs approval
    authority over the same sentences the figures need."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.VIEWER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")

    with pytest.raises(content.MembersDenied):
        content.decide_promotion(
            db_session,
            _ctx(VIEWER),
            plane,
            measure,
            actor_user_id=VIEWER,
            decision="approve",
            reason="Reviewed",
            expression_digest_reviewed=measure.expression_digest,
            surface=SURFACE,
        )
    assert measure.state == "proposed"
    assert Permission.APPROVE.value == "approve"


def test_a_decision_against_a_moved_formula_is_refused(db_session: Session, plane: Bank) -> None:
    """An approver approves a specific formula. If it moved, it is reviewed again."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")

    with pytest.raises(content.ExpressionMoved):
        content.decide_promotion(
            db_session,
            _ctx(VIEWER),
            plane,
            measure,
            actor_user_id=VIEWER,
            decision="approve",
            reason="Reviewed",
            expression_digest_reviewed="0" * 64,
            surface=SURFACE,
        )


def test_an_edit_after_certification_drops_the_certification(
    db_session: Session, plane: Bank
) -> None:
    """A later edit must not inherit the approval, and the row cannot hold both."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board pack")
    content.decide_promotion(
        db_session,
        _ctx(VIEWER),
        plane,
        measure,
        actor_user_id=VIEWER,
        decision="approve",
        reason="Reviewed against the register",
        expression_digest_reviewed=measure.expression_digest,
        surface=SURFACE,
    )

    edited = content.compile_expression(
        db_session, _ctx(USER_1), plane, f"SAFE_DIV([m:{DEPOSITS}], [m:{LOANS}])", surface=SURFACE
    )
    content.update_measure(
        db_session,
        measure,
        actor_user_id=USER_1,
        label="Deposits to loans",
        description="",
        compiled=edited,
        value_type="fraction",
        favourable_direction="neutral",
    )

    assert measure.state == "personal"
    assert measure.approved_by_user_id is None
    assert measure.approved_expression is None
    assert measure.proposed_by_user_id is None
    assert content.badge_for(measure) == "personal"


def test_renaming_a_certified_measure_keeps_its_certification(
    db_session: Session, plane: Bank
) -> None:
    """The approval is frozen at a FORMULA, so a label change is not an edit of it."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board")
    content.decide_promotion(
        db_session,
        _ctx(VIEWER),
        plane,
        measure,
        actor_user_id=VIEWER,
        decision="approve",
        reason="Reviewed",
        expression_digest_reviewed=measure.expression_digest,
        surface=SURFACE,
    )
    same_formula = content.compile_expression(
        db_session, _ctx(USER_1), plane, measure.expression, surface=SURFACE
    )

    content.update_measure(
        db_session,
        measure,
        actor_user_id=USER_1,
        label="Loan-to-deposit ratio",
        description="Board pack line 4",
        compiled=same_formula,
        value_type="fraction",
        favourable_direction="neutral",
    )

    assert measure.state == "bank_certified"
    assert measure.label == "Loan-to-deposit ratio"


def test_a_rejection_returns_the_measure_to_its_author(db_session: Session, plane: Bank) -> None:
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.APPROVER,
    )
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board")

    rejected, _ = content.decide_promotion(
        db_session,
        _ctx(VIEWER),
        plane,
        measure,
        actor_user_id=VIEWER,
        decision="reject",
        reason="The denominator should be total funding",
        expression_digest_reviewed=measure.expression_digest,
        surface=SURFACE,
    )

    assert rejected.state == "personal"
    assert rejected.proposed_by_user_id is None
    assert rejected.approved_by_user_id is None


def test_only_the_owner_may_propose_or_edit_a_measure(db_session: Session, plane: Bank) -> None:
    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    _grant(
        db_session,
        VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bundle=RoleBundle.ORG_OWNER,
    )
    measure = _measure(db_session, plane)

    with pytest.raises(content.NotTheOwner):
        content.propose_measure(db_session, measure, actor_user_id=VIEWER, reason="Mine now")


def test_a_measure_can_only_be_proposed_once(db_session: Session, plane: Bank) -> None:
    """So a checker is never looking at a proposal that was replaced under them."""

    _grant(db_session, USER_1, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    measure = _measure(db_session, plane)
    content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="For the board")

    with pytest.raises(content.MeasureStateConflict):
        content.propose_measure(db_session, measure, actor_user_id=USER_1, reason="Again")


def test_the_sod_verdict_is_the_platforms_own_shape() -> None:
    """Reused, not reinvented: the surfaces that display an SoD decision already
    read this shape."""

    allow = content.promotion_sod_decision(proposed_by_user_id=USER_1, approver_user_id=VIEWER)
    block = content.promotion_sod_decision(proposed_by_user_id=USER_1, approver_user_id=USER_1)

    assert isinstance(allow, grant_administration.SodDecision)
    assert allow.outcome is grant_administration.SodOutcome.ALLOW
    assert block.outcome is grant_administration.SodOutcome.BLOCK
    assert isinstance(block.findings[0], grant_administration.SodFinding)


def test_an_unfamiliar_reader_of_an_absent_measure_gets_the_same_answer(
    db_session: Session, plane: Bank
) -> None:
    """A measure of another institution does not exist for this one."""

    _ = plane
    with pytest.raises(content.MeasureNotFound):
        content.load_measure(db_session, organization_id=ORG_1, bank_id=BANK_ID, measure_id=uuid4())
