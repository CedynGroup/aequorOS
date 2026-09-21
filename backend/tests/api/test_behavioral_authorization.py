"""Scoped-binding enforcement for Behavioral estimates, liquidity effects, and training."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
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
from app.core.config import get_settings
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.ml.behavioral.config import MODEL_VERSIONS, Accuracy, ModelResult
from app.models import AuditEvent, AuthorizationBinding, Bank, IngestionBatch, User
from app.services import authorization, behavioral_models
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers, integration_key_headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/behavioral"
SIBLING_BANK_ID = "BK-BEH00002"
MODEL = "nmd-duration"
VIEW_DENIAL = "Behavioral access requires an active scoped binding."
TRAIN_DENIAL = "Training behavioral models requires an active scoped binding."


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


@pytest.fixture(autouse=True)
def _isolated_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with an empty cache and an empty artifact store."""

    monkeypatch.setenv("BEHAVIORAL_ARTIFACTS_DIR", str(tmp_path / "behavioral"))
    get_settings.cache_clear()
    behavioral_models.reset_cache()
    yield
    behavioral_models.reset_cache()


@pytest.fixture
def training_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace the estimator with a recorder so a run is observable, not costly."""

    calls: list[tuple[str, str]] = []

    def fake_compute(_db: object, _ctx: object, bank_id: str, model: str) -> ModelResult:
        calls.append((bank_id, model))
        return ModelResult(
            model_id=MODEL_VERSIONS[model],
            model_version=MODEL_VERSIONS[model],
            method="baseline",
            as_of_date=None,
            accuracy=Accuracy(
                cv_rmse=None, cv_mae=None, sample_count=0, month_coverage=0, method="baseline"
            ),
            products=[],
        )

    monkeypatch.setattr(behavioral_models, "_compute", fake_compute)
    return calls


def _seed_book() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.commit()
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
                name="Behavioral sibling bank",
                short_name="BEH sibling",
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
    module: ModuleScope = ModuleScope.BEHAVIORAL,
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED,
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
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "behavioral-test"),
            reason="Behavioral authorization regression",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version
    finally:
        session.close()


def _mutate_binding(binding_id: UUID, changes: Mapping[str, object]) -> None:
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


def _model(client: TestClient, version: int = 1, model: str = MODEL):
    return client.get(f"{BASE}/{model}", headers=_auth(version))


def _liquidity(client: TestClient, version: int = 1):
    return client.get(f"{BASE}/liquidity", headers=_auth(version))


def _train(client: TestClient, version: int = 1, *roles: str):
    return client.post(f"{BASE}/{MODEL}/train", headers=_auth(version, *roles))


def _side_effect_counts() -> tuple[int, int]:
    with get_sessionmaker()() as session:
        audit = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        batches = session.scalar(select(func.count()).select_from(IngestionBatch)) or 0
    return audit, batches


def _artifact_files() -> list[Path]:
    root = Path(get_settings().behavioral.artifacts_dir)
    return sorted(root.rglob("estimates.json")) if root.exists() else []


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


def _clear_bindings() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def test_t1_exact_and_explicit_organization_bindings_allow_aggregated_view(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _, exact_version = _grant()
    assert _model(db_client, exact_version).status_code == 200
    assert _liquidity(db_client, exact_version).status_code == 200
    # The first read trains lazily and persists the artifact; that is the read
    # path's cache fill, not a governed run, and it happens only after allow.
    assert training_calls == [(SAMPLE_BANK_ID, MODEL)]

    _clear_bindings()
    behavioral_models.reset_cache()
    _, organization_version = _grant(
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
    )
    assert _model(db_client, organization_version).status_code == 200
    assert _liquidity(db_client, organization_version).status_code == 200


def test_t1_analyst_confidential_binding_allows_training(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = _train(db_client, version, "viewer")

    assert response.status_code == 200, response.text
    assert response.json()["modelVersion"] == MODEL_VERSIONS[MODEL]
    assert training_calls == [(SAMPLE_BANK_ID, MODEL)]
    assert len(_artifact_files()) == 1


def test_t2_scalar_roles_cannot_read_or_train_without_a_binding(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    for response in (_model(db_client), _liquidity(db_client)):
        assert response.status_code == 403
        assert response.json()["error"]["message"] == VIEW_DENIAL
    train = _train(db_client, 1, "admin", "approver", "analyst")
    assert train.status_code == 403
    assert train.json()["error"]["message"] == TRAIN_DENIAL
    assert training_calls == []
    assert _artifact_files() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.FORECASTING.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
)
def test_t3_partial_behavioral_binding_denies(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
    changes: dict[str, str],
) -> None:
    _seed_book()
    if changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    binding_id, version = _grant()
    _mutate_binding(binding_id, changes)

    assert _model(db_client, version).status_code == 403
    assert _liquidity(db_client, version).status_code == 403
    assert training_calls == []


def test_t3_partial_bindings_never_compose_into_behavioral_authority(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    # BEH at the wrong sensitivity plus the right sensitivity on another module.
    _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL, module=ModuleScope.IRRBB)
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.AGGREGATED)

    assert _train(db_client, version).status_code == 403
    assert training_calls == []

    # Aggregated view on this bank plus confidential run on a sibling bank.
    _clear_bindings()
    _add_sibling_bank()
    _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
        institution_id=SIBLING_BANK_ID,
    )
    _, version = _grant()
    assert _train(db_client, version).status_code == 403
    assert training_calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "behavioral-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
)
def test_t4_inactive_behavioral_binding_denies(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
    changes: dict[str, object],
) -> None:
    _seed_book()
    view_id, _ = _grant()
    run_id, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)
    _mutate_binding(view_id, changes)
    _mutate_binding(run_id, changes)

    assert _model(db_client, version).status_code == 403
    assert _train(db_client, version).status_code == 403
    assert training_calls == []


def test_t5_cross_tenant_behavioral_probe_stays_hidden(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    probe = headers(ORG_2)
    assert db_client.get(f"{BASE}/{MODEL}", headers=probe).status_code == 404
    assert db_client.get(f"{BASE}/liquidity", headers=probe).status_code == 404
    assert db_client.post(f"{BASE}/{MODEL}/train", headers=probe).status_code == 404
    assert training_calls == []


def test_t5_wrong_bank_binding_denies_despite_admin_claims(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _add_sibling_bank()
    _grant(institution_id=SIBLING_BANK_ID)
    _, version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
        institution_id=SIBLING_BANK_ID,
    )

    assert _model(db_client, version).status_code == 403
    assert _liquidity(db_client, version).status_code == 403
    assert _train(db_client, version, "admin").status_code == 403
    assert training_calls == []


def test_t6_stale_authorization_version_denies_before_behavioral_evaluation(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _grant()
    _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    assert _model(db_client, 1).status_code == 401
    assert _liquidity(db_client, 1).status_code == 401
    assert _train(db_client, 1).status_code == 401
    assert training_calls == []


def test_t7_evaluator_failure_denies_closed_with_telemetry(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_prefetched_permission", fail_evaluation)
    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        read = _model(db_client, version)
        train = _train(db_client, version)
    finally:
        logger.remove(sink_id)

    assert read.status_code == 403
    assert train.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 2
    assert {decision["reason"] for decision in decisions} == {"binding_evaluation_failed"}
    assert {decision["severity"] for decision in decisions} == {"error"}
    assert training_calls == []


def test_t8_denied_training_leaves_no_run_artifact_cache_or_audit_trace(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _, version = _grant()  # aggregated view only
    before = _side_effect_counts()

    response = _train(db_client, version, "admin")

    assert response.status_code == 403
    assert response.json()["error"]["message"] == TRAIN_DENIAL
    assert training_calls == []
    assert _artifact_files() == []
    assert _side_effect_counts() == before


def test_t8_denied_read_never_trains_lazily(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    response = _model(db_client, version)

    assert response.status_code == 403
    assert training_calls == []
    assert _artifact_files() == []


def test_confidential_run_binding_does_not_grant_aggregated_view(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    assert _model(db_client, version).status_code == 403
    assert _liquidity(db_client, version).status_code == 403


def test_viewer_bundle_at_confidential_cannot_train(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _, version = _grant(RoleBundle.VIEWER, sensitivity=SensitivityScope.CONFIDENTIAL)

    assert _train(db_client, version).status_code == 403
    assert training_calls == []


def test_service_train_path_requires_run_even_for_direct_callers(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    """The artifact-writing service re-decides, so a bypassed route still denies."""

    from fastapi import HTTPException  # noqa: PLC0415

    from app.api.deps import TenantContext  # noqa: PLC0415

    _seed_book()
    _, version = _grant()
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version)
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        with pytest.raises(HTTPException) as denied:
            behavioral_models.get_estimates(session, ctx, SAMPLE_BANK_ID, MODEL, refresh=True)
    assert denied.value.status_code == 403
    assert training_calls == []


def test_unknown_model_names_stay_closed_schema_for_everyone(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    unbound = db_client.get(f"{BASE}/not-a-model", headers=_auth())
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.ALL)
    bound = db_client.get(f"{BASE}/not-a-model", headers=_auth(version))
    bound_train = db_client.post(f"{BASE}/not-a-model/train", headers=_auth(version))
    cross_tenant = db_client.get(f"{BASE}/not-a-model", headers=headers(ORG_2))

    assert unbound.status_code == 422
    assert bound.status_code == 422
    assert bound_train.status_code == 422
    assert cross_tenant.status_code == 404
    assert training_calls == []


def test_t10_machine_principal_never_satisfies_interactive_behavioral_routes(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    """A bank-scoped integration key is refused at the credential boundary."""

    _seed_book()
    machine = integration_key_headers(SAMPLE_BANK_ID)

    assert db_client.get(f"{BASE}/{MODEL}", headers=machine).status_code == 401
    assert db_client.get(f"{BASE}/liquidity", headers=machine).status_code == 401
    assert db_client.post(f"{BASE}/{MODEL}/train", headers=machine).status_code == 401
    assert training_calls == []


def test_training_result_is_the_route_payload_and_refreshes_the_cache(
    db_client: TestClient,
    training_calls: list[tuple[str, str]],
) -> None:
    _seed_book()
    _grant()
    _, version = _grant(RoleBundle.ANALYST, sensitivity=SensitivityScope.CONFIDENTIAL)

    first = _model(db_client, version)
    trained = _train(db_client, version)
    second = _model(db_client, version)

    assert first.status_code == 200
    assert trained.status_code == 200
    assert second.status_code == 200
    # One lazy fit on first read, one governed retrain, and the retrain result
    # served from cache afterwards.
    assert training_calls == [(SAMPLE_BANK_ID, MODEL), (SAMPLE_BANK_ID, MODEL)]
    assert (
        dataclasses.asdict(
            behavioral_models._cache[(ORG_1, SAMPLE_BANK_ID, MODEL)]  # noqa: SLF001
        )["model_version"]
        == trained.json()["modelVersion"]
    )
