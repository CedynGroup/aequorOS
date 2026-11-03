"""Examiner mode v1 (Phase 2 item 7), against the ACTUAL primary database.

Authority is two independent sentences (issue 180): the bundle needs a
whole-institution AUDIT/restricted ``view`` binding, and each embedded source
needs its own module grant. The suite user here holds both — created inside the
shared, rolled-back transaction — so the bundle is complete and these tests keep
asserting the read model over real rows rather than a permission story, which
``tests/reporting/api/test_examiner_authorization.py`` owns.

Invariants over the real Sample Bank: the run list is the period's stored runs;
reproduction re-hashes the stored snapshot to its stored ``input_hash`` and a
tampered snapshot no longer reproduces; the documentation package bundles the
period's latest succeeded runs, its packages, the CFP approval state and the
audit trail, and is tenant-isolated. Opt-in via REAL_DATA_DATABASE_URL;
everything rolls back inside ``real_client``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.security import ROLES, has_role
from app.identity.models.user import User
from app.identity.service import authorization
from app.models import RegulatoryRun
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_USER_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

# The ladder test is pure and stays hermetic; the DB-backed tests opt in per test.
BASE = f"/api/v1/banks/{REAL_BANK_ID}"


def _evidence_reader(real_session: Session) -> dict[str, str]:
    """Grant the entry sentence and every source sentence, then act as them.

    Written on the shared transaction, so the real primary database's grants are
    untouched after the rollback. The scalar ``examiner`` role is still carried
    in the token to prove it is authority-neutral: the bindings decide.
    """
    real_session.info["organization_id"] = REAL_ORG_ID
    grants = (
        (RoleBundle.AUDITOR, ModuleScope.AUDIT, SensitivityScope.RESTRICTED),
        (RoleBundle.VIEWER, ModuleScope.LIQUIDITY, SensitivityScope.CONFIDENTIAL),
        (RoleBundle.VIEWER, ModuleScope.CAPITAL, SensitivityScope.CONFIDENTIAL),
        (RoleBundle.VIEWER, ModuleScope.REGULATORY, SensitivityScope.RESTRICTED),
    )
    for bundle, module_scope, sensitivity_scope in grants:
        authorization.create_role_binding(
            real_session,
            organization_id=REAL_ORG_ID,
            principal_user_id=REAL_USER_ID,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION, None, module_scope, sensitivity_scope
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "real-data-examiner-suite"),
            reason="Exercise the examiner evidence bundle over real rows",
            commit=False,
        )
    real_session.commit()
    user = real_session.get(User, REAL_USER_ID)
    assert user is not None
    return real_headers(roles=("examiner",), authorization_version=user.authorization_version)


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BASE}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _period_with_liquidity_run(client: TestClient) -> tuple[str, str]:
    """The latest period plus a succeeded baseline liquidity run for it — the
    real bank's stored one where present, else the engine runs now."""
    period_id = _latest_period(client)["id"]
    listed = client.get(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        params={
            "module": "liquidity",
            "scenario_code": "baseline",
            "reporting_period_id": period_id,
            "limit": 100,
        },
    )
    assert listed.status_code == 200, listed.text
    for run in listed.json()["runs"]:
        if run["status"] == "succeeded":
            return period_id, run["id"]
    run = client.post(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        json={"module": "liquidity", "reporting_period_id": period_id, "scenario_code": "baseline"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "succeeded", run.json()
    return period_id, run.json()["id"]


def test_examiner_ladder_reads_but_never_mutates() -> None:
    assert ROLES == ("admin", "approver", "analyst", "examiner", "viewer")
    assert has_role(["examiner"], "viewer") is True
    assert has_role(["examiner"], "examiner") is True
    assert has_role(["examiner"], "analyst") is False
    assert has_role(["viewer"], "examiner") is False


@requires_real_data
def test_examiner_surfaces_and_run_reproduction(
    real_client: TestClient, real_session: Session
) -> None:
    period_id, run_id = _period_with_liquidity_run(real_client)
    examiner = _evidence_reader(real_session)

    runs = real_client.get(
        f"{BASE}/examiner/runs", headers=examiner, params={"reporting_period_id": period_id}
    )
    assert runs.status_code == 200, runs.text
    listed = runs.json()["runs"]
    assert any(entry["run_id"] == run_id for entry in listed)
    # The examiner list IS the period's stored run set (module filter narrows it).
    liquidity_only = real_client.get(
        f"{BASE}/examiner/runs",
        headers=examiner,
        params={"reporting_period_id": period_id, "module": "liquidity"},
    ).json()["runs"]
    assert {entry["run_id"] for entry in liquidity_only} == {
        entry["run_id"] for entry in listed if entry["module"] == "liquidity"
    }

    # Reproduction: the stored snapshot re-hashes to the stored input_hash.
    reproduction = real_client.get(f"{BASE}/examiner/runs/{run_id}/reproduction", headers=examiner)
    assert reproduction.status_code == 200, reproduction.text
    body = reproduction.json()
    assert body["reproducible"] is True
    assert body["stored_input_hash"] == body["recomputed_input_hash"]
    assert len(body["stored_input_hash"]) == 64
    assert body["fact_count"] > 0

    # A tampered snapshot is caught: the recomputed hash diverges. (Written on
    # the shared transaction — the real run is untouched after the rollback.)
    real_session.info["organization_id"] = REAL_ORG_ID
    run_row = real_session.get(RegulatoryRun, UUID(run_id))
    assert run_row is not None
    tampered = dict(run_row.inputs)
    tampered["as_of_date"] = "2031-12-31"
    real_session.execute(
        update(RegulatoryRun).where(RegulatoryRun.id == UUID(run_id)).values(inputs=tampered)
    )
    real_session.commit()
    tampered_check = real_client.get(
        f"{BASE}/examiner/runs/{run_id}/reproduction", headers=examiner
    ).json()
    assert tampered_check["reproducible"] is False
    assert tampered_check["stored_input_hash"] == body["stored_input_hash"]
    assert tampered_check["recomputed_input_hash"] != body["recomputed_input_hash"]

    # The examiner cannot mutate: analyst-gated endpoints refuse the role.
    blocked = real_client.post(
        f"{BASE}/regulatory-runs",
        headers=examiner,
        json={
            "module": "liquidity",
            "reporting_period_id": period_id,
            "scenario_code": "combined",
        },
    )
    assert blocked.status_code == 403
    # ...while a viewer-only surface stays readable to it.
    assert real_client.get(f"{BASE}/regulatory-runs", headers=examiner).status_code == 200


@requires_real_data
def test_documentation_package_bundles_period_evidence(
    real_client: TestClient, real_session: Session
) -> None:
    period_id, run_id = _period_with_liquidity_run(real_client)
    examiner = _evidence_reader(real_session)
    package = real_client.get(
        f"{BASE}/examiner/documentation-package",
        headers=examiner,
        params={"reporting_period_id": period_id},
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["bank_id"] == REAL_BANK_ID
    assert body["reporting_period_id"] == period_id
    latest = {(entry["module"], entry["scenario_code"]): entry for entry in body["latest_runs"]}
    assert ("liquidity", "baseline") in latest
    # One latest run per (module, scenario) — the newest succeeded one, which is
    # the run generation binds too.
    assert len(latest) == len(body["latest_runs"])
    assert latest[("liquidity", "baseline")]["run_id"] == run_id
    assert all(entry["status"] == "succeeded" for entry in body["latest_runs"])
    assert body["audit_event_count"] > 0
    assert any(path.endswith("/liquidity-thresholds") for path in body["register_endpoints"])

    # The CFP block mirrors the CFP register's approval state.
    cfp = real_client.get(f"{BASE}/liquidity/cfp", headers=examiner)
    assert cfp.status_code == 200, cfp.text
    approved = cfp.json()["approved"]
    assert body["cfp_approved_version"] == (approved["version"] if approved else None)
    assert body["cfp_active"] == bool(approved and approved["active"])

    # Every package for the period's reporting date is bundled, with its digest.
    listed = real_client.get(
        f"{BASE}/regulatory-packages",
        headers=examiner,
        params={"reporting_date": body["as_of_date"], "limit": 100},
    )
    assert listed.status_code == 200, listed.text
    bundled = {entry["package_id"] for entry in body["packages"]}
    assert {item["id"] for item in listed.json()["packages"]} <= bundled

    foreign = real_client.get(
        f"{BASE}/examiner/documentation-package",
        headers=other_headers(roles=("examiner",)),
        params={"reporting_period_id": period_id},
    )
    assert foreign.status_code == 404
