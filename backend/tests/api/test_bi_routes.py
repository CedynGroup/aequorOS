"""The BI read surface end to end: ``/api/v1/banks/{bank_id}/bi/*``.

The order the handler consults its guards in is the security property, so this
file asserts the order rather than the pieces: a sibling tenant's institution is
404 on every route whatever the deployment flag says; the flag makes the whole
surface absent; an impersonated operator and a machine key never reach a query;
a denial names the members and carries no rows; the catalogue advertises exactly
what the query path will serve; and neither the row cap, the page size nor the
grid's paging can be widened from the request.

The mart below is hand-built (four positions, two branches, two obligors, one
engine copy) for the same reason the compiler's own fixture is: every expected
number is derivable by reading it, so a failure names a broken rule instead of a
drifted golden. It is written directly rather than through the builder because
what is under test here is the ROUTE — the builder has its own suites.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.api.deps import MUTATION_ROLE_DEPENDENCY_NAMES
from app.core import security
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.base import utc_now
from app.domain.bi.catalogue import Catalogue, ColumnRef, DimensionDef, MeasureDef, catalogue
from app.domain.bi.catalogue.dimensions import POSITION_TABLE
from app.features import read_bi
from app.models import AuthorizationBinding, Bank, User
from app.models.bi import (
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactPositionDaily,
    BiMartBuild,
    BiQueryLog,
    BiReconciliationResult,
)
from app.schemas.bi import BiDateRange, BiQuery, BiTime
from app.services import authorization
from app.services.bi import compiler, query_log, reconciliation
from app.services.bi.authorization import query_members
from app.services.bi.compiler import compile_query
from app.services.bi.errors import BiQueryError
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers

AS_OF = dt.date(2026, 8, 31)
BUILT_AT = dt.datetime(2026, 9, 1, 2, tzinfo=dt.UTC)
BANK_ID = "BK-BIROUTE1"
SIBLING_BANK_ID = "BK-BIOTHER1"
#: A SECOND institution of the same tenant: in scope for the 404 rule, out of
#: scope for an institution-scoped binding that names only the first (S3).
SAME_TENANT_BANK_ID = "BK-BIROUTE2"
CP_ONE = UUID("11111111-1111-4111-8111-111111111111")
CP_TWO = UUID("22222222-2222-4222-8222-222222222222")
FINGERPRINT = "f" * 64
#: Figures that exist ONLY on the second institution of the same tenant, so a
#: response that contains one of them has leaked across institutions.
OTHER_BANK_LOANS = Decimal("8123456789.12")
OTHER_BANK_BRANCH = "BR-KUMASI-07"
OTHER_BANK_ROWS = 41234
OTHER_BANK_FINGERPRINT = "a1b2c3d4" * 8

BASE = f"/api/v1/banks/{BANK_ID}/bi"
SIBLING_BASE = f"/api/v1/banks/{SIBLING_BANK_ID}/bi"

#: Every route, as (method, path suffix, minimal valid body). One list so a new
#: route cannot be added without deciding what it answers in each case below.
ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/catalogue", None),
    ("GET", "/trust?as_of=2026-08-31", None),
    ("POST", "/query", {"measures": ["loans.balance_rc"], "time": {"as_of": "2026-08-31"}}),
    (
        "POST",
        "/grid",
        {"query": {"measures": ["loans.balance_rc"], "time": {"as_of": "2026-08-31"}}},
    ),
    (
        "POST",
        "/drill",
        {
            "query": {
                "measures": ["loans.balance_rc"],
                "dimensions": ["position.source_reference"],
                "time": {"as_of": "2026-08-31"},
            }
        },
    ),
    (
        "POST",
        "/explain",
        {
            "query": {"measures": ["loans.balance_rc"], "time": {"as_of": "2026-08-31"}},
            "measure": "loans.balance_rc",
        },
    ),
    # T3: a governed export is a BI read that leaves the platform, so it must
    # answer every one of the sweeps below exactly as the read routes do — the
    # flag, the cross-tenant 404, the impersonated operator, the zero-binding
    # human. Its own policy (summary vs record-level) is tested in
    # ``tests/api/test_bi_exports.py``.
    (
        "POST",
        "/export",
        {
            "query": {"measures": ["loans.balance_rc"], "time": {"as_of": "2026-08-31"}},
            "format": "csv",
        },
    ),
    # T7: a certified dashboard resolved for a reader, and the statements the
    # platform will make about a date. Both join the sweeps for the same reason
    # the export did — the flag, the cross-tenant 404, the impersonated operator,
    # the zero-binding human — and each answers the last of those differently,
    # which is stated route by route in
    # ``test_no_route_serves_a_principal_holding_no_binding``. Their own behaviour
    # is tested in ``tests/api/test_bi_packs.py`` and ``test_bi_insights.py``.
    ("GET", "/packs?as_of=2026-08-31", None),
    ("GET", "/packs/board?as_of=2026-08-31", None),
    ("GET", "/insights?as_of=2026-08-31", None),
)

BALANCE_BY_BRANCH_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["branch.code"],
    "time": {"as_of": AS_OF.isoformat()},
}
OBLIGOR_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["counterparty.name"],
    "time": {"as_of": AS_OF.isoformat()},
}
RECORD_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["position.source_reference"],
    "time": {"as_of": AS_OF.isoformat()},
}


# --- the mart -----------------------------------------------------------------------------


def _bank(db: Session, bank_id: str, organization_id: str) -> Bank:
    existing = db.get(Bank, bank_id)
    if existing is not None:
        return existing
    bank = Bank(
        id=bank_id,
        organization_id=organization_id,
        name=f"BI route bank {bank_id}",
        short_name="BI route",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _loan(  # noqa: PLR0913 - one keyword per column the assertions reason about
    bank: Bank,
    *,
    reference: str,
    branch: str,
    balance: Decimal,
    counterparty: UUID,
    non_performing: bool = False,
    position_type: str = "LOAN",
) -> BiFactPositionDaily:
    return BiFactPositionDaily(
        as_of_date=AS_OF,
        snapshot_id=uuid4(),
        position_id=uuid4(),
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_system="API_PUSH",
        source_reference=reference,
        position_type=position_type,
        currency=bank.currency,
        balance_native=balance,
        balance_rc=balance,
        fx_unconverted=False,
        classification_exposure_rc=balance if position_type == "LOAN" else None,
        non_performing=non_performing if position_type == "LOAN" else None,
        branch_code=branch,
        counterparty_id=counterparty,
        product_code="P-RETAIL",
        product_family="retail_loans",
        sector="trade",
        dpd_band="current",
        grade="standard",
        ifrs9_stage=1,
        builder_version=1,
        built_at=BUILT_AT,
    )


_AGG_GRAIN: tuple[str, ...] = (
    "position_type",
    "product_family",
    "branch_code",
    "currency",
    "ifrs9_stage",
    "dpd_band",
    "grade",
    "deposit_account_type",
)


def _aggregates(facts: list[BiFactPositionDaily]) -> list[BiAggPositionDaily]:
    """``bi_agg_position_daily`` by the contract's definition: additive sums per grain.

    The compiler reads the daily aggregate whenever every requested member is
    grain-local, so a fixture with facts and no aggregate answers a perfectly
    ordinary query with zero rows. Derived from the facts here rather than
    hand-written, so the two can never disagree.
    """

    groups: dict[tuple[Any, ...], list[BiFactPositionDaily]] = {}
    for fact in facts:
        key = tuple(getattr(fact, column) for column in _AGG_GRAIN)
        groups.setdefault(key, []).append(fact)

    def total(rows: list[BiFactPositionDaily], column: str) -> Decimal:
        return sum((getattr(row, column) or Decimal("0") for row in rows), Decimal("0"))

    out: list[BiAggPositionDaily] = []
    for key, rows in groups.items():
        first = rows[0]
        out.append(
            BiAggPositionDaily(
                as_of_date=AS_OF,
                id=uuid4(),
                organization_id=first.organization_id,
                bank_id=first.bank_id,
                **dict(zip(_AGG_GRAIN, key, strict=True)),
                row_count=len(rows),
                balance_rc_sum=total(rows, "balance_rc"),
                classification_exposure_rc_sum=total(rows, "classification_exposure_rc"),
                non_performing_exposure_rc_sum=sum(
                    (
                        row.classification_exposure_rc or Decimal("0")
                        for row in rows
                        if row.non_performing
                    ),
                    Decimal("0"),
                ),
                provision_required_rc_sum=total(rows, "provision_required_rc"),
                provision_held_rc_sum=total(rows, "provision_held_rc"),
                collateral_rc_sum=total(rows, "collateral_rc"),
                rate_x_balance_rc_sum=Decimal("0"),
                fx_unconverted_count=sum(1 for row in rows if row.fx_unconverted),
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    return out


def seed_bi_mart(db: Session) -> Bank:
    """One institution's mart for :data:`AS_OF`, plus a sibling tenant's bank.

    Loans: B1 carries 100 + 300 (the 300 non-performing), B2 carries 200. One
    deposit of 50 sits outside every loan measure. Two obligors, so a group by
    obligor name has something to hide.
    """

    bank = _bank(db, BANK_ID, ORG_1)
    _bank(db, SIBLING_BANK_ID, ORG_2)
    _bank(db, SAME_TENANT_BANK_ID, ORG_1)
    facts = [
        _loan(
            bank,
            reference="L-1",
            branch="B1",
            balance=Decimal("100"),
            counterparty=CP_ONE,
        ),
        _loan(
            bank,
            reference="L-2",
            branch="B1",
            balance=Decimal("300"),
            counterparty=CP_TWO,
            non_performing=True,
        ),
        _loan(
            bank,
            reference="L-3",
            branch="B2",
            balance=Decimal("200"),
            counterparty=CP_ONE,
        ),
        _loan(
            bank,
            reference="D-1",
            branch="B2",
            balance=Decimal("50"),
            counterparty=CP_ONE,
            position_type="DEPOSIT",
        ),
    ]
    db.add_all(facts)
    db.add_all(_aggregates(facts))
    for code, name in (("B1", "Head office"), ("B2", "Harbour branch")):
        db.add(
            BiDimBranch(
                organization_id=ORG_1,
                bank_id=bank.id,
                branch_code=code,
                name=name,
                region="Unassigned region",
                mapped=True,
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    for counterparty_id, name in ((CP_ONE, "Ada Traders"), (CP_TWO, "Kwesi Holdings")):
        db.add(
            BiDimCounterparty(
                organization_id=ORG_1,
                bank_id=bank.id,
                counterparty_id=counterparty_id,
                source_reference=str(counterparty_id)[:8],
                name=name,
                counterparty_type="corporate",
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    db.add(
        BiDimProduct(
            organization_id=ORG_1,
            bank_id=bank.id,
            product_code="P-RETAIL",
            name="Retail term loan",
            product_family="retail_loans",
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    db.add(
        BiDimDate(
            organization_id=ORG_1,
            bank_id=bank.id,
            date=AS_OF,
            has_data=True,
            is_last_in_month=True,
            is_last_in_quarter=False,
            is_last_in_year=False,
            calendar_month=AS_OF.replace(day=1),
            calendar_quarter=dt.date(2026, 7, 1),
            calendar_year=2026,
            fiscal_year=2026,
            fiscal_quarter=3,
            builder_version=1,
            built_at=BUILT_AT,
        )
    )
    for tier in ("official", "live"):
        db.add(
            BiFactEngineMetric(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                module="capital",
                metric_id="car_pct",
                tier=tier,
                value=Decimal("14.25"),
                unit="pct",
                status="green",
                regime="crd",
                institution_class="bank",
                advisory_designation="filed",
                input_hash="a" * 64,
                engine_version="7",
                pipeline_state="ready",
                reconciliation_blocked=False,
                computed_at=BUILT_AT,
                builder_version=1,
                built_at=BUILT_AT,
            )
        )
    for scope in ("positions", "events", "gl", "engine", "dims"):
        db.add(
            BiMartBuild(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                scope=scope,
                fingerprint=FINGERPRINT,
                status="succeeded",
                builder_version=1,
                started_at=BUILT_AT,
                finished_at=BUILT_AT,
                row_counts={},
            )
        )
    for check_id, status in (("R2", reconciliation.GREEN), ("R7", reconciliation.AMBER)):
        db.add(
            BiReconciliationResult(
                organization_id=ORG_1,
                bank_id=bank.id,
                as_of_date=AS_OF,
                check_id=check_id,
                status=status,
                lhs=Decimal("600"),
                rhs=Decimal("600"),
                difference=Decimal("0"),
                tolerance=Decimal("0.0001"),
                detail={"note": "seeded"},
                builder_version=1,
                evaluated_at=BUILT_AT,
            )
        )
    # The SECOND institution of the same tenant carries figures of its own, with
    # values that appear nowhere else, so "the 403 leaked nothing" is a real
    # assertion rather than a statement about an empty table (audit A6-01).
    db.add(
        BiReconciliationResult(
            organization_id=ORG_1,
            bank_id=SAME_TENANT_BANK_ID,
            as_of_date=AS_OF,
            check_id="R2",
            status=reconciliation.RED,
            lhs=OTHER_BANK_LOANS,
            rhs=Decimal("0"),
            difference=OTHER_BANK_LOANS,
            tolerance=Decimal("0.0001"),
            detail={"unmapped_codes": [OTHER_BANK_BRANCH]},
            builder_version=1,
            evaluated_at=BUILT_AT,
        )
    )
    db.add(
        BiMartBuild(
            organization_id=ORG_1,
            bank_id=SAME_TENANT_BANK_ID,
            as_of_date=AS_OF,
            scope="positions",
            fingerprint=OTHER_BANK_FINGERPRINT,
            status="succeeded",
            builder_version=1,
            started_at=BUILT_AT,
            finished_at=BUILT_AT,
            row_counts={"bi_fact_position_daily": OTHER_BANK_ROWS},
        )
    )
    db.commit()
    return bank


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_bi_mart(db_session)


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()


# --- authority ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Grant:
    module: ModuleScope
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED
    institution: InstitutionScope = InstitutionScope.INSTITUTION
    bundle: RoleBundle = RoleBundle.VIEWER


#: Enough to read loan totals by branch: the loan measures are CREDIT and the
#: branch dimension is RISK. Nothing here covers an obligor NAME (CREDIT /
#: restricted) or a position reference (RISK / confidential).
AGGREGATE_ONLY: tuple[Grant, ...] = (
    Grant(ModuleScope.CREDIT),
    Grant(ModuleScope.RISK),
)

#: The three pairs ``GET trust`` discloses (credit loans / NPL / arrears, the
#: liquidity deposit total, and the risk-plane positions, GL and branch figures).
AGGREGATE_ONLY_WITH_DEPOSITS: tuple[Grant, ...] = (*AGGREGATE_ONLY, Grant(ModuleScope.LIQUIDITY))

#: Every member the trust payload discloses. Derived from the route's own probe;
#: the SET is pinned against ``CHECK_DISCLOSURES`` by its own test.
TRUST_MEMBERS: tuple[str, ...] = tuple(
    member.id for member in query_members(catalogue(), read_bi.trust_probe(AS_OF))
)


def grant_only(db: Session, grants: tuple[Grant, ...]) -> int:
    """Replace the fixture's org-wide sentence with exactly ``grants``.

    Returns the principal's resulting ``authv``, which every token must carry:
    creating a binding invalidates the user's sessions by design.
    """

    db.execute(delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1))
    db.commit()
    for grant in grants:
        authorization.create_role_binding(
            db,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=grant.bundle,
            scope=authorization.BindingScope(
                grant.institution,
                BANK_ID if grant.institution is InstitutionScope.INSTITUTION else None,
                grant.module,
                grant.sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the BI read surface.",
            commit=False,
        )
    db.commit()
    user = db.get(User, USER_1)
    assert user is not None
    db.refresh(user)
    return user.authorization_version


def impersonation_headers() -> dict[str, str]:
    secret = get_settings().auth.impersonation_jwt_secret
    assert secret
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=ORG_1,
        act_operator="ops@aequoros.com",
        session_id="11111111-2222-4333-8444-555555555555",
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    return {"Authorization": f"Bearer {token}"}


def call(  # noqa: PLR0913 - one request, spelled out
    client: TestClient,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
    *,
    base: str = BASE,
    request_headers: dict[str, str] | None = None,
) -> Any:
    return client.request(
        method,
        f"{base}{suffix}",
        json=body,
        headers=request_headers if request_headers is not None else headers(),
    )


# --- the deployment flag ------------------------------------------------------------------


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_every_route_is_absent_when_bi_is_off(
    db_client: TestClient, mart: Bank, method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    """The hermetic default is off, which is the product default."""
    response = call(db_client, method, suffix, body)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_a_sibling_tenant_bank_is_404_with_bi_on(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    mart: Bank,
    bi_on: None,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    """S1: institution existence is tenant-confidential, on every BI route."""
    response = call(db_client, method, suffix, body, base=SIBLING_BASE)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Bank not found."


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_a_sibling_tenant_bank_is_404_with_bi_off_too(
    db_client: TestClient, mart: Bank, method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    """The bank resolver runs BEFORE the flag, so the cross-tenant answer never
    changes with a deployment switch — which is also what keeps the platform's
    own bank-route sweep (``test_cross_tenant_bank_routes``) honest here."""
    response = call(db_client, method, suffix, body, base=SIBLING_BASE)
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Bank not found."


# --- the principal -------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_an_impersonated_operator_is_refused_on_every_route(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    mart: Bank,
    bi_on: None,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    """D-026 / S8: BI is not available in an impersonated session, read or not.

    Two layers answer, and which one depends on the method: an unsafe method is
    refused structurally at the authentication boundary
    (``deps.refuse_impersonated_mutation``, which is why the BI POST reads are
    NOT in ``IMPERSONATION_READ_ONLY_ROUTES``), and a GET reaches
    ``require_bi_read``. Both are 403 with no data, and both are asserted here so
    a change in either is visible.
    """
    response = call(db_client, method, suffix, body, request_headers=impersonation_headers())
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    if method == "GET":
        assert error["details"]["error_code"] == "bi_human_principal_required"
    else:
        assert "read-only" in error["message"]
    assert "rows" not in response.text


def test_a_machine_key_never_reaches_a_bi_query(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """D-026 / S9. The refusal is the authentication boundary's: an integration
    key is admitted on the push routes only."""
    from tests.api.helpers import integration_key_headers  # noqa: PLC0415 - issues a real key

    response = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=integration_key_headers(BANK_ID),
    )
    assert response.status_code == 401, response.text


#: Every BI route that mutates, or that POSTs a query body. The sweep in
#: ``test_impersonation_boundary`` classifies by dependency NAME, so each of these
#: must carry ``require_bi_read`` and ``resolve_tenant_bank``; the test below
#: asserts that for each, and asserts this set is exactly what the app mounts.
EXPECTED_UNSAFE_BI_ROUTES: frozenset[str] = frozenset(
    {
        "/api/v1/banks/{bank_id}/bi/query",
        "/api/v1/banks/{bank_id}/bi/grid",
        "/api/v1/banks/{bank_id}/bi/drill",
        "/api/v1/banks/{bank_id}/bi/explain",
        "/api/v1/banks/{bank_id}/bi/export",
        "/api/v1/banks/{bank_id}/bi/dashboards",
        "/api/v1/banks/{bank_id}/bi/dashboards/{dashboard_id}",
        "/api/v1/banks/{bank_id}/bi/dashboards/{dashboard_id}/shares",
        "/api/v1/banks/{bank_id}/bi/measures",
        "/api/v1/banks/{bank_id}/bi/measures/validation",
        "/api/v1/banks/{bank_id}/bi/measures/{measure_id}",
        "/api/v1/banks/{bank_id}/bi/measures/{measure_id}/decision",
        "/api/v1/banks/{bank_id}/bi/measures/{measure_id}/proposal",
    }
)


def test_the_post_reads_are_credited_as_guarded_by_the_route_sweep(db_client: TestClient) -> None:
    """D-027: the sweep in ``test_impersonation_boundary`` classifies by
    dependency NAME, so the dependency has to be in the registered set and on
    every unsafe BI route."""
    assert "require_bi_read" in MUTATION_ROLE_DEPENDENCY_NAMES
    app = db_client.app
    assert isinstance(app, FastAPI)
    unsafe = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and "/bi/" in route.path
        and route.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    # The SET, not a count. A bare number went stale the moment Phase 3 mounted
    # ten more routes, and a stale tripwire teaches the next reader to bump it
    # without looking. Naming the paths means a new unsafe BI route fails HERE by
    # name, and clearing the failure requires deciding that the route belongs and
    # confirming below that it carries both guards.
    assert {route.path for route in unsafe} == EXPECTED_UNSAFE_BI_ROUTES, sorted(
        {route.path for route in unsafe} ^ EXPECTED_UNSAFE_BI_ROUTES
    )
    for route in unsafe:
        names = _dependency_names(route)
        assert "require_bi_read" in names, route.path
        assert "resolve_tenant_bank" in names, route.path


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        stack.extend(dependant.dependencies)
    return names


# --- authorization -------------------------------------------------------------------------


def test_a_query_the_caller_holds_is_served(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=headers(authorization_version=authv),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [column["id"] for column in body["columns"]] == ["branch.code", "loans.balance_rc"]
    assert {row[0]: float(row[1]) for row in body["rows"]} == {"B1": 400.0, "B2": 200.0}
    assert body["build_fingerprint"] == FINGERPRINT
    # R7 is amber in the fixture, so the badge may not read green.
    assert body["trust"] == {"status": reconciliation.AMBER, "failing_checks": ["R7"]}


@pytest.mark.parametrize(
    ("query", "denied"),
    [
        (OBLIGOR_QUERY, "counterparty.name"),
        (RECORD_QUERY, "position.source_reference"),
    ],
)
def test_a_denial_names_the_member_and_carries_no_rows(  # noqa: PLR0913 - one fixture per guard in the chain
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    query: dict[str, Any],
    denied: str,
) -> None:
    """S4 / S5: a dimension the principal has no sentence for refuses the whole
    query, names itself, and returns nothing that could be a row."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = call(
        db_client, "POST", "/query", query, request_headers=headers(authorization_version=authv)
    )
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == "bi_authorization_denied"
    assert details["denied_members"] == [denied]
    assert details["denied_member_labels"] and all(details["denied_member_labels"])
    assert "rows" not in response.text
    assert "Ada Traders" not in response.text


def test_an_institution_of_this_tenant_without_coverage_is_403_not_404(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """S3: the sibling-tenant institution is 404 (existence is confidential) and
    an institution of THIS tenant the principal holds no coverage for is 403 with
    no rows — two different answers, and the difference is deliberate."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    request_headers = headers(authorization_version=authv)
    covered = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=request_headers,
    )
    assert covered.status_code == 200, covered.text
    uncovered = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        base=f"/api/v1/banks/{SAME_TENANT_BANK_ID}/bi",
        request_headers=request_headers,
    )
    assert uncovered.status_code == 403, uncovered.text
    # Every member is denied, not one: the binding names another institution, so
    # no sentence in the query is satisfied here.
    assert uncovered.json()["error"]["details"]["denied_members"] == [
        "loans.balance_rc",
        "branch.code",
    ]
    assert "rows" not in uncovered.text


@pytest.mark.parametrize(("method", "suffix", "body"), ROUTES)
def test_no_route_serves_a_principal_holding_no_binding(  # noqa: PLR0913 - one fixture per guard
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    method: str,
    suffix: str,
    body: dict[str, Any] | None,
) -> None:
    """The sweep that would have caught A6-01: every route, one zero-binding human.

    Every route that serves a tenant FIGURE must refuse. Three answer 200 or a
    different refusal, and each is stated here rather than excluded, because "this
    route answers 200 for a principal with no sentence" is exactly the claim that
    has to be written down:

    * ``catalogue`` discloses the platform's own metadata and no tenant value, so a
      principal with no sentence sees an empty dictionary;
    * the two ``packs`` routes resolve a FILE, not a figure. Every widget that
      would read one is refused individually, and the pack says so in one sentence
      — the alternative, a canvas of lock tiles with no statement over it, reads as
      "there is nothing to show for this date", which is a claim about the bank;
    * ``insights`` refuses with its own code and names NO measure. It cannot answer
      200 with an empty list, because an empty strip renders as "nothing stands out
      for this reporting date" — a statement about the bank's figures made to a
      reader who was shown none of them.

    Whatever the status, nothing in any answer may be a figure.
    """
    authv = grant_only(db_session, ())
    response = call(
        db_client,
        method,
        suffix,
        body,
        request_headers=headers(authorization_version=authv),
    )
    for leak in ("rows", "lhs", "row_counts", "builds", "checks", "columns"):
        assert leak not in response.text, leak
    if suffix == "/catalogue":
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["measures"] == []
        assert payload["dimensions"] == []
        assert payload["hierarchies"] == []
        assert payload["withheld_members"] == len(catalogue().members())
        return
    if suffix.startswith("/packs"):
        assert response.status_code == 200, response.text
        payload = response.json()
        served = payload.get("packs", [payload])
        assert served
        for pack in served:
            if not pack["readable_widgets"]:
                continue
            assert pack["access"] == "restricted", pack["id"]
            assert pack["restricted_widgets"] == pack["readable_widgets"], pack["id"]
            assert pack["message"]
            for widget in pack["widgets"]:
                if widget["access"] != "restricted":
                    continue
                assert widget["title"] is None
                assert widget["query"] is None
        return
    if suffix.startswith("/insights"):
        assert response.status_code == 403, response.text
        details = response.json()["error"]["details"]
        assert details["error_code"] == "bi_insights_authorization_denied"
        assert "denied_members" not in details
        return
    assert response.status_code == 403, response.text
    details = response.json()["error"]["details"]
    assert details["error_code"] == "bi_authorization_denied"
    assert details["denied_members"]


def test_a_query_naming_an_unknown_member_is_422_before_any_decision(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(
        db_client,
        "POST",
        "/query",
        {"measures": ["loans.nope"], "time": {"as_of": AS_OF.isoformat()}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_unknown_member"


# --- the catalogue -------------------------------------------------------------------------


def test_the_catalogue_advertises_only_what_this_caller_may_query(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = db_client.get(f"{BASE}/catalogue", headers=headers(authorization_version=authv))
    assert response.status_code == 200, response.text
    body = response.json()
    dimensions = {dimension["id"] for dimension in body["dimensions"]}
    measures = {measure["id"] for measure in body["measures"]}
    assert "branch.code" in dimensions
    assert "loans.balance_rc" in measures
    # Denied by the same rule the query path applies, so never advertised.
    assert "counterparty.name" not in dimensions
    assert "position.source_reference" not in dimensions
    assert "loan.employer" not in dimensions
    assert "engine.lcr_pct.crd.live" not in measures  # LIQ, and this caller has no LIQ
    assert body["withheld_members"] > 0
    assert all(dimension["sensitivity"] != "restricted" for dimension in body["dimensions"])


def test_the_catalogue_reduces_a_hierarchy_to_the_visible_levels(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Drilling from obligor TYPE into a named obligor crosses into restricted;
    a path the query path would refuse is not offered."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    body = db_client.get(f"{BASE}/catalogue", headers=headers(authorization_version=authv)).json()
    paths = {hierarchy["id"]: hierarchy["levels"] for hierarchy in body["hierarchies"]}
    assert paths["counterparty"] == ["counterparty.type"]
    assert paths["geography"] == ["branch.region", "branch.code"]


def _time_for(measure: MeasureDef) -> dict[str, Any]:
    """A flow measure is read over a window, a stock measure at a date."""
    if measure.time_behaviour == "flow":
        return {"range": {"start": AS_OF.isoformat(), "end": AS_OF.isoformat()}}
    return {"as_of": AS_OF.isoformat()}


def _unqueryable(
    db: Session, cat: Catalogue, measures: Sequence[str], dimensions: Sequence[str]
) -> list[str]:
    """Every advertised member that no query can actually read, with the reason.

    A measure is probed alone; a dimension is probed through the first measure
    whose ``allowed_dimensions`` admits it, and a dimension no measure admits is
    itself the failure (that is A6-05's defect, and it needs no compile to see).
    """

    failures: list[str] = []
    for measure_id in measures:
        measure = cat.measure(measure_id)
        try:
            compile_query(
                db,
                cat,
                BiQuery(measures=[measure_id], time=BiTime(**_time_for(measure))),
                organization_id=ORG_1,
                bank_id=BANK_ID,
            )
        except BiQueryError as exc:
            failures.append(f"measure {measure_id}: {exc}")
    for dimension_id in dimensions:
        admitting = [
            measure for measure in cat.measures() if dimension_id in measure.allowed_dimensions
        ]
        if not admitting:
            failures.append(f"dimension {dimension_id}: no measure admits it")
            continue
        measure = admitting[0]
        try:
            compile_query(
                db,
                cat,
                BiQuery(
                    measures=[measure.id],
                    dimensions=[dimension_id],
                    time=BiTime(**_time_for(measure)),
                ),
                organization_id=ORG_1,
                bank_id=BANK_ID,
            )
        except BiQueryError as exc:
            failures.append(f"dimension {dimension_id} (via {measure.id}): {exc}")
    return failures


def test_every_advertised_member_is_actually_queryable(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """EVERY member the catalogue advertises, not a sample (audit A6-05).

    A6-05 found four dimensions the catalogue published that no measure admitted,
    so every query naming one was a 422 the UI could offer but never satisfy —
    and the earlier version of this test sampled eight ids and missed them.

    Structure, deliberately: the advertised SET comes from the route (one request,
    full authority, so the set is the whole catalogue) and each member is then
    compiled directly rather than through its own HTTP request. 288 requests would
    add minutes to the suite for no extra coverage — the refusal A6-05 is about is
    the COMPILER's, and the route path is proven to agree on a sample below.
    """
    cat = catalogue()
    body = db_client.get(f"{BASE}/catalogue", headers=headers()).json()
    measures = [entry["id"] for entry in body["measures"]]
    dimensions = [entry["id"] for entry in body["dimensions"]]
    assert len(measures) + len(dimensions) == len(cat.members()), (
        "the fixture principal must hold every pair, or this proves nothing"
    )
    failures = _unqueryable(db_session, cat, measures, dimensions)
    assert failures == [], f"{len(failures)} advertised members cannot be queried: {failures}"


def test_the_queryability_guard_catches_an_unadmitted_dimension(
    db_session: Session, mart: Bank
) -> None:
    """The converse: a dimension advertised but admitted by no measure IS reported.

    Built here rather than by editing the catalogue (D1's file): this is the exact
    shape A6-05 found in the real one, and it proves the guard above is not green
    because it looks at nothing.
    """
    real = catalogue()
    orphan = DimensionDef(
        id="test.orphan_dimension",
        module="risk",
        sensitivity="aggregated",
        label="Orphan",
        source=ColumnRef(POSITION_TABLE, "source_system"),
    )
    with_orphan = Catalogue(
        real.version,
        {measure.id: measure for measure in real.measures()},
        {**{d.id: d for d in real.dimensions()}, orphan.id: orphan},
        real.hierarchies(),
    )
    failures = _unqueryable(db_session, with_orphan, (), (orphan.id,))
    assert failures == ["dimension test.orphan_dimension: no measure admits it"]


def test_the_route_agrees_with_the_compiler_on_advertised_members(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The sample the test above delegates: the HTTP path serves what compiles."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    request_headers = headers(authorization_version=authv)
    body = db_client.get(f"{BASE}/catalogue", headers=request_headers).json()
    dimensions = [
        entry["id"]
        for entry in body["dimensions"]
        if entry["id"].startswith(("branch.", "loan.", "position."))
    ]
    assert dimensions
    for dimension_id in dimensions[:8]:
        response = call(
            db_client,
            "POST",
            "/query",
            {
                "measures": ["loans.balance_rc"],
                "dimensions": [dimension_id],
                "time": {"as_of": AS_OF.isoformat()},
            },
            request_headers=request_headers,
        )
        assert response.status_code == 200, (dimension_id, response.text)


def test_the_full_authority_catalogue_emits_only_catalogue_vocabulary(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The reads carry the catalogue's own values as text (they cannot import the
    domain Literals). This pins the values a route can emit."""
    body = db_client.get(f"{BASE}/catalogue", headers=headers()).json()
    assert body["version"] == read_bi.CATALOGUE_VERSION
    assert {measure["measure_kind"] for measure in body["measures"]} <= {
        "certified_engine",
        "portfolio",
        "calculated",
    }
    assert {measure["time_behaviour"] for measure in body["measures"]} <= {"stock", "flow"}
    assert {dimension["sensitivity"] for dimension in body["dimensions"]} <= {
        "published",
        "aggregated",
        "confidential",
        "restricted",
    }
    certified = [measure for measure in body["measures"] if measure["certified"]]
    assert certified and all(
        measure["advisory_designation"] == "filed" and measure["engine_tier"] == "official"
        for measure in certified
    )


# --- the ETag ------------------------------------------------------------------------------


def test_a_matching_if_none_match_is_answered_304_without_a_body(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    first = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert first.status_code == 200, first.text
    etag = first.headers["ETag"]
    again = db_client.post(
        f"{BASE}/query", json=BALANCE_BY_BRANCH_QUERY, headers={**headers(), "If-None-Match": etag}
    )
    assert again.status_code == 304
    assert not again.content
    assert again.headers["ETag"] == etag


def test_the_etag_moves_when_authority_changes(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """S19: revoke-and-regrant bumps ``authv``, so a held ETag cannot survive it."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    first = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=headers(authorization_version=authv),
    )
    assert first.status_code == 200, first.text
    wider = grant_only(db_session, (*AGGREGATE_ONLY, Grant(ModuleScope.LIQUIDITY)))
    assert wider != authv
    second = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=headers(authorization_version=wider),
    )
    assert second.status_code == 200, second.text
    assert second.headers["ETag"] != first.headers["ETag"]


def test_the_etag_moves_when_the_mart_is_rebuilt(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    first = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert first.status_code == 200, first.text
    db_session.execute(
        update(BiMartBuild)
        .where(BiMartBuild.organization_id == ORG_1, BiMartBuild.bank_id == BANK_ID)
        .values(fingerprint="e" * 64)
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    second = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert second.status_code == 200, second.text
    assert second.json()["build_fingerprint"] == "e" * 64
    assert second.headers["ETag"] != first.headers["ETag"]


def test_a_stale_token_is_refused_before_anything_is_served(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=headers(authorization_version=authv - 1),
    )
    assert response.status_code == 401, response.text


# --- caps ----------------------------------------------------------------------------------


def test_the_client_cannot_raise_the_row_cap(
    db_client: TestClient, mart: Bank, bi_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S13: ``limit`` may only lower the surface's cap."""
    monkeypatch.setenv("BI_UI_ROW_CAP", "1")
    get_settings.cache_clear()
    response = call(
        db_client,
        "POST",
        "/query",
        {**BALANCE_BY_BRANCH_QUERY, "limit": 5_000},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["rows"]) == 1
    assert body["truncated"] is True


def test_the_grid_page_is_capped_and_reports_whether_more_remain(
    db_client: TestClient, mart: Bank, bi_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_GRID_PAGE_CAP", "1")
    get_settings.cache_clear()
    first = call(
        db_client,
        "POST",
        "/grid",
        {"query": BALANCE_BY_BRANCH_QUERY, "start_row": 0, "end_row": 500},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["rows"]) == 1
    assert body["truncated"] is True
    assert body["last_row"] is None
    assert body["start_row"] == 0
    second = call(
        db_client,
        "POST",
        "/grid",
        {"query": BALANCE_BY_BRANCH_QUERY, "start_row": 1, "end_row": 2},
    )
    assert second.status_code == 200, second.text
    assert second.json()["start_row"] == 1


def test_the_grid_refuses_two_paging_mechanisms(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(
        db_client,
        "POST",
        "/grid",
        {"query": {**BALANCE_BY_BRANCH_QUERY, "offset": 10}, "start_row": 0, "end_row": 10},
    )
    assert response.status_code == 422, response.text


def test_a_grid_page_keys_its_rows_by_column_id(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(db_client, "POST", "/grid", {"query": BALANCE_BY_BRANCH_QUERY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dimension_count"] == 1
    assert {row["values"]["branch.code"] for row in body["rows"]} == {"B1", "B2"}
    assert all(row["level"] is None and row["subtotal"] is False for row in body["rows"])
    assert body["last_row"] == 2


def test_a_grid_page_marks_subtotal_rows(db_client: TestClient, mart: Bank, bi_on: None) -> None:
    response = call(
        db_client,
        "POST",
        "/grid",
        {"query": {**BALANCE_BY_BRANCH_QUERY, "subtotals": True}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "__level" not in {column["id"] for column in body["columns"]}
    subtotals = [row for row in body["rows"] if row["subtotal"]]
    assert len(subtotals) == 1
    assert subtotals[0]["level"] == 0
    assert float(subtotals[0]["values"]["loans.balance_rc"]) == 600.0


# --- drill ---------------------------------------------------------------------------------


def test_a_drill_must_name_a_record_level_field(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(db_client, "POST", "/drill", {"query": BALANCE_BY_BRANCH_QUERY})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_drill_needs_a_record_field"


def test_a_drill_serves_records_under_the_record_level_sentence(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(
        db_session,
        (*AGGREGATE_ONLY, Grant(ModuleScope.RISK, SensitivityScope.CONFIDENTIAL)),
    )
    response = call(
        db_client,
        "POST",
        "/drill",
        {"query": RECORD_QUERY},
        request_headers=headers(authorization_version=authv),
    )
    assert response.status_code == 200, response.text
    rows = {
        row["values"]["position.source_reference"]: row["values"]["loans.balance_rc"]
        for row in response.json()["rows"]
    }
    assert {reference: float(value) for reference, value in rows.items() if value is not None} == {
        "L-1": 100.0,
        "L-2": 300.0,
        "L-3": 200.0,
    }
    # The deposit is in the population and outside the loan measure, so it is a
    # row with no value rather than a row with a zero (the compiler's NULL-vs-0
    # rule, D-042): a record grid must not claim a deposit has a loan balance of
    # nought. A client that wants loans only filters on the position type.
    assert rows["D-1"] is None


def test_a_drill_without_the_record_level_sentence_is_refused(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = call(
        db_client,
        "POST",
        "/drill",
        {"query": RECORD_QUERY},
        request_headers=headers(authorization_version=authv),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["denied_members"] == ["position.source_reference"]


def test_a_drill_page_is_capped(
    db_client: TestClient, mart: Bank, bi_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_GRID_PAGE_CAP", "2")
    get_settings.cache_clear()
    response = call(
        db_client, "POST", "/drill", {"query": RECORD_QUERY, "start_row": 0, "end_row": 500}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == 2


# --- explain -------------------------------------------------------------------------------


def test_explain_names_the_engine_metric_and_its_input_hash(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """A certified figure is a COPY; provenance is the run it was copied from."""
    response = call(
        db_client,
        "POST",
        "/explain",
        {
            "query": {
                "measures": ["engine.car_pct.crd.official"],
                "time": {"as_of": AS_OF.isoformat()},
            },
            "measure": "engine.car_pct.crd.official",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["measure"]["certified"] is True
    assert body["source_table"] == "bi_fact_engine_metric"
    engine = body["engine"]
    # ``computed_at`` is compared apart, as an INSTANT: Postgres returns it with
    # the session's offset and SQLite returns it naive, so only the moment is a
    # property of the platform.
    stamp = dt.datetime.fromisoformat(engine.pop("computed_at"))
    assert (stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.UTC)) == BUILT_AT
    assert engine == {
        "metric_id": "car_pct",
        "module": "capital",
        "tier": "official",
        "regime": "crd",
        "as_of": AS_OF.isoformat(),
        "value": "14.250000",
        "unit": "pct",
        "input_hash": "a" * 64,
        "engine_version": "7",
        "pipeline_state": "ready",
        "status": "green",
        "advisory_designation": "filed",
        "reconciliation_blocked": False,
        "run_id": None,
        "reporting_period_id": None,
    }
    assert "select" not in response.text.lower()


def test_explain_names_the_checks_that_govern_a_portfolio_measure(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(
        db_client,
        "POST",
        "/explain",
        {"query": BALANCE_BY_BRANCH_QUERY, "measure": "loans.balance_rc"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_table"] in {"bi_fact_position_daily", "bi_agg_position_daily"}
    assert body["engine"] is None
    assert body["fx_rule"] == "derivation"
    assert [check["check_id"] for check in body["checks"]] == ["R2"]
    assert body["checks"][0]["status"] == reconciliation.GREEN
    assert body["checks"][0]["label"] == read_bi.CHECK_LABELS["R2"]
    assert body["as_of"] == AS_OF.isoformat()
    assert body["build_fingerprint"] == FINGERPRINT


def test_explain_reports_a_composed_measures_parts(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(
        db_client,
        "POST",
        "/explain",
        {
            "query": {
                "measures": ["loans.npl_ratio_pct"],
                "time": {"as_of": AS_OF.isoformat()},
            },
            "measure": "loans.npl_ratio_pct",
        },
    )
    assert response.status_code == 200, response.text
    roles = {part["role"]: part["member_id"] for part in response.json()["components"]}
    assert roles == {
        "numerator": "loans.npl_exposure_rc",
        "denominator": "loans.classification_exposure_rc",
    }


def test_explain_refuses_a_measure_the_query_never_asked_for(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = call(
        db_client,
        "POST",
        "/explain",
        {"query": BALANCE_BY_BRANCH_QUERY, "measure": "deposits.balance_rc"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["error_code"] == "bi_explain_measure_not_in_query"


# --- trust ---------------------------------------------------------------------------------
#
# The reproduction the A6 audit ran: a principal bound to institution A asked for
# institution B's trust payload and was served its total loans, total deposits,
# total assets, real branch codes and mart row counts — the figures the same
# principal is refused on ``POST /bi/query``. ``resolve_tenant_bank`` scopes by
# ORGANIZATION; institution coverage is a binding question, and the route asked
# nobody. These are the tests that make that answer 403.


def test_trust_on_an_institution_without_coverage_is_403_with_no_figures(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """A6-01, the auditor's shape: bound to bank A, asking for bank B."""
    authv = grant_only(db_session, AGGREGATE_ONLY_WITH_DEPOSITS)
    request_headers = headers(authorization_version=authv)
    covered = db_client.get(
        f"{BASE}/trust", params={"as_of": AS_OF.isoformat()}, headers=request_headers
    )
    assert covered.status_code == 200, covered.text
    uncovered = db_client.get(
        f"/api/v1/banks/{SAME_TENANT_BANK_ID}/bi/trust",
        params={"as_of": AS_OF.isoformat()},
        headers=request_headers,
    )
    assert uncovered.status_code == 403, uncovered.text
    details = uncovered.json()["error"]["details"]
    assert details["error_code"] == "bi_authorization_denied"
    # A set: the evaluator reports denials grouped by (module, sensitivity) pair,
    # so the ORDER is the pair order, not the member order. Completeness is the
    # property — every member the payload would have disclosed is refused.
    assert set(details["denied_members"]) == set(TRUST_MEMBERS)
    assert len(details["denied_members"]) == len(TRUST_MEMBERS)
    # None of the other institution's evidence may appear: not its figures, not
    # its branch codes, not its build state, not even the payload's shape.
    for leak in (
        str(OTHER_BANK_LOANS),
        OTHER_BANK_BRANCH,
        str(OTHER_BANK_ROWS),
        OTHER_BANK_FINGERPRINT,
        "lhs",
        "row_counts",
        "builds",
        "checks",
    ):
        assert leak not in uncovered.text, leak


def test_trust_is_refused_to_a_principal_with_no_bindings(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The other half of A6-01: an account administrator "sees no bank and no
    module" by design, and that must include the reconciliation evidence."""
    authv = grant_only(db_session, ())
    response = db_client.get(
        f"{BASE}/trust",
        params={"as_of": AS_OF.isoformat()},
        headers=headers(authorization_version=authv),
    )
    assert response.status_code == 403, response.text
    assert set(response.json()["error"]["details"]["denied_members"]) == set(TRUST_MEMBERS)


def test_trust_requires_every_pair_its_checks_disclose(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The badge is one verdict over the whole book, so a principal missing the
    deposit sentence is not told what the deposit reconciliation says either."""
    authv = grant_only(db_session, AGGREGATE_ONLY)  # credit + risk, no liquidity
    response = db_client.get(
        f"{BASE}/trust",
        params={"as_of": AS_OF.isoformat()},
        headers=headers(authorization_version=authv),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["denied_members"] == ["deposits.balance_rc"]


def test_the_trust_probe_names_one_catalogue_member_per_stored_check(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    """The sentence comes from the catalogue, not from a literal: every stored
    check maps to a member that resolves, and the probe is exactly that set."""
    cat = catalogue()
    assert set(read_bi.CHECK_DISCLOSURES) == set(reconciliation.STORABLE_CHECK_IDS)
    for check_id, member_id in read_bi.CHECK_DISCLOSURES.items():
        member = cat.member(member_id)
        assert member.sensitivity == "aggregated", (check_id, member_id)
    probe = read_bi.trust_probe(AS_OF)
    assert set(probe.measures) | set(probe.dimensions) == set(read_bi.CHECK_DISCLOSURES.values())
    assert probe.time.as_of == AS_OF


def test_trust_reports_every_check_and_greys_the_ones_that_did_not_run(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = db_client.get(
        f"{BASE}/trust", params={"as_of": AS_OF.isoformat()}, headers=headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    statuses = {check["check_id"]: check["status"] for check in body["checks"]}
    assert list(statuses) == list(reconciliation.STORABLE_CHECK_IDS)
    assert statuses["R2"] == reconciliation.GREEN
    assert statuses["R7"] == reconciliation.AMBER
    assert statuses["R1"] == reconciliation.GREY
    assert body["status"] == reconciliation.AMBER
    assert body["build_fingerprint"] == FINGERPRINT
    assert {build["scope"] for build in body["builds"]} == {
        "positions",
        "events",
        "gl",
        "engine",
        "dims",
    }
    assert all(check["label"] for check in body["checks"])


def test_trust_agrees_with_the_reconciliation_services_own_badge(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    served = db_client.get(
        f"{BASE}/trust", params={"as_of": AS_OF.isoformat()}, headers=headers()
    ).json()
    stored = reconciliation.trust_for(db_session, ORG_1, BANK_ID, AS_OF)
    assert served["status"] == stored[reconciliation.OVERALL]
    assert {check["check_id"]: check["status"] for check in served["checks"]} == {
        check_id: status
        for check_id, status in stored.items()
        if check_id != reconciliation.OVERALL
    }


def test_trust_on_a_date_with_nothing_built_is_grey_not_green(
    db_client: TestClient, mart: Bank, bi_on: None
) -> None:
    response = db_client.get(f"{BASE}/trust", params={"as_of": "2026-07-31"}, headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == reconciliation.GREY
    assert body["build_fingerprint"] is None
    assert body["builds"] == []


# --- the read budget -----------------------------------------------------------------------


def test_the_read_budget_refuses_a_principal_that_has_spent_it(
    db_client: TestClient, mart: Bank, bi_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-010: the budget is counted over ``bi_query_log``, so it holds across
    workers. One recorded read is enough to spend a budget of one."""
    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", 1)
    first = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert first.status_code == 200, first.text
    second = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert second.status_code == 429, second.text
    assert second.json()["error"]["details"]["error_code"] == "bi_rate_limited"
    assert int(second.headers["Retry-After"]) >= 1


def test_the_catalogue_and_trust_are_subject_to_the_budget_but_do_not_refill_it(  # noqa: PLR0913 - one fixture per guard
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both are metered, and neither can be counted (no log row): the honest
    residue is that a principal polling ONLY these two is never throttled, which
    is what the vocabulary follow-up in the module docstring closes."""
    trust_url = f"{BASE}/trust"
    assert db_client.get(f"{BASE}/catalogue", headers=headers()).status_code == 200
    assert (
        db_client.get(trust_url, params={"as_of": AS_OF.isoformat()}, headers=headers()).status_code
        == 200
    )
    db_session.expire_all()
    assert db_session.query(BiQueryLog).count() == 0, "neither refills the budget"

    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", 1)
    assert call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY).status_code == 200
    assert db_client.get(f"{BASE}/catalogue", headers=headers()).status_code == 429
    assert (
        db_client.get(trust_url, params={"as_of": AS_OF.isoformat()}, headers=headers()).status_code
        == 429
    )


# --- shared rules --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "time",
    [
        BiTime(as_of=AS_OF),
        BiTime(as_of=AS_OF, compare_to=dt.date(2026, 7, 31)),
        BiTime(range=BiDateRange(start=dt.date(2026, 8, 1), end=AS_OF)),
        BiTime(
            range=BiDateRange(start=dt.date(2026, 8, 1), end=AS_OF),
            compare_to=dt.date(2026, 7, 31),
        ),
    ],
)
def test_the_badged_window_covers_every_date_the_compiler_reads(time: BiTime) -> None:
    """The badge and the fingerprint must span the comparison period too, so this
    mirrors the compiler's own window arithmetic rather than restating it."""
    current, prior = compiler._windows(time)  # noqa: SLF001 - the rule under comparison
    expected_start = current.start if prior is None else min(current.start, prior.start)
    expected_end = current.end if prior is None else max(current.end, prior.end)
    assert read_bi._data_window(time) == (expected_start, expected_end)  # noqa: SLF001


def test_every_storable_check_has_production_copy() -> None:
    missing = [
        check_id
        for check_id in reconciliation.STORABLE_CHECK_IDS
        if not read_bi.CHECK_LABELS.get(check_id)
    ]
    assert missing == []
    forbidden = ("GHS", "cedi", "BoG", "Bank of Ghana", "BSD")
    leaks = [
        label
        for label in read_bi.CHECK_LABELS.values()
        if any(token.lower() in label.lower() for token in forbidden)
    ]
    assert leaks == []
