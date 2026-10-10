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

import ast
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    AuthorizationDecision,
    ConditionCheck,
    DataScope,
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
from app.features import read_bi
from app.identity import public as scoped_authorization
from app.identity.service import authorization
from app.models import AuthorizationBinding, Bank, Job, User
from app.models.bi_notifications import BiSubscription
from app.schemas.bi import BI_MAX_MEASURES, BiFilter, BiPivot, BiQuery, BiSort, BiTime, BiTopN
from app.services import institution_types
from app.services.bi import authorization as bi_authorization
from app.services.bi import subscriptions
from app.services.bi.authorization import (
    ALL_INSTITUTION_DATA,
    ENTITLEMENT_SLUGS,
    NO_INSTITUTION_DATA,
    REASON_ALLOWED,
    REASON_DATA_SCOPE_CONFLICT,
    REASON_EVALUATION_FAILED,
    REASON_NO_AUTHORIZING_BINDING,
    REASON_NO_PERMISSION_EVALUATED,
    REASON_NOT_ENTITLED,
    REASON_SCOPE_UNRECOGNIZED,
    BiAuthorization,
    BiDataScope,
    authorize_query,
    authorize_query_conjunctive,
    combine_pair_scopes,
    combine_resource_scopes,
    query_members,
    scope_pairs,
)
from app.services.bi.errors import UnknownMember
from app.services.bi.exports import policy
from tests.support.helpers import ORG_1, USER_1

BACKEND = Path(__file__).parents[3]

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
        require_whole_institution: bool = True,
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
            require_whole_institution=require_whole_institution,
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


# --- the Phase 5 members' own sentences ------------------------------------------------------


def test_the_officer_dimension_needs_a_restricted_sentence_to_read_or_to_filter(
    db_session: Session, bank: Bank
) -> None:
    """``position.officer_code`` names one member of STAFF, so it sits a step above
    its two neighbours over the same new columns.

    Both directions are asserted, because D-028 is the reason for the level: a
    filter on it discloses that individual's whole book just as much as grouping by
    it does, and an officer-level export inherits the same authority.
    """
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.AGGREGATED)

    grouped = _authorize(
        db_session,
        bank,
        _query(dimensions=["position.officer_code"]),
        authorization_version=version,
    )
    assert grouped.allowed is False
    assert grouped.denied_members == ("position.officer_code",)

    filtered = _authorize(
        db_session,
        bank,
        _query(filters=[BiFilter(member="position.officer_code", op="eq", values=["RM-014"])]),
        authorization_version=version,
    )
    assert filtered.allowed is False
    assert filtered.denied_members == ("position.officer_code",)
    assert "RM-014" not in " ".join((*filtered.denied_members, filtered.reason))

    with_sentence = _grant(
        db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.RESTRICTED
    )
    allowed = _authorize(
        db_session,
        bank,
        _query(dimensions=["position.officer_code"]),
        authorization_version=with_sentence,
    )
    assert allowed.allowed is True


def test_the_channel_and_status_dimensions_read_on_an_aggregated_sentence(
    db_session: Session, bank: Bank
) -> None:
    """Their contrast with the officer code is the decision, so it is asserted.

    A channel and an account status are attributes of an ACCOUNT, at the same level
    as ``position.type``; requiring a restricted sentence for them would gate a
    business attribute behind the control that protects a person.
    """
    version = _grant(db_session, module=ModuleScope.ALL, sensitivity=SensitivityScope.AGGREGATED)

    for member_id in ("position.channel", "position.account_status"):
        result = _authorize(
            db_session, bank, _query(dimensions=[member_id]), authorization_version=version
        )
        assert result.allowed is True, member_id


def test_the_arrears_measures_need_the_credit_sentence(db_session: Session, bank: Bank) -> None:
    """They are credit figures, so a liquidity-only reader is refused both."""
    version = _grant(
        db_session, module=ModuleScope.LIQUIDITY, sensitivity=SensitivityScope.AGGREGATED
    )

    for member_id in ("loans.arrears_amount_rc", "loans.arrears_share_pct"):
        result = _authorize(
            db_session, bank, _query(measures=[member_id]), authorization_version=version
        )
        assert result.allowed is False, member_id
        assert member_id in result.denied_members

    credit = _grant(db_session, module=ModuleScope.CREDIT, sensitivity=SensitivityScope.AGGREGATED)
    for member_id in ("loans.arrears_amount_rc", "loans.arrears_share_pct"):
        result = _authorize(
            db_session, bank, _query(measures=[member_id]), authorization_version=credit
        )
        assert result.allowed is True, member_id


def test_the_branch_ledger_measures_need_the_risk_sentence_and_an_sdi_may_hold_it(
    db_session: Session,
) -> None:
    """The module decision, asserted from both sides.

    ``risk`` rather than a bank-only module because a chart of accounts split by
    branch is the institution's own bookkeeping and depends on no capital regime —
    so an SDI keeps its own ledger, which is what ``ENTITLEMENT_BY_MODULE[RISK]``
    being in both licence classes' module sets means. A credit-only reader is still
    refused: the entitlement is the licence's, the sentence is the reader's.
    """
    sdi = _ensure_bank(db_session, "BK-BIAUTH02", institution_type=SDI_CLASS)
    assert ENTITLEMENT_BY_MODULE["risk"] in institution_types.default_modules(db_session, sdi)

    credit_only = _grant(
        db_session,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
        institution=InstitutionScope.ORGANIZATION,
    )
    refused = _authorize(
        db_session,
        sdi,
        _query(measures=["gl.branch_ytd_rc"]),
        authorization_version=credit_only,
    )
    assert refused.allowed is False
    assert refused.denied_members == ("gl.branch_ytd_rc",)

    risk = _grant(
        db_session,
        module=ModuleScope.RISK,
        sensitivity=SensitivityScope.AGGREGATED,
        institution=InstitutionScope.ORGANIZATION,
    )
    for member_id in ("gl.branch_ytd_rc", "gl.branch_movement_rc"):
        allowed = _authorize(
            db_session, sdi, _query(measures=[member_id]), authorization_version=risk
        )
        assert allowed.allowed is True, member_id


# --- WHICH SLICE, across resources (A10-01 / A360-1 H8) -----------------------------------


B1 = BiDataScope(kind="branch", branches=("B1",))
B2 = BiDataScope(kind="branch", branches=("B2",))
B1_B2 = BiDataScope(kind="branch", branches=("B1", "B2"))
NORTH = BiDataScope(kind="region", regions=("North",))


def test_all_yields_to_a_narrow_slice_across_resources() -> None:
    """``all`` is the universe, so the other resource's restriction still applies."""
    for scopes in ([ALL_INSTITUTION_DATA, B1], [B1, ALL_INSTITUTION_DATA], [B1, B1, B1]):
        combined = combine_resource_scopes(scopes)
        assert combined.allowed, scopes
        assert combined.reason == REASON_ALLOWED
        assert combined.scope == B1
    everything = combine_resource_scopes([ALL_INSTITUTION_DATA, ALL_INSTITUTION_DATA])
    assert everything.allowed and everything.scope == ALL_INSTITUTION_DATA


def test_two_different_narrow_slices_refuse_rather_than_guess() -> None:
    for scopes in ([B1, B2], [B1, NORTH], [ALL_INSTITUTION_DATA, B1, NORTH]):
        combined = combine_resource_scopes(scopes)
        assert combined.allowed is False, scopes
        assert combined.reason == REASON_DATA_SCOPE_CONFLICT
        assert combined.scope is NO_INSTITUTION_DATA


def test_the_combiner_is_identical_or_refuse_and_not_a_set_intersection() -> None:
    """The documented over-refusal, pinned so nobody "fixes" the prose the other way.

    A true narrowest-wins would serve B1 for ``{B1, B2}`` beside ``{B1}``. The
    platform refuses instead — the SAFE direction — and every comment describing
    the rule must say so. Changing this needs a test for every mixed case
    (branch beside region, region beside region, both beside ``mixed``), not a
    one-line edit here.
    """
    combined = combine_resource_scopes([B1_B2, B1])
    assert combined.allowed is False
    assert combined.reason == REASON_DATA_SCOPE_CONFLICT


def test_nothing_authorized_is_never_the_whole_institution() -> None:
    for scopes in ([], [NO_INSTITUTION_DATA], [ALL_INSTITUTION_DATA, NO_INSTITUTION_DATA]):
        combined = combine_resource_scopes(scopes)
        assert combined.allowed is False, scopes
        assert combined.reason == REASON_NO_AUTHORIZING_BINDING
        assert combined.scope is NO_INSTITUTION_DATA


def _scoped_grant(  # noqa: PLR0913 - one keyword per binding dimension
    db: Session,
    *,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    data_scope: DataScope,
    values: tuple[str, ...] = (),
) -> UUID:
    """One sentence for USER_1 with a data scope; returns the binding id."""
    binding = authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION, TARGET_BANK, module, sensitivity, data_scope, values
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise per-pair scope reduction.",
        commit=False,
    )
    db.commit()
    return binding.id


def test_pairs_are_reduced_apart_where_the_union_would_have_widened(
    db_session: Session, bank: Bank
) -> None:
    """The A10-01 / H8 shape against REAL bindings, through the shared helper.

    The Credit branch sentence covers B2; the
    institution-wide ``risk/aggregated`` sentence authorized only the dimension
    pair. Per pair: credit → B2, risk → all → combined B2. The one-resource
    reducer over the UNION of the same ids says ``all`` — that is the misuse both
    surfaces once committed, shown beside the fix so the difference is concrete.
    """
    branch_sentence = _scoped_grant(
        db_session,
        module=ModuleScope.CREDIT,
        sensitivity=SensitivityScope.AGGREGATED,
        data_scope=DataScope.BRANCH,
        values=("B2",),
    )
    whole_book_on_risk = _scoped_grant(
        db_session,
        module=ModuleScope.RISK,
        sensitivity=SensitivityScope.AGGREGATED,
        data_scope=DataScope.ALL,
    )
    per_pair = [(branch_sentence,), (whole_book_on_risk,)]

    combined = combine_pair_scopes(db_session, organization_id=ORG_1, per_pair=per_pair)
    assert combined.allowed
    assert combined.scope == B2

    misused = authorization.effective_data_scope(
        db_session, organization_id=ORG_1, binding_ids=(branch_sentence, whole_book_on_risk)
    )
    assert misused.whole_institution is True  # what unioning first would have served


def test_a_pair_whose_bindings_all_fell_away_authorizes_nothing(
    db_session: Session, bank: Bank
) -> None:
    """A stale or foreign id is a selector that selects nothing — and the pair it
    was the only authority for is then a pair NOTHING authorized, refused."""
    live = _scoped_grant(
        db_session,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.AGGREGATED,
        data_scope=DataScope.ALL,
    )
    combined = combine_pair_scopes(
        db_session, organization_id=ORG_1, per_pair=[(live,), (uuid4(),)]
    )
    assert combined.allowed is False
    assert combined.reason == REASON_NO_AUTHORIZING_BINDING


# --- the conjunctive decision (A360-1: last-permission and empty-permission shapes) ------


def _scripted_authorize_query(
    monkeypatch: pytest.MonkeyPatch, by_permission: dict[Permission, BiDataScope]
) -> list[Permission]:
    """Replace the single-permission evaluator with a scripted one, everywhere
    a module may hold a reference to it, and record the order it was asked in."""
    asked: list[Permission] = []

    def fake(  # noqa: PLR0913 - mirrors the real signature
        db: Session,
        ctx: TenantContext,
        bank: Bank,
        cat: Catalogue,
        q: BiQuery,
        *,
        permission: Permission,
        surface: str,
    ) -> BiAuthorization:
        asked.append(permission)
        scope = by_permission[permission]
        return BiAuthorization(
            allowed=True,
            denied_members=(),
            matching_binding_ids=(uuid4(),),
            data_scope=scope,
            reason=REASON_ALLOWED,
            member_ids=("loans.balance_rc",),
        )

    monkeypatch.setattr(bi_authorization, "authorize_query", fake)
    # The pre-fix subscription module imported the name directly; patch it too if
    # it is there, so this script convicts the old shape and not just the new one.
    monkeypatch.setattr(subscriptions, "authorize_query", fake, raising=False)
    return asked


def test_a_conjunctive_read_is_served_under_the_narrow_pass_whichever_came_last(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _scripted_authorize_query(
        monkeypatch, {Permission.VIEW: B1, Permission.EXPORT: ALL_INSTITUTION_DATA}
    )
    for order in ((Permission.VIEW, Permission.EXPORT), (Permission.EXPORT, Permission.VIEW)):
        decision = authorize_query_conjunctive(
            db_session,
            _context(authorization_version=1),
            bank,
            catalogue(),
            _query(),
            permissions=order,
            surface="export",
        )
        assert decision.allowed is True, order
        assert decision.data_scope == B1, order
    assert asked == [Permission.VIEW, Permission.EXPORT, Permission.EXPORT, Permission.VIEW]


def test_a_conjunctive_read_over_two_different_narrow_passes_refuses(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted_authorize_query(monkeypatch, {Permission.VIEW: B1, Permission.EXPORT: B2})
    decision = authorize_query_conjunctive(
        db_session,
        _context(authorization_version=1),
        bank,
        catalogue(),
        _query(),
        permissions=(Permission.VIEW, Permission.EXPORT),
        surface="export",
    )
    assert decision.allowed is False
    assert decision.reason == REASON_DATA_SCOPE_CONFLICT
    assert decision.data_scope is NO_INSTITUTION_DATA
    assert decision.matching_binding_ids == ()


def test_an_empty_permission_set_is_refused_not_waved_through(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deny-by-default applies to the question "which sentences?" too.

    With no permission there is no evaluator call, so nothing authorized the
    read; the pre-fix ``read_bi._merged_decision`` answered ``allowed=True`` over
    the whole institution. Both the helper and the feature seam are asserted,
    and the evaluator must not have been consulted at all.
    """
    asked = _scripted_authorize_query(monkeypatch, {})
    access = read_bi.BiReadAccess(
        ctx=_context(authorization_version=1),
        bank=bank,
        principal_user_id=USER_1,
        authorization_version=1,
    )
    for decision in (
        authorize_query_conjunctive(
            db_session,
            access.ctx,
            bank,
            catalogue(),
            _query(),
            permissions=(),
            surface="query",
        ),
        read_bi._merged_decision(
            db_session, access, catalogue(), _query(), surface="query", permissions=()
        ),
    ):
        assert decision.allowed is False
        assert decision.reason == REASON_NO_PERMISSION_EVALUATED
        assert decision.data_scope is NO_INSTITUTION_DATA
        assert decision.matching_binding_ids == ()
    assert asked == []


def test_a_subscription_is_rendered_under_the_conjunctive_scope_not_the_last_pass(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subscription renderer, driven to the point where the scope is chosen.

    Only ``summary`` content is attachable today and its sentence is ``view``
    alone, so the two-permission case is reached by asking the policy for
    ``(view, export)`` directly — the exact future the module's own comment says
    the rule is written out for. ``view`` covers B1, ``export`` covers the book,
    and the artifact must be rendered as B1. The pre-fix loop kept the LAST
    pass's decision and would have rendered the whole institution.
    """
    _scripted_authorize_query(
        monkeypatch, {Permission.VIEW: B1, Permission.EXPORT: ALL_INSTITUTION_DATA}
    )
    monkeypatch.setattr(
        subscriptions.policy,
        "permissions_for",
        lambda export_class: (Permission.VIEW, Permission.EXPORT),
    )
    rendered_under: list[BiAuthorization] = []

    def capture_render(  # noqa: PLR0913 - mirrors the real signature
        db: Session,
        *,
        request: subscriptions.RunRequest,
        cat: Catalogue,
        query: BiQuery,
        decision: BiAuthorization,
        recipient: User,
    ) -> subscriptions._Rendered:
        rendered_under.append(decision)
        return subscriptions._Rendered(refusal=subscriptions.REASON_NO_FIGURES)

    monkeypatch.setattr(subscriptions, "_render_for", capture_render)

    recipient = User(
        id=uuid4(),
        organization_id=ORG_1,
        email="board.member@example.test",
        display_name="Board member",
        role="viewer",
        authorization_version=1,
    )
    db_session.add(recipient)
    subscription = BiSubscription(
        organization_id=ORG_1,
        bank_id=bank.id,
        name="Board pack",
        owner_user_id=USER_1,
        query={"measures": ["loans.balance_rc"], "time": {"as_of": AS_OF.isoformat()}},
        artifact_format="csv",
        cadence="daily",
        hour=7,
        minute=30,
        recipient_user_ids=[str(recipient.id)],
        is_active=True,
    )
    db_session.add(subscription)
    job = Job(
        organization_id=ORG_1,
        job_type=subscriptions.JOB_TYPE_RUN,
        status="running",
        bank_id=bank.id,
        payload={},
    )
    db_session.add(job)
    db_session.commit()
    request = subscriptions.RunRequest(
        subscription=subscription,
        bank=bank,
        scheduled_for=datetime(2026, 8, 31, 7, 30, tzinfo=UTC),
        trigger="schedule",
        as_of_date=AS_OF,
    )

    outcome = subscriptions._deliver_one(
        db_session,
        job=job,
        request=request,
        cat=catalogue(),
        query=_query(),
        export_class=policy.SUMMARY,
        mode="attachment",
        as_of=AS_OF,
        fingerprint=None,
        recipient_id=recipient.id,
        relay=None,  # type: ignore[arg-type] - never opened: the render refuses first
    )

    assert outcome is not None and outcome.reason == subscriptions.REASON_NO_FIGURES
    assert len(rendered_under) == 1
    assert rendered_under[0].data_scope == B1, rendered_under[0]


# --- one helper, every caller: pinned by AST ------------------------------------------------


def _calls(path: Path) -> set[str]:
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _names(path: Path) -> set[str]:
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


#: Every module that authorizes MORE THAN ONE permission over one query. Each
#: must take the one conjunctive decision and never loop over ``authorize_query``
#: itself — the loop is how a surface ends up serving the LAST pass's scope.
#: ``app/services/bi/exports/jobs.py::_decide`` still carries an inline copy of
#: the same rule (it takes the narrow pass and refuses a disagreement) and is not
#: listed; when it is converted, add it here so it cannot regress separately.
CONJUNCTIVE_SURFACES: tuple[Path, ...] = (
    Path("app/features/read_bi.py"),
    Path("app/services/bi/subscriptions.py"),
)


def _permission_loops_calling(path: Path, callee: str) -> list[int]:
    """Lines of ``for`` loops over PERMISSIONS whose body calls ``callee``.

    A loop over chunks of one permission (``read_bi._probe``) is a different
    shape and is combined through ``combine_resource_scopes`` by its caller; the
    shape this convicts is the one that iterates the permissions themselves,
    which is how a surface ends up keeping the LAST pass's scope.
    """
    tree = ast.parse((BACKEND / path).read_text(encoding="utf-8"))
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        loop_text = f"{ast.unparse(node.target)} {ast.unparse(node.iter)}".lower()
        if "permission" not in loop_text:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == callee:
                    found.append(node.lineno)
    return found


@pytest.mark.parametrize("path", CONJUNCTIVE_SURFACES, ids=str)
def test_a_multi_permission_surface_takes_one_conjunctive_decision(path: Path) -> None:
    assert "authorize_query_conjunctive" in _calls(path), (
        f"{path} does not take the shared conjunctive decision"
    )
    loops = _permission_loops_calling(path, "authorize_query")
    assert loops == [], (
        f"{path}:{loops} loops over permissions calling authorize_query; that shape "
        "keeps the LAST pass's scope (audit A360-1). Take one "
        "authorize_query_conjunctive decision instead."
    )


#: The two surfaces that evaluate per ``(module, sensitivity)`` pair. Each must
#: reduce through ``combine_pair_scopes`` and must never touch the ONE-resource
#: reducer, whose own docstring names reducing several pairs through it as the
#: A10-01 defect — committed once in each of these modules.
PAIR_SURFACES: tuple[Path, ...] = (
    Path("app/services/bi/authorization.py"),
    Path("app/services/bi/feeds/authorization.py"),
)


@pytest.mark.parametrize("path", PAIR_SURFACES, ids=str)
def test_a_pair_surface_reduces_through_the_shared_helper_only(path: Path) -> None:
    assert "combine_pair_scopes" in _calls(path), f"{path} does not reduce per pair"
    # Three names, not one. Verification auditor V3 planted the same misuse
    # re-expressed as `load_effective_grants(<union of every pair's ids>)` followed
    # by `reduce_data_scope(...)` and this test did NOT fire — it forbade the NAME
    # `effective_data_scope` while the DEFECT is the shape: reducing ids that were
    # matched against different (module, sensitivity) pairs as though they were one
    # resource. Forbidding the primitives closes the re-spelling.
    # `app/services/bi/authorization.py` is where `combine_pair_scopes` is DEFINED,
    # so it necessarily names the primitives. The rule is about CALLERS reaching
    # past the helper; the helper itself is the exception.
    primitives = ("effective_data_scope", "reduce_data_scope", "load_effective_grants")
    forbidden_here = (
        ("effective_data_scope",)
        if path.name == "authorization.py" and path.parent.name == "bi"
        else primitives
    )
    for forbidden in forbidden_here:
        assert forbidden not in _names(path), (
            f"{path} reaches for `{forbidden}` rather than the shared reducer. "
            "Reducing ids matched against different pairs as one resource lets an "
            "`all` binding on one pair discard the branch restriction on another "
            "(A10-01, A360-1 H8). Call `combine_pair_scopes`, which does it per pair."
        )
