"""The automatic pipeline: cheap live tier + immutable official tier.

Drives the worker handlers directly (``pipeline.run_refresh`` /
``pipeline.run_official``) on the compact canonical fixture, asserting the live
tier stays free of ``RegulatoryRun`` writes while the official tier mints
immutable, reproducible runs.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job, LiveFinding, LiveMetric, RegulatoryRun
from app.services import job_queue, pipeline
from app.services.sample_bank_seed import SAMPLE_BANK_ID, seed_sample_bank
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import (
    FIXTURE_AS_OF,
    seed_canonical_fixture,
    seed_hedge_and_swap_positions,
)

_CHEAP = {"liquidity", "capital", "irr", "fx", "ftp"}


def _seed(db_session: Session, *, hedged: bool = False) -> None:
    seed_sample_bank(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    if hedged:
        seed_hedge_and_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.commit()


def _refresh_job(db_session: Session) -> Job:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "pipeline_refresh",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
    )
    db_session.commit()
    return job


def _official_job(db_session: Session) -> Job:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "official_run",
        bank_id=SAMPLE_BANK_ID,
        payload={"as_of_date": FIXTURE_AS_OF.isoformat(), "actor_user_id": str(USER_1)},
    )
    db_session.commit()
    return job


def _live_rows(db_session: Session) -> dict[str, LiveMetric]:
    return {
        row.module: row
        for row in db_session.scalars(
            select(LiveMetric).where(LiveMetric.bank_id == SAMPLE_BANK_ID)
        )
    }


def test_run_refresh_populates_live_metrics_for_every_module(db_session: Session) -> None:
    _seed(db_session)
    pipeline.run_refresh(db_session, _refresh_job(db_session))

    rows = _live_rows(db_session)
    assert set(rows) >= _CHEAP
    assert Decimal(rows["liquidity"].metrics["lcr_pct"]) > 0
    assert Decimal(rows["capital"].metrics["car_pct"]) > 0
    for row in rows.values():
        assert row.status in ("green", "amber", "red", "na")
    # Cheap modules carry a baseline input hash for the freshness comparison.
    assert rows["liquidity"].computed_from_input_hash
    assert len(rows["liquidity"].computed_from_input_hash) == 64


def test_run_refresh_creates_zero_regulatory_runs(db_session: Session) -> None:
    _seed(db_session)
    before = db_session.scalar(select(func.count()).select_from(RegulatoryRun))
    pipeline.run_refresh(db_session, _refresh_job(db_session))
    after = db_session.scalar(select(func.count()).select_from(RegulatoryRun))
    assert before == 0
    assert after == 0


def test_run_refresh_emits_fx_breach_then_clears_after_hedges(db_session: Session) -> None:
    # Raw book: USD long ~29% of Tier 1 — breaches the 20%/10% NOP limits.
    _seed(db_session)
    pipeline.run_refresh(db_session, _refresh_job(db_session))

    open_fx = list(
        db_session.scalars(
            select(LiveFinding).where(
                LiveFinding.bank_id == SAMPLE_BANK_ID,
                LiveFinding.module == "fx",
                LiveFinding.status == "open",
            )
        )
    )
    breach = next(f for f in open_fx if f.rule_id == "nop_within_aggregate_limit")
    assert breach.severity in ("critical", "high")

    # Add the hedge book (sells 700k USD) and refresh — the breach clears.
    seed_hedge_and_swap_positions(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    db_session.commit()
    pipeline.run_refresh(db_session, _refresh_job(db_session))

    open_after = list(
        db_session.scalars(
            select(LiveFinding).where(
                LiveFinding.bank_id == SAMPLE_BANK_ID,
                LiveFinding.module == "fx",
                LiveFinding.status == "open",
                LiveFinding.rule_id == "nop_within_aggregate_limit",
            )
        )
    )
    assert open_after == []
    superseded = list(
        db_session.scalars(
            select(LiveFinding).where(
                LiveFinding.bank_id == SAMPLE_BANK_ID,
                LiveFinding.module == "fx",
                LiveFinding.status == "superseded",
                LiveFinding.rule_id == "nop_within_aggregate_limit",
            )
        )
    )
    assert superseded


def test_run_refresh_is_idempotent(db_session: Session) -> None:
    _seed(db_session)
    pipeline.run_refresh(db_session, _refresh_job(db_session))
    pipeline.run_refresh(db_session, _refresh_job(db_session))

    per_module = db_session.execute(
        select(LiveMetric.module, func.count())
        .where(LiveMetric.bank_id == SAMPLE_BANK_ID)
        .group_by(LiveMetric.module)
    ).all()
    assert all(count == 1 for _module, count in per_module)


def test_run_refresh_missing_canonical_is_a_noop(db_session: Session) -> None:
    seed_sample_bank(db_session)
    db_session.commit()  # bank + params only, no canonical positions
    job = _refresh_job(db_session)

    pipeline.run_refresh(db_session, job)  # must not raise

    assert job.progress.get("status") == "skipped"
    assert db_session.scalar(select(func.count()).select_from(LiveMetric)) == 0


def test_run_official_mints_immutable_reproducible_runs(db_session: Session) -> None:
    _seed(db_session)
    # A refresh first derives + stamps the live view but no immutable runs.
    pipeline.run_refresh(db_session, _refresh_job(db_session))
    assert db_session.scalar(select(func.count()).select_from(RegulatoryRun)) == 0

    pipeline.run_official(db_session, _official_job(db_session))
    baseline_after_first = _baseline_liquidity_hashes(db_session)
    assert len(baseline_after_first) == 1  # one baseline liquidity run

    pipeline.run_official(db_session, _official_job(db_session))
    baseline_after_second = _baseline_liquidity_hashes(db_session)
    # A second official run on unchanged facts reproduces the same input hash.
    assert baseline_after_second == baseline_after_first


def test_official_input_hash_survives_fact_rederivation(db_session: Session) -> None:
    """Reproducibility must hold across the live engine's fact churn.

    Every ``run_refresh`` re-derives facts, deleting and re-inserting each
    ``BankFinancialFact`` with a fresh UUID. A filed official run must still
    reproduce byte-identical ``input_hash`` values afterwards, so the snapshot
    hash may not depend on the (churning) fact row id or its DB return order.
    """
    _seed(db_session)
    pipeline.run_refresh(db_session, _refresh_job(db_session))
    pipeline.run_official(db_session, _official_job(db_session))
    before = _baseline_hashes_by_module(db_session)
    assert set(before) >= _CHEAP  # one baseline hash per cheap module

    # A refresh re-derives facts (new UUIDs, possibly reordered), then a second
    # official run on the same economics must reproduce the identical hashes.
    pipeline.run_refresh(db_session, _refresh_job(db_session))
    pipeline.run_official(db_session, _official_job(db_session))
    after = _baseline_hashes_by_module(db_session)

    assert after == before


def _baseline_liquidity_hashes(db_session: Session) -> set[str]:
    return set(
        db_session.scalars(
            select(RegulatoryRun.input_hash).where(
                RegulatoryRun.bank_id == SAMPLE_BANK_ID,
                RegulatoryRun.module == "liquidity",
                RegulatoryRun.scenario_code == "baseline",
                RegulatoryRun.status == "succeeded",
            )
        )
    )


def _baseline_hashes_by_module(db_session: Session) -> dict[str, set[str]]:
    """Distinct baseline ``input_hash`` per cheap module across all official runs.

    A reproducible module collapses to a single hash even after several official
    runs; a non-reproducible one accumulates a distinct hash per run.
    """
    rows = db_session.execute(
        select(RegulatoryRun.module, RegulatoryRun.input_hash).where(
            RegulatoryRun.bank_id == SAMPLE_BANK_ID,
            RegulatoryRun.scenario_code == "baseline",
            RegulatoryRun.status == "succeeded",
            RegulatoryRun.module.in_(_CHEAP),
        )
    ).all()
    hashes: dict[str, set[str]] = {}
    for module, input_hash in rows:
        hashes.setdefault(module, set()).add(input_hash)
    return hashes
