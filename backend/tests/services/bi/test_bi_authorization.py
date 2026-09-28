"""``authorize_query`` itself: the walk, the call budget, and the fail-closed edges.

The cross-product of principals, bundles and scopes lives in
``tests/api/test_bi_authorization_matrix.py`` (the spec's required matrix).
This file pins the mechanics that matrix depends on being right:

* every INDIRECT reference is collected — a ratio's components, a concentration
  measure's ``over`` dimension, a hierarchy's levels, the Top-N and pivot axes,
  the sort keys and every filter — because a member reached indirectly is read
  just as much as one asked for by name;
* the evaluator is asked once per distinct ``(module, sensitivity)`` pair, so a
  twenty-five-measure query is three questions and not twenty-five;
* everything unrecognised denies: an unmapped catalogue module, an evaluator
  failure, a licence class that does not resolve.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    AuthorizationDecision,
    ConditionCheck,
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.domain.bi.authority import AUTHORIZATION_MODULE, ENTITLEMENT_SLUG
from app.domain.bi.catalogue import Catalogue, ColumnRef, DimensionDef, MeasureDef, catalogue
from app.domain.bi.catalogue.dimensions import POSITION_DIMENSION_IDS, POSITION_TABLE
from app.domain.bi.catalogue.measures import ENTITLEMENT_BY_MODULE
from app.models import AuthorizationBinding, Bank, User
from app.schemas.bi import BI_MAX_MEASURES, BiFilter, BiPivot, BiQuery, BiSort, BiTime, BiTopN
from app.services import authorization, institution_types, scoped_authorization
from app.services.bi.authorization import (
    ENTITLEMENT_SLUGS,
    REASON_ALLOWED,
    REASON_EVALUATION_FAILED,
    REASON_NOT_ENTITLED,
    REASON_SCOPE_UNRECOGNIZED,
    BiAuthorization,
    authorize_query,
    query_members,
    scope_pairs,
)
from app.services.bi.errors import UnknownMember
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 8, 31)
TARGET_BANK = "BK-BIAUTH01"
BANK_CLASS = "universal_bank"
SDI_CLASS = "savings_and_loans"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_session: Session) -> None:
    """Drop the hermetic ``viewer/all/all`` sentence so each test grants exactly."""
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.commit()


def _ensure_bank(db: Session, bank_id: str, *, institution_type: str) -> Bank:
    existing = db.get(Bank, bank_id)
    if existing is not None:
        return existing
    bank = Bank(
        id=bank_id,
        organization_id=ORG_1,
        name="BI authorization bank",
        short_name="BI auth",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type=institution_type,
    )
    db.add(bank)
    db.commit()
    return bank


def _grant(  # noqa: PLR0913 - one keyword per binding dimension
    db: Session,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    bundle: RoleBundle = RoleBundle.VIEWER,
    institution: InstitutionScope = InstitutionScope.INSTITUTION,
    bank_id: str = TARGET_BANK,
) -> int:
    """One indivisible sentence for USER_1; returns the resulting ``authv``."""
    user = db.get(User, USER_1)
    assert user is not None
    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            institution,
            bank_id if institution is InstitutionScope.INSTITUTION else None,
            module,
            sensitivity,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise BI query authorization.",
        commit=False,
    )
    db.commit()
    db.refresh(user)
    return user.authorization_version


def _context(*, authorization_version: int) -> TenantContext:
    return TenantContext(
        organization_id=ORG_1,
        actor_user_id=USER_1,
        roles=("viewer",),
        authorization_version=authorization_version,
    )


@pytest.fixture
def bank(db_session: Session) -> Bank:
    return _ensure_bank(db_session, TARGET_BANK, institution_type=BANK_CLASS)


@pytest.fixture
def evaluator_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, str]]]:
    """Record every evaluator call while keeping the real decision."""
    real = scoped_authorization.evaluate_bank_permission
    recorded: list[dict[str, str]] = []

    def spy(  # noqa: PLR0913 - mirrors the wrapped signature
        db: Session,
        ctx: TenantContext,
        target: Bank,
        *,
        permission: Permission,
        module: Module,
        sensitivity: Sensitivity,
        surface: str,
        conditions: Sequence[ConditionCheck] = (),
    ) -> AuthorizationDecision | None:
        recorded.append(
            {
                "module": module.value,
                "sensitivity": sensitivity.value,
                "permission": permission.value,
                "surface": surface,
                "bank_id": target.id,
            }
        )
        return real(
            db,
            ctx,
            target,
            permission=permission,
            module=module,
            sensitivity=sensitivity,
            surface=surface,
            conditions=conditions,
        )

    monkeypatch.setattr(scoped_authorization, "evaluate_bank_permission", spy)
    yield recorded


def _query(**overrides: Any) -> BiQuery:
    payload: dict[str, Any] = {"measures": ["loans.balance_rc"], "time": BiTime(as_of=AS_OF)}
    payload.update(overrides)
    return BiQuery(**payload)


def _catalogue_with(*extra: MeasureDef | DimensionDef) -> Catalogue:
    """The real catalogue plus hand-built members, for shapes it has none of."""
    real = catalogue()
    measures = {m.id: m for m in real.measures()}
    dimensions = {d.id: d for d in real.dimensions()}
    for member in extra:
        if isinstance(member, MeasureDef):
            measures[member.id] = member
        else:
            dimensions[member.id] = member
    return Catalogue(real.version, measures, dimensions, real.hierarchies())


def _measure(member_id: str, module: str, sensitivity: str, **overrides: Any) -> MeasureDef:
    payload: dict[str, Any] = {
        "id": member_id,
        "module": module,
        "sensitivity": sensitivity,
        "label": "Hand-built measure",
        "source": ColumnRef(POSITION_TABLE, "balance_rc"),
        "allowed_dimensions": POSITION_DIMENSION_IDS,
        "entitlement": "risk",
    }
    payload.update(overrides)
    return MeasureDef(**payload)


def _authorize(  # noqa: PLR0913 - one keyword per decision input
    db: Session,
    target: Bank,
    query: BiQuery,
    *,
    authorization_version: int,
    cat: Catalogue | None = None,
    permission: Permission = Permission.VIEW,
    surface: str = "query",
) -> BiAuthorization:
    return authorize_query(
        db,
        _context(authorization_version=authorization_version),
        target,
        cat or catalogue(),
        query,
        permission=permission,
        surface=surface,
    )


# --- the walk -----------------------------------------------------------------------------


def test_every_indirect_reference_is_collected() -> None:
    """One query whose every clause reaches a member the client never named."""
    members = query_members(
        catalogue(),
        _query(
            # a ratio (two component measures) and a concentration measure
            # (an ``over`` dimension the client never lists)
            measures=["loans.npl_ratio_pct", "loans.sector_hhi"],
            # a hierarchy id, standing for three levels
            dimensions=["counterparty"],
            filters=[BiFilter(member="loan.grade", op="in", values=["substandard"])],
            top_n=BiTopN(dimension="counterparty.type", n=5),
            pivot=BiPivot(dimension="branch.region"),
            sort=[BiSort(member="loans.npl_ratio_pct", direction="desc")],
        ),
    )

    assert [member.id for member in members] == [
        "loans.npl_ratio_pct",
        "loans.npl_exposure_rc",
        "loans.classification_exposure_rc",
        "loans.sector_hhi",
        "loan.sector",
        "counterparty.type",
        "counterparty.group",
        "counterparty.id",
        "loan.grade",
        "branch.region",
    ]


def test_a_weighted_average_collects_its_weight() -> None:
    members = query_members(catalogue(), _query(measures=["loans.weighted_average_rate"]))

    assert "loans.balance_rc" in {member.id for member in members}


def test_a_sort_key_is_collected_even_when_nothing_else_names_it() -> None:
    """The compiler also refuses this shape; authorization must not rely on that.

    Order of enforcement is a route decision, so the pair collection stands on
    its own: a sort over a restricted member is walked whether or not the
    compiler would have rejected the query for a different reason first.
    """
    members = query_members(
        catalogue(), _query(sort=[BiSort(member="counterparty.name", direction="asc")])
    )

    assert ("counterparty.name", "credit", "restricted") in {
        (member.id, member.module, member.sensitivity) for member in members
    }


def test_a_self_referential_composition_terminates() -> None:
    cyclic = _measure(
        "test.cyclic",
        "risk",
        "aggregated",
        aggregation="ratio_of_sums",
        numerator="test.cyclic",
        denominator="positions.count",
    )

    members = query_members(_catalogue_with(cyclic), _query(measures=["test.cyclic"]))

    assert {member.id for member in members} == {"test.cyclic", "positions.count"}


def test_an_unknown_member_is_refused_before_any_decision(
    db_session: Session, bank: Bank, evaluator_calls: list[dict[str, str]]
) -> None:
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    with pytest.raises(UnknownMember) as excinfo:
        _authorize(
            db_session,
            bank,
            _query(measures=["loans.balance_rc", "loans.not_a_measure"]),
            authorization_version=version,
        )

    assert excinfo.value.member_id == "loans.not_a_measure"
    assert evaluator_calls == []


def test_scope_pairs_group_members_by_their_declared_sentence() -> None:
    members = query_members(
        catalogue(), _query(measures=["loans.balance_rc", "deposits.balance_rc"])
    )

    assert dict(scope_pairs(members)) == {
        ("credit", "aggregated"): ("loans.balance_rc",),
        ("liq", "aggregated"): ("deposits.balance_rc",),
    }


# --- the call budget ----------------------------------------------------------------------


def _aggregated_engine_measures(module: str, count: int) -> list[str]:
    return [
        measure.id
        for measure in catalogue().engine_measures()
        if measure.module == module and measure.sensitivity == "aggregated"
    ][:count]


def test_twenty_five_measures_over_three_modules_cost_three_evaluations(
    db_session: Session, bank: Bank, evaluator_calls: list[dict[str, str]]
) -> None:
    measures = [
        *_aggregated_engine_measures("credit", 9),
        *_aggregated_engine_measures("liq", 8),
        *_aggregated_engine_measures("cap", 8),
    ]
    assert len(measures) == BI_MAX_MEASURES
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.AGGREGATED)

    result = _authorize(db_session, bank, _query(measures=measures), authorization_version=version)

    assert result.allowed is True
    assert [(call["module"], call["sensitivity"]) for call in evaluator_calls] == [
        ("credit", "aggregated"),
        ("liq", "aggregated"),
        ("cap", "aggregated"),
    ]


def test_the_evaluator_is_asked_for_the_exact_sentence(
    db_session: Session, bank: Bank, evaluator_calls: list[dict[str, str]]
) -> None:
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    _authorize(db_session, bank, _query(), authorization_version=version, surface="drill")

    assert evaluator_calls == [
        {
            "module": "credit",
            "sensitivity": "aggregated",
            "permission": "view",
            "surface": "bi_drill",
            "bank_id": TARGET_BANK,
        }
    ]


def test_an_entitlement_denial_never_reaches_the_evaluator(
    db_session: Session, evaluator_calls: list[dict[str, str]]
) -> None:
    """A licence class that does not carry the module is not a grant question."""
    sdi = _ensure_bank(db_session, "BK-BIAUTSDI", institution_type=SDI_CLASS)
    version = _grant(
        db_session,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
        bank_id="BK-BIAUTSDI",
    )
    fx_measure = _aggregated_engine_measures("fx", 1)

    result = _authorize(db_session, sdi, _query(measures=fx_measure), authorization_version=version)

    assert result.allowed is False
    assert result.reason == REASON_NOT_ENTITLED
    assert result.denied_members == tuple(fx_measure)
    assert evaluator_calls == []


def test_entitlement_is_checked_per_module_not_per_pair(
    db_session: Session, bank: Bank, evaluator_calls: list[dict[str, str]]
) -> None:
    """Two credit sensitivities are two evaluations but one entitlement question."""
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.ALL)

    result = _authorize(
        db_session,
        bank,
        _query(dimensions=["counterparty.name"]),
        authorization_version=version,
    )

    assert result.allowed is True
    assert [(call["module"], call["sensitivity"]) for call in evaluator_calls] == [
        ("credit", "aggregated"),
        ("credit", "restricted"),
    ]


# --- the entitlement map --------------------------------------------------------------------


def test_every_catalogue_module_has_an_entitlement_slug() -> None:
    """A module with no slug denies, so a new one must be mapped deliberately."""
    assert {member.module for member in catalogue().members()} <= set(ENTITLEMENT_SLUGS)


def test_the_two_entitlement_sources_agree_where_they_overlap() -> None:
    """The map is DERIVED from the catalogue's own two tables, not restated.

    Where the engine side (``AUTHORIZATION_MODULE``/``ENTITLEMENT_SLUG``) and the
    portfolio side (``ENTITLEMENT_BY_MODULE``) both name a module they must agree,
    or the dict merge would silently pick one and gate the wrong licence question.
    """
    engine_side = {AUTHORIZATION_MODULE[key]: ENTITLEMENT_SLUG[key] for key in AUTHORIZATION_MODULE}
    for module, slug in ENTITLEMENT_BY_MODULE.items():
        assert engine_side.get(module, slug) == slug


def test_every_slug_is_a_real_licence_class_module() -> None:
    """A typo'd slug would deny every query for that module, silently."""
    assert set(ENTITLEMENT_SLUGS.values()) <= set(institution_types.BANK_MODULES)


# --- deny by default ----------------------------------------------------------------------


def test_a_filter_on_a_restricted_member_denies_on_an_aggregated_grant(
    db_session: Session, bank: Bank
) -> None:
    """S4 at the service level: filtering reveals, so it is authorized as reading."""
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    result = _authorize(
        db_session,
        bank,
        _query(filters=[BiFilter(member="counterparty.name", op="eq", values=["Ada Traders"])]),
        authorization_version=version,
    )

    assert result.allowed is False
    assert result.denied_members == ("counterparty.name",)
    assert result.matching_binding_ids == ()


def test_a_denial_names_ids_only_and_never_the_filter_value(
    db_session: Session, bank: Bank
) -> None:
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    result = _authorize(
        db_session,
        bank,
        _query(filters=[BiFilter(member="counterparty.name", op="contains", values=["Ada"])]),
        authorization_version=version,
    )

    assert "Ada" not in " ".join((*result.denied_members, result.reason, *result.member_ids))


def test_sensitivity_is_exact_and_never_a_ladder(db_session: Session, bank: Bank) -> None:
    """S6: the blotter sentence does not unlock the aggregated feeds."""
    version = _grant(
        db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.CONFIDENTIAL
    )

    result = _authorize(db_session, bank, _query(), authorization_version=version)

    assert result.allowed is False
    assert result.denied_members == ("loans.balance_rc",)


def test_one_denied_member_denies_the_whole_query(db_session: Session, bank: Bank) -> None:
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    result = _authorize(
        db_session,
        bank,
        _query(measures=["loans.balance_rc", "deposits.balance_rc"]),
        authorization_version=version,
    )

    assert result.allowed is False
    assert result.denied_members == ("deposits.balance_rc",)


def test_an_unrecognised_catalogue_scope_denies(db_session: Session, bank: Bank) -> None:
    """A member declaring a module or sensitivity the platform has no value for."""
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    invented = _catalogue_with(
        _measure("test.unknown_module", "quantum", "aggregated"),
        _measure("test.unknown_sensitivity", "risk", "eyes_only"),
    )

    for member_id in ("test.unknown_module", "test.unknown_sensitivity"):
        result = _authorize(
            db_session,
            bank,
            _query(measures=[member_id]),
            cat=invented,
            authorization_version=version,
        )
        assert result.allowed is False, member_id
        assert result.reason == REASON_SCOPE_UNRECOGNIZED
        assert result.denied_members == (member_id,)


def test_an_evaluator_failure_denies(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``None`` from the evaluator is a denial, never an absence of an answer."""
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)
    monkeypatch.setattr(
        scoped_authorization, "evaluate_bank_permission", lambda *args, **kwargs: None
    )

    result = _authorize(db_session, bank, _query(), authorization_version=version)

    assert result.allowed is False
    assert result.reason == REASON_EVALUATION_FAILED


def test_an_unresolved_licence_class_fails_closed(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration failure stays a 409, not a denied grant.

    ``banks.institution_type`` is NOT NULL with a registry foreign key, so an
    unresolvable licence class cannot be inserted on this schema; the resolver
    is made to fail the way a deployment missing the registry seed does.
    """
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.ALL)

    def unresolved(db: Session, target: Bank) -> None:
        raise institution_types._unresolved(target, registry_empty=True)  # noqa: SLF001

    monkeypatch.setattr(institution_types, "get_type", unresolved)

    with pytest.raises(institution_types.InstitutionTypeUnresolved):
        _authorize(db_session, bank, _query(), authorization_version=version)


def test_permission_is_a_parameter_the_caller_chooses(db_session: Session, bank: Bank) -> None:
    """Record-level export asks for EXPORT, which a Viewer sentence does not carry."""
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    viewing = _authorize(db_session, bank, _query(), authorization_version=version)
    exporting = _authorize(
        db_session,
        bank,
        _query(),
        authorization_version=version,
        permission=Permission.EXPORT,
        surface="export",
    )

    assert viewing.allowed is True
    assert exporting.allowed is False


# --- what an allowed decision carries ------------------------------------------------------


def test_an_allowed_decision_names_the_bindings_and_its_data_scope(
    db_session: Session, bank: Bank
) -> None:
    """An institution-wide binding still reads the whole institution.

    The assertion on the scope's SHAPE moved with Phase 4: ``BiDataScope`` carried
    one ``values`` tuple while the only storable kind was ``all``, and now mirrors
    ``EffectiveDataScope`` with separate ``branches`` and ``regions``. The
    PROPERTY under test is unchanged and is still asserted — an ``all`` grant
    names no values at all, so nothing narrows the read.
    """
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    granted = tuple(
        db_session.scalars(
            select(AuthorizationBinding.id).where(AuthorizationBinding.organization_id == ORG_1)
        )
    )

    result = _authorize(db_session, bank, _query(), authorization_version=version)

    assert result.allowed is True
    assert result.reason == REASON_ALLOWED
    assert result.matching_binding_ids == granted
    assert result.data_scope.kind == "all"
    assert result.data_scope.branches == ()
    assert result.data_scope.regions == ()
    assert result.data_scope.whole_institution is True
    assert result.data_scope.serves_nothing is False
    assert result.member_ids == ("loans.balance_rc",)


def test_an_advisory_measure_is_authorized_like_a_filed_one(
    db_session: Session, bank: Bank
) -> None:
    """D-022: designation governs the badge, not the grant."""
    advisory = next(
        measure
        for measure in catalogue().engine_measures()
        if measure.module == "credit" and measure.advisory_designation == "supervisory_monitoring"
    )
    assert advisory.certified is False
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    result = _authorize(
        db_session, bank, _query(measures=[advisory.id]), authorization_version=version
    )

    assert result.allowed is True


def test_the_decision_object_cannot_carry_data() -> None:
    """A structural guarantee: a denial has nothing to leak and no rows to drop."""
    assert set(BiAuthorization.__dataclass_fields__) == {
        "allowed",
        "denied_members",
        "matching_binding_ids",
        "data_scope",
        "reason",
        "member_ids",
    }


def test_authorize_query_does_not_re_check_the_token_version(
    db_session: Session, bank: Bank
) -> None:
    """``authv`` currency is the request gate's job (``deps.get_current_principal``).

    A stale token never reaches this function — it is refused with 401 before
    any dependency runs — so re-comparing it here would cost a query and invite
    two answers to one question. What this function requires of ``authv`` is
    only that it EXISTS, which is what distinguishes an app token from a key.
    """
    version = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)

    stale = _authorize(db_session, bank, _query(), authorization_version=version - 1)

    assert stale.allowed is True
