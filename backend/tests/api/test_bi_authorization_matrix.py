"""The BI authorization matrix (spec verification X-5; security rows S3–S9, S18–S19).

One table, one assertion per row: for each principal kind × permission bundle ×
module scope × sensitivity scope × institution scope × query shape, is the query
served, and when it is not, exactly which member ids caused the refusal. The
table is the artefact — a reviewer should be able to read a row and say whether
that is the intended answer without reading any code.

BI routes are not mounted yet (this contract ships ahead of them,
``backend/docs/bi_enforcement_rollout.md``), so the subject here is
``authorize_query`` — the ONE decision every BI surface will consult — called
with the contexts ``app/api/deps.py`` builds for each credential kind. Two
boundaries therefore sit in the route layer and are deliberately NOT asserted
here: a bank belonging to ANOTHER tenant is 404 before authorization runs
(``resolve_tenant_bank``, S1), and the 403 body, the ``bi_query_log`` row and
the ETag are the handler's. A bank of THIS tenant that the principal holds no
coverage for is authorization's own answer and is row S3 below.

The scalar role on each context is carried only to prove it is ignored: a
tenant ``examiner`` is a human principal whose access is exactly its bindings
(S7), and no row in this file is decided by ``users.role`` or the token's
``roles[]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.domain.bi.catalogue import Catalogue, ColumnRef, MeasureDef, catalogue
from app.domain.bi.catalogue.dimensions import POSITION_DIMENSION_IDS, POSITION_TABLE
from app.models import AuditEvent, AuthorizationBinding, Bank, User
from app.schemas.bi import BiFilter, BiPivot, BiQuery, BiSort, BiTime, BiTopN
from app.services import authorization
from app.services.bi.authorization import (
    REASON_HUMAN_REQUIRED,
    REASON_NOT_ENTITLED,
    authorize_query,
)
from tests.api.helpers import ORG_1, USER_1

AS_OF = date(2026, 8, 31)
#: The institution every row targets unless it says ``other``.
TARGET_BANK = "BK-BIMTRX001"
#: A second institution of the SAME tenant: in scope for the 404 rule, out of
#: scope for an institution-scoped binding that names only the target (S3).
OTHER_BANK = "BK-BIMTRX002"
#: A savings-&-loans licence class, which carries no FX entitlement.
SDI_BANK = "BK-BIMTRXSDI"
SERVICE_USER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

PrincipalKind = Literal["human", "examiner", "impersonated", "machine"]


# --- the query shapes ----------------------------------------------------------------------


def _query(**overrides: Any) -> BiQuery:
    payload: dict[str, Any] = {"measures": ["loans.balance_rc"], "time": BiTime(as_of=AS_OF)}
    payload.update(overrides)
    return BiQuery(**payload)


#: A ratio whose components sit in two OTHER modules. The catalogue has no such
#: measure today, so it is hand-built here to pin the rule before one appears:
#: a composed measure is authorized for every module it reads, not just its own.
CROSS_MODULE_RATIO = MeasureDef(
    id="test.cross_module_ratio",
    module="risk",
    sensitivity="aggregated",
    label="Loans to deposits",
    source=ColumnRef(POSITION_TABLE, "balance_rc"),
    measure_kind="portfolio",
    aggregation="ratio_of_sums",
    allowed_dimensions=POSITION_DIMENSION_IDS,
    entitlement="risk",
    numerator="loans.balance_rc",
    denominator="deposits.balance_rc",
)


def _catalogue_with_cross_module_ratio() -> Catalogue:
    real = catalogue()
    return Catalogue(
        real.version,
        {**{m.id: m for m in real.measures()}, CROSS_MODULE_RATIO.id: CROSS_MODULE_RATIO},
        {d.id: d for d in real.dimensions()},
        real.hierarchies(),
    )


CROSS_MODULE_CATALOGUE = _catalogue_with_cross_module_ratio()

#: Every shape the compiler accepts, and what each one reads.
#: ``measure`` credit/aggregated · ``group_restricted`` + credit/restricted ·
#: ``filter_restricted`` the same members through a WHERE · ``hierarchy`` three
#: levels from one id · ``pivot`` / ``top_n`` / ``sort`` the axes and keys ·
#: ``mixed`` one allowed and one denied module · ``record`` risk/confidential ·
#: ``cross_module`` a ratio reaching two further modules.
SHAPES: dict[str, BiQuery] = {
    "measure": _query(),
    "group_restricted": _query(dimensions=["counterparty.name"]),
    "filter_restricted": _query(
        filters=[BiFilter(member="counterparty.name", op="eq", values=["Ada Traders"])]
    ),
    "hierarchy": _query(dimensions=["counterparty"]),
    "pivot": _query(pivot=BiPivot(dimension="counterparty.name")),
    "top_n": _query(
        dimensions=["counterparty.name"], top_n=BiTopN(dimension="counterparty.name", n=5)
    ),
    "sort": _query(
        dimensions=["counterparty.name"], sort=[BiSort(member="counterparty.name", direction="asc")]
    ),
    "mixed": _query(measures=["loans.balance_rc", "deposits.balance_rc"]),
    "record": _query(dimensions=["position.source_reference"]),
    "cross_module": _query(measures=[CROSS_MODULE_RATIO.id]),
    "fx_engine": _query(measures=["engine.nop_ghs.crd.official"]),
}

#: Which members each shape needs a RESTRICTED credit sentence for.
RESTRICTED_IN = {
    "group_restricted": ("counterparty.name",),
    "filter_restricted": ("counterparty.name",),
    "hierarchy": ("counterparty.group", "counterparty.id"),
    "pivot": ("counterparty.name",),
    "top_n": ("counterparty.name",),
    "sort": ("counterparty.name",),
}


# --- the table ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Grant:
    """One indivisible binding row, exactly as an Org Owner would create it."""

    bundle: RoleBundle = RoleBundle.VIEWER
    module: ModuleScope = ModuleScope.CREDIT
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED
    institution: InstitutionScope = InstitutionScope.INSTITUTION
    bank: str = TARGET_BANK


@dataclass(frozen=True, slots=True)
class Row:
    """One matrix row: who asks for what, and the answer."""

    id: str
    shape: str
    allowed: bool
    #: The member ids the refusal must name, in query order.
    denied: tuple[str, ...] = ()
    principal: PrincipalKind = "human"
    grants: tuple[Grant, ...] = (Grant(),)
    bank: str = TARGET_BANK
    reason: str | None = None
    #: Which security-matrix row this pins, when it pins a named one.
    pins: str = ""
    catalogue: Catalogue | None = None


CREDIT_AGGREGATED = Grant()
CREDIT_RESTRICTED = Grant(sensitivity=SensitivityScope.RESTRICTED)
ORG_WIDE_ALL = Grant(
    module=ModuleScope.ALL,
    sensitivity=SensitivityScope.ALL,
    institution=InstitutionScope.ORGANIZATION,
)

ROWS: tuple[Row, ...] = (
    # --- bundles: does the bundle carry VIEW at all? -------------------------------------
    *(
        Row(
            id=f"bundle-{bundle.value}-views",
            shape="measure",
            allowed=True,
            grants=(Grant(bundle=bundle),),
        )
        for bundle in (RoleBundle.VIEWER, RoleBundle.AUDITOR, RoleBundle.ANALYST)
    ),
    Row(
        id="bundle-approver-views",
        shape="measure",
        allowed=True,
        grants=(Grant(bundle=RoleBundle.APPROVER),),
    ),
    # ACCOUNT_ADMIN and ORG_OWNER carry ``administer`` and nothing else: the
    # Owner's own product access is a SEPARATE viewer sentence (AGENTS.md,
    # "ownership is two sentences"). Scoped to credit here to show it is the
    # bundle, not the scope, that refuses.
    *(
        Row(
            id=f"bundle-{bundle.value}-does-not-view",
            shape="measure",
            allowed=False,
            denied=("loans.balance_rc",),
            grants=(Grant(bundle=bundle),),
        )
        for bundle in (RoleBundle.ACCOUNT_ADMIN, RoleBundle.ORG_OWNER)
    ),
    # --- module scope --------------------------------------------------------------------
    Row(id="module-exact-credit", shape="measure", allowed=True),
    Row(
        id="module-exact-liq-does-not-cover-credit",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(Grant(module=ModuleScope.LIQUIDITY),),
    ),
    Row(
        id="module-all",
        shape="measure",
        allowed=True,
        grants=(Grant(module=ModuleScope.ALL),),
    ),
    Row(
        id="module-none-no-binding-at-all",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(),
    ),
    Row(
        id="module-risk-is-not-credit",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(Grant(module=ModuleScope.RISK),),
    ),
    # --- sensitivity scope: exact or ``all``, never a ladder -----------------------------
    Row(id="sensitivity-aggregated", shape="measure", allowed=True),
    Row(
        id="sensitivity-confidential-does-not-cover-aggregated",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(Grant(sensitivity=SensitivityScope.CONFIDENTIAL),),
        pins="S6",
    ),
    Row(
        id="sensitivity-restricted-does-not-cover-aggregated",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(CREDIT_RESTRICTED,),
        pins="S6",
    ),
    Row(
        id="sensitivity-all",
        shape="measure",
        allowed=True,
        grants=(Grant(sensitivity=SensitivityScope.ALL),),
    ),
    # --- institution scope ---------------------------------------------------------------
    Row(id="institution-exact-target", shape="measure", allowed=True),
    Row(
        id="institution-organization-wide-covers-the-target",
        shape="measure",
        allowed=True,
        grants=(Grant(institution=InstitutionScope.ORGANIZATION),),
    ),
    Row(
        id="institution-organization-wide-covers-a-sibling",
        shape="measure",
        allowed=True,
        grants=(Grant(institution=InstitutionScope.ORGANIZATION),),
        bank=OTHER_BANK,
    ),
    Row(
        id="institution-exact-target-does-not-cover-a-sibling",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        bank=OTHER_BANK,
        pins="S3",
    ),
    # --- principal kinds -----------------------------------------------------------------
    Row(id="principal-human-with-a-binding", shape="measure", allowed=True),
    Row(
        id="principal-human-without-a-binding",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        grants=(),
    ),
    Row(
        id="principal-tenant-examiner-with-a-binding",
        shape="measure",
        allowed=True,
        principal="examiner",
        pins="S7",
    ),
    Row(
        id="principal-tenant-examiner-without-a-binding",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        principal="examiner",
        grants=(),
        pins="S7",
    ),
    Row(
        id="principal-impersonated-operator",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        principal="impersonated",
        grants=(ORG_WIDE_ALL,),
        reason=REASON_HUMAN_REQUIRED,
        pins="S8",
    ),
    Row(
        id="principal-machine-integration-key",
        shape="measure",
        allowed=False,
        denied=("loans.balance_rc",),
        principal="machine",
        grants=(ORG_WIDE_ALL,),
        reason=REASON_HUMAN_REQUIRED,
        pins="S9",
    ),
    # --- query shapes, held at aggregated only -------------------------------------------
    Row(
        id="shape-group-by-a-restricted-dimension",
        shape="group_restricted",
        allowed=False,
        denied=("counterparty.name",),
        pins="S5",
    ),
    Row(
        id="shape-filter-on-a-restricted-dimension",
        shape="filter_restricted",
        allowed=False,
        denied=("counterparty.name",),
        pins="S4",
    ),
    Row(
        id="shape-hierarchy-crosses-into-restricted-levels",
        shape="hierarchy",
        allowed=False,
        denied=("counterparty.group", "counterparty.id"),
        pins="S5",
    ),
    Row(
        id="shape-pivot-on-a-restricted-dimension",
        shape="pivot",
        allowed=False,
        denied=("counterparty.name",),
        pins="S4",
    ),
    Row(
        id="shape-top-n-over-a-restricted-dimension",
        shape="top_n",
        allowed=False,
        denied=("counterparty.name",),
        pins="S4",
    ),
    Row(
        id="shape-sort-by-a-restricted-dimension",
        shape="sort",
        allowed=False,
        denied=("counterparty.name",),
        pins="S4",
    ),
    Row(
        id="shape-record-level-reference-is-confidential",
        shape="record",
        allowed=False,
        denied=("position.source_reference",),
    ),
    Row(
        id="shape-one-denied-module-denies-the-query",
        shape="mixed",
        allowed=False,
        denied=("deposits.balance_rc",),
    ),
    Row(
        id="shape-cross-module-ratio-needs-every-module-it-reads",
        shape="cross_module",
        allowed=False,
        denied=("test.cross_module_ratio", "deposits.balance_rc"),
        catalogue=CROSS_MODULE_CATALOGUE,
    ),
    # --- query shapes, with the sentences they actually need -----------------------------
    Row(
        id="shape-group-by-a-restricted-dimension-with-both-sentences",
        shape="group_restricted",
        allowed=True,
        grants=(CREDIT_AGGREGATED, CREDIT_RESTRICTED),
    ),
    Row(
        id="shape-filter-on-a-restricted-dimension-with-both-sentences",
        shape="filter_restricted",
        allowed=True,
        grants=(CREDIT_AGGREGATED, CREDIT_RESTRICTED),
    ),
    Row(
        id="shape-hierarchy-with-both-sentences",
        shape="hierarchy",
        allowed=True,
        grants=(CREDIT_AGGREGATED, CREDIT_RESTRICTED),
    ),
    Row(
        id="shape-record-level-reference-with-a-risk-confidential-sentence",
        shape="record",
        allowed=True,
        grants=(
            CREDIT_AGGREGATED,
            Grant(module=ModuleScope.RISK, sensitivity=SensitivityScope.CONFIDENTIAL),
        ),
    ),
    Row(
        id="shape-mixed-modules-with-both-sentences",
        shape="mixed",
        allowed=True,
        grants=(CREDIT_AGGREGATED, Grant(module=ModuleScope.LIQUIDITY)),
    ),
    Row(
        id="shape-cross-module-ratio-with-organization-wide-authority",
        shape="cross_module",
        allowed=True,
        grants=(ORG_WIDE_ALL,),
        catalogue=CROSS_MODULE_CATALOGUE,
    ),
    Row(
        id="shape-everything-with-organization-wide-authority",
        shape="hierarchy",
        allowed=True,
        grants=(ORG_WIDE_ALL,),
    ),
    # --- entitlement is not a grant ------------------------------------------------------
    Row(
        id="entitlement-sdi-holds-every-grant-but-runs-no-fx",
        shape="fx_engine",
        allowed=False,
        denied=("engine.nop_ghs.crd.official",),
        grants=(ORG_WIDE_ALL,),
        bank=SDI_BANK,
        reason=REASON_NOT_ENTITLED,
    ),
    Row(
        id="entitlement-a-bank-runs-fx",
        shape="fx_engine",
        allowed=True,
        grants=(ORG_WIDE_ALL,),
    ),
)


# --- fixtures -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_session: Session) -> None:
    """Drop the hermetic ``viewer/all/all`` sentence so each row grants exactly."""
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.commit()


@pytest.fixture(autouse=True)
def _institutions(db_session: Session) -> None:
    for bank_id, institution_type in (
        (TARGET_BANK, "universal_bank"),
        (OTHER_BANK, "universal_bank"),
        (SDI_BANK, "savings_and_loans"),
    ):
        if db_session.get(Bank, bank_id) is None:
            db_session.add(
                Bank(
                    id=bank_id,
                    organization_id=ORG_1,
                    name=f"Matrix institution {bank_id}",
                    short_name="Matrix",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=institution_type,
                )
            )
    db_session.commit()


def _service_user(db: Session) -> User:
    existing = db.get(User, SERVICE_USER)
    if existing is not None:
        return existing
    user = User(
        id=SERVICE_USER,
        organization_id=ORG_1,
        email="bi.feed@service.test",
        display_name="BI feed service identity",
        role="viewer",
        auth_provider="service",
    )
    db.add(user)
    db.commit()
    return user


def _apply(db: Session, grants: tuple[Grant, ...]) -> int:
    """Create each row and return the principal's resulting ``authv``."""
    user = db.get(User, USER_1)
    assert user is not None
    for row in grants:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=row.bundle,
            scope=authorization.BindingScope(
                row.institution,
                row.bank if row.institution is InstitutionScope.INSTITUTION else None,
                row.module,
                row.sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the BI authorization matrix.",
            commit=False,
        )
    db.commit()
    db.refresh(user)
    return user.authorization_version


def _context(db: Session, principal: PrincipalKind, authorization_version: int) -> TenantContext:
    """The context ``app/api/deps.py`` builds for each credential kind."""
    if principal == "human":
        return TenantContext(
            organization_id=ORG_1,
            actor_user_id=USER_1,
            roles=("viewer",),
            authorization_version=authorization_version,
        )
    if principal == "examiner":
        # The tenant scalar role, not the operator view: a normal human token.
        return TenantContext(
            organization_id=ORG_1,
            actor_user_id=USER_1,
            roles=("examiner",),
            authorization_version=authorization_version,
        )
    if principal == "impersonated":
        # Operator act-as-examiner: staff identity, no tenant user, no authv.
        return TenantContext(
            organization_id=ORG_1,
            actor_user_id=None,
            roles=("examiner",),
            impersonation_context="inspector-session-1",
            actor_operator="staff@aequoros.test",
        )
    _service_user(db)
    return TenantContext(
        organization_id=ORG_1,
        actor_user_id=SERVICE_USER,
        roles=(),
        integration_key_id=uuid4(),
        integration_key_bank_id=TARGET_BANK,
    )


# --- the matrix ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", ROWS, ids=[row.id for row in ROWS])
def test_bi_authorization_matrix(db_session: Session, row: Row) -> None:
    version = _apply(db_session, row.grants)
    bank = db_session.get(Bank, row.bank)
    assert bank is not None
    ctx = _context(db_session, row.principal, version)

    result = authorize_query(
        db_session,
        ctx,
        bank,
        row.catalogue or catalogue(),
        SHAPES[row.shape],
        surface="query",
    )

    assert result.allowed is row.allowed
    assert result.denied_members == row.denied
    if row.reason is not None:
        assert result.reason == row.reason
    if row.allowed:
        # An authorized query names the rows that authorized it (the ETag and
        # the query log key on them) and reads the whole institution in Phase 1.
        assert result.matching_binding_ids
        assert result.data_scope.kind == "all"
    else:
        # A refusal carries no authorizing binding and, by the shape of the
        # decision object, no rows at all.
        assert result.matching_binding_ids == ()
        assert result.reason != "allowed"


def test_the_matrix_covers_every_dimension_the_contract_names() -> None:
    """A guard on the table itself: a dimension silently dropped is a hole."""
    assert {row.principal for row in ROWS} == {"human", "examiner", "impersonated", "machine"}
    assert {grant.bundle for row in ROWS for grant in row.grants} == {
        RoleBundle.VIEWER,
        RoleBundle.AUDITOR,
        RoleBundle.ANALYST,
        RoleBundle.APPROVER,
        RoleBundle.ACCOUNT_ADMIN,
        RoleBundle.ORG_OWNER,
    }
    assert {grant.module for row in ROWS for grant in row.grants} >= {
        ModuleScope.CREDIT,
        ModuleScope.LIQUIDITY,
        ModuleScope.RISK,
        ModuleScope.ALL,
    }
    assert {grant.sensitivity for row in ROWS for grant in row.grants} == {
        SensitivityScope.AGGREGATED,
        SensitivityScope.CONFIDENTIAL,
        SensitivityScope.RESTRICTED,
        SensitivityScope.ALL,
    }
    assert {grant.institution for row in ROWS for grant in row.grants} == {
        InstitutionScope.INSTITUTION,
        InstitutionScope.ORGANIZATION,
    }
    assert {row.shape for row in ROWS} == set(SHAPES)
    assert any(row.grants == () for row in ROWS), "the no-binding case must be in the table"
    assert {row.bank for row in ROWS} == {TARGET_BANK, OTHER_BANK, SDI_BANK}
    assert {row.pins for row in ROWS} >= {"S3", "S4", "S5", "S6", "S7", "S8", "S9"}


# --- the rows that need more than one assertion --------------------------------------------


@pytest.mark.parametrize("shape", sorted(RESTRICTED_IN))
def test_a_restricted_sentence_alone_still_denies_the_aggregated_body(
    db_session: Session, shape: str
) -> None:
    """S6 both ways: holding only ``restricted`` does not serve the aggregate.

    Every shape that reaches a named obligor also reads an aggregated measure,
    so the two sentences are both required and neither implies the other.
    """
    version = _apply(db_session, (CREDIT_RESTRICTED,))
    bank = db_session.get(Bank, TARGET_BANK)
    assert bank is not None

    result = authorize_query(
        db_session,
        _context(db_session, "human", version),
        bank,
        catalogue(),
        SHAPES[shape],
        surface="query",
    )

    assert result.allowed is False
    assert "loans.balance_rc" in result.denied_members
    # The restricted members are the ones this sentence DOES cover, so the
    # refusal must not name them.
    assert not set(RESTRICTED_IN[shape]) & set(result.denied_members)


def test_an_impersonated_operator_is_denied_before_any_binding_is_read(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S8: refused on the principal, not on the grant, and nothing is written.

    The operator view is defined by what it may not persist, and every served
    BI query writes ``bi_query_log``; refusing the credential keeps that log
    mandatory (D-026). The evaluator is never consulted, so a tenant-wide grant
    on the impersonated identity could not help even if one existed.
    """
    version = _apply(db_session, (ORG_WIDE_ALL,))
    bank = db_session.get(Bank, TARGET_BANK)
    assert bank is not None
    audits_before = db_session.scalar(select(func.count()).select_from(AuditEvent))
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.scoped_authorization.evaluate_bank_permission",
        lambda *args, **kwargs: calls.append("called"),
    )

    result = authorize_query(
        db_session,
        _context(db_session, "impersonated", version),
        bank,
        catalogue(),
        SHAPES["measure"],
        surface="query",
    )

    assert result.allowed is False
    assert result.reason == REASON_HUMAN_REQUIRED
    assert calls == []
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == audits_before


def test_a_machine_principal_is_denied_even_holding_its_own_binding(
    db_session: Session,
) -> None:
    """S9: the key's ``integration_writer`` sentence is for ingest, never reads.

    The Phase 4 Power BI feed is a separate route with its own machine
    dependency; this function authorizes human principals only, so a valid,
    unrevoked key with a complete DATA/restricted binding is still refused.
    """
    service = _service_user(db_session)
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=service.id,
        principal_type=PrincipalType.MACHINE,
        role_bundle=RoleBundle.INTEGRATION_WRITER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            TARGET_BANK,
            ModuleScope.DATA,
            SensitivityScope.RESTRICTED,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Machine ingest authority for the matrix.",
        commit=False,
    )
    db_session.commit()
    bank = db_session.get(Bank, TARGET_BANK)
    assert bank is not None

    result = authorize_query(
        db_session,
        _context(db_session, "machine", 1),
        bank,
        catalogue(),
        SHAPES["measure"],
        surface="query",
    )

    assert result.allowed is False
    assert result.reason == REASON_HUMAN_REQUIRED


def test_a_revoked_sentence_stops_serving_the_query(db_session: Session) -> None:
    """S19's authorization half: the decision follows the rows, not a cache.

    The token half — a stale ``authv`` is 401 at the request gate, and the ETag
    changes with it — belongs to ``deps.get_current_principal`` and the route.
    """
    version = _apply(db_session, (CREDIT_AGGREGATED,))
    bank = db_session.get(Bank, TARGET_BANK)
    assert bank is not None
    ctx = _context(db_session, "human", version)

    before = authorize_query(db_session, ctx, bank, catalogue(), SHAPES["measure"], surface="query")
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.commit()
    after = authorize_query(db_session, ctx, bank, catalogue(), SHAPES["measure"], surface="query")

    assert before.allowed is True
    assert after.allowed is False


def test_the_projected_sentences_are_the_platform_vocabulary(db_session: Session) -> None:
    """Every sentence this matrix asserts is expressible in the shipped enums.

    A member declaring a module or sensitivity outside the platform's own
    vocabulary denies (pinned in ``tests/services/bi/test_bi_authorization.py``);
    this checks the other direction — the catalogue the matrix runs against is
    entirely inside it, so no row above is vacuous.
    """
    modules = {Module(member.module) for member in CROSS_MODULE_CATALOGUE.members()}
    sensitivities = {Sensitivity(member.sensitivity) for member in catalogue().members()}

    assert Module.CREDIT in modules
    assert sensitivities == {
        Sensitivity.AGGREGATED,
        Sensitivity.CONFIDENTIAL,
        Sensitivity.RESTRICTED,
    }
