"""Operating-Environment service: resolution, maker-checker, desk fan-out.

Explicit-input happy path proves the full lifecycle and that publish fans
``GHANA_OPERATING_ENVIRONMENT_SCORE`` into every tenant's canonical store (the
value the implied-rating model reads); a seeded-canonical test proves the
sovereign + MPR auto-pull; four-eyes is enforced at the service layer (the
operator dev session is a single identity, so it cannot be tested end-to-end
through the API).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    CanonicalCounterpartyRating,
    CanonicalMarketIndex,
    DeskOperatingEnvironmentAssessment,
    IngestionBatch,
    LineageRecord,
)
from app.services.market_desk import operating_environment as oe
from tests.api.helpers import ORG_1, ORG_2
from tests.storage.inmemory import InMemoryStorageClient

COB = date(2026, 8, 7)
ANALYST = "analyst@aequoros.com"
LEAD = "lead@aequoros.com"

STORAGE_FACTORY_TARGETS = (
    "app.adapters.market_data.pull_runner.get_storage_client",
    "app.adapters.market_data.cache.get_storage_client",
)

# Explicit-input payload → the domain worked example (BBB sovereign, MPR 27).
_EXPLICIT_INPUTS: dict[str, object] = {
    "real_gdp_growth_pct": "4.0",
    "gdp_per_capita_usd": "2200",
    "cpi_inflation_pct": "23",
    "private_credit_to_gdp_growth_pct": "11",
    "system_npl_pct": "21",
    "private_debt_to_gdp_pct": "32",
    "regulatory_quality_score": 4,
    "system_roa_pct": "1.2",
    "system_credit_growth_pct": "18",
    "system_loan_to_deposit_pct": "70",
    "system_car_pct": "14",
    "external_funding_pct": "25",
    "sovereign_rating": "BBB-",
    "policy_rate_pct": "27",
}

# Same aggregates, but sovereign + MPR left for auto-pull.
_AUTOPULL_INPUTS: dict[str, object] = {
    key: value
    for key, value in _EXPLICIT_INPUTS.items()
    if key not in ("sovereign_rating", "policy_rate_pct")
}


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorageClient:
    client = InMemoryStorageClient()
    for target in STORAGE_FACTORY_TARGETS:
        monkeypatch.setattr(target, lambda: client)
    return client


def _make_bank(db: Session, org_id: str, name: str) -> Bank:
    bank = Bank(
        organization_id=org_id,
        name=name,
        short_name=name.lower().replace(" ", "-"),
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.commit()
    return bank


@pytest.fixture
def banks(db_session: Session) -> tuple[Bank, Bank]:
    return (
        _make_bank(db_session, ORG_1, "OE Bank One"),
        _make_bank(db_session, ORG_2, "OE Bank Two"),
    )


def _seed_published_market_data(
    db: Session, bank: Bank, *, sovereign_rating: str, mpr_pct: str
) -> None:
    """Seed a published sovereign rating + MPR index (with the batch/lineage
    provenance the canonical FKs require) so the service can auto-pull them."""
    batch = IngestionBatch(
        organization_id=bank.organization_id,
        bank_id=bank.id,
        source_system="AEQUOR_DESK",
        adapter_version="1",
        extraction_mode="full",
        status="accepted",
        as_of_date=COB,
    )
    db.add(batch)
    db.flush()
    node = LineageRecord(
        organization_id=bank.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="VALIDATION",
        operation_ref="test:market_data",
    )
    db.add(node)
    db.flush()
    meta = {
        "organization_id": bank.organization_id,
        "bank_id": bank.id,
        "as_of_date": COB,
        "source_system": "AEQUOR_DESK",
        "ingestion_batch_id": batch.id,
        "validation_status": "accepted",
        "lineage_id": node.id,
    }
    db.add(
        CanonicalCounterpartyRating(
            **meta,
            issuer="GHANA_SOVEREIGN",
            agency="fitch",
            rating=sovereign_rating,
            rating_date=COB,
            source_reference="test:sovereign",
        )
    )
    db.add(
        CanonicalMarketIndex(
            **meta,
            index_code="GHS.MPR",
            value=Decimal(mpr_pct),
            scenario="base",
            horizon_months=None,
            source_reference="test:mpr",
        )
    )
    db.commit()


def _current_index(db: Session, bank: Bank) -> list[CanonicalMarketIndex]:
    return list(
        db.scalars(
            select(CanonicalMarketIndex).where(
                CanonicalMarketIndex.organization_id == bank.organization_id,
                CanonicalMarketIndex.bank_id == bank.id,
                CanonicalMarketIndex.index_code == oe.INDEX_CODE,
                CanonicalMarketIndex.scenario == "base",
                CanonicalMarketIndex.superseded_by.is_(None),
            )
        )
    )


def test_compute_preview_writes_nothing(db_session: Session) -> None:
    preview = oe.compute_preview(
        db_session, jurisdiction_code="GH", cob_date=COB, inputs=dict(_EXPLICIT_INPUTS)
    )
    assert preview["score"] == Decimal("0.491000")
    assert preview["breakdown"]["sovereign_category"] == "bbb"
    assert preview["inputs"]["provenance"]["sovereign"]["source"] == "desk_entered"
    assert preview["inputs"]["provenance"]["policy_rate"]["source"] == "desk_entered"
    # Nothing persisted.
    assert db_session.scalars(select(DeskOperatingEnvironmentAssessment)).all() == []


def test_lifecycle_stage_submit_approve_publish_fans_out(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """draft → submit → approve (distinct signer) → publish; the [0,1] score
    lands as the jurisdiction index in EVERY tenant."""
    _ = storage
    draft = oe.stage_draft(
        db_session,
        jurisdiction_code="GH",
        cob_date=COB,
        inputs=dict(_EXPLICIT_INPUTS),
        proposed_by=ANALYST,
    )
    db_session.commit()
    assert draft.status == "draft"
    assert draft.score == Decimal("0.491000")
    # No index has fanned out yet.
    assert _current_index(db_session, banks[0]) == []

    oe.submit(db_session, draft.id)
    db_session.commit()
    assert oe.get(db_session, draft.id).status == "pending_review"

    approved = oe.approve(db_session, draft.id, approved_by=LEAD)
    db_session.commit()
    assert approved.status == "approved"
    assert approved.approved_by == LEAD

    result = oe.publish(db_session, draft.id, actor=LEAD)
    assert result["status"] == "complete"
    assert result["banks"] == 2
    assert {entry["bank_id"] for entry in result["results"]} == {banks[0].id, banks[1].id}

    for bank in banks:
        rows = _current_index(db_session, bank)
        assert len(rows) == 1
        index = rows[0]
        assert index.value == Decimal("0.491000")
        assert Decimal("0") <= index.value <= Decimal("1")
        assert index.scenario == "base"
        assert index.source_system == "AEQUOR_DESK"

    published = oe.get(db_session, draft.id)
    assert published.status == "published"
    assert published.published_at is not None


def test_publish_is_idempotent(
    db_session: Session, banks: tuple[Bank, Bank], storage: InMemoryStorageClient
) -> None:
    """Re-publishing supersedes cleanly — one current index per bank, not two."""
    _ = storage, banks
    draft = oe.stage_draft(
        db_session,
        jurisdiction_code="GH",
        cob_date=COB,
        inputs=dict(_EXPLICIT_INPUTS),
        proposed_by=ANALYST,
    )
    db_session.commit()
    oe.submit(db_session, draft.id)
    oe.approve(db_session, draft.id, approved_by=LEAD)
    db_session.commit()
    oe.publish(db_session, draft.id, actor=LEAD)
    oe.publish(db_session, draft.id, actor=LEAD)
    for bank in banks:
        assert len(_current_index(db_session, bank)) == 1


def test_four_eyes_blocks_self_approval(db_session: Session) -> None:
    draft = oe.stage_draft(
        db_session,
        jurisdiction_code="GH",
        cob_date=COB,
        inputs=dict(_EXPLICIT_INPUTS),
        proposed_by=ANALYST,
    )
    oe.submit(db_session, draft.id)
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        oe.approve(db_session, draft.id, approved_by=ANALYST)
    assert excinfo.value.status_code == 422
    assert oe.get(db_session, draft.id).status == "pending_review"


def test_approve_requires_pending_review(db_session: Session) -> None:
    draft = oe.stage_draft(
        db_session,
        jurisdiction_code="GH",
        cob_date=COB,
        inputs=dict(_EXPLICIT_INPUTS),
        proposed_by=ANALYST,
    )
    db_session.commit()
    with pytest.raises(HTTPException) as excinfo:
        oe.approve(db_session, draft.id, approved_by=LEAD)
    assert excinfo.value.status_code == 409


def test_auto_pull_sovereign_and_mpr(
    db_session: Session, banks: tuple[Bank, Bank]
) -> None:
    """With no explicit sovereign/MPR, the service resolves the published
    sovereign rating (CCC → governor binds) and the GHS.MPR reference index."""
    _seed_published_market_data(
        db_session, banks[0], sovereign_rating="CCC+", mpr_pct="27"
    )
    preview = oe.compute_preview(
        db_session, jurisdiction_code="GH", cob_date=COB, inputs=dict(_AUTOPULL_INPUTS)
    )
    assert preview["breakdown"]["sovereign_category"] == "ccc"
    assert preview["breakdown"]["governor_applied"] is True
    assert preview["score"] == Decimal("0.400000")
    provenance = preview["inputs"]["provenance"]
    assert provenance["sovereign"]["source"] == "canonical_rating"
    assert provenance["sovereign"]["rating"] == "CCC+"
    assert provenance["policy_rate"]["source"] == "market_index"
    assert Decimal(preview["inputs"]["observations"]["policy_rate_pct"]) == Decimal("27")


def test_missing_sovereign_is_conflict(db_session: Session) -> None:
    """No explicit sovereign and none published → 409 (not a silent default)."""
    with pytest.raises(HTTPException) as excinfo:
        oe.compute_preview(
            db_session, jurisdiction_code="GH", cob_date=COB, inputs=dict(_AUTOPULL_INPUTS)
        )
    assert excinfo.value.status_code == 409
