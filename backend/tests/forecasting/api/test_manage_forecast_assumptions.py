"""The governed forecast assumption register, end to end through the API.

A maker drafts and submits a complete set, a DIFFERENT checker approves it, and
the next forecast resolves it and records which version it used, while the run
saved before the approval keeps its inputs, hash and results. Approval is
four-eyes by construction, saved runs stay immutable through effective-dated
corrections, and a bank with no approved version stays not computable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

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
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.forecasting import service
from app.forecasting.schemas import ForecastAssumptionDecision, ForecastAssumptionVersionUpdate
from app.identity.service import authorization, membership
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    BankReportingPeriod,
    CurrentFinancialFact,
    ForecastAssumptionVersion,
    Job,
    RegulatoryRun,
    User,
)
from tests.fixtures.canonical_bank_fixture import (
    FORECAST_APPROVER_ID,
    FORECAST_APPROVER_NAME,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.fixtures.forecast_assumptions import FORECAST_PRESETS
from tests.support.helpers import ORG_1, USER_1, headers

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/forecast"
VERSIONS = f"{BASE}/assumption-versions"
CHECKER_ID = UUID("cccccccc-2222-4ccc-8ccc-cccccccccc22")


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()


def _seed_book() -> tuple[UUID, date]:
    with get_sessionmaker()() as session:
        materialize_canonical_test_book(session)
        period = session.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        ).first()
        assert period is not None
        session.commit()
        return period.id, period.period_end


def _ensure_checker() -> None:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        if session.get(User, CHECKER_ID) is None:
            checker = User(
                id=CHECKER_ID,
                organization_id=ORG_1,
                email="checker@aequoros.example",
                display_name="Checker Two",
                role="analyst",
            )
            session.add(checker)
            session.flush()
            membership.ensure_baseline_membership(
                session, user=checker, granted_by_id="forecast-assumption-test", commit=False
            )
        session.commit()


def _grant(user_id: UUID, bundle: RoleBundle) -> dict[str, str]:
    """One Forecasting binding over every sensitivity on the Sample Bank; the caller's headers."""
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=SAMPLE_BANK_ID,
                module_scope=ModuleScope.FORECASTING,
                sensitivity_scope=SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "forecast-assumption-test"),
            reason="Forecast assumption governance regression",
        )
        user = session.get(User, user_id)
        assert user is not None
        return headers(
            ORG_1,
            user_id=user_id,
            roles=("analyst",),
            authorization_version=user.authorization_version,
        )


def _maker_and_checker() -> tuple[dict[str, str], dict[str, str]]:
    _ensure_checker()
    _grant(USER_1, RoleBundle.ANALYST)
    _grant(CHECKER_ID, RoleBundle.APPROVER)
    # The maker's headers carry the version after BOTH grants on their account.
    return _headers_for(USER_1), _headers_for(CHECKER_ID)


def _headers_for(user_id: UUID) -> dict[str, str]:
    with get_sessionmaker()() as session:
        user = session.get(User, user_id)
        assert user is not None
        return headers(
            ORG_1,
            user_id=user_id,
            roles=("analyst",),
            authorization_version=user.authorization_version,
        )


def _revised(**base: str) -> dict[str, dict[str, str]]:
    presets = {code: dict(values) for code, values in FORECAST_PRESETS.items()}
    presets["base"].update(base)
    return presets


def _draft(
    client: TestClient,
    maker: dict[str, str],
    effective_from: date,
    presets: dict[str, dict[str, str]],
) -> dict[str, Any]:
    response = client.post(
        VERSIONS,
        headers=maker,
        json={
            "effective_from": effective_from.isoformat(),
            "presets": presets,
            "change_note": "Board plan revision: wider margin, faster loan growth.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approved(
    client: TestClient,
    maker: dict[str, str],
    checker: dict[str, str],
    effective_from: date,
    presets: dict[str, dict[str, str]],
) -> dict[str, Any]:
    version = _draft(client, maker, effective_from, presets)
    submitted = client.post(f"{VERSIONS}/{version['id']}/submit", headers=maker)
    assert submitted.status_code == 200, submitted.text
    approved = client.post(
        f"{VERSIONS}/{version['id']}/approve", headers=checker, json={"note": "Board minute 14."}
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def _run(client: TestClient, caller: dict[str, str], period_id: UUID) -> dict[str, Any]:
    response = client.post(
        f"{BASE}/runs",
        headers=caller,
        json={"reporting_period_id": str(period_id), "scenario_code": "base"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_maker_drafts_checker_approves_and_the_next_run_resolves_the_new_version(
    db_client: TestClient,
) -> None:
    period_id, period_end = _seed_book()
    maker, checker = _maker_and_checker()
    _grant(USER_1, RoleBundle.ANALYST)  # run authority rides the same binding family
    maker = _headers_for(USER_1)

    before = _run(db_client, maker, period_id)
    provenance = dict(before["assumption_version"])
    # SQLite keeps no offset; PostgreSQL renders the same instant in its session timezone.
    approved_at = datetime.fromisoformat(provenance.pop("approved_at"))
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=UTC)
    assert approved_at == datetime(2025, 1, 1, tzinfo=UTC)
    assert provenance == {
        "version_id": provenance["version_id"],
        "version_number": 1,
        "effective_from": "2000-01-01",
        "approved_by": str(FORECAST_APPROVER_ID),
        "approved_by_name": FORECAST_APPROVER_NAME,
    }
    assert before["assumptions"]["nim_pct"] == "4.8"

    version = _draft(db_client, maker, period_end, _revised(nim_pct="5.5", loan_growth_pct="22"))
    assert (version["version_number"], version["status"]) == (2, "draft")
    assert version["created_by"] == str(USER_1)

    # The maker can neither approve their own submission nor reach the decision at all
    # without the checker's authority.
    submitted = db_client.post(f"{VERSIONS}/{version['id']}/submit", headers=maker)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["submitted_by"] == str(USER_1)
    assert (
        db_client.post(f"{VERSIONS}/{version['id']}/approve", headers=maker, json={}).status_code
        == 403
    )

    # Still the old version until the checker decides.
    assert _run(db_client, maker, period_id)["assumption_version"]["version_number"] == 1

    approved = db_client.post(
        f"{VERSIONS}/{version['id']}/approve", headers=checker, json={"note": "Board minute 14."}
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert (body["status"], body["reviewed_by"], body["reviewed_by_name"], body["review_note"]) == (
        "approved",
        str(CHECKER_ID),
        "Checker Two",
        "Board minute 14.",
    )

    after = _run(db_client, maker, period_id)
    assert after["assumption_version"]["version_id"] == version["id"]
    assert after["assumption_version"]["version_number"] == 2
    assert after["assumption_version"]["approved_by"] == str(CHECKER_ID)
    assert after["assumption_version"]["approved_by_name"] == "Checker Two"
    assert after["assumptions"]["nim_pct"] == "5.5"
    assert after["assumptions"]["loan_growth_pct"] == "22"
    assert after["input_hash"] != before["input_hash"]
    assert after["summary"]["cumulative_net_income"] != before["summary"]["cumulative_net_income"]

    # The run saved before the approval is exactly what was persisted.
    reread = db_client.get(f"{BASE}/runs/{before['id']}", headers=maker)
    assert reread.status_code == 200, reread.text
    assert reread.json() == before

    scenarios = db_client.get(f"{BASE}/scenarios", headers=maker).json()
    assert scenarios["assumption_version"]["version_number"] == 2
    base = next(s for s in scenarios["scenarios"] if s["code"] == "base")
    assert base["assumptions"]["nim_pct"] == "5.5"

    register = db_client.get(VERSIONS, headers=checker).json()
    assert register["effective_version_id"] == version["id"]
    assert register["open_version_id"] is None
    assert [v["version_number"] for v in register["versions"]] == [2, 1]

    with get_sessionmaker()() as session:
        events = session.scalars(
            select(AuditEvent.event_type)
            .where(AuditEvent.entity_id == version["id"])
            .order_by(AuditEvent.created_at)
        ).all()
    assert events == [
        "forecast_assumptions.drafted",
        "forecast_assumptions.submitted",
        "forecast_assumptions.approved",
    ]


def test_a_future_approved_version_allows_current_corrections_without_changing_saved_runs(
    db_client: TestClient,
) -> None:
    period_id, period_end = _seed_book()
    maker, checker = _maker_and_checker()

    before = _run(db_client, maker, period_id)
    future_date = period_end + timedelta(days=1)

    later = _approved(db_client, maker, checker, future_date, _revised(nim_pct="6"))
    future_saved = db_client.get(f"{VERSIONS}/{later['id']}", headers=maker).json()

    run = _run(db_client, maker, period_id)
    assert run["assumption_version"]["version_number"] == 1
    assert run["assumptions"]["nim_pct"] == "4.8"
    register = db_client.get(VERSIONS, headers=maker).json()
    assert register["as_of"] == period_end.isoformat()
    assert register["effective_version_id"] != later["id"]

    correction = _draft(db_client, maker, period_end, _revised(nim_pct="5.2"))
    correction_url = f"{VERSIONS}/{correction['id']}"
    corrected_date = period_end - timedelta(days=1)
    revised = db_client.patch(
        correction_url,
        headers=maker,
        json={"effective_from": corrected_date.isoformat(), "presets": _revised(nim_pct="5.5")},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["effective_from"] == corrected_date.isoformat()
    submitted = db_client.post(f"{correction_url}/submit", headers=maker)
    assert submitted.status_code == 200, submitted.text
    approved = db_client.post(f"{correction_url}/approve", headers=checker, json={})
    assert approved.status_code == 200, approved.text

    after = _run(db_client, maker, period_id)
    assert after["status"] == "succeeded"
    assert after["assumption_version"]["version_id"] == correction["id"]
    assert after["assumption_version"]["effective_from"] == corrected_date.isoformat()
    assert after["assumptions"]["nim_pct"] == "5.5"
    assert after["input_hash"] != run["input_hash"]
    assert after["summary"]["cumulative_net_income"] != run["summary"]["cumulative_net_income"]
    register = db_client.get(VERSIONS, headers=maker).json()
    assert register["effective_version_id"] == correction["id"]
    for saved in (before, run):
        reread = db_client.get(f"{BASE}/runs/{saved['id']}", headers=maker)
        assert reread.status_code == 200, reread.text
        assert reread.json() == saved
    assert db_client.get(f"{VERSIONS}/{later['id']}", headers=maker).json() == future_saved
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        future = service.resolve_effective(
            session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=future_date
        )
        assert future.provenance is not None
        assert future.provenance["version_id"] == later["id"]
        assert future.presets["base"]["nim_pct"] == Decimal("6")


def test_the_checker_must_not_have_drafted_or_submitted_the_version(
    db_client: TestClient,
) -> None:
    _seed_book()
    maker, checker = _maker_and_checker()
    # One person holding both bundles is still refused on their own submission.
    _grant(USER_1, RoleBundle.APPROVER)
    both = _headers_for(USER_1)

    version = _draft(db_client, both, date(2026, 1, 1), _revised(nim_pct="5"))
    assert db_client.post(f"{VERSIONS}/{version['id']}/submit", headers=both).status_code == 200
    for verb in ("approve", "reject"):
        refused = db_client.post(
            f"{VERSIONS}/{version['id']}/{verb}", headers=both, json={"note": "Self-review"}
        )
        assert refused.status_code == 403, refused.text

    rejected = db_client.post(
        f"{VERSIONS}/{version['id']}/reject", headers=checker, json={"note": "Margin too wide."}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    # Decided versions are final.
    again = db_client.post(f"{VERSIONS}/{version['id']}/approve", headers=checker, json={})
    assert again.status_code == 409, again.text


def test_lifecycle_refusals(db_client: TestClient) -> None:
    _seed_book()
    maker, checker = _maker_and_checker()

    incomplete = _revised()
    del incomplete["adverse"]
    missing = db_client.post(
        VERSIONS,
        headers=maker,
        json={"effective_from": "2026-01-01", "presets": incomplete, "change_note": "x"},
    )
    assert missing.status_code == 422, missing.text

    out_of_range = db_client.post(
        VERSIONS,
        headers=maker,
        json={
            "effective_from": "2026-01-01",
            "presets": _revised(dividend_payout_pct="130"),
            "change_note": "x",
        },
    )
    assert out_of_range.status_code == 422, out_of_range.text
    assert out_of_range.json()["error"]["details"]["problems"] == [
        {
            "scenario_code": "base",
            "assumption_key": "dividend_payout_pct",
            "message": "'base' dividend_payout_pct must be between 0 and 100.",
        }
    ]

    version = _draft(db_client, maker, date(2026, 1, 1), _revised())
    second = db_client.post(
        VERSIONS,
        headers=maker,
        json={"effective_from": "2026-01-01", "presets": _revised(), "change_note": "x"},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"]["error_code"] == "assumption_version_open"

    revised = db_client.patch(
        f"{VERSIONS}/{version['id']}", headers=maker, json={"presets": _revised(nim_pct="5.1")}
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["presets"]["base"]["nim_pct"] == "5.1"

    not_submitted = db_client.post(f"{VERSIONS}/{version['id']}/approve", headers=checker, json={})
    assert not_submitted.status_code == 409, not_submitted.text

    assert db_client.post(f"{VERSIONS}/{version['id']}/submit", headers=maker).status_code == 200
    frozen = db_client.patch(
        f"{VERSIONS}/{version['id']}", headers=maker, json={"change_note": "y"}
    )
    assert frozen.status_code == 409, frozen.text

    no_reason = db_client.post(f"{VERSIONS}/{version['id']}/reject", headers=checker, json={})
    assert no_reason.status_code == 422, no_reason.text


def test_each_verb_requires_its_own_authority(db_client: TestClient) -> None:
    _seed_book()
    _ensure_checker()
    analyst = _grant(USER_1, RoleBundle.ANALYST)
    approver = _grant(CHECKER_ID, RoleBundle.APPROVER)

    # The approver cannot author; the analyst cannot reach the decision.
    drafted = db_client.post(
        VERSIONS,
        headers=approver,
        json={"effective_from": "2026-01-01", "presets": _revised(), "change_note": "x"},
    )
    assert drafted.status_code == 403, drafted.text
    version = _draft(db_client, analyst, date(2026, 1, 1), _revised())
    assert db_client.post(f"{VERSIONS}/{version['id']}/submit", headers=analyst).status_code == 200
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.principal_user_id == CHECKER_ID)
        )
        session.commit()
    _ensure_checker()
    viewer = _grant(CHECKER_ID, RoleBundle.VIEWER)
    assert db_client.get(VERSIONS, headers=viewer).status_code == 200
    assert (
        db_client.post(f"{VERSIONS}/{version['id']}/approve", headers=viewer, json={}).status_code
        == 403
    )


def test_bank_authored_assumptions_never_resolve_until_approved(db_client: TestClient) -> None:
    period_id, _ = _seed_book()
    maker, checker = _maker_and_checker()
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.execute(
            delete(ForecastAssumptionVersion).where(
                ForecastAssumptionVersion.bank_id == SAMPLE_BANK_ID
            )
        )
        session.commit()

    register = db_client.get(VERSIONS, headers=maker).json()
    assert register["versions"] == []
    assert register["open_version_id"] is None
    draft = _draft(db_client, maker, date(2000, 1, 1), _revised())
    draft_id = draft["id"]
    assert draft["created_by"] == str(USER_1)

    refused = _run(db_client, maker, period_id)
    assert refused["status"] == "failed"
    assert refused["error"]["code"] == "missing_parameter"
    assert refused["assumption_version"] is None
    scenarios = db_client.get(f"{BASE}/scenarios", headers=maker).json()
    assert scenarios["scenarios"] == []
    assert scenarios["assumption_version"] is None

    assert db_client.post(f"{VERSIONS}/{draft_id}/submit", headers=maker).status_code == 200
    approved = db_client.post(f"{VERSIONS}/{draft_id}/approve", headers=checker, json={})
    assert approved.status_code == 200, approved.text

    run = _run(db_client, maker, period_id)
    assert run["status"] == "succeeded"
    assert run["assumption_version"]["version_id"] == str(draft_id)
    with get_sessionmaker()() as session:
        stored = session.get(RegulatoryRun, UUID(run["id"]))
        assert stored is not None
        assert "version_id" not in str(stored.inputs)
    # The generic run registry carries the same record, beside the snapshot.
    registry = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-runs/{run['id']}", headers=maker
    )
    assert registry.status_code == 200, registry.text
    assert registry.json()["assumption_provenance"] == run["assumption_version"]


@pytest.mark.parametrize("verb", ["approve", "reject"])
@pytest.mark.parametrize(
    "revision",
    [
        {"presets": _revised(nim_pct="6")},
        {"effective_from": "2026-02-01"},
        {"change_note": "Checker's revised board plan"},
    ],
)
def test_revision_actors_cannot_decide_even_after_the_author_restores_the_values(
    db_client: TestClient, verb: str, revision: dict[str, Any]
) -> None:
    _seed_book()
    maker, _ = _maker_and_checker()
    reviser = _grant(CHECKER_ID, RoleBundle.ANALYST)
    version = _draft(db_client, maker, date(2026, 1, 1), _revised())
    target = f"{VERSIONS}/{version['id']}"
    assert db_client.patch(target, headers=reviser, json=revision).status_code == 200
    restored = db_client.patch(
        target,
        headers=maker,
        json={
            "presets": _revised(),
            "effective_from": "2026-01-01",
            "change_note": "Author restored the original plan",
        },
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["created_by"] == str(USER_1)
    assert db_client.post(f"{target}/submit", headers=maker).status_code == 200
    refused = db_client.post(f"{target}/{verb}", headers=reviser, json={"note": "Self-review"})
    assert refused.status_code == 403, refused.text
    current = db_client.get(target, headers=maker).json()
    assert current["status"] == "submitted"
    assert current["reviewed_by"] is None


def _mutate_version(session: Session, ctx: TenantContext, version_id: UUID, verb: str) -> None:
    if verb == "revise":
        service.update_version(
            session,
            ctx,
            SAMPLE_BANK_ID,
            version_id,
            ForecastAssumptionVersionUpdate.model_validate(
                {"presets": _revised(nim_pct="6"), "change_note": "Concurrent revision"}
            ),
        )
    elif verb == "submit":
        service.submit_version(session, ctx, SAMPLE_BANK_ID, version_id)
    elif verb == "approve":
        service.approve_version(
            session, ctx, SAMPLE_BANK_ID, version_id, ForecastAssumptionDecision()
        )
    else:
        service.reject_version(
            session,
            ctx,
            SAMPLE_BANK_ID,
            version_id,
            ForecastAssumptionDecision(note="Concurrent rejection"),
        )


@pytest.mark.committing_db
@pytest.mark.parametrize("verb", ["revise", "submit", "approve", "reject"])
def test_mutations_wait_for_the_version_lock_and_refuse_a_concurrent_final_decision(
    db_client: TestClient, verb: str
) -> None:
    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks are required for concurrency coverage.")
    _seed_book()
    maker, _ = _maker_and_checker()
    version = _draft(db_client, maker, date(2026, 1, 1), _revised())
    version_id = UUID(version["id"])
    if verb in {"approve", "reject"}:
        assert db_client.post(f"{VERSIONS}/{version_id}/submit", headers=maker).status_code == 200
    loaded_by_racer = Event()

    def mutate() -> int:
        with sessionmaker() as session:
            session.info["organization_id"] = ORG_1
            loaded = session.get(ForecastAssumptionVersion, version_id)
            assert loaded is not None and loaded.status == (
                "submitted" if verb in {"approve", "reject"} else "draft"
            )
            loaded_by_racer.set()
            actor = USER_1 if verb in {"revise", "submit"} else CHECKER_ID
            user = session.get(User, actor)
            assert user is not None
            ctx = TenantContext(
                organization_id=ORG_1,
                actor_user_id=actor,
                authorization_version=user.authorization_version,
            )
            try:
                _mutate_version(session, ctx, version_id, verb)
            except HTTPException as exc:
                session.rollback()
                return exc.status_code
            return 200

    with sessionmaker() as winner:
        winner.info["organization_id"] = ORG_1
        row = winner.scalar(
            select(ForecastAssumptionVersion)
            .where(ForecastAssumptionVersion.id == version_id)
            .with_for_update()
        )
        assert row is not None
        row.status = "approved"
        row.submitted_by = USER_1
        row.reviewed_by = CHECKER_ID
        row.reviewed_at = utc_now()
        row.review_note = "Winning decision"
        winner.flush()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mutate)
            try:
                assert loaded_by_racer.wait(timeout=5)
                with pytest.raises(FutureTimeoutError):
                    future.result(timeout=0.2)
            finally:
                winner.commit()
            assert future.result(timeout=5) == 409
    current = db_client.get(f"{VERSIONS}/{version_id}", headers=maker).json()
    assert current["status"] == "approved"
    assert current["presets"] == version["presets"]
    assert current["change_note"] == version["change_note"]
    assert current["review_note"] == "Winning decision"


@pytest.mark.parametrize("live_input", [False, True])
def test_approval_enqueues_live_and_bi_refreshes_only_when_the_bank_has_live_inputs(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch, live_input: bool
) -> None:
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    _, live_date = _seed_book()
    maker, checker = _maker_and_checker()
    if live_input:
        with get_sessionmaker()() as session:
            session.info["organization_id"] = ORG_1
            bank = session.get(Bank, SAMPLE_BANK_ID)
            assert bank is not None
            session.add(
                CurrentFinancialFact(
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    source_as_of_date=live_date,
                    source_generation=1,
                    fact_group="balance_sheet",
                    category="cash_vault",
                    amount=Decimal("1"),
                    currency=bank.currency,
                )
            )
            session.commit()
    version = _draft(db_client, maker, date(2025, 1, 1), _revised(nim_pct="6"))
    assert db_client.post(f"{VERSIONS}/{version['id']}/submit", headers=maker).status_code == 200
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        assert session.scalars(select(Job).where(Job.bank_id == SAMPLE_BANK_ID)).all() == []
    approved = db_client.post(f"{VERSIONS}/{version['id']}/approve", headers=checker, json={})
    assert approved.status_code == 200, approved.text
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        jobs = session.scalars(select(Job).where(Job.bank_id == SAMPLE_BANK_ID)).all()
        assert {job.job_type for job in jobs} == (
            {"pipeline_refresh", "bi_mart_refresh"} if live_input else set()
        )
        for job in jobs:
            assert job.status == "queued"
            assert job.payload["as_of_date"] == live_date.isoformat()
            assert (
                job.payload["reason"]
                == f"forecast assumptions approved:v{version['version_number']}"
            )


@pytest.mark.parametrize("later_approval", [False, True])
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("runs", {"scenario_code": "base"}),
        ("runs", {"scenario_code": "base", "assumptions": dict(FORECAST_PRESETS["base"])}),
        ("runs", {"scenario_code": "adverse", "assumptions": dict(FORECAST_PRESETS["base"])}),
        (
            "runs",
            {"scenario_code": "severely_adverse", "assumptions": dict(FORECAST_PRESETS["base"])},
        ),
        ("runs", {"scenario_code": "custom", "assumptions": dict(FORECAST_PRESETS["base"])}),
        ("runs", {"scenario_code": "custom", "assumptions": {"nim_pct": "6"}}),
        ("whatif", {"shock_code": "default_spike"}),
        ("optimizer", {}),
    ],
)
def test_runs_cannot_replace_missing_approved_presets_with_overrides(
    db_client: TestClient, endpoint: str, payload: dict[str, Any], later_approval: bool
) -> None:
    latest_id, latest_date = _seed_book()
    maker, _ = _maker_and_checker()
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        oldest = session.scalars(
            select(BankReportingPeriod)
            .where(BankReportingPeriod.bank_id == SAMPLE_BANK_ID)
            .order_by(BankReportingPeriod.period_end)
            .limit(1)
        ).one()
        assert oldest.period_end < latest_date
        period_id = str(oldest.id)
        approved = session.scalars(
            select(ForecastAssumptionVersion).where(
                ForecastAssumptionVersion.bank_id == SAMPLE_BANK_ID
            )
        ).one()
        if later_approval:
            approved.effective_from = latest_date
        else:
            session.delete(approved)
        session.commit()
    catalogue = db_client.get(f"{BASE}/scenarios", headers=maker).json()
    assert bool(catalogue["scenarios"]) == later_approval
    response = db_client.post(
        f"{BASE}/{endpoint}", headers=maker, json={"reporting_period_id": period_id, **payload}
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "failed"
    assert run["error"]["code"] == "missing_parameter"
    assert run["assumption_version"] is None
    if later_approval and endpoint == "runs":
        current = db_client.post(
            f"{BASE}/runs", headers=maker, json={"reporting_period_id": str(latest_id), **payload}
        )
        assert current.status_code == 201, current.text
        assert current.json()["status"] == "succeeded"
        assert current.json()["assumption_version"] == catalogue["assumption_version"]
