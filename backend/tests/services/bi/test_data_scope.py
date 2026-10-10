"""The data scope: what a branch or region grant resolves to, and what it refuses.

The compiler's own suite already proves that an injected filter is ANDed with the
client's predicates and survives the Top-N ranking and the pivot probe
(``test_compiler.py``). This file is about the layer above it — what the DECLARED
grant becomes — and about the two rules that are refusals rather than filters:

* an institution-grain figure is DENIED to a scoped principal, never narrowed,
  and so is a portfolio figure the platform cannot attribute to a branch;
* a scope that resolves to no branch serves NO ROWS. That is the one case where
  the natural reader (``if codes: filter``) hands over the whole book, so each of
  its three shapes is exercised: a region no branch belongs to, a granted branch
  code the Data Engine has never seen, and the ``none`` scope, which must not be
  reachable from an allowed decision at all.

The mart is ``test_compiler``'s, reused rather than rebuilt: B1 is in region
North and carries 100 + 300, B2 and B3 are in region South and carry 600 (plus an
unconverted USD loan and a 1 000 deposit), and ORG_2's own B1 sits in region
"Elsewhere" so a resolution that leaked across tenants would show.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.domain.bi.catalogue import Catalogue, catalogue
from app.identity.service import authorization
from app.models import AuthorizationBinding, Bank, User
from app.models.bi import BiDimBranch
from app.schemas.bi import BI_FILTER_IN_CAP, BiFilter, BiQuery
from app.services.bi import data_scope
from app.services.bi.authorization import (
    ALL_INSTITUTION_DATA,
    BRANCH_FACT_KEY,
    NO_INSTITUTION_DATA,
    REASON_BANK_WIDE_FIGURE,
    REASON_INSTITUTION_GRAIN,
    BiDataScope,
    authorize_query,
    bank_wide_measures,
    branch_attributable,
    branch_readable,
    institution_grain_measures,
)
from app.services.bi.compiler import _DIM_JOIN_KEYS, compile_query
from app.services.bi.errors import BiQueryError
from app.services.bi.execution import execute
from tests.services.bi.test_compiler import BUILT_AT, SEP_18, seed_compiler_mart
from tests.support.helpers import ORG_1, USER_1

AS_OF = SEP_18

#: A branch-attributable portfolio measure, an institution-grain engine ratio, and
#: a bank-wide target variant of that ratio: the three cases the rules divide on.
PORTFOLIO_MEASURE = "loans.balance_rc"
INSTITUTION_MEASURE = "engine.npl_ratio_pct.crd.official"
TARGET_MEASURE = "loans.balance_rc.budget.actual"


@pytest.fixture
def cat() -> Catalogue:
    return catalogue()


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_compiler_mart(db_session)


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_session: Session) -> None:
    """Drop the hermetic ``viewer/all/all`` sentence so each test grants exactly.

    It is an institution-wide grant with ``data_scope='all'``, and bindings OR —
    so leaving it in place would make every scoped grant below resolve to the
    whole institution, which is the correct reduction and the wrong test.
    """
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    db_session.flush()


def _query(**overrides: Any) -> BiQuery:
    payload: dict[str, Any] = {"measures": [PORTFOLIO_MEASURE], "time": {"as_of": AS_OF}}
    payload.update(overrides)
    return BiQuery.model_validate(payload)


def _grant(  # noqa: PLR0913 - one keyword per binding dimension
    db: Session,
    bank: Bank,
    *,
    module: ModuleScope,
    scope: DataScope = DataScope.ALL,
    values: tuple[str, ...] = (),
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED,
) -> int:
    """One indivisible sentence for USER_1, with its data scope. Returns ``authv``."""

    authorization.create_role_binding(
        db,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            bank.id,
            module,
            sensitivity,
            scope,
            values,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the BI data scope.",
    )
    user = db.get(User, USER_1)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def _context(version: int) -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version)


def _decide(db: Session, bank: Bank, cat: Catalogue, query: BiQuery, version: int) -> Any:
    return authorize_query(db, _context(version), bank, cat, query, surface="query")


def _run(
    db: Session,
    cat: Catalogue,
    bank: Bank,
    query: BiQuery,
    injected: tuple[BiFilter, ...],
) -> list[tuple[Any, ...]]:
    compiled = compile_query(
        db,
        cat,
        query,
        organization_id=bank.organization_id,
        bank_id=bank.id,
        injected_filters=injected,
    )
    return execute(db, compiled, timeout_ms=5_000, row_cap=100).rows


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _regions(db: Session, bank: Bank, assignment: dict[str, str]) -> None:
    """Re-label the fixture's branches so a region grant has something to mean."""

    for code, region in assignment.items():
        row = db.get(BiDimBranch, (bank.organization_id, bank.id, code))
        assert row is not None
        row.region = region
    db.flush()


# --- the catalogue's own two grains ---------------------------------------------------------


def test_the_catalogue_divides_into_the_grains_the_rules_name(cat: Catalogue) -> None:
    """A guard on the premise: the rules are only as good as the declarations.

    984 of the 1 416 measures are ``grain="institution"`` (the 174 engine copies
    and the 810 target variants over them) and 432 are ``portfolio``; of those,
    388 sit on ``bi_fact_target``, which carries no branch key, so a scoped
    principal may be served exactly the 44 measures on the position and event
    facts. The numbers are asserted as RELATIONS rather than as literals, so
    adding a measure does not break this — but a measure landing in neither grain,
    or a fact quietly losing its branch key, does.
    """
    measures = list(cat.measures())
    grains = {measure.grain for measure in measures}
    assert grains == {"institution", "portfolio"}
    institution = institution_grain_measures(measures)
    bank_wide = bank_wide_measures(measures)
    readable = [measure for measure in measures if branch_readable(measure)]
    assert len(institution) + len(bank_wide) + len(readable) == len(measures)
    assert institution and bank_wide and readable
    # Every readable measure is on a fact with a branch key, and no other is.
    assert {measure.table for measure in readable} == {
        table for table in {m.table for m in measures} if branch_attributable(table)
    }
    # Every dimension is readable: a dimension is a way of slicing, not a figure.
    assert all(branch_readable(dimension) for dimension in cat.dimensions())


def test_branch_attributability_is_read_off_the_same_key_the_compiler_joins_on() -> None:
    """The restatement in ``authorization`` cannot drift from the compiler's map."""
    fact_key, _ = _DIM_JOIN_KEYS[BiDimBranch.__tablename__]
    assert fact_key == "branch_code"
    assert fact_key == BRANCH_FACT_KEY
    assert branch_attributable("bi_fact_position_daily") is True
    assert branch_attributable("bi_fact_target") is False
    # Deny-by-default applies to the QUESTION too.
    assert branch_attributable("no_such_table") is False


# --- resolution ------------------------------------------------------------------------------


def test_an_institution_wide_scope_resolves_to_no_filter(db_session: Session, mart: Bank) -> None:
    resolved = data_scope.resolve(
        db_session, ALL_INSTITUTION_DATA, organization_id=ORG_1, bank_id=mart.id
    )
    assert resolved.whole_institution is True
    assert resolved.filters == ()
    assert resolved.label == "Whole institution"


def test_a_branch_scope_filters_on_the_codes_as_granted(db_session: Session, mart: Bank) -> None:
    declared = BiDataScope(kind="branch", branches=("B1",))
    resolved = data_scope.resolve(db_session, declared, organization_id=ORG_1, bank_id=mart.id)
    assert resolved.branch_codes == ("B1",)
    assert resolved.filters == (BiFilter(member="branch.code", op="in", values=["B1"]),)
    assert resolved.label == "Branches: B1"


def test_a_region_scope_resolves_to_the_branches_that_region_holds(
    db_session: Session, mart: Bank
) -> None:
    _regions(db_session, mart, {"B1": "North", "B2": "South", "B3": "South"})
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="region", regions=("South",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.branch_codes == ("B2", "B3")
    assert resolved.filters == (BiFilter(member="branch.code", op="in", values=["B2", "B3"]),)
    assert resolved.label == "Regions: South · 2 branches in scope"


def test_a_region_scope_picks_up_a_branch_ingested_after_the_grant(
    db_session: Session, mart: Bank
) -> None:
    """What a region grant MEANS, and deliberately not a leak."""
    _regions(db_session, mart, {"B1": "North", "B2": "South", "B3": "South"})
    before = data_scope.resolve(
        db_session,
        BiDataScope(kind="region", regions=("North",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert before.branch_codes == ("B1",)
    db_session.add(
        BiDimBranch(
            organization_id=ORG_1,
            bank_id=mart.id,
            branch_code="B4",
            name="New northern branch",
            region="North",
            mapped=True,
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    db_session.flush()
    after = data_scope.resolve(
        db_session,
        BiDataScope(kind="region", regions=("North",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert after.branch_codes == ("B1", "B4")


def test_a_mixed_scope_unions_the_declared_codes_with_the_regions(
    db_session: Session, mart: Bank
) -> None:
    _regions(db_session, mart, {"B1": "North", "B2": "South", "B3": "South"})
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="mixed", branches=("B1",), regions=("South",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.branch_codes == ("B1", "B2", "B3")
    assert "Branches: B1" in resolved.label
    assert "Regions: South" in resolved.label


def test_a_resolution_never_crosses_the_tenant_or_the_sibling_institution(
    db_session: Session, mart: Bank
) -> None:
    """ORG_2's own B1 is in region "Elsewhere"; neither id may reach it."""
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="region", regions=("Elsewhere",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.branch_codes == ()


# --- the three ways a scope can serve nothing ------------------------------------------------


def test_a_region_naming_no_branch_serves_no_rows_rather_than_the_book(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The empty resolution: filtered on the REGION, which matches no fact row."""
    _regions(db_session, mart, {"B1": "North", "B2": "South", "B3": "South"})
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="region", regions=("Volta",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.branch_codes == ()
    assert resolved.filters == (BiFilter(member="branch.region", op="in", values=["Volta"]),)
    assert resolved.label == "Regions: Volta · no branches in scope"

    rows = _run(db_session, cat, mart, _query(), resolved.filters)
    assert _num(rows[0][0]) is None  # a sum of nothing is NULL, never 0 and never 1 000


def test_a_granted_branch_code_that_was_never_ingested_serves_no_rows(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The code stays in the filter on purpose: dropping it would widen the scope."""
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="branch", branches=("BR-NEVER-FED",)),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.branch_codes == ("BR-NEVER-FED",)
    rows = _run(db_session, cat, mart, _query(), resolved.filters)
    assert _num(rows[0][0]) is None


def test_the_none_scope_refuses_loudly_instead_of_returning_no_filters(
    db_session: Session, mart: Bank
) -> None:
    """Returning ``()`` from here would BE "serve the whole institution"."""
    resolved = data_scope.resolve(
        db_session, NO_INSTITUTION_DATA, organization_id=ORG_1, bank_id=mart.id
    )
    assert resolved.serves_nothing is True
    with pytest.raises(data_scope.DataScopeServesNothing):
        _ = resolved.filters
    # Not a BiQueryError, so no serving surface can file it as "query refused".
    assert not issubclass(data_scope.DataScopeServesNothing, BiQueryError)


def test_a_narrow_scope_naming_no_value_refuses_as_the_typed_invariant_violation(
    db_session: Session, mart: Bank
) -> None:
    """Fail-closed by design, not by accident (audit A360-1).

    Unreachable through the database — the column CHECK and the binding writer
    both refuse an empty value list — so before this it was ``BiFilter`` that
    refused, with a pydantic ``ValidationError`` about an ``in`` list needing a
    value. The platform's own invariant violation is the answer, at both seams:
    the declared scope arriving at ``resolve`` and a ``ResolvedDataScope`` built
    by hand and asked for its filters.
    """
    for kind in ("branch", "region", "mixed"):
        with pytest.raises(data_scope.DataScopeServesNothing):
            _ = data_scope.ResolvedDataScope(kind=kind).filters
        with pytest.raises(data_scope.DataScopeServesNothing):
            data_scope.resolve(
                db_session, BiDataScope(kind=kind), organization_id=ORG_1, bank_id=mart.id
            )
    # And the refusal is the invariant kind, never a client error the surfaces
    # would file as "the query was refused" and move on from.
    assert not issubclass(data_scope.DataScopeServesNothing, BiQueryError)


def test_a_scope_wider_than_one_filter_list_refuses_rather_than_truncating(
    db_session: Session, mart: Bank
) -> None:
    codes = tuple(f"BR-{index:05d}" for index in range(BI_FILTER_IN_CAP + 1))
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="branch", branches=codes),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    with pytest.raises(data_scope.DataScopeUnservable) as excinfo:
        _ = resolved.filters
    assert str(BI_FILTER_IN_CAP) in excinfo.value.message


def test_the_label_summarises_a_long_scope_rather_than_printing_a_page(
    db_session: Session, mart: Bank
) -> None:
    codes = tuple(f"BR-{index:03d}" for index in range(30))
    resolved = data_scope.resolve(
        db_session,
        BiDataScope(kind="branch", branches=codes),
        organization_id=ORG_1,
        bank_id=mart.id,
    )
    assert resolved.label.endswith("and 18 more")


# --- the fingerprint the ETag is keyed on ----------------------------------------------------


def test_two_different_scopes_fingerprint_differently(db_session: Session, mart: Bank) -> None:
    _regions(db_session, mart, {"B1": "North", "B2": "South", "B3": "South"})
    kwargs = {"organization_id": ORG_1, "bank_id": mart.id}
    one = data_scope.resolve(db_session, BiDataScope(kind="branch", branches=("B1",)), **kwargs)
    two = data_scope.resolve(db_session, BiDataScope(kind="branch", branches=("B2",)), **kwargs)
    whole = data_scope.resolve(db_session, ALL_INSTITUTION_DATA, **kwargs)
    north = BiDataScope(kind="region", regions=("North",))
    region = data_scope.resolve(db_session, north, **kwargs)
    assert len({one.fingerprint, two.fingerprint, whole.fingerprint, region.fingerprint}) == 4
    # A region resolving to the same branch as a branch grant is still a different
    # SENTENCE, and a branch joining the region later changes the answer.
    assert region.branch_codes == one.branch_codes
    assert region.fingerprint != one.fingerprint


# --- the injected filter is an intersection, on every compiler shape -------------------------


def test_a_contradictory_client_filter_is_intersected_not_honoured(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """The client asks for B2; the grant says B1. Neither side wins — both apply."""
    scope = (BiFilter(member="branch.code", op="in", values=["B1"]),)
    rows = _run(
        db_session,
        cat,
        mart,
        _query(filters=[{"member": "branch.code", "op": "in", "values": ["B2"]}]),
        scope,
    )
    assert _num(rows[0][0]) is None  # not 600 (B2) and not 400 (B1)
    # And the grant alone still serves B1, so the intersection is what emptied it.
    assert _num(_run(db_session, cat, mart, _query(), scope)[0][0]) == 400.0


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("plain", {}),
        ("grouped", {"dimensions": ["loan.sector"]}),
        (
            "top_n",
            {"dimensions": ["loan.sector"], "top_n": {"dimension": "loan.sector", "n": 1}},
        ),
        ("pivot", {"dimensions": ["branch.code"], "pivot": {"dimension": "loan.sector"}}),
        ("comparison", {"time": {"as_of": AS_OF, "compare_to": date(2026, 8, 31)}}),
        (
            "range",
            {
                "dimensions": ["time.calendar_month"],
                "time": {"range": {"start": date(2026, 8, 1), "end": AS_OF}},
            },
        ),
        ("drill", {"dimensions": ["position.source_reference"]}),
        ("sorted", {"dimensions": ["loan.sector"], "sort": [{"member": "loan.sector"}]}),
    ],
)
def test_the_scope_survives_every_compiler_shape(
    db_session: Session, cat: Catalogue, mart: Bank, label: str, overrides: dict[str, Any]
) -> None:
    """A filter dropped in ONE of these paths is a leak; each is its own code path.

    The test is value-based rather than structural: B2's 600 (and B3's 1 000
    deposit) exist in the mart, so any shape that returns a figure containing them
    under a B1-only grant has lost the filter. Every number here is ≤ 400.
    """
    _ = label
    scope = (BiFilter(member="branch.code", op="in", values=["B1"]),)
    rows = _run(db_session, cat, mart, _query(**overrides), scope)
    figures = [
        _num(value)
        for row in rows
        for value in row
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    ]
    assert figures, "the shape returned no figure at all, so it proves nothing"
    assert all(figure is None or figure <= 400.0 for figure in figures), figures


def test_the_scope_reaches_the_query_log_members_without_naming_a_branch_code(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``bi_query_log`` records THAT the read was narrowed, never by which value."""
    scope = (BiFilter(member="branch.code", op="in", values=["B1"]),)
    compiled = compile_query(
        db_session,
        cat,
        _query(),
        organization_id=ORG_1,
        bank_id=mart.id,
        injected_filters=scope,
    )
    assert compiled.injected_member_ids == ("branch.code",)
    assert "branch.code" in compiled.member_ids
    assert "B1" not in compiled.injected_member_ids


# --- the two refusals ------------------------------------------------------------------------


def test_an_institution_grain_measure_is_denied_to_a_branch_scoped_principal(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    version = _grant(
        db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.BRANCH, values=("B1",)
    )

    decision = _decide(db_session, mart, cat, _query(measures=[INSTITUTION_MEASURE]), version)

    assert decision.allowed is False
    assert decision.reason == REASON_INSTITUTION_GRAIN
    assert decision.denied_members == (INSTITUTION_MEASURE,)
    assert decision.matching_binding_ids == ()
    # A refusal can never be read as institution-wide authority.
    assert decision.data_scope.whole_institution is False
    assert decision.data_scope.serves_nothing is True


def test_the_same_measure_is_allowed_to_an_institution_wide_principal(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    version = _grant(db_session, mart, module=ModuleScope.ALL)

    decision = _decide(db_session, mart, cat, _query(measures=[INSTITUTION_MEASURE]), version)

    assert decision.allowed is True
    assert decision.data_scope == ALL_INSTITUTION_DATA


def test_a_bank_wide_target_figure_is_denied_to_a_scoped_principal(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """Portfolio grain, but ``bi_fact_target`` rows are stated for the whole bank.

    Serving one beside a branch's actual would produce an attainment of a branch
    against the institution's budget — a wrong number wearing a right name, which
    is the same defect the grain rule exists for.
    """
    assert cat.measure(TARGET_MEASURE).grain == "portfolio"
    version = _grant(
        db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.BRANCH, values=("B1",)
    )

    decision = _decide(db_session, mart, cat, _query(measures=[TARGET_MEASURE]), version)

    assert decision.allowed is False
    assert decision.reason == REASON_BANK_WIDE_FIGURE
    assert decision.denied_members == (TARGET_MEASURE,)


def test_a_mixed_query_names_every_figure_the_grant_would_have_to_widen_for(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """What the Org Owner needs is the complete list, not the first refusal."""
    version = _grant(
        db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.REGION, values=("North",)
    )

    decision = _decide(
        db_session,
        mart,
        cat,
        _query(measures=[PORTFOLIO_MEASURE, INSTITUTION_MEASURE, TARGET_MEASURE]),
        version,
    )

    assert decision.allowed is False
    assert decision.reason == REASON_INSTITUTION_GRAIN
    assert set(decision.denied_members) == {INSTITUTION_MEASURE, TARGET_MEASURE}
    assert PORTFOLIO_MEASURE not in decision.denied_members


def test_a_scoped_principal_is_served_a_branch_attributable_figure_with_its_scope(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    version = _grant(
        db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.BRANCH, values=("B1",)
    )

    decision = _decide(db_session, mart, cat, _query(), version)

    assert decision.allowed is True
    assert decision.data_scope == BiDataScope(kind="branch", branches=("B1",))
    resolved = data_scope.resolve(
        db_session, decision.data_scope, organization_id=ORG_1, bank_id=mart.id
    )
    assert _num(_run(db_session, cat, mart, _query(), resolved.filters)[0][0]) == 400.0


def test_the_widest_matching_binding_wins(db_session: Session, cat: Catalogue, mart: Bank) -> None:
    """Bindings OR: narrowing a reader who also holds an institution-wide sentence
    would revoke authority the Org Owner granted."""
    _grant(db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.BRANCH, values=("B1",))
    version = _grant(db_session, mart, module=ModuleScope.ALL)

    decision = _decide(db_session, mart, cat, _query(), version)

    assert decision.allowed is True
    assert decision.data_scope == ALL_INSTITUTION_DATA


def test_a_revoked_binding_contributes_no_scope_and_no_authority(
    db_session: Session, cat: Catalogue, mart: Bank
) -> None:
    """``effective_data_scope`` re-reads the rows; the id list is only a selector."""
    version = _grant(
        db_session, mart, module=ModuleScope.CREDIT, scope=DataScope.BRANCH, values=("B1",)
    )
    assert _decide(db_session, mart, cat, _query(), version).allowed is True

    db_session.execute(
        update(AuthorizationBinding)
        .where(AuthorizationBinding.organization_id == ORG_1)
        .values(
            status="revoked",
            revoked_at=utc_now(),
            revoked_by_type=GrantorType.SYSTEM.value,
            revoked_by_id="test-suite",
            revoked_reason="Exercise the re-read.",
        )
    )
    db_session.flush()

    assert _decide(db_session, mart, cat, _query(), version).allowed is False
