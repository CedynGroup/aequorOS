"""Governed Behavioral proposal, independent review, and accepted-batch atomicity."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Connection, event, func, select

from app.behavioral import service as behavioral_models
from app.behavioral.ml.config import MODEL_VERSIONS, Accuracy, ModelResult, ProductEstimate
from app.behavioral.models import BehavioralModelProposal
from app.behavioral.schemas import (
    BehavioralApplyRead,
    BehavioralProposalAcknowledgement,
    BehavioralProposalListRead,
)
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
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import (
    AuthorizationBinding,
    Bank,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    User,
)
from tests.fixtures.canonical_bank_fixture import (
    FORECAST_APPROVER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.helpers import ORG_1, USER_1, headers

__all__ = ["_clean_authority_and_artifacts", "_stable_estimates"]

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/behavioral"
SECOND_BANK_ID = "BK-SAMP0002"
MODEL = "nmd-duration"
PROPOSE_BODY = {
    "products": [
        {
            "productCode": "retail_savings",
            "value": 36.5,
            "unit": "months",
            "confidence": 0.8,
        }
    ]
}


class _StoredProvenance(BaseModel):
    proposal_id: UUID
    proposed_by: UUID
    reviewed_by: UUID
    model_version: str


class _StoredAssumption(BaseModel):
    assumption_type: str
    value: float
    provenance: _StoredProvenance | None = None


_STORED_ASSUMPTIONS = TypeAdapter(list[_StoredAssumption])


@pytest.fixture(autouse=True)
def _clean_authority_and_artifacts(
    db_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.query(AuthorizationBinding).filter(
            AuthorizationBinding.organization_id == ORG_1
        ).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    monkeypatch.setenv("BEHAVIORAL_ARTIFACTS_DIR", str(tmp_path / "behavioral"))
    get_settings.cache_clear()
    behavioral_models.reset_cache()
    yield
    behavioral_models.reset_cache()


@pytest.fixture(autouse=True)
def _stable_estimates(monkeypatch: pytest.MonkeyPatch) -> None:
    def compute(_db: object, _ctx: object, _bank_id: str, model: str) -> ModelResult:
        return ModelResult(
            model_id=MODEL_VERSIONS[model],
            model_version=MODEL_VERSIONS[model],
            method="baseline",
            as_of_date="2026-03-31",
            accuracy=Accuracy(
                cv_rmse=None,
                cv_mae=None,
                sample_count=0,
                month_coverage=0,
                method="baseline",
            ),
            products=[
                ProductEstimate(
                    product_code="retail_savings",
                    assumption_type="NMD_DURATION",
                    value=30.75,
                    unit="months",
                    confidence=0.65,
                    method="baseline",
                )
            ],
        )

    monkeypatch.setattr(behavioral_models, "_compute", compute)


def _seed_book() -> None:
    with get_sessionmaker()() as session:
        materialize_canonical_test_book(session)
        session.commit()


def _grant(
    user_id: UUID = USER_1,
    bundle: RoleBundle = RoleBundle.CONFIGURATOR,
    *,
    module: ModuleScope = ModuleScope.BEHAVIORAL,
    sensitivity: SensitivityScope = SensitivityScope.RESTRICTED,
    institution_id: str | None = SAMPLE_BANK_ID,
) -> tuple[UUID, int]:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=institution_id,
                module_scope=module,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "behavioral-application-test"),
            reason="Behavioral governed application regression",
        )
        user = session.get(User, user_id)
        assert user is not None
        return binding.id, user.authorization_version


def _auth(user_id: UUID, version: int, *roles: str) -> dict[str, str]:
    return headers(
        ORG_1,
        user_id=user_id,
        roles=roles or ("viewer",),
        authorization_version=version,
    )


def _propose(client: TestClient, version: int) -> httpx2.Response:
    return client.post(
        f"{BASE}/{MODEL}/apply",
        json=PROPOSE_BODY,
        headers=_auth(USER_1, version, "analyst"),
    )


def _review(
    client: TestClient,
    proposal_id: str | UUID,
    user_id: UUID,
    version: int,
) -> httpx2.Response:
    return client.post(
        f"{BASE}/proposals/{proposal_id}/review",
        headers=_auth(user_id, version, "approver"),
    )


def _ack(response: httpx2.Response) -> BehavioralProposalAcknowledgement:
    assert response.status_code == 200, response.text
    return BehavioralProposalAcknowledgement.model_validate(response.json())


def _proposal_list(response: httpx2.Response) -> BehavioralProposalListRead:
    assert response.status_code == 200, response.text
    return BehavioralProposalListRead.model_validate(response.json())


def _accepted(response: httpx2.Response) -> BehavioralApplyRead:
    assert response.status_code == 200, response.text
    return BehavioralApplyRead.model_validate(response.json())


def _counts() -> tuple[int, int]:
    with get_sessionmaker()() as session:
        proposals = session.scalar(select(func.count()).select_from(BehavioralModelProposal)) or 0
        batches = session.scalar(select(func.count()).select_from(IngestionBatch)) or 0
    return proposals, batches


def _proposal(proposal_id: str | UUID) -> BehavioralModelProposal:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        row = session.get(
            BehavioralModelProposal,
            UUID(proposal_id) if isinstance(proposal_id, str) else proposal_id,
        )
        assert row is not None
        session.expunge(row)
        return row


def _seed_accepted_assumptions(*, as_of_date: date = date(2026, 2, 28)) -> UUID:
    batch_id = new_uuid7()
    lineage_id = new_uuid7()
    payloads = [
        {
            "assumption_type": "NMD_DURATION",
            "product_code": "retail_savings",
            "value": 24.0,
            "unit": "months",
        },
        {
            "assumption_type": "PREPAYMENT_RATE",
            "product_code": "mortgage",
            "value": 0.08,
            "unit": "annual_rate",
        },
        {
            "assumption_type": "DEPOSIT_STABILITY",
            "product_code": "current",
            "value": 0.7,
            "unit": "ratio",
        },
    ]
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.add(
            IngestionBatch(
                id=batch_id,
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                source_system="API_PUSH",
                adapter_version="behavioral_test_fixture",
                extraction_mode="full",
                status="accepted",
                as_of_date=as_of_date,
                created_by=USER_1,
            )
        )
        session.add(
            LineageRecord(
                id=lineage_id,
                organization_id=ORG_1,
                bank_id=SAMPLE_BANK_ID,
                ingestion_batch_id=batch_id,
                operation_type="ENRICHMENT",
                operation_ref="behavioral_test_fixture",
            )
        )
        session.flush()
        for index, payload in enumerate(payloads, start=1):
            session.add(
                CanonicalReferenceRow(
                    id=new_uuid7(),
                    organization_id=ORG_1,
                    bank_id=SAMPLE_BANK_ID,
                    ingestion_batch_id=batch_id,
                    as_of_date=as_of_date,
                    dataset_kind="behavioral_assumptions",
                    row_index=index,
                    payload=payload,
                    source_reference=f"behavioral_test_fixture#{index}",
                    lineage_id=lineage_id,
                )
            )
        session.commit()
    return batch_id


def test_apply_requires_configure_and_creates_only_an_immutable_pending_proposal(
    db_client: TestClient,
) -> None:
    _seed_book()
    before = _counts()
    denied = _propose(db_client, 1)
    assert denied.status_code == 403
    assert _counts() == before

    _, version = _grant(bundle=RoleBundle.ANALYST)
    analyst = _propose(db_client, version)
    assert analyst.status_code == 403
    assert _counts() == before

    _clear_bindings()
    _, version = _grant()
    proposed = _propose(db_client, version)
    acknowledgement = _ack(proposed)
    assert set(acknowledgement.model_dump(by_alias=True)) == {"proposalId", "status"}
    assert acknowledgement.status == "pending"
    assert _counts() == (before[0] + 1, before[1])

    row = _proposal(acknowledgement.proposal_id)
    assert row.status == "pending"
    assert row.model == MODEL
    assert row.model_version == MODEL_VERSIONS[MODEL]
    assert row.as_of_date == date(2026, 3, 31)
    assert row.values == [
        {
            "product_code": "retail_savings",
            "value": 30.75,
            "unit": "months",
            "confidence": 0.65,
        }
    ]
    assert row.proposed_by_user_id == USER_1
    assert row.reviewed_by_user_id is None
    assert row.reviewed_at is None
    assert row.ingestion_batch_id is None

    queue = db_client.get(f"{BASE}/proposals", headers=_auth(USER_1, version))
    assert queue.status_code == 403
    assert proposed.text.find("36.5") == -1
    assert proposed.text.find(MODEL_VERSIONS[MODEL]) == -1


@pytest.mark.parametrize(
    "products",
    [
        [],
        [
            {
                "productCode": "unestimated",
                "value": 999.0,
                "unit": "months",
                "confidence": 1.0,
            }
        ],
    ],
)
def test_proposal_rejects_empty_and_unestimated_product_selections(
    db_client: TestClient,
    products: list[dict[str, object]],
) -> None:
    _seed_book()
    _, version = _grant()
    before = _counts()

    response = db_client.post(
        f"{BASE}/{MODEL}/apply",
        json={"products": products},
        headers=_auth(USER_1, version, "analyst"),
    )

    assert response.status_code == 422
    assert _counts() == before


def _clear_bindings() -> None:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.query(AuthorizationBinding).filter(
            AuthorizationBinding.organization_id == ORG_1
        ).delete(synchronize_session=False)
        session.commit()


def test_distinct_approver_accepts_atomically_and_preserves_other_models(
    db_client: TestClient,
) -> None:
    _seed_book()
    _seed_accepted_assumptions()
    _, proposer_version = _grant()
    proposed = _propose(db_client, proposer_version)
    proposal_id = _ack(proposed).proposal_id
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)

    queue = db_client.get(
        f"{BASE}/proposals", headers=_auth(FORECAST_APPROVER_ID, reviewer_version)
    )
    proposals = _proposal_list(queue).proposals
    assert [item.proposal_id for item in proposals] == [proposal_id]
    assert proposals[0].values[0].product_code == "retail_savings"
    assert proposals[0].values[0].value == 30.75

    before_review = _counts()
    reviewed = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)
    accepted = _accepted(reviewed)
    assert accepted.applied_rows == 1
    assert accepted.total_rows == 3
    assert accepted.as_of_date == date(2026, 3, 31)
    assert _counts() == (before_review[0], before_review[1] + 1)

    row = _proposal(proposal_id)
    assert row.status == "accepted"
    assert row.reviewed_by_user_id == FORECAST_APPROVER_ID
    assert row.reviewed_at is not None
    assert str(row.ingestion_batch_id) == accepted.ingestion_batch_id
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        payloads = list(
            session.scalars(
                select(CanonicalReferenceRow.payload)
                .where(CanonicalReferenceRow.ingestion_batch_id == row.ingestion_batch_id)
                .order_by(CanonicalReferenceRow.row_index)
            ).all()
        )
    stored = _STORED_ASSUMPTIONS.validate_python(payloads)
    by_type = {payload.assumption_type: payload for payload in stored}
    assert by_type["PREPAYMENT_RATE"].value == 0.08
    assert by_type["DEPOSIT_STABILITY"].value == 0.7
    assert by_type["NMD_DURATION"].value == 30.75
    provenance = by_type["NMD_DURATION"].provenance
    assert provenance is not None
    assert provenance.proposal_id == proposal_id
    assert provenance.proposed_by == USER_1
    assert provenance.reviewed_by == FORECAST_APPROVER_ID
    assert provenance.model_version == MODEL_VERSIONS[MODEL]

    replay = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)
    assert replay.status_code == 409
    assert _counts() == (before_review[0], before_review[1] + 1)


def test_superseded_same_model_proposal_cannot_be_accepted(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    older_id = _ack(_propose(db_client, proposer_version)).proposal_id
    newer_id = _ack(_propose(db_client, proposer_version)).proposal_id
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)

    assert _review(db_client, newer_id, FORECAST_APPROVER_ID, reviewer_version).status_code == 200
    before = _counts()
    stale = _review(db_client, older_id, FORECAST_APPROVER_ID, reviewer_version)

    assert stale.status_code == 409, stale.text
    assert "newer proposal" in stale.text
    assert _counts() == before
    assert _proposal(older_id).status == "pending"
    assert _proposal(newer_id).status == "accepted"


def test_older_proposal_cannot_relabel_newer_peer_assumptions(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    newer_peer = db_client.post(
        f"{BASE}/prepayment/apply",
        json=PROPOSE_BODY,
        headers=_auth(USER_1, proposer_version, "analyst"),
    )
    peer_id = _ack(newer_peer).proposal_id
    older_id = _ack(_propose(db_client, proposer_version)).proposal_id
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        peer = session.get(BehavioralModelProposal, peer_id)
        assert peer is not None
        peer.as_of_date = date(2026, 4, 30)
        session.commit()
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)
    approved = _review(db_client, peer_id, FORECAST_APPROVER_ID, reviewer_version)
    accepted = _accepted(approved)

    before = _counts()
    stale = _review(db_client, older_id, FORECAST_APPROVER_ID, reviewer_version)

    assert stale.status_code == 409, stale.text
    assert "older proposal" in stale.text
    assert _counts() == before
    assert _proposal(older_id).status == "pending"
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        batch = session.get(IngestionBatch, UUID(accepted.ingestion_batch_id))
        assert batch is not None
        assert batch.as_of_date == date(2026, 4, 30)
        rows = list(
            session.scalars(
                select(CanonicalReferenceRow).where(
                    CanonicalReferenceRow.organization_id == ORG_1,
                    CanonicalReferenceRow.ingestion_batch_id == UUID(accepted.ingestion_batch_id),
                )
            ).all()
        )
    assert rows
    assert all(row.as_of_date == date(2026, 4, 30) for row in rows)
    assert rows[0].payload["assumption_type"] == "PREPAYMENT_RATE"


def test_older_proposal_cannot_relabel_newer_ingested_assumptions(
    db_client: TestClient,
) -> None:
    _seed_book()
    _seed_accepted_assumptions(as_of_date=date(2026, 4, 30))
    _, proposer_version = _grant()
    proposal_id = _ack(_propose(db_client, proposer_version)).proposal_id
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)

    before = _counts()
    denied = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)

    assert denied.status_code == 409, denied.text
    assert "older proposal" in denied.text
    assert _counts() == before
    assert _proposal(proposal_id).status == "pending"


def test_self_review_and_configurator_only_review_are_denied_without_acceptance(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    proposal_id = _ack(_propose(db_client, proposer_version)).proposal_id

    before = _counts()
    configurator_review = _review(db_client, proposal_id, USER_1, proposer_version)
    unknown_review = _review(db_client, new_uuid7(), USER_1, proposer_version)
    assert configurator_review.status_code == 403
    assert unknown_review.status_code == configurator_review.status_code
    for response in (unknown_review, configurator_review):
        error = response.json()["error"]
        error.pop("request_id")
        assert error == {
            "code": "forbidden",
            "message": "Behavioral proposal review requires an active scoped binding.",
        }
    assert _counts() == before

    _, both_version = _grant(USER_1, RoleBundle.APPROVER)
    self_review = _review(db_client, proposal_id, USER_1, both_version)
    assert self_review.status_code == 403
    assert _counts() == before
    assert _proposal(proposal_id).status == "pending"


@pytest.mark.parametrize(
    "changes",
    [
        {"module_scope": ModuleScope.LIQUIDITY.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.APPROVER.value},
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "behavioral-test",
            "revoked_reason": "Exercise revoked configure denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {"valid_from": utc_now() - timedelta(days=2), "valid_until": utc_now() - timedelta(days=1)},
    ],
)
def test_incomplete_or_inactive_configure_bindings_do_not_propose(
    db_client: TestClient,
    changes: Mapping[str, object],
) -> None:
    _seed_book()
    binding_id, version = _grant()
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()

    before = _counts()
    response = _propose(db_client, version)
    assert response.status_code == 403
    assert _counts() == before


def test_wrong_bank_configure_binding_cannot_propose(db_client: TestClient) -> None:
    _seed_book()
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.add(
            Bank(
                id=SECOND_BANK_ID,
                organization_id=ORG_1,
                name="Sibling bank",
                short_name=SECOND_BANK_ID,
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type="universal_bank",
            )
        )
        session.commit()
    _, version = _grant(institution_id=SECOND_BANK_ID)
    before = _counts()

    response = _propose(db_client, version)

    assert response.status_code == 403
    assert _counts() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"module_scope": ModuleScope.LIQUIDITY.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.CONFIGURATOR.value},
        {"status": BindingStatus.SUSPENDED.value},
    ],
)
def test_incomplete_or_inactive_review_bindings_do_not_accept(
    db_client: TestClient,
    changes: Mapping[str, object],
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    proposal_id = _ack(_propose(db_client, proposer_version)).proposal_id
    binding_id, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    before = _counts()

    response = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)

    assert response.status_code == 403
    assert _counts() == before
    assert _proposal(proposal_id).status == "pending"


def test_stale_session_and_evaluator_failure_fail_closed_without_writes(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant()
    before = _counts()
    stale = _propose(db_client, 1)
    assert stale.status_code == 401
    assert _counts() == before

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    failed = _propose(db_client, version)
    assert failed.status_code == 403
    assert _counts() == before


def test_review_evaluator_failure_does_not_accept(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    proposal_id = _ack(_propose(db_client, proposer_version)).proposal_id
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)
    before = _counts()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("review evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    failed = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)

    assert failed.status_code == 403
    assert _counts() == before
    assert _proposal(proposal_id).status == "pending"


def test_acceptance_rolls_back_proposal_and_batch_when_row_persistence_fails(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, proposer_version = _grant()
    proposal_id = _ack(_propose(db_client, proposer_version)).proposal_id
    _, reviewer_version = _grant(FORECAST_APPROVER_ID, RoleBundle.APPROVER)
    before = _counts()
    with get_sessionmaker()() as session:
        lineages_before = session.scalar(select(func.count()).select_from(LineageRecord)) or 0
    observed: list[tuple[int, int]] = []

    def fail_write(_mapper: object, connection: Connection, _target: object) -> None:
        observed.append(
            (
                connection.scalar(select(func.count()).select_from(IngestionBatch)) or 0,
                connection.scalar(select(func.count()).select_from(LineageRecord)) or 0,
            )
        )
        raise RuntimeError("reference row persistence failed")

    event.listen(CanonicalReferenceRow, "before_insert", fail_write)
    try:
        failed = _review(db_client, proposal_id, FORECAST_APPROVER_ID, reviewer_version)
    finally:
        event.remove(CanonicalReferenceRow, "before_insert", fail_write)

    # Assertions cannot be masked by the expected 500 outside the event listener.
    assert observed == [(before[1] + 1, lineages_before + 1)]
    assert failed.status_code == 500
    assert _counts() == before
    row = _proposal(proposal_id)
    assert row.status == "pending"
    assert row.reviewed_by_user_id is None
    assert row.ingestion_batch_id is None
