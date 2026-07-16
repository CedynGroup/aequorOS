"""Freshness: live view vs the last official filing run, keyed on input hash."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, Job
from app.schemas.live import FreshnessModuleRead
from app.services import freshness, job_queue, pipeline
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import (
    FIXTURE_AS_OF,
    seed_canonical_fixture,
    seed_hedge_and_swap_positions,
)


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _seed(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.commit()


def _enqueue(db_session: Session, job_type: str) -> Job:
    payload = {"as_of_date": FIXTURE_AS_OF.isoformat(), "actor_user_id": str(USER_1)}
    job = job_queue.enqueue(db_session, ORG_1, job_type, bank_id=SAMPLE_BANK_ID, payload=payload)
    db_session.commit()
    return job


def _liquidity(read) -> FreshnessModuleRead:
    return next(module for module in read.modules if module.module == "liquidity")


def test_freshness_stale_without_official_run(db_session: Session) -> None:
    _seed(db_session)
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))

    read = freshness.get_bank_freshness(db_session, _ctx(), SAMPLE_BANK_ID)
    liquidity = _liquidity(read)
    assert liquidity.live_hash is not None
    assert liquidity.official_run_hash is None
    assert liquidity.is_stale is True
    assert read.is_stale is True


def test_freshness_fresh_after_official_then_stale_after_data_change(
    db_session: Session,
) -> None:
    _seed(db_session)
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))

    # An official run on the same facts makes the live hash match the run hash.
    # (Checked at module granularity: the forecast live row only mirrors an
    # official forecast run on the *next* refresh, so the aggregate can lag.)
    pipeline.run_official(db_session, _enqueue(db_session, "official_run"))
    fresh_liquidity = _liquidity(freshness.get_bank_freshness(db_session, _ctx(), SAMPLE_BANK_ID))
    assert fresh_liquidity.official_run_hash is not None
    assert fresh_liquidity.live_hash == fresh_liquidity.official_run_hash
    assert fresh_liquidity.is_stale is False

    # Genuinely change the economics (overlay a hedge/swap book), then refresh:
    # the affected module's live hash moves ahead of the last official run.
    # Staleness must track economics, not fact-row identity, so we mutate real
    # data rather than relying on a bare re-derivation.
    seed_hedge_and_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.commit()
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))

    stale = freshness.get_bank_freshness(db_session, _ctx(), SAMPLE_BANK_ID)
    stale_fx = next(module for module in stale.modules if module.module == "fx")
    assert stale_fx.live_hash != stale_fx.official_run_hash
    assert stale_fx.is_stale is True
    assert stale.is_stale is True


def test_freshness_rederiving_unchanged_data_stays_fresh(db_session: Session) -> None:
    """A bare re-derivation must not fabricate staleness.

    ``run_refresh`` deletes and re-inserts every fact with a new UUID. Because
    the input hash is value-based (not keyed on fact-row identity), re-deriving
    identical canonical data leaves the live hash equal to the last official
    run's hash — the module stays fresh.
    """
    _seed(db_session)
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))
    pipeline.run_official(db_session, _enqueue(db_session, "official_run"))
    # Re-derive twice on unchanged data (churns fact UUIDs each time).
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))
    pipeline.run_refresh(db_session, _enqueue(db_session, "pipeline_refresh"))

    read = freshness.get_bank_freshness(db_session, _ctx(), SAMPLE_BANK_ID)
    liquidity = _liquidity(read)
    assert liquidity.live_hash == liquidity.official_run_hash
    assert liquidity.is_stale is False
    assert read.is_stale is False


def test_freshness_without_any_period_is_not_stale(db_session: Session) -> None:
    bank = Bank(
        organization_id=ORG_1,
        name="Bare Bank Ltd",
        short_name="BARE",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.commit()

    read = freshness.get_bank_freshness(db_session, _ctx(), bank.id)
    assert read.reporting_period_id is None
    assert read.modules == []
    assert read.is_stale is False


def test_freshness_unknown_bank_404s(db_session: Session) -> None:
    with pytest.raises(HTTPException) as excinfo:
        freshness.get_bank_freshness(db_session, _ctx(), uuid4())
    assert excinfo.value.status_code == 404
