"""Scoped-binding enforcement and data scopes on the DIRECT credit routes.

``tests/api/test_credit_authorization.py`` covers Phase 1 — the credit rows of
the shared live surfaces. This file covers the Phase 4 cutover
(``backend/docs/credit_enforcement_rollout.md``): every ``/credit/*`` route now
requires a complete ``Module.CREDIT`` binding chosen from what the response
discloses, and the three row-level surfaces apply the reader's data scope.

Two properties are worth naming, because both were true of this module before the
cutover and neither was visible from a green suite:

* a scalar ``analyst`` token with NO binding reached the loan blotter, which
  returns named obligor rows; and
* ``total`` counted the whole institution's book, so a filter was a client's
  suggestion rather than a boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    Bank,
    CanonicalLoanEvent,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    User,
)
from app.services import authorization, job_queue, pipeline
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

#: A second institution of the SAME organization. Two banks of one organization
#: share an RLS tenant, so organization scoping alone cannot isolate them: the
#: sibling is how the tests prove institution coverage is exact.
SIBLING_BANK_ID = "BK-CRED0002"
#: A bank of ANOTHER tenant. It is never created — the route must answer the same
#: 404 for an unknown identifier and for one owned elsewhere.
FOREIGN_BANK_ID = "BK-CREDXXXX"

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"

#: The fixture book's branches: four current loans on BR-001, three on BR-002.
BR_ONE = "BR-001"
BR_TWO = "BR-002"
BR_ONE_LOANS = {"LOAN/1", "LOAN/4", "LOAN/6", "LOAN/USD"}
BR_TWO_LOANS = {"LOAN/2", "LOAN/3", "LOAN/5"}


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    """Drop the hermetic ``viewer / all / all`` sentence so each test grants exactly."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def _seed_book() -> None:
    """The canonical fixture book plus one live refresh, as the credit reads need."""
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
        )
        session.commit()
        pipeline.run_refresh(session, job)
        session.commit()
    finally:
        session.close()


def _add_sibling_bank() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        if session.get(Bank, SIBLING_BANK_ID) is None:
            session.add(
                Bank(
                    id=SIBLING_BANK_ID,
                    organization_id=ORG_1,
                    name="Credit sibling bank",
                    short_name="Credit sibling",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
    finally:
        session.close()


def _declare_regions(regions: dict[str, str]) -> None:
    """Restate the institution's ``business_units`` register with declared regions.

    The register is the ONLY source of a branch's region (that schema's module
    docstring is explicit), so a region grant is only resolvable once the bank has
    declared one. The fixture's rows carry no ``region``; this rewrites the payload
    of the existing rows rather than pushing a new batch, because what is under
    test is scope resolution, not ingestion.
    """
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        rows = list(
            session.scalars(
                select(CanonicalReferenceRow).where(
                    CanonicalReferenceRow.organization_id == ORG_1,
                    CanonicalReferenceRow.bank_id == SAMPLE_BANK_ID,
                    CanonicalReferenceRow.dataset_kind == "business_units",
                )
            )
        )
        assert rows, "the fixture register must already carry business units"
        for row in rows:
            payload = dict(row.payload or {})
            unit_id = str(payload.get("business_unit_id") or "")
            if unit_id in regions:
                payload["region"] = regions[unit_id]
                row.payload = payload
        session.commit()
    finally:
        session.close()


def _seed_events(events: list[dict[str, Any]]) -> None:
    """Loan events, each naming the facility it belongs to.

    ``source_system`` defaults to the fixture positions' own ``EXCEL_CSV`` so the
    event is attributable to that facility under D-018; a test that wants the
    cross-system case passes a different one explicitly.
    """
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_system="EXCEL_CSV",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=FIXTURE_AS_OF,
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="credit-scope-events",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        for event in events:
            session.add(
                CanonicalLoanEvent(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    as_of_date=FIXTURE_AS_OF,
                    source_system=event.get("source_system", "EXCEL_CSV"),
                    ingestion_batch_id=batch.id,
                    lineage_id=lineage.id,
                    validation_status="accepted",
                    source_reference=event["ref"],
                    event_type=event["type"],
                    event_subtype=event.get("subtype"),
                    event_date=date_type.fromisoformat(event["date"]),
                    position_source_reference=event["position"],
                    amount=Decimal(event["amount"]),
                    currency="GHS",
                    amount_ghs=Decimal(event["amount"]),
                    attributes={},
                )
            )
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - each binding dimension is an enforcement input
    *,
    role_bundle: RoleBundle = RoleBundle.VIEWER,
    module_scope: ModuleScope = ModuleScope.CREDIT,
    sensitivity_scope: SensitivityScope = SensitivityScope.AGGREGATED,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
    data_scope: DataScope = DataScope.ALL,
    data_scope_values: tuple[str, ...] = (),
) -> int:
    """One indivisible sentence for USER_1; returns the new ``authv``."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                institution_scope,
                institution_id if institution_scope is InstitutionScope.INSTITUTION else None,
                module_scope,
                sensitivity_scope,
                data_scope,
                data_scope_values,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise the credit route cutover.",
        )
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


# --- the authority table, executable --------------------------------------


@dataclass(frozen=True)
class Surface:
    """One credit route and the complete sentence it requires."""

    label: str
    method: str
    path: str
    sensitivity: SensitivityScope
    #: The least bundle carrying the required permission.
    bundle: RoleBundle
    #: True when the route refuses a branch- or region-scoped principal outright.
    whole_institution_only: bool
    ok_status: int = 200

    def call(self, client: TestClient, version: int, bank_id: str = SAMPLE_BANK_ID) -> Any:
        url = f"/api/v1/banks/{bank_id}{self.path}"
        auth = headers(authorization_version=version)
        if self.method == "POST":
            return client.post(url, headers=auth, json={"reporting_period_id": _period_id()})
        return client.get(url, headers=auth)


def _period_id() -> str:
    from app.models import BankReportingPeriod  # noqa: PLC0415 - test-local

    session = get_sessionmaker()()
    try:
        found = session.scalar(
            select(BankReportingPeriod.id).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == FIXTURE_AS_OF,
            )
        )
        assert found is not None
        return str(found)
    finally:
        session.close()


SURFACES: tuple[Surface, ...] = (
    Surface(
        "dashboard",
        "GET",
        "/credit/dashboard",
        SensitivityScope.AGGREGATED,
        RoleBundle.VIEWER,
        True,
    ),
    Surface(
        "migration",
        "GET",
        "/credit/migration",
        SensitivityScope.AGGREGATED,
        RoleBundle.VIEWER,
        True,
    ),
    Surface(
        "vintages", "GET", "/credit/vintages", SensitivityScope.AGGREGATED, RoleBundle.VIEWER, True
    ),
    Surface("pd", "GET", "/credit/pd", SensitivityScope.AGGREGATED, RoleBundle.VIEWER, True),
    Surface(
        "concentration",
        "GET",
        "/credit/concentration",
        SensitivityScope.RESTRICTED,
        RoleBundle.VIEWER,
        True,
    ),
    Surface("loans", "GET", "/credit/loans", SensitivityScope.RESTRICTED, RoleBundle.VIEWER, False),
    Surface(
        "facets",
        "GET",
        "/credit/loans/facets",
        SensitivityScope.RESTRICTED,
        RoleBundle.VIEWER,
        False,
    ),
    Surface(
        "activity",
        "GET",
        "/credit/activity",
        SensitivityScope.CONFIDENTIAL,
        RoleBundle.VIEWER,
        False,
    ),
    Surface(
        "run",
        "POST",
        "/credit/run-all-scenarios",
        SensitivityScope.CONFIDENTIAL,
        RoleBundle.ANALYST,
        True,
        ok_status=201,
    ),
)

#: Every sensitivity a credit sentence can name, so "the wrong one" is exhaustive.
_SENSITIVITIES = (
    SensitivityScope.AGGREGATED,
    SensitivityScope.CONFIDENTIAL,
    SensitivityScope.RESTRICTED,
)


def _ids(surfaces: tuple[Surface, ...]) -> list[str]:
    return [surface.label for surface in surfaces]


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_the_correct_sentence_is_admitted(db_client: TestClient, surface: Surface) -> None:
    _seed_book()
    version = _grant(role_bundle=surface.bundle, sensitivity_scope=surface.sensitivity)

    response = surface.call(db_client, version)

    assert response.status_code == surface.ok_status, f"{surface.label}: {response.text}"


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_a_scalar_role_alone_is_refused(db_client: TestClient, surface: Surface) -> None:
    """The defect this cutover fixes: no binding, only a token role claim.

    ``admin`` and ``analyst`` are the two scalar roles that used to reach these
    routes — ``analyst`` satisfied the run gate and any role at all satisfied the
    reads. The evaluator ignores scalar claims entirely, so both are 403.
    """
    _seed_book()
    for role in ("admin", "analyst", "approver", "examiner", "viewer"):
        url = f"{BASE}{surface.path}"
        auth = headers(roles=(role,))
        response = (
            db_client.post(url, headers=auth, json={"reporting_period_id": _period_id()})
            if surface.method == "POST"
            else db_client.get(url, headers=auth)
        )
        assert response.status_code == 403, f"{surface.label}/{role}: {response.text}"


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_a_binding_for_another_module_is_refused(db_client: TestClient, surface: Surface) -> None:
    """Right bundle, right sensitivity, wrong module — including ``risk``, which
    was only ever the dashboard's label for credit."""
    _seed_book()
    for module in (ModuleScope.RISK, ModuleScope.LIQUIDITY, ModuleScope.CAPITAL):
        version = _grant(
            role_bundle=surface.bundle,
            module_scope=module,
            sensitivity_scope=surface.sensitivity,
        )
        response = surface.call(db_client, version)
        assert response.status_code == 403, f"{surface.label}/{module.value}: {response.text}"


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_every_other_sensitivity_is_refused(db_client: TestClient, surface: Surface) -> None:
    """Sensitivity is exact-or-``all``, never a ladder.

    So the aggregated dashboard sentence does not open the restricted blotter AND
    the restricted blotter sentence does not open the aggregated dashboard. Each
    is its own row.
    """
    _seed_book()
    for sensitivity in _SENSITIVITIES:
        if sensitivity is surface.sensitivity:
            continue
        version = _grant(role_bundle=surface.bundle, sensitivity_scope=sensitivity)
        response = surface.call(db_client, version)
        assert response.status_code == 403, f"{surface.label}/{sensitivity.value}: {response.text}"


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_sensitivity_all_admits_every_credit_surface(
    db_client: TestClient, surface: Surface
) -> None:
    """``all`` is the one sensitivity that covers each of them."""
    _seed_book()
    version = _grant(role_bundle=surface.bundle, sensitivity_scope=SensitivityScope.ALL)

    assert surface.call(db_client, version).status_code == surface.ok_status


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_a_sibling_institutions_sentence_does_not_reach_this_one(
    db_client: TestClient, surface: Surface
) -> None:
    """Institution coverage is exact. Two banks of one organization share an RLS
    tenant, so a sentence for the sibling must not be read as coverage here."""
    _seed_book()
    _add_sibling_bank()
    version = _grant(
        role_bundle=surface.bundle,
        sensitivity_scope=surface.sensitivity,
        institution_id=SIBLING_BANK_ID,
    )

    assert surface.call(db_client, version).status_code == 403


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_this_institutions_sentence_does_not_reach_the_sibling(
    db_client: TestClient, surface: Surface
) -> None:
    """The same rule read the other way: the sibling exists inside this tenant, so
    it is visible (403, not 404) but uncovered."""
    _seed_book()
    _add_sibling_bank()
    version = _grant(role_bundle=surface.bundle, sensitivity_scope=surface.sensitivity)

    response = surface.call(db_client, version, bank_id=SIBLING_BANK_ID)

    assert response.status_code == 403, f"{surface.label}: {response.text}"


@pytest.mark.parametrize("surface", SURFACES, ids=_ids(SURFACES))
def test_a_bank_of_another_tenant_is_not_found(db_client: TestClient, surface: Surface) -> None:
    """Existence is tenant-confidential: the answer is 404 even when the caller
    holds an organization-wide credit sentence, and it is the SAME 404 an unknown
    identifier gets."""
    _seed_book()
    version = _grant(
        role_bundle=surface.bundle,
        sensitivity_scope=surface.sensitivity,
        institution_scope=InstitutionScope.ORGANIZATION,
    )

    response = surface.call(db_client, version, bank_id=FOREIGN_BANK_ID)

    assert response.status_code == 404, f"{surface.label}: {response.text}"


def test_another_tenants_own_reader_cannot_see_this_institution(db_client: TestClient) -> None:
    """The foreign identifier above is never created, so the 404 it earns could in
    principle be "unknown" rather than "not yours". This asserts the real case."""
    _seed_book()
    response = db_client.get(f"{BASE}/credit/loans", headers=headers(org_id=ORG_2))
    assert response.status_code == 404, response.text


# --- data scopes -----------------------------------------------------------


_WHOLE_INSTITUTION_ONLY = tuple(s for s in SURFACES if s.whole_institution_only)
_SCOPED = tuple(s for s in SURFACES if not s.whole_institution_only)


@pytest.mark.parametrize("surface", _WHOLE_INSTITUTION_ONLY, ids=_ids(_WHOLE_INSTITUTION_ONLY))
def test_an_institution_figure_refuses_a_narrowed_scope(
    db_client: TestClient, surface: Surface
) -> None:
    """A branch slice under an institution heading is a wrong number with a
    right-looking name, so these surfaces refuse rather than narrow."""
    _seed_book()
    for data_scope, values in (
        (DataScope.BRANCH, (BR_ONE,)),
        (DataScope.REGION, ("Coastal",)),
    ):
        version = _grant(
            role_bundle=surface.bundle,
            sensitivity_scope=surface.sensitivity,
            data_scope=data_scope,
            data_scope_values=values,
        )
        response = surface.call(db_client, version)
        assert response.status_code == 403, f"{surface.label}/{data_scope.value}: {response.text}"
        assert "whole institution" in response.text


def test_a_branch_scoped_reader_sees_only_their_branch(db_client: TestClient) -> None:
    _seed_book()
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )

    page = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    )

    assert page.status_code == 200, page.text
    body = page.json()
    assert {row["source_reference"] for row in body["rows"]} == BR_TWO_LOANS
    assert {row["branch_id"] for row in body["rows"]} == {BR_TWO}
    # The count is over the SCOPED set: a total of 7 would disclose the size of
    # the book outside this reader's grant.
    assert body["total"] == len(BR_TWO_LOANS)
    assert body["filtered"] == len(BR_TWO_LOANS)
    assert body["data_scope"] == {
        "kind": "branch",
        "branches": [BR_TWO],
        "regions": [],
        "unresolved_regions": [],
    }


def test_the_whole_institution_reader_still_sees_the_whole_book(db_client: TestClient) -> None:
    """The control: the same request on an ``all`` scope is unchanged by the
    cutover, and its disclosure says so."""
    _seed_book()
    version = _grant(sensitivity_scope=SensitivityScope.RESTRICTED)

    body = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    ).json()

    assert {row["source_reference"] for row in body["rows"]} == BR_ONE_LOANS | BR_TWO_LOANS
    assert body["total"] == len(BR_ONE_LOANS | BR_TWO_LOANS)
    assert body["data_scope"]["kind"] == "all"
    assert body["data_scope"]["branches"] == []


def test_an_out_of_scope_branch_filter_yields_the_intersection_and_leaks_nothing(
    db_client: TestClient,
) -> None:
    """``branch`` is the client's filter and the scope is the server's; the two
    intersect. Asking for the other branch must not answer its rows, must not
    error, and must not say anything that distinguishes "not yours" from
    "not there"."""
    _seed_book()
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    auth = headers(authorization_version=version)

    outside = db_client.get(f"{BASE}/credit/loans?branch={BR_ONE}&limit=500", headers=auth)
    absent = db_client.get(f"{BASE}/credit/loans?branch=BR-NOPE&limit=500", headers=auth)

    assert outside.status_code == 200, outside.text
    assert absent.status_code == 200, absent.text
    assert outside.json()["rows"] == []
    assert outside.json()["filtered"] == 0
    # Indistinguishable: a branch that exists but is not granted answers exactly
    # as a branch that does not exist.
    assert outside.json() == absent.json()
    # And the client's own filter never widens the scope back out.
    inside = db_client.get(f"{BASE}/credit/loans?branch={BR_TWO}&limit=500", headers=auth)
    assert {row["source_reference"] for row in inside.json()["rows"]} == BR_TWO_LOANS


def test_pagination_runs_over_the_scoped_set(db_client: TestClient) -> None:
    """Every page of a scoped blotter comes from the scoped set, and the pages
    reassemble into exactly it — not into the institution's book."""
    _seed_book()
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    auth = headers(authorization_version=version)

    collected: set[str] = set()
    for offset in range(0, len(BR_TWO_LOANS) + 2, 2):
        page = db_client.get(f"{BASE}/credit/loans?limit=2&offset={offset}", headers=auth).json()
        assert page["total"] == len(BR_TWO_LOANS)
        collected.update(row["source_reference"] for row in page["rows"])
    assert collected == BR_TWO_LOANS


def test_facet_counts_are_over_the_scoped_set(db_client: TestClient) -> None:
    """A facet the page cannot show would be a count of rows the reader may not
    see — and the ``branches`` facet would enumerate the institution's branches."""
    _seed_book()
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )

    facets = db_client.get(
        f"{BASE}/credit/loans/facets", headers=headers(authorization_version=version)
    )

    assert facets.status_code == 200, facets.text
    body = facets.json()
    assert [facet["value"] for facet in body["branches"]] == [BR_TWO]
    assert sum(facet["count"] for facet in body["branches"]) == len(BR_TWO_LOANS)
    assert sum(facet["count"] for facet in body["grades"]) == len(BR_TWO_LOANS)
    assert body["data_scope"]["branches"] == [BR_TWO]


def test_a_region_scoped_reader_resolves_through_the_business_unit_register(
    db_client: TestClient,
) -> None:
    """A region grant names no branch codes; the institution's own register does.

    The credit module is in the calculation plane and must not read ``bi_*``, so
    the resolution goes through ``business_units`` — the one place a branch's
    region is declared.
    """
    _seed_book()
    _declare_regions({BR_ONE: "Coastal", BR_TWO: "Northern"})
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.REGION,
        data_scope_values=("Coastal",),
    )

    body = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    ).json()

    assert {row["source_reference"] for row in body["rows"]} == BR_ONE_LOANS
    assert body["total"] == len(BR_ONE_LOANS)
    assert body["data_scope"] == {
        "kind": "region",
        "branches": [BR_ONE],
        "regions": ["Coastal"],
        "unresolved_regions": [],
    }


def test_a_region_the_register_never_declared_serves_nothing_and_says_so(
    db_client: TestClient,
) -> None:
    """Zero rows for a mis-typed or not-yet-declared region must be
    distinguishable from zero rows because the bank has no loans — otherwise the
    only honest reading of an empty blotter is unavailable to the reader."""
    _seed_book()
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.REGION,
        data_scope_values=("Nowhere",),
    )

    body = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    ).json()

    assert body["rows"] == []
    assert body["total"] == 0
    assert body["data_scope"]["unresolved_regions"] == ["Nowhere"]
    assert body["data_scope"]["branches"] == []


def test_a_region_scope_follows_the_register_not_the_grant(db_client: TestClient) -> None:
    """A branch declared into a region after the grant joins it automatically —
    that is what a region grant means, and it is not a leak."""
    _seed_book()
    _declare_regions({BR_ONE: "Coastal"})
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.REGION,
        data_scope_values=("Coastal",),
    )
    auth = headers(authorization_version=version)
    before = db_client.get(f"{BASE}/credit/loans?limit=500", headers=auth).json()
    assert {row["source_reference"] for row in before["rows"]} == BR_ONE_LOANS

    _declare_regions({BR_ONE: "Coastal", BR_TWO: "Coastal"})
    after = db_client.get(f"{BASE}/credit/loans?limit=500", headers=auth).json()

    assert {row["source_reference"] for row in after["rows"]} == BR_ONE_LOANS | BR_TWO_LOANS
    assert after["data_scope"]["branches"] == sorted((BR_ONE, BR_TWO))


def test_a_mixed_scope_unions_its_branches_and_regions(db_client: TestClient) -> None:
    """Two rows OR: a branch sentence and a region sentence together read both."""
    _seed_book()
    _declare_regions({BR_ONE: "Coastal"})
    _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    version = _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.REGION,
        data_scope_values=("Coastal",),
    )

    body = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    ).json()

    assert {row["source_reference"] for row in body["rows"]} == BR_ONE_LOANS | BR_TWO_LOANS
    assert body["data_scope"]["kind"] == "mixed"
    assert body["data_scope"]["branches"] == sorted((BR_ONE, BR_TWO))


def test_one_whole_institution_row_beside_a_branch_row_reads_the_whole_book(
    db_client: TestClient,
) -> None:
    """Bindings OR, so the WIDEST wins: narrowing a reader who also holds an
    institution-wide sentence would revoke authority the Org Owner granted."""
    _seed_book()
    _grant(
        sensitivity_scope=SensitivityScope.RESTRICTED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    version = _grant(sensitivity_scope=SensitivityScope.RESTRICTED)

    body = db_client.get(
        f"{BASE}/credit/loans?limit=500", headers=headers(authorization_version=version)
    ).json()

    assert body["data_scope"]["kind"] == "all"
    assert body["total"] == len(BR_ONE_LOANS | BR_TWO_LOANS)
    # And the institution figures it also unlocks are reachable again.
    aggregated = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    assert (
        db_client.get(
            f"{BASE}/credit/dashboard", headers=headers(authorization_version=aggregated)
        ).status_code
        == 200
    )


def test_an_unconverted_module_refuses_a_narrowed_scope_rather_than_ignoring_it(
    db_client: TestClient,
) -> None:
    """The safe default on the shared institution gate, observed through capital.

    Capital has not applied a data scope, and its figures are institution ratios.
    If it ignored a branch grant it would serve the whole institution's capital
    position to a reader the Org Owner restricted to one branch — a grant that
    lies. It refuses instead; the same request on an ``all`` scope is unchanged.
    """
    _seed_book()
    scoped = _grant(
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.AGGREGATED,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_ONE,),
    )
    refused = db_client.get(
        f"{BASE}/capital/dashboard", headers=headers(authorization_version=scoped)
    )
    assert refused.status_code == 403, refused.text
    assert "whole institution" in refused.text

    whole = _grant(module_scope=ModuleScope.CAPITAL, sensitivity_scope=SensitivityScope.AGGREGATED)
    allowed = db_client.get(
        f"{BASE}/capital/dashboard", headers=headers(authorization_version=whole)
    )
    assert allowed.status_code == 200, allowed.text


# --- activity: scope through the facility the event names ------------------


#: The fixture book's only computed position date, so an event dated here is the
#: one an attribution rule reading "the snapshot on or before the event" can place.
_BOOK_DATE = FIXTURE_AS_OF.isoformat()


def test_activity_is_scoped_through_the_facility_each_event_names(
    db_client: TestClient,
) -> None:
    """Events carry no branch, so attribution runs through the facility's own
    ``(source_system, position_source_reference)`` — and the counts and monthly
    flows are taken after that filter, not before."""
    _seed_book()
    _seed_events(
        [
            {
                "ref": "W1",
                "type": "WRITE_OFF",
                "date": _BOOK_DATE,
                "amount": "100",
                "position": "LOAN/1",
            },
            {
                "ref": "W2",
                "type": "WRITE_OFF",
                "date": _BOOK_DATE,
                "amount": "250",
                "position": "LOAN/2",
            },
            {
                "ref": "R1",
                "type": "RECOVERY",
                "date": _BOOK_DATE,
                "amount": "40",
                "position": "LOAN/3",
            },
            {
                "ref": "D1",
                "type": "DISBURSEMENT",
                "date": _BOOK_DATE,
                "amount": "500",
                "position": "LOAN/1",
            },
            {
                "ref": "D2",
                "type": "DISBURSEMENT",
                "date": _BOOK_DATE,
                "amount": "600",
                "position": "LOAN/5",
            },
        ]
    )
    version = _grant(
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )

    response = db_client.get(
        f"{BASE}/credit/activity", headers=headers(authorization_version=version)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [event["source_reference"] for event in body["write_offs"]] == ["W2"]
    assert [event["source_reference"] for event in body["recoveries"]] == ["R1"]
    assert body["disbursement_count"] == 1
    june = next(flow for flow in body["monthly_flows"] if flow["month"] == "2026-06")
    assert Decimal(june["write_offs_ghs"]) == Decimal("250")
    assert body["data_scope"]["branches"] == [BR_TWO]
    # The control: the whole-institution reader sees all five.
    whole = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    everything = db_client.get(
        f"{BASE}/credit/activity", headers=headers(authorization_version=whole)
    ).json()
    assert {event["source_reference"] for event in everything["write_offs"]} == {"W1", "W2"}
    assert everything["disbursement_count"] == 2
    assert Decimal(
        next(f for f in everything["monthly_flows"] if f["month"] == "2026-06")["write_offs_ghs"]
    ) == Decimal("350")


def test_an_event_predating_the_first_computed_book_is_in_no_branch_scope(
    db_client: TestClient,
) -> None:
    """Where the facility WAS when the event happened is read from the computed
    position on or before the event date — never a later one, which could name a
    branch the facility only moved to afterwards.

    So an event that predates the institution's earliest computed book is claimed
    by no narrowed scope. That is deny-by-default and it is a REAL limitation, not
    a fixture artefact: it applies to every event before a bank's first ingested
    book, and to a bank that ingests only month-end books for the events of that
    first month. It is recorded in ``credit_enforcement_rollout.md``; the
    whole-institution reader is unaffected.
    """
    _seed_book()
    _seed_events(
        [
            {
                "ref": "EARLY",
                "type": "WRITE_OFF",
                "date": "2026-06-10",
                "amount": "100",
                "position": "LOAN/2",
            }
        ]
    )
    # Granted one at a time: every grant bumps ``authv`` and invalidates the
    # previous token, so the scoped read must happen before the wider grant.
    scoped = _grant(
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    assert (
        db_client.get(
            f"{BASE}/credit/activity", headers=headers(authorization_version=scoped)
        ).json()["write_offs"]
        == []
    )

    whole = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    assert [
        event["source_reference"]
        for event in db_client.get(
            f"{BASE}/credit/activity", headers=headers(authorization_version=whole)
        ).json()["write_offs"]
    ] == ["EARLY"]


def test_an_event_from_another_source_system_is_claimed_by_no_branch_scope(
    db_client: TestClient,
) -> None:
    """D-018: the facility is named by the event's OWN source system, never a
    cross-system guess on the bare reference. An event the platform cannot place
    is therefore in nobody's scope — deny-by-default — while the
    whole-institution reader still sees it."""
    _seed_book()
    _seed_events(
        [
            {
                "ref": "X1",
                "type": "WRITE_OFF",
                "date": _BOOK_DATE,
                "amount": "100",
                "position": "LOAN/2",
                "source_system": "API_PUSH",
            }
        ]
    )
    scoped = _grant(
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        data_scope=DataScope.BRANCH,
        data_scope_values=(BR_TWO,),
    )
    assert (
        db_client.get(
            f"{BASE}/credit/activity", headers=headers(authorization_version=scoped)
        ).json()["write_offs"]
        == []
    )

    whole = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    body = db_client.get(
        f"{BASE}/credit/activity", headers=headers(authorization_version=whole)
    ).json()
    assert [event["source_reference"] for event in body["write_offs"]] == ["X1"]
