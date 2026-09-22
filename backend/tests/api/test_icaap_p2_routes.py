"""One analyst's pass through the risk-and-capital half of the ICAAP, over HTTP.

The route layer is where the wire contract is proved: the shapes the dashboard
binds to, the operation ids the generated client names, and the refusals a user
actually sees.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, BankReportingPeriod, RegulatoryParameter, User
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import authorization, regulatory_capital
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/icaap"
AS_OF = date(2025, 12, 31)
CHECKER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


@pytest.fixture(autouse=True)
def _enable_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()


def _context() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


def _bind(session, user_id: UUID, bundle: RoleBundle) -> int:
    authorization.create_role_binding(
        session,
        organization_id=ORG_1,
        principal_user_id=user_id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            SAMPLE_BANK_ID,
            ModuleScope.CAPITAL,
            SensitivityScope.CONFIDENTIAL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="Exercise the ICAAP risk and capital routes.",
    )
    user = session.get(User, user_id)
    assert user is not None
    session.refresh(user)
    return user.authorization_version


@pytest.fixture
def auth(db_client: TestClient) -> dict[str, str]:
    """An analyst with a sealed capital run to bind figures to."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
        version = _bind(session, USER_1, RoleBundle.ANALYST)
        period = session.scalar(
            select(BankReportingPeriod.id).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == AS_OF,
            )
        )
        assert period is not None
        regulatory_capital.create_capital_run(
            session,
            _context(),
            SAMPLE_BANK_ID,
            RegulatoryRunCreate(
                module="capital", reporting_period_id=period, scenario_code="baseline"
            ),
        )
        session.commit()
        return headers(roles=("analyst",), authorization_version=version)
    finally:
        session.close()


@pytest.fixture
def approver_auth(db_client: TestClient, auth: dict[str, str]) -> dict[str, str]:
    """A second person who may approve but not edit."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            User(
                id=CHECKER,
                organization_id=ORG_1,
                email="route.checker@example.test",
                display_name="Second Person",
            )
        )
        session.commit()
        version = _bind(session, CHECKER, RoleBundle.APPROVER)
        return headers(roles=("approver",), user_id=CHECKER, authorization_version=version)
    finally:
        session.close()


def _cycle(client: TestClient, auth: dict[str, str]) -> str:
    response = client.post(
        f"{BASE}/cycles",
        json={
            "fiscal_year": 2025,
            "cycle_kind": "rehearsal",
            "basis": "solo",
            "framework_code": "bog_icaap",
            "framework_version": "2026.02-ed.1",
            "reason": "Dry run before the first filing",
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_the_risk_register_is_readable_and_writable_over_http(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    listing = db_client.get(f"{BASE}/cycles/{cycle_id}/risks", headers=auth)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["matrix"]["material_min_score"] == 10
    assert body["summary"]["assessed_risk_count"] == 0

    saved = db_client.put(
        f"{BASE}/cycles/{cycle_id}/risks/credit",
        json={
            "likelihood_score": 4,
            "impact_score": 4,
            "materiality_rationale": "Concentrated book and a thin buffer.",
            "pillar2_treatment": "quantified",
            "reason": "Score the risk.",
        },
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["verdict"] == "material"
    assert saved.json()["rating_key"] == "high"


def test_the_appetite_refusal_names_the_violation_a_user_can_act_on(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    response = db_client.post(
        f"{BASE}/cycles/{cycle_id}/appetite/metrics",
        json={
            "metric_key": "car",
            "label": "Total capital ratio",
            "measure_kind": "quantitative",
            "qualitative_statement": "Capital is held above the regulatory minimum.",
            "direction": "floor",
            "appetite_value": "16",
            "tolerance_value": "14.5",
            "capacity_value": "12.5",
            "value_source": "block_fact",
            "reason": "Record the Board's appetite.",
        },
        headers=auth,
    )
    assert response.status_code == 422, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "appetite_ordering_invalid"
    assert "capacity_weaker_than_regulatory" in detail["violations"]


def test_a_pillar_two_figure_is_computed_then_approved_by_a_second_person(
    db_client: TestClient, auth: dict[str, str], approver_auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    created = db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items",
        json={
            "component_key": "irrbb",
            "method": "irrbb_interim_delta_eve",
            "input_mode": "manual_with_evidence",
            "rationale": "Group ALM figures.",
            "reason": "Quantify interest rate risk.",
        },
        headers=auth,
    )
    assert created.status_code == 201, created.text
    item = created.json()

    computed = db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/compute",
        json={
            "base_revision_no": item["current_revision_no"],
            "manual_inputs": {
                "tier1": "700",
                "irrbb_deltas": {
                    "parallel_up_450": "-116.759902",
                    "parallel_down_450": "142.996097",
                },
            },
            "reason": "Compute from the group ALM figures.",
        },
        headers=auth,
    )
    assert computed.status_code == 200, computed.text
    body = computed.json()
    assert body["method_status"] == "interim_non_sf"
    assert body["baseline_amount"] == "116.7599"
    assert body["approvable"] is True

    # The maker cannot approve their own figure: the dependency refuses first.
    refused = db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/approve",
        json={"revision_no": body["current_revision_no"], "note": "Mine."},
        headers=auth,
    )
    assert refused.status_code == 403, refused.text

    approved = db_client.post(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/approve",
        json={"revision_no": body["current_revision_no"], "note": "Reviewed."},
        headers=approver_auth,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_current"] is True

    revisions = db_client.get(
        f"{BASE}/cycles/{cycle_id}/pillar2/items/{item['id']}/revisions", headers=auth
    )
    assert revisions.status_code == 200
    assert len(revisions.json()["revisions"]) == 2


def test_the_register_read_carries_the_grid_totals_and_the_parameters(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    response = db_client.get(f"{BASE}/cycles/{cycle_id}/pillar2", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["diversification_allowed"] is False
    assert {row["row"] for row in body["table5_totals"]["rows"]} == {
        "credit_concentration",
        "irrbb",
        "sovereign",
        "country_and_fx",
        "reputational",
        "others",
    }
    assert all(row["baseline"] is None for row in body["table5_totals"]["rows"])


def test_table_five_reports_what_it_still_needs(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    response = db_client.get(f"{BASE}/cycles/{cycle_id}/pillar2/table5", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is False
    assert body["unavailable_reason"]
    assert body["unit"] == "thousands"


def test_the_parameter_listing_shows_every_governed_figure_with_provenance(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    response = db_client.get(f"{BASE}/cycles/{cycle_id}/parameters", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == AS_OF.isoformat()
    assert body["missing"] == []
    codes = {entry["param_code"] for entry in body["parameters"]}
    assert "ccr_name_bands_hhi" in codes
    representative = [entry for entry in body["parameters"] if entry["representative"] is True]
    assert representative, "REPRESENTATIVE rows must be labelled on the wire"
    assert all(entry["source_citation"] for entry in representative)


def test_a_missing_governed_row_is_a_409_naming_the_code(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(RegulatoryParameter).where(
                RegulatoryParameter.param_code == "icaap_materiality_rating_bands"
            )
        )
        session.commit()
    finally:
        session.close()
    response = db_client.get(f"{BASE}/cycles/{cycle_id}/risks", headers=auth)
    assert response.status_code == 409, response.text
    detail = response.json()["error"]["details"]
    assert detail["error_code"] == "missing_parameter"
    assert detail["param_code"] == "icaap_materiality_rating_bands"


def test_the_reconciliation_and_allocation_reads_are_available_from_the_start(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    reconciliation = db_client.get(f"{BASE}/cycles/{cycle_id}/reconciliation", headers=auth)
    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()["requirement"]["lines"] == []

    allocation = db_client.get(f"{BASE}/cycles/{cycle_id}/allocation", headers=auth)
    assert allocation.status_code == 200, allocation.text
    assert allocation.json()["available"] is False


def test_the_requirement_is_computed_once_the_capital_blocks_are_linked(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    for block_type in ("capital_position", "pillar1_rwa"):
        created = db_client.post(
            f"{BASE}/cycles/{cycle_id}/blocks",
            json={"block_type": block_type},
            headers=auth,
        )
        assert created.status_code == 201, created.text
    computed = db_client.post(
        f"{BASE}/cycles/{cycle_id}/reconciliation/requirement/compute",
        json={"reason": "Reconcile the capital requirement."},
        headers=auth,
    )
    assert computed.status_code == 200, computed.text
    lines = computed.json()["requirement"]["lines"]
    assert {line["line_key"] for line in lines} >= {"pillar1_credit", "pillar2_irrbb"}


def test_the_challenge_log_records_a_question_and_its_answer(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    raised = db_client.post(
        f"{BASE}/cycles/{cycle_id}/challenges",
        json={
            "raised_in": "board_risk_committee",
            "raised_by_name": "A. Mensah, Chair",
            "raised_on": "2026-02-12",
            "target_kind": "pillar2_item",
            "target_ref": "irrbb",
            "challenge_text": "Why does the add-on ignore the 450bp shocks?",
            "severity": "high",
        },
        headers=auth,
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["open"] is True

    answered = db_client.post(
        f"{BASE}/cycles/{cycle_id}/challenges/{raised.json()['id']}/responses",
        json={
            "outcome": "accepted_changed",
            "response_text": "The shock set now includes both 450bp scenarios.",
            "responder_function": "Chief Risk Officer",
        },
        headers=auth,
    )
    assert answered.status_code == 201, answered.text
    assert answered.json()["open"] is False

    listing = db_client.get(f"{BASE}/cycles/{cycle_id}/challenges", headers=auth)
    assert listing.json()["board_challenge_count"] == 1


def test_the_supervisory_add_on_list_is_bank_scoped_and_never_public(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    response = db_client.get(f"{BASE}/supervisory-addons", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bank_id"] == SAMPLE_BANK_ID
    assert body["never_public"] is True
    assert body["addons"] == []


def test_readiness_now_reports_the_assessment_as_well_as_the_document(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    cycle_id = _cycle(db_client, auth)
    response = db_client.get(f"{BASE}/cycles/{cycle_id}/readiness", headers=auth)
    assert response.status_code == 200, response.text
    codes = {item["code"] for item in response.json()["items"]}
    assert "risk_unassessed" in codes
    assert "section_not_committed" in codes
    assert response.json()["ready_for_freeze"] is False


def test_an_unknown_cycle_is_not_found_on_every_new_route(
    db_client: TestClient, auth: dict[str, str]
) -> None:
    missing = uuid4()
    for path in (
        f"{BASE}/cycles/{missing}/risks",
        f"{BASE}/cycles/{missing}/appetite",
        f"{BASE}/cycles/{missing}/pillar2",
        f"{BASE}/cycles/{missing}/reconciliation",
        f"{BASE}/cycles/{missing}/allocation",
        f"{BASE}/cycles/{missing}/audit-reviews",
        f"{BASE}/cycles/{missing}/challenges",
        f"{BASE}/cycles/{missing}/capital-triggers",
    ):
        response = db_client.get(path, headers=auth)
        assert response.status_code == 404, path
