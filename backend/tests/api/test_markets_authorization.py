"""Scoped-binding enforcement for Markets views, ratings, overlays, and uploads.

Authority is split by what the data is: vendor and desk market data is
``published`` (views, source planes, templates, manual uploads), material
derived from the bank's own book is ``confidential`` (implied ratings, private
curve overlays), and credential-bearing connection metadata is ``restricted``.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from loguru import logger
from sqlalchemy import delete, func, select

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    BankReportingPeriod,
    CanonicalYieldCurve,
    CanonicalYieldCurvePoint,
    ImpliedRatingRun,
    IngestionBatch,
    LineageRecord,
    MarketDataOverlay,
    User,
)
from app.services import authorization, implied_rating, market_data_overlays
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.adapters.market_data.manual_upload.fixtures import (
    FIXTURE_AS_OF,
    build_full_coverage_workbook,
)
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.storage.inmemory import InMemoryStorageClient

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"
SIBLING_BANK_ID = "BK-MKT00002"
CURVE = "GHS_SOVEREIGN"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Every read route with the sensitivity its exact grant must name.
READ_ROUTES: tuple[tuple[str, str, SensitivityScope], ...] = (
    ("views", f"{BASE}/market-data/views", SensitivityScope.PUBLISHED),
    ("source_preferences", f"{BASE}/market-data/source-preferences", SensitivityScope.PUBLISHED),
    ("planes", f"{BASE}/market-data/planes?category=curves", SensitivityScope.PUBLISHED),
    (
        "forward_grid",
        f"{BASE}/market-data/curves/{CURVE}/forward-grid",
        SensitivityScope.PUBLISHED,
    ),
    ("scopes", f"{BASE}/market-data/scopes", SensitivityScope.PUBLISHED),
    ("quota", f"{BASE}/market-data/quota", SensitivityScope.PUBLISHED),
    (
        "template",
        f"/api/v1/market-data/templates/yield_curve?bank_id={SAMPLE_BANK_ID}",
        SensitivityScope.PUBLISHED,
    ),
    ("rating_runs", f"{BASE}/implied-rating/runs", SensitivityScope.CONFIDENTIAL),
    ("overlays", f"{BASE}/market-data/overlays", SensitivityScope.CONFIDENTIAL),
    ("connections", f"{BASE}/market-data/connections", SensitivityScope.RESTRICTED),
)
READ_ROUTE_IDS = [route[0] for route in READ_ROUTES]


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture
def upload_storage(
    storage_engine: InMemoryStorageClient, monkeypatch: pytest.MonkeyPatch
) -> InMemoryStorageClient:
    """Point the pull runner, cache, and adapter at the client the app serves."""
    for target in (
        "app.adapters.market_data.pull_runner.get_storage_client",
        "app.adapters.market_data.cache.get_storage_client",
        "app.adapters.market_data.manual_upload.adapter.get_storage_client",
    ):
        monkeypatch.setattr(target, lambda: storage_engine)
    return storage_engine


def _seed_book() -> UUID:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert period_id is not None
        session.commit()
        return period_id
    finally:
        session.close()


def _seed_curve(bank_id: str = SAMPLE_BANK_ID) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        batch = IngestionBatch(
            organization_id=ORG_1,
            bank_id=bank_id,
            source_system="BLOOMBERG",
            adapter_version="1.0",
            extraction_mode="full",
            status="accepted",
            as_of_date=date(2026, 7, 15),
        )
        session.add(batch)
        session.flush()
        lineage = LineageRecord(
            organization_id=ORG_1,
            ingestion_batch_id=batch.id,
            operation_type="ADAPTER_TRANSLATE",
            operation_ref="markets-authorization-fixture",
            input_lineage_ids=[],
        )
        session.add(lineage)
        session.flush()
        meta: dict[str, Any] = {
            "organization_id": ORG_1,
            "bank_id": bank_id,
            "as_of_date": date(2026, 7, 15),
            "ingested_at": datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
            "source_system": "BLOOMBERG",
            "ingestion_batch_id": batch.id,
            "lineage_id": lineage.id,
            "validation_status": "accepted",
        }
        curve = CanonicalYieldCurve(
            **meta,
            source_reference=f"BLOOMBERG/{CURVE}",
            currency="GHS",
            curve_name=CURVE,
            curve_type="sovereign",
        )
        session.add(curve)
        session.flush()
        for tenor_months, rate in ((3, "0.2400"), (12, "0.2200")):
            session.add(
                CanonicalYieldCurvePoint(
                    **meta,
                    source_reference=f"BLOOMBERG/{CURVE}/{tenor_months}m",
                    yield_curve_id=curve.id,
                    tenor_months=tenor_months,
                    rate=rate,
                )
            )
        session.commit()
    finally:
        session.close()


def _add_sibling_bank() -> UUID:
    """A same-tenant sibling with one reporting period; returns the period id."""
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="Markets sibling bank",
                short_name="Markets sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.flush()
        period = BankReportingPeriod(
            organization_id=ORG_1,
            bank_id=SIBLING_BANK_ID,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31),
            label="2026-03",
            status="closed",
        )
        session.add(period)
        session.commit()
        return period.id
    finally:
        session.close()


def _add_overlay(bank_id: str = SAMPLE_BANK_ID) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        overlay = MarketDataOverlay(
            organization_id=ORG_1,
            bank_id=bank_id,
            base_ref_kind="curve",
            base_curve_name=CURVE,
            tenor_months=None,
            adjustment_type="additive_bps",
            value="25",
            component_tag="liquidity_premium",
            effective_from=date(2026, 1, 1),
            effective_to=None,
            note=None,
            created_by=USER_1,
        )
        session.add(overlay)
        session.commit()
        return overlay.id
    finally:
        session.close()


def _add_rating_run(period_id: UUID, bank_id: str = SAMPLE_BANK_ID) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        run = ImpliedRatingRun(
            organization_id=ORG_1,
            bank_id=bank_id,
            reporting_period_id=period_id,
            methodology_code="AEQ-GH-BANK",
            methodology_version=1,
            status="succeeded",
            engine_version="test-rating-v1",
            input_hash=uuid4().hex * 2,
            input_snapshot={},
            results={},
            completed_at=utc_now(),
            created_by=USER_1,
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def _grant(
    bundle: RoleBundle = RoleBundle.VIEWER,
    *,
    module: ModuleScope = ModuleScope.MARKETS,
    sensitivity: SensitivityScope = SensitivityScope.PUBLISHED,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=institution_scope,
                institution_id=institution_id,
                module_scope=module,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "markets-test"),
            reason="Markets authorization regression",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version
    finally:
        session.close()


def _amend_binding(binding_id: UUID, changes: dict[str, object]) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    finally:
        session.close()


def _auth(version: int = 1, *roles: str) -> dict[str, str]:
    return headers(
        ORG_1,
        user_id=USER_1,
        roles=roles or ("admin",),
        authorization_version=version,
    )


def _count(model: type) -> int:
    with get_sessionmaker()() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _assert_authorized(name: str, response: Response) -> None:
    """The gate passed: the service answered, even when it had no data to serve."""
    if name == "forward_grid":
        # The sample book publishes no desk determination, so the service
        # itself reports the missing grid — proof the request reached it.
        assert response.status_code == 404, response.text
        assert response.json()["error"]["message"] == (
            "No published forward grid for this curve at the requested date."
        )
        return
    assert response.status_code == 200, response.text
    if name == "template":
        assert response.headers["content-type"].startswith(XLSX_MEDIA_TYPE)


def _overlay_payload() -> dict[str, Any]:
    return {
        "base_ref_kind": "curve",
        "base_curve_name": CURVE,
        "adjustment_type": "additive_bps",
        "value": "25",
        "component_tag": "liquidity_premium",
        "effective_from": "2026-01-01",
    }


def _upload(client: TestClient, auth: dict[str, str], bank_id: str = SAMPLE_BANK_ID) -> Response:
    return client.post(
        f"/api/v1/banks/{bank_id}/market-data/uploads",
        headers=auth,
        files={"file": ("full.xlsx", io.BytesIO(build_full_coverage_workbook()), XLSX_MEDIA_TYPE)},
        data={"as_of_date": FIXTURE_AS_OF.isoformat()},
    )


def _capture_binding_records() -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    return records, logger.add(
        lambda message: records.append(dict(message.record)),
        level="DEBUG",
    )


def _binding_extras(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        record["extra"]  # type: ignore[index]
        for record in records
        if record["extra"].get("condition")  # type: ignore[union-attr,index]
        == Condition.AUTHORIZATION_BINDING_DECISION.value
    ]


# ---------------------------------------------------------------------------
# T1 – T7: the binding dimensions, exercised on every read route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "url", "sensitivity"), READ_ROUTES, ids=READ_ROUTE_IDS)
def test_t1_exact_and_explicit_organization_bindings_allow_reads(
    db_client: TestClient,
    name: str,
    url: str,
    sensitivity: SensitivityScope,
) -> None:
    _seed_book()
    _seed_curve()
    _, exact_version = _grant(sensitivity=sensitivity)
    _assert_authorized(name, db_client.get(url, headers=_auth(exact_version)))

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()
    _, organization_version = _grant(
        sensitivity=sensitivity,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
    )
    _assert_authorized(name, db_client.get(url, headers=_auth(organization_version)))


@pytest.mark.parametrize(("name", "url", "sensitivity"), READ_ROUTES, ids=READ_ROUTE_IDS)
def test_t2_scalar_roles_cannot_read_markets_without_a_binding(
    db_client: TestClient,
    name: str,
    url: str,
    sensitivity: SensitivityScope,
) -> None:
    _ = name, sensitivity
    _seed_book()
    for role in ("admin", "analyst", "approver", "viewer"):
        response = db_client.get(url, headers=_auth(1, role))
        assert response.status_code == 403, (role, response.text)
    assert response.json()["error"]["message"].endswith("requires an active scoped binding.")


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.DATA.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
    ids=["wrong_bank", "wrong_module", "wrong_sensitivity", "wrong_bundle"],
)
def test_t3_partial_markets_binding_denies(
    db_client: TestClient,
    changes: dict[str, str],
) -> None:
    _seed_book()
    if changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    binding_id, version = _grant()
    _amend_binding(binding_id, changes)

    response = db_client.get(f"{BASE}/market-data/views", headers=_auth(version))
    assert response.status_code == 403, response.text


def test_t3_partial_bindings_never_compose_into_markets_authority(
    db_client: TestClient,
) -> None:
    """A Data grant on the bank plus a Markets grant on a sibling do not add up."""
    _seed_book()
    _add_sibling_bank()
    _grant(module=ModuleScope.DATA, sensitivity=SensitivityScope.ALL)
    _, version = _grant(institution_id=SIBLING_BANK_ID)
    for url in (
        f"{BASE}/market-data/views",
        f"{BASE}/market-data/overlays",
        f"{BASE}/market-data/connections",
    ):
        assert db_client.get(url, headers=_auth(version)).status_code == 403, url


def test_data_engine_authority_does_not_open_markets(db_client: TestClient) -> None:
    """Ingestion operators see Markets only through a separate Markets grant."""
    _seed_book()
    _, version = _grant(
        RoleBundle.ANALYST,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.ALL,
    )
    for _name, url, _sensitivity in READ_ROUTES:
        assert db_client.get(url, headers=_auth(version)).status_code == 403, url
    assert _upload(db_client, _auth(version)).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "markets-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
    ids=["suspended", "revoked", "not_yet_valid", "expired"],
)
def test_t4_inactive_markets_binding_denies_despite_admin_claim(
    db_client: TestClient,
    changes: dict[str, object],
) -> None:
    _seed_book()
    binding_id, version = _grant()
    _amend_binding(binding_id, changes)

    response = db_client.get(f"{BASE}/market-data/views", headers=_auth(version, "admin"))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(("name", "url", "sensitivity"), READ_ROUTES, ids=READ_ROUTE_IDS)
def test_t5_cross_tenant_markets_probe_stays_hidden(
    db_client: TestClient,
    name: str,
    url: str,
    sensitivity: SensitivityScope,
) -> None:
    _ = name, sensitivity
    _seed_book()
    response = db_client.get(url, headers=headers(ORG_2))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["message"] == "Bank not found."


def test_t6_stale_authorization_version_denies_before_markets_evaluation(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant()
    response = db_client.get(f"{BASE}/market-data/views", headers=_auth(1))
    assert response.status_code == 401


def test_t7_evaluator_failure_denies_closed_with_telemetry(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response = db_client.get(f"{BASE}/market-data/views", headers=_auth(version))
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# Sensitivity tiers are separate rows
# ---------------------------------------------------------------------------


def test_published_view_does_not_open_confidential_or_restricted_reads(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, version = _grant(sensitivity=SensitivityScope.PUBLISHED)
    auth = _auth(version)
    assert db_client.get(f"{BASE}/market-data/views", headers=auth).status_code == 200
    for url in (
        f"{BASE}/implied-rating/runs",
        f"{BASE}/market-data/overlays",
        f"{BASE}/market-data/connections",
    ):
        assert db_client.get(url, headers=auth).status_code == 403, url


def test_confidential_view_does_not_open_published_or_restricted_reads(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    auth = _auth(version)
    assert db_client.get(f"{BASE}/market-data/overlays", headers=auth).status_code == 200
    assert db_client.get(f"{BASE}/market-data/views", headers=auth).status_code == 403
    assert db_client.get(f"{BASE}/market-data/connections", headers=auth).status_code == 403


def test_template_download_names_the_institution(db_client: TestClient) -> None:
    _seed_book()
    _add_sibling_bank()
    _, version = _grant()
    auth = _auth(version)
    template = "/api/v1/market-data/templates/yield_curve"

    granted = db_client.get(template, params={"bank_id": SAMPLE_BANK_ID}, headers=auth)
    assert granted.status_code == 200, granted.text
    sibling = db_client.get(template, params={"bank_id": SIBLING_BANK_ID}, headers=auth)
    assert sibling.status_code == 403, sibling.text
    unknown = db_client.get(template, params={"bank_id": "BK-ZZZZZZZZ"}, headers=auth)
    assert unknown.status_code == 404, unknown.text
    unnamed = db_client.get(template, headers=auth)
    assert unnamed.status_code == 422, unnamed.text


# ---------------------------------------------------------------------------
# T8: mutations require their exact permission and deny before side effects
# ---------------------------------------------------------------------------


def _mutation_calls(
    client: TestClient, auth: dict[str, str], period_id: UUID, overlay_id: UUID
) -> dict[str, Callable[[], Response]]:
    return {
        "rating_run": lambda: client.post(
            f"{BASE}/implied-rating/runs",
            headers=auth,
            json={"reporting_period_id": str(period_id)},
        ),
        "overlay_create": lambda: client.post(
            f"{BASE}/market-data/overlays", headers=auth, json=_overlay_payload()
        ),
        "overlay_end": lambda: client.post(
            f"{BASE}/market-data/overlays/{overlay_id}/end",
            headers=auth,
            json={"effective_to": "2026-12-31"},
        ),
        "upload": lambda: _upload(client, auth),
    }


@pytest.mark.parametrize(
    "grant",
    [
        None,
        (RoleBundle.VIEWER, SensitivityScope.ALL),
        (RoleBundle.APPROVER, SensitivityScope.ALL),
        (RoleBundle.ANALYST, SensitivityScope.AGGREGATED),
    ],
    ids=["unbound", "viewer_all", "approver_all", "analyst_wrong_tier"],
)
def test_t8_denied_mutations_run_no_engine_and_persist_nothing(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    grant: tuple[RoleBundle, SensitivityScope] | None,
) -> None:
    period_id = _seed_book()
    overlay_id = _add_overlay()
    version = 1
    if grant is not None:
        _, version = _grant(grant[0], sensitivity=grant[1])

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Unauthorized Markets mutation reached a side effect")

    monkeypatch.setattr(implied_rating, "run", forbidden)
    monkeypatch.setattr(market_data_overlays, "create_overlay", forbidden)
    monkeypatch.setattr(market_data_overlays, "end_overlay", forbidden)
    monkeypatch.setattr(
        "app.adapters.market_data.manual_upload.service.upload_market_data", forbidden
    )
    models = (ImpliedRatingRun, MarketDataOverlay, IngestionBatch, AuditEvent)
    before = [_count(model) for model in models]

    for name, call in _mutation_calls(
        db_client, _auth(version, "admin"), period_id, overlay_id
    ).items():
        response = call()
        assert response.status_code == 403, (name, response.text)

    assert [_count(model) for model in models] == before
    with get_sessionmaker()() as session:
        overlay = session.get(MarketDataOverlay, overlay_id)
        assert overlay is not None
        assert overlay.effective_to is None


def test_overlay_create_and_end_are_separate_permissions(db_client: TestClient) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    analyst = _auth(version, "viewer")

    created = db_client.post(
        f"{BASE}/market-data/overlays", headers=analyst, json=_overlay_payload()
    )
    assert created.status_code == 201, created.text
    overlay_id = created.json()["id"]
    listed = db_client.get(f"{BASE}/market-data/overlays", headers=analyst)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["overlays"]] == [overlay_id]

    ended = db_client.post(
        f"{BASE}/market-data/overlays/{overlay_id}/end",
        headers=analyst,
        json={"effective_to": "2026-12-31"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["effective_to"] == "2026-12-31"


def test_implied_rating_run_requires_confidential_run(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    period_id = _seed_book()
    run_id = _add_rating_run(period_id)
    calls: list[tuple[str, UUID]] = []

    def stub_run(
        db: object, ctx: object, bank_id: str, reporting_period_id: UUID, **_kwargs: object
    ) -> ImpliedRatingRun:
        _ = db, ctx
        calls.append((bank_id, reporting_period_id))
        with get_sessionmaker()() as session:
            row = session.get(ImpliedRatingRun, run_id)
            assert row is not None
            session.expunge(row)
            return row

    monkeypatch.setattr(implied_rating, "run", stub_run)
    payload = {"reporting_period_id": str(period_id)}

    _, viewer_version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    denied = db_client.post(
        f"{BASE}/implied-rating/runs", headers=_auth(viewer_version, "admin"), json=payload
    )
    assert denied.status_code == 403, denied.text
    assert calls == []

    _, analyst_version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    allowed = db_client.post(
        f"{BASE}/implied-rating/runs", headers=_auth(analyst_version, "viewer"), json=payload
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["id"] == str(run_id)
    assert calls == [(SAMPLE_BANK_ID, period_id)]


@pytest.mark.usefixtures("upload_storage")
def test_manual_upload_requires_published_create_on_the_target_bank(
    db_client: TestClient,
) -> None:
    _seed_book()
    _add_sibling_bank()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.PUBLISHED)
    auth = _auth(version, "viewer")
    batches_before = _count(IngestionBatch)

    sibling = _upload(db_client, auth, SIBLING_BANK_ID)
    assert sibling.status_code == 403, sibling.text
    assert _count(IngestionBatch) == batches_before

    accepted = _upload(db_client, auth)
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["bank_id"] == SAMPLE_BANK_ID
    assert body["quota_consumed"] == 0
    assert body["canonical_records_produced"] > 0
    assert _count(IngestionBatch) == batches_before + 1


# ---------------------------------------------------------------------------
# T10: object references stay bank-scoped and hidden
# ---------------------------------------------------------------------------


def test_t10_unauthorized_object_ids_return_404_like_a_cross_tenant_probe(
    db_client: TestClient,
) -> None:
    _seed_book()
    sibling_period_id = _add_sibling_bank()
    sibling_overlay = _add_overlay(SIBLING_BANK_ID)
    sibling_run = _add_rating_run(sibling_period_id, SIBLING_BANK_ID)
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    auth = _auth(version)

    run_detail = db_client.get(f"{BASE}/implied-rating/runs/{sibling_run}", headers=auth)
    assert run_detail.status_code == 404, run_detail.text
    assert run_detail.json()["error"]["message"] == "Implied rating run not found."
    unknown_run = db_client.get(f"{BASE}/implied-rating/runs/{uuid4()}", headers=auth)
    assert unknown_run.status_code == 404

    ended = db_client.post(
        f"{BASE}/market-data/overlays/{sibling_overlay}/end",
        headers=auth,
        json={"effective_to": "2026-12-31"},
    )
    assert ended.status_code == 404, ended.text
    assert ended.json()["error"]["message"] == "Overlay not found."
    superseding = db_client.post(
        f"{BASE}/market-data/overlays",
        headers=auth,
        json={**_overlay_payload(), "supersedes": str(sibling_overlay)},
    )
    assert superseding.status_code == 404, superseding.text

    listed = db_client.get(f"{BASE}/market-data/overlays", headers=auth)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 0
    runs = db_client.get(f"{BASE}/implied-rating/runs", headers=auth)
    assert runs.status_code == 200, runs.text
    assert runs.json()["runs"] == []

    sibling_probe = db_client.get(
        f"/api/v1/banks/{SIBLING_BANK_ID}/market-data/overlays", headers=auth
    )
    assert sibling_probe.status_code == 403, sibling_probe.text
    foreign_probe = db_client.get(
        f"/api/v1/banks/{SIBLING_BANK_ID}/market-data/overlays", headers=headers(ORG_2)
    )
    assert foreign_probe.status_code == 404, foreign_probe.text
