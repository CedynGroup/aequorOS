"""The three Standardised Framework routes, and the authority each one needs.

Running the framework mints filing evidence, so it takes a CONFIDENTIAL run
binding; reading the result — or the attempt history behind it — takes an AGGREGATED
view binding. Denial HIDES: a principal with no IRRBB binding gets the same
404 as a bank that does not exist, so no route can be used to enumerate
institutions.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, RegulatoryRun, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID
from tests.services.sf_book import seed_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/irr/standardised-framework"
SIBLING_BANK_ID = "BK-SFSIB001"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    del db_client
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def _seed() -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        period_id = seed_book(session)
        session.commit()
        return period_id
    finally:
        session.close()


def _add_sibling_bank() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="SF sibling bank",
                short_name="SF sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _grant(
    bundle: RoleBundle = RoleBundle.VIEWER,
    *,
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED,
    institution_id: str | None = SAMPLE_BANK_ID,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=institution_id,
                module_scope=ModuleScope.IRRBB,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "irrbb-sf-test"),
            reason="IRRBB Standardised Framework route regression",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return user.authorization_version
    finally:
        session.close()


def _auth(version: int) -> dict[str, str]:
    return headers(ORG_1, user_id=USER_1, roles=("admin",), authorization_version=version)


def test_the_run_route_mints_a_standardised_framework_run(db_client: TestClient) -> None:
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["module"] == "irr_sf"
    assert body["scenario_code"] == "standardised_framework"
    assert body["status"] == "succeeded", body.get("error")


def test_a_scalar_role_alone_never_satisfies_the_run_route(db_client: TestClient) -> None:
    """The shell's role claim is not authority; the binding is."""
    period_id = _seed()

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(1),
    )

    assert response.status_code in {403, 404}, response.text


def test_an_aggregated_viewer_cannot_mint_a_run(db_client: TestClient) -> None:
    period_id = _seed()
    version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.AGGREGATED)

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code in {403, 404}, response.text


def test_the_read_route_returns_the_result_with_production_copy(
    db_client: TestClient,
) -> None:
    period_id = _seed()
    # Sensitivity scopes are exact, not ordered: minting is CONFIDENTIAL and
    # reading is AGGREGATED, so an analyst who can run the framework still
    # needs the read scope to see the result.
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)
    minted = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )
    assert minted.status_code == 201, minted.text

    response = db_client.get(
        BASE, params={"reporting_period_id": str(period_id)}, headers=_auth(version)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run"]["module"] == "irr_sf"
    # Every code that has printed copy carries it: no raw enum reaches a reader.
    assert [row["label"] for row in body["scenarios"]] == [
        "Parallel up",
        "Parallel down",
        "Steepener",
        "Flattener",
        "Short rate up",
        "Short rate down",
    ]
    assert body["measures"]["outlier_set"]["label"] == "Outlier test scenarios"
    assert len(body["buckets"]) == 19
    assert body["buckets"][0]["label"] == "Overnight"
    # The outlier verdict and the threshold it was measured against travel
    # together, so the page cannot state one without the other.
    assert isinstance(body["outlier"], bool)
    assert body["outlier_threshold_pct"] is not None
    # A representative or unconfirmed parameter says so in the payload itself.
    statements = {row["code"]: row["statement"] for row in body["parameters"]}
    assert "pending confirmation with the supervisor" in (
        statements["irrbb_sf_parallel_shock_bp"]
    )
    assert "representative only" in statements["irrbb_sf_default_cash_flow_profile"]
    assert body["automatic_option_statement"].startswith("No automatic interest-rate options")
    # The floor statement says what the ENGINE did and names the point as open,
    # rather than asserting to the filer what a supervisory standard requires
    # (audit U-3, 2026-09-20). Both halves are pinned because the first half on
    # its own is what the discarded sentence also said.
    floor_statement = body["post_shock_floor_statement"]
    assert floor_statement.startswith("No post-shock rate floor was applied")
    assert "open point for the supervisor" in floor_statement
    assert "framework text prescribes no post-shock rate floor" not in floor_statement
    assert all(
        row["label"] != row["marker"] for row in body["data_quality"]["assumptions"]
    )


def test_the_read_route_hides_rather_than_announces_a_denial(
    db_client: TestClient,
) -> None:
    period_id = _seed()

    response = db_client.get(
        BASE, params={"reporting_period_id": str(period_id)}, headers=_auth(1)
    )

    assert response.status_code == 404, response.text


def test_a_sibling_bank_in_the_same_tenant_is_not_readable(
    db_client: TestClient,
) -> None:
    period_id = _seed()
    _add_sibling_bank()
    version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.ALL,
        institution_id=SAMPLE_BANK_ID,
    )

    response = db_client.get(
        f"/api/v1/banks/{SIBLING_BANK_ID}/irr/standardised-framework",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 404, response.text


def test_a_period_with_no_run_is_a_404_that_says_what_to_do(
    db_client: TestClient,
) -> None:
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)

    response = db_client.get(
        BASE, params={"reporting_period_id": str(period_id)}, headers=_auth(version)
    )

    assert response.status_code == 404, response.text
    # The copy tells the reader what to do, rather than leaving an empty page.
    assert "Run the framework" in response.text


def test_the_run_route_rejects_an_unknown_reporting_period(
    db_client: TestClient,
) -> None:
    _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(uuid4())},
        headers=_auth(version),
    )

    assert response.status_code == 404, response.text


def test_the_run_body_takes_no_scenario_code(db_client: TestClient) -> None:
    """The framework prescribes all six shapes; choosing one is not a choice."""
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id), "scenario_code": "parallel_up"},
        headers=_auth(version),
    )

    assert response.status_code == 422, response.text


def test_a_refused_run_reaches_the_api_as_data_not_a_500(db_client: TestClient) -> None:
    """An option book fails the run, and the failure is the response body."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        period_id = seed_book(session, with_options=True)
        session.commit()
    finally:
        session.close()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "irrbb_sf_options_unsupported"
    assert "automatic_options_not_modelled" not in response.text


def test_the_generic_run_registry_hides_sf_runs_without_irrbb_authority(
    db_client: TestClient,
) -> None:
    """``irr_sf`` answers to the same module authority as the rest of IRRBB."""
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    minted = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )
    assert minted.status_code == 201, minted.text

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
        user_version = session.scalar(select(User.authorization_version).where(User.id == USER_1))
    finally:
        session.close()
    assert user_version is not None

    listing = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs",
        headers=_auth(user_version),
    )

    if listing.status_code == 200:
        assert all(row["module"] != "irr_sf" for row in listing.json()["runs"])
    else:
        assert listing.status_code in {403, 404}, listing.text


# ---------------------------------------------------------------------------
# The attempt history (GAP-4 item 2)
# ---------------------------------------------------------------------------
#
# Before this route existed the dashboard reconstructed "was it tried, and what
# happened" from the generic run registry — which is where a defect lived: the
# registry pages its rows under `runs` while the screen read `items`, so after a
# refused run the screen said "nobody has run it".


def _record_refusal(period_id: UUID, *, code: str, message: str) -> None:
    """A refused attempt on the run table.

    Written directly because the only refusal the engine can raise needs a book
    holding interest-rate options, and this test is about what the READ says
    about a refusal — not about reproducing one.
    """
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            RegulatoryRun(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                reporting_period_id=period_id,
                module="irr_sf",
                scenario_code="standardised_framework",
                status="failed",
                engine_version="test",
                input_schema_version="test",
                output_schema_version="test",
                input_hash="0" * 64,
                inputs={},
                metrics={},
                error_code=code,
                error_message=message,
                created_by=USER_1,
            )
        )
        session.commit()
    finally:
        session.close()


def test_an_untried_reporting_date_answers_rather_than_404s(
    db_client: TestClient,
) -> None:
    """"Nobody has run it" is an ANSWER — the one a refusal must be told from."""
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)

    response = db_client.get(
        f"{BASE}/attempts",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempted"] is False
    assert body["has_result"] is False
    assert body["latest"] is None
    assert body["refusal"] is None
    assert body["attempts"] == []
    assert body["statement"] == (
        "The standardised framework has not been run for this reporting date. "
        "Nothing has been refused — nobody has tried."
    )


def test_a_refused_attempt_is_reachable_and_says_why(db_client: TestClient) -> None:
    """The result route 404s on a refusal; this one reports it, in a sentence."""
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)
    _record_refusal(
        period_id,
        code="irrbb_sf_options_unsupported",
        message="Automatic interest-rate options are present.",
    )

    result = db_client.get(
        BASE, params={"reporting_period_id": str(period_id)}, headers=_auth(version)
    )
    assert result.status_code == 404, result.text

    response = db_client.get(
        f"{BASE}/attempts",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempted"] is True
    assert body["has_result"] is False
    assert body["latest"]["status"] == "failed"
    assert body["latest"]["status_label"] == "Refused to measure"
    assert body["latest"]["error_code"] == "irrbb_sf_options_unsupported"
    assert body["refusal"]["run_id"] == body["latest"]["run_id"]
    # The field the dashboard adapter reads for the sentence it prints instead
    # of composing its own. Pinned by name: reading the wrong key here fails
    # silently, which is the class of defect this whole route exists to end.
    assert body["latest"]["refusal_statement"].startswith(
        "The standardised framework could not be measured for this date because"
    )
    # The engine's refusal name never reaches the reader on its own.
    assert body["statement"].startswith(
        "The standardised framework could not be measured for this date because "
        "the banking book holds interest-rate options"
    )
    assert "irrbb_sf_options_unsupported" not in body["statement"]


def test_a_refusal_after_a_result_says_which_figures_are_on_the_page(
    db_client: TestClient,
) -> None:
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)
    minted = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )
    assert minted.status_code == 201, minted.text
    _record_refusal(period_id, code="irrbb_sf_options_unsupported", message="")

    response = db_client.get(
        f"{BASE}/attempts",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_result"] is True
    assert body["latest"]["status"] == "failed"
    assert len(body["attempts"]) == 2
    assert "the figures on this page are the earlier ones" in body["statement"]


def test_a_succeeded_attempt_is_reported_as_a_result(db_client: TestClient) -> None:
    period_id = _seed()
    version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)
    minted = db_client.post(
        f"{BASE}/runs",
        json={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )
    assert minted.status_code == 201, minted.text

    response = db_client.get(
        f"{BASE}/attempts",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(version),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attempted"] is True
    assert body["has_result"] is True
    assert body["latest"]["status_label"] == "Produced a result"
    assert body["latest"]["refusal_statement"] == ""
    assert body["statement"] == (
        "The standardised framework produced a result for this reporting date."
    )


def test_the_attempts_route_hides_rather_than_announces_a_denial(
    db_client: TestClient,
) -> None:
    period_id = _seed()

    response = db_client.get(
        f"{BASE}/attempts",
        params={"reporting_period_id": str(period_id)},
        headers=_auth(1),
    )

    assert response.status_code == 404, response.text
