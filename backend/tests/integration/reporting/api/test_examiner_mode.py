"""Examiner read-model checks over a disposable canonical bank book.

The reader holds an explicit AUDIT/restricted view binding plus each source
module's view binding. Grants, stored evidence and snapshot tampering share the
hermetic fixture transaction; permission-denial depth lives in
``tests/integration/reporting/api/test_examiner_authorization.py``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
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
from app.models import (
    AuthorizationBinding,
    BankReportingPeriod,
    ContingencyFundingPlan,
    RegulatoryPackage,
    RegulatoryRun,
)
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


def _evidence_reader(db_session: Session) -> dict[str, str]:
    db_session.info["organization_id"] = ORG_1
    grants = (
        (RoleBundle.AUDITOR, ModuleScope.AUDIT, SensitivityScope.RESTRICTED),
        (RoleBundle.VIEWER, ModuleScope.LIQUIDITY, SensitivityScope.CONFIDENTIAL),
        (RoleBundle.VIEWER, ModuleScope.CAPITAL, SensitivityScope.CONFIDENTIAL),
        (RoleBundle.VIEWER, ModuleScope.REGULATORY, SensitivityScope.RESTRICTED),
    )
    for bundle, module_scope, sensitivity_scope in grants:
        authorization.create_role_binding(
            db_session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION, None, module_scope, sensitivity_scope
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "hermetic-examiner-suite"),
            reason="Exercise the examiner evidence bundle over disposable rows",
            commit=False,
        )
    db_session.commit()
    user = db_session.get(User, USER_1)
    assert user is not None
    return headers(roles=("examiner",), authorization_version=user.authorization_version)


@pytest.fixture
def evidence_book(db_client: TestClient, db_session: Session) -> tuple[UUID, UUID]:
    _ = db_client
    materialize_canonical_test_book(db_session)
    db_session.execute(
        delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
    )
    period = db_session.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ORG_1,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == date(2026, 3, 31),
        )
    )
    assert period is not None
    inputs: dict[str, object] = {
        "as_of_date": period.period_end.isoformat(),
        "facts": [{"amount": "125.00"}],
    }
    run = RegulatoryRun(
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        reporting_period_id=period.id,
        module="liquidity",
        scenario_code="baseline",
        status="succeeded",
        engine_version="examiner-test-v1",
        input_schema_version="examiner-test-input-v1",
        output_schema_version="examiner-test-output-v1",
        input_hash=hashlib.sha256(
            json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest(),
        inputs=inputs,
        metrics={},
        created_by=USER_1,
    )
    db_session.add_all(
        [
            run,
            RegulatoryPackage(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                return_family="liquidity",
                return_code="LCR-NSFR",
                reporting_date=period.period_end,
                frequency="monthly",
                basis="solo",
                status="generated",
                version=1,
                content_digest="a" * 64,
                generated_by=USER_1,
            ),
            ContingencyFundingPlan(
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                version=1,
                status="approved",
                content={},
                active=True,
            ),
        ]
    )
    db_session.commit()
    return period.id, run.id


def test_examiner_ladder_reads_but_never_mutates() -> None:
    assert ROLES == ("admin", "approver", "analyst", "examiner", "viewer")
    assert has_role(["examiner"], "viewer") is True
    assert has_role(["examiner"], "examiner") is True
    assert has_role(["examiner"], "analyst") is False
    assert has_role(["viewer"], "examiner") is False


def test_examiner_surfaces_and_run_reproduction(
    db_client: TestClient, db_session: Session, evidence_book: tuple[UUID, UUID]
) -> None:
    stored_period_id, stored_run_id = evidence_book
    period_id = str(stored_period_id)
    run_id = str(stored_run_id)
    examiner = _evidence_reader(db_session)

    runs = db_client.get(
        f"{BASE}/examiner/runs", headers=examiner, params={"reporting_period_id": period_id}
    )
    assert runs.status_code == 200, runs.text
    listed = runs.json()["runs"]
    assert any(entry["run_id"] == run_id for entry in listed)
    # The examiner list IS the period's stored run set (module filter narrows it).
    liquidity_only = db_client.get(
        f"{BASE}/examiner/runs",
        headers=examiner,
        params={"reporting_period_id": period_id, "module": "liquidity"},
    ).json()["runs"]
    assert {entry["run_id"] for entry in liquidity_only} == {
        entry["run_id"] for entry in listed if entry["module"] == "liquidity"
    }

    # Reproduction: the stored snapshot re-hashes to the stored input_hash.
    reproduction = db_client.get(f"{BASE}/examiner/runs/{run_id}/reproduction", headers=examiner)
    assert reproduction.status_code == 200, reproduction.text
    body = reproduction.json()
    assert body["reproducible"] is True
    assert body["stored_input_hash"] == body["recomputed_input_hash"]
    assert len(body["stored_input_hash"]) == 64
    assert body["fact_count"] > 0

    # A tampered snapshot is caught: the recomputed hash diverges.
    db_session.info["organization_id"] = ORG_1
    run_row = db_session.get(RegulatoryRun, UUID(run_id))
    assert run_row is not None
    tampered = dict(run_row.inputs)
    tampered["as_of_date"] = "2031-12-31"
    db_session.execute(
        update(RegulatoryRun).where(RegulatoryRun.id == UUID(run_id)).values(inputs=tampered)
    )
    db_session.commit()
    tampered_check = db_client.get(
        f"{BASE}/examiner/runs/{run_id}/reproduction", headers=examiner
    ).json()
    assert tampered_check["reproducible"] is False
    assert tampered_check["stored_input_hash"] == body["stored_input_hash"]
    assert tampered_check["recomputed_input_hash"] != body["recomputed_input_hash"]

    # The examiner cannot mutate: analyst-gated endpoints refuse the role.
    blocked = db_client.post(
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
    assert db_client.get(f"{BASE}/regulatory-runs", headers=examiner).status_code == 200


def test_documentation_package_bundles_period_evidence(
    db_client: TestClient, db_session: Session, evidence_book: tuple[UUID, UUID]
) -> None:
    stored_period_id, stored_run_id = evidence_book
    period_id = str(stored_period_id)
    run_id = str(stored_run_id)
    examiner = _evidence_reader(db_session)
    package = db_client.get(
        f"{BASE}/examiner/documentation-package",
        headers=examiner,
        params={"reporting_period_id": period_id},
    )
    assert package.status_code == 200, package.text
    body = package.json()
    assert body["bank_id"] == SAMPLE_BANK_ID
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
    cfp = db_client.get(f"{BASE}/liquidity/cfp", headers=examiner)
    assert cfp.status_code == 200, cfp.text
    approved = cfp.json()["approved"]
    assert approved is not None
    assert body["cfp_approved_version"] == approved["version"] == 1
    assert body["cfp_active"] == approved["active"] is True

    # Every package for the period's reporting date is bundled, with its digest.
    listed = db_client.get(
        f"{BASE}/regulatory-packages",
        headers=examiner,
        params={"reporting_date": body["as_of_date"], "limit": 100},
    )
    assert listed.status_code == 200, listed.text
    stored_packages = listed.json()["packages"]
    assert stored_packages
    bundled = {entry["package_id"]: entry["content_digest"] for entry in body["packages"]}
    assert set(bundled) == {item["id"] for item in stored_packages}
    assert set(bundled.values()) == {"a" * 64}

    foreign = db_client.get(
        f"{BASE}/examiner/documentation-package",
        headers=headers(ORG_2, roles=("examiner",)),
        params={"reporting_period_id": period_id},
    )
    assert foreign.status_code == 404
