"""The ONE ``bi_mart_refresh`` enqueue seam and the product hooks that call it.

``app.services.bi.enqueue.enqueue_mart_refresh`` is inert unless
``BI_MART_ENQUEUE_ENABLED`` (pinned ``"0"`` in ``tests/conftest.py``), and
when on it queues exactly one coalesced job per ``(bank, as_of)`` with the
contract payload. The hook sites — ingestion, the live-plane triggers — are
proven here through their real entrypoints; the withdrawal and pipeline hooks
are proven beside the tests that already drive those paths
(``tests/api/test_system_of_record.py``, ``tests/services/test_pipeline.py``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.jobs import bi_common, bi_retention
from app.models import BiFactPositionEom, CurrentFinancialFact, Job
from app.services import job_queue, live_refresh_triggers
from app.services.bi import enqueue, versions
from tests.adapters.excel_csv import fixtures
from tests.api.test_ingestion import (
    FULL_MAPPING,
    RECON_MAPPING,
    activate_mapping,
    seed_bank,
    start_batch,
)
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.canonical import FIXTURE_AS_OF
from tests.support.helpers import ORG_1

AS_OF = date(2026, 6, 30)
OTHER_AS_OF = date(2026, 5, 31)


@pytest.fixture
def bi_enqueue_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_MART_ENQUEUE_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bi_jobs(db: Session, *, bank_id: str | None = None) -> list[Job]:
    db.expire_all()
    stmt = select(Job).where(Job.job_type == enqueue.JOB_TYPE).order_by(Job.queued_at)
    if bank_id is not None:
        stmt = stmt.where(Job.bank_id == bank_id)
    return list(db.scalars(stmt))


def _count(db: Session, job_type: str) -> int:
    return db.scalar(select(func.count()).select_from(Job).where(Job.job_type == job_type)) or 0


# ---------------------------------------------------------------------------
# The seam itself
# ---------------------------------------------------------------------------


def test_the_seam_is_inert_when_the_switch_is_off(db_session: Session) -> None:
    """Default off: nothing is enqueued, and the session is never touched."""
    materialize_canonical_test_book(db_session)
    db_session.commit()

    job = enqueue.enqueue_mart_refresh(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        as_of=AS_OF,
        reason="ingestion",
    )

    assert job is None
    assert _count(db_session, "bi_mart_refresh") == 0
    assert not db_session.new


@pytest.mark.usefixtures("bi_enqueue_on")
def test_the_seam_enqueues_the_contract_payload_once(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.commit()
    before = utc_now()

    job = enqueue.enqueue_mart_refresh(
        db_session,
        organization_id=ORG_1,
        bank_id=SAMPLE_BANK_ID,
        as_of=AS_OF,
        reason="ingestion",
    )
    db_session.commit()

    assert job is not None
    assert job.job_type == "bi_mart_refresh"
    assert job.status == "queued"
    assert job.bank_id == SAMPLE_BANK_ID
    assert job.coalesce_key == f"bi:{SAMPLE_BANK_ID}:2026-06-30"
    assert job.entity_type == "bank"
    assert job.entity_id == SAMPLE_BANK_ID
    assert job.payload == {
        "organization_id": ORG_1,
        "bank_id": SAMPLE_BANK_ID,
        "as_of_date": "2026-06-30",
        "builder_version": versions.BUILDER_VERSION,
        "reason": "ingestion",
    }
    # Debounced on the same window as pipeline_refresh, never immediate.
    assert job.run_after is not None
    debounce = get_settings().worker.pipeline_debounce_seconds
    assert job.run_after.replace(tzinfo=None) >= (
        before + timedelta(seconds=debounce) - timedelta(seconds=1)
    ).replace(tzinfo=None)
    assert _count(db_session, "bi_mart_refresh") == 1


@pytest.mark.usefixtures("bi_enqueue_on")
def test_two_triggers_for_one_day_coalesce_and_two_days_do_not(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    db_session.commit()

    first = enqueue.enqueue_mart_refresh(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF, reason="a"
    )
    second = enqueue.enqueue_mart_refresh(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=AS_OF, reason="b"
    )
    other = enqueue.enqueue_mart_refresh(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, as_of=OTHER_AS_OF, reason="c"
    )
    db_session.commit()

    assert first is not None and second is not None and other is not None
    assert second.id == first.id
    assert first.payload["reason"] == "b"  # the latest trigger's reason wins
    assert other.id != first.id
    assert other.coalesce_key == f"bi:{SAMPLE_BANK_ID}:2026-05-31"
    assert _count(db_session, "bi_mart_refresh") == 2


def test_the_version_stamp_is_the_shared_constant_the_handlers_compare() -> None:
    """The enqueue side and the builder agree on ONE integer, defined in a
    module with no imports so the hot path never loads the builder. The
    builder module must re-export it under the contract name."""
    assert isinstance(versions.BUILDER_VERSION, int)
    assert versions.BUILDER_VERSION >= 1
    builder = bi_common.load_builder()
    assert builder.BUILDER_VERSION == versions.BUILDER_VERSION


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_seam_does_not_import_the_builder() -> None:
    """The one structural guarantee of the module docstring, as a test: the
    hot path loads the version constant, the queue and the two BI models the
    due-check reads — never the builder, the catalogue, the compiler or the
    handler tree.

    ``app.models.bi`` is deliberately NOT forbidden (A5-04/D-043 moved the
    sweep's read here so ``scheduler.py`` imports no BI model) and it costs the
    hot path nothing: ``app/models/__init__.py`` already loads it for every
    consumer of ``app.models``."""
    imported = _imported_modules(Path(enqueue.__file__))
    forbidden = {
        name
        for name in imported
        if name.startswith(
            (
                "app.services.bi.mart_builder",
                "app.services.bi.compiler",
                "app.services.bi.partitions",
                "app.domain.bi",
                "app.jobs",
            )
        )
    }
    assert forbidden == set(), forbidden
    assert "app.services.bi.versions" in imported
    # And the constants module itself imports nothing at all.
    assert _imported_modules(Path(versions.__file__)) == {"__future__", "__future__.annotations"}


def test_the_regulatory_plane_imports_only_the_seam_and_the_versions_module() -> None:
    """D-041/D-043 for the five hook sites, ahead of wave 3's boundary test:
    the sweep's BI-model read moved into the seam, so ``scheduler.py`` — the
    one module that used to import ``app.models.bi`` (A5-04) — imports only
    ``enqueue`` and ``versions``."""
    hook_sites = (
        "app/services/scheduler.py",
        "app/services/ingestion.py",
        "app/services/pipeline.py",
        "app/services/live_refresh_triggers.py",
        "app/services/canonical_withdrawal.py",
    )
    allowed = {"app.services.bi.enqueue", "app.services.bi.versions"}
    offenders: dict[str, set[str]] = {}
    for relative in hook_sites:
        imported = _imported_modules(Path(enqueue.__file__).parents[3] / relative)
        bi_imports = {
            name
            for name in imported
            if name.startswith(("app.services.bi", "app.domain.bi", "app.models.bi"))
            and name not in allowed
            # ``from app.services.bi.enqueue import x`` also yields the dotted
            # member name; only the MODULE matters here.
            and not any(name.startswith(f"{ok}.") for ok in allowed)
        }
        if bi_imports:
            offenders[relative] = bi_imports
    assert offenders == {}, offenders


# ---------------------------------------------------------------------------
# Hook: the live-plane triggers (one insertion in _enqueue_bank covers all)
# ---------------------------------------------------------------------------


def _seed_live_input(db: Session, *, as_of: date = FIXTURE_AS_OF) -> None:
    materialize_canonical_test_book(db)
    db.add(
        CurrentFinancialFact(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_as_of_date=as_of,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            amount=Decimal("1"),
            currency="GHS",
        )
    )
    db.commit()


def test_live_triggers_enqueue_nothing_for_bi_when_off(db_session: Session) -> None:
    _seed_live_input(db_session)

    job = live_refresh_triggers.enqueue_bank_change(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, reason="register updated"
    )
    db_session.commit()

    assert job is not None and job.job_type == "pipeline_refresh"
    assert _count(db_session, "bi_mart_refresh") == 0


@pytest.mark.usefixtures("bi_enqueue_on")
def test_live_triggers_enqueue_the_bi_sibling_on_the_live_date(db_session: Session) -> None:
    """Every entitlement/methodology/parameter/register/withdrawal trigger
    funnels through ``_enqueue_bank``; the BI job it adds is keyed on the
    bank's LIVE date — the date whose engine metrics are about to move."""
    _seed_live_input(db_session)

    job = live_refresh_triggers.enqueue_bank_change(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, reason="register updated"
    )
    db_session.commit()

    assert job is not None and job.job_type == "pipeline_refresh"  # the return is unchanged
    (bi_job,) = _bi_jobs(db_session, bank_id=SAMPLE_BANK_ID)
    assert bi_job.coalesce_key == f"bi:{SAMPLE_BANK_ID}:{FIXTURE_AS_OF.isoformat()}"
    assert bi_job.payload["as_of_date"] == FIXTURE_AS_OF.isoformat()
    assert bi_job.payload["reason"] == "register updated"
    assert bi_job.payload["builder_version"] == versions.BUILDER_VERSION


@pytest.mark.usefixtures("bi_enqueue_on")
def test_live_triggers_enqueue_no_bi_job_for_a_bank_with_no_live_generation(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    db_session.commit()

    job = live_refresh_triggers.enqueue_bank_change(
        db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID, reason="register updated"
    )

    assert job is None
    assert _count(db_session, "bi_mart_refresh") == 0


# ---------------------------------------------------------------------------
# Hook: ingestion (the batch's OWN as-of, coalesced per day)
# ---------------------------------------------------------------------------


def _bi_jobs_via_app(bank_id: str) -> list[Job]:
    session = get_sessionmaker()()
    try:
        return list(
            session.scalars(
                select(Job)
                .where(Job.job_type == enqueue.JOB_TYPE, Job.bank_id == bank_id)
                .order_by(Job.queued_at)
            )
        )
    finally:
        session.close()


def test_accepted_ingestion_enqueues_no_bi_job_when_off(
    db_client: TestClient, tmp_path: Path
) -> None:
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, FULL_MAPPING)
    started = start_batch(db_client, bank_id, fixtures.build_well_formed(tmp_path / "bank.xlsx"))
    assert started["batch"]["status"] == "accepted"

    assert _bi_jobs_via_app(bank_id) == []


@pytest.mark.usefixtures("bi_enqueue_on")
def test_accepted_ingestion_enqueues_one_bi_job_on_the_batch_date(
    db_client: TestClient, tmp_path: Path
) -> None:
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, FULL_MAPPING)
    started = start_batch(db_client, bank_id, fixtures.build_well_formed(tmp_path / "bank.xlsx"))
    assert started["batch"]["status"] == "accepted"
    batch_as_of = started["batch"]["as_of_date"]

    (job,) = _bi_jobs_via_app(bank_id)
    assert job.coalesce_key == f"bi:{bank_id}:{batch_as_of}"
    assert job.payload["as_of_date"] == batch_as_of
    assert job.payload["reason"] == "ingestion"
    assert job.payload["builder_version"] == versions.BUILDER_VERSION
    assert job.run_after is not None  # debounced with the live refresh


@pytest.mark.usefixtures("bi_enqueue_on")
def test_two_batches_for_one_date_coalesce_into_one_bi_job(
    db_client: TestClient, tmp_path: Path
) -> None:
    """A multi-file upload burst for one day is one build, not two."""
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, FULL_MAPPING)
    workbook = fixtures.build_well_formed(tmp_path / "bank.xlsx")
    first = start_batch(db_client, bank_id, workbook)
    # A restatement of the same day: a second, distinct accepted batch.
    loaded = openpyxl.load_workbook(workbook)
    loaded["Loans"]["D2"] = 1600000.00
    loaded.save(workbook)
    second = start_batch(db_client, bank_id, workbook)
    assert first["batch"]["status"] == "accepted"
    assert second["batch"]["status"] == "accepted"
    assert second["reused"] is False
    assert first["batch"]["as_of_date"] == second["batch"]["as_of_date"]
    assert first["batch"]["id"] != second["batch"]["id"]

    jobs = _bi_jobs_via_app(bank_id)
    assert len(jobs) == 1
    assert jobs[0].coalesce_key == f"bi:{bank_id}:{first['batch']['as_of_date']}"


@pytest.mark.usefixtures("bi_enqueue_on")
def test_a_rejected_batch_enqueues_no_bi_job(db_client: TestClient, tmp_path: Path) -> None:
    """Same guard as the live refresh: a rejected batch changed no canonical
    row, so there is no slice to rebuild."""
    bank_id = seed_bank(db_client)
    activate_mapping(db_client, bank_id, RECON_MAPPING)
    started = start_batch(
        db_client,
        bank_id,
        fixtures.build_reconciliation_workbook(tmp_path / "recon.xlsx", gl_balance="1500"),
    )
    assert started["batch"]["status"] == "rejected"
    assert _bi_jobs_via_app(bank_id) == []


# ---------------------------------------------------------------------------
# Retention wiring (task 4): handler -> builder -> definer wrapper, D-039 pinned
# ---------------------------------------------------------------------------


@pytest.fixture
def bi_scheduler_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_retention_may_name_only_the_daily_parents() -> None:
    """D-039 at the constant level: the month-end fact is kept forever, and
    the BI service layer exposes no yearly drop at all."""
    from app.services.bi import mart_builder, partitions  # noqa: PLC0415

    eom = BiFactPositionEom.__tablename__
    assert eom not in mart_builder.RETENTION_PARENTS
    assert set(mart_builder.RETENTION_PARENTS) <= set(partitions.MONTHLY_BUILD_PARENTS)
    assert eom in partitions.YEARLY_BUILD_PARENTS
    assert not hasattr(partitions, "drop_year_partition")
    assert "drop_year_partition" not in partitions.__all__


@pytest.mark.usefixtures("bi_scheduler_on")
def test_the_retention_job_drops_through_the_real_builder_and_never_the_eom_table(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-039 at the JOB level, with no stub in the chain: the ``bi_retention``
    handler E1 built calls D2's ``apply_retention`` with the configured
    window, which walks ONLY the daily parents' children through the
    definer wrapper. SQLite has no partitions, so the wrapper is spied and
    the catalogue read is faked to offer an old child under EVERY parent —
    including the month-end fact — and the pin is that the EOM child is
    never asked for and never reported dropped."""
    from app.services.bi import mart_builder, partitions  # noqa: PLC0415

    old_month = date(2025, 1, 1)
    asked: list[tuple[str, date]] = []
    listed: list[str] = []

    def fake_children(db: Session, parent: str) -> list[tuple[str, date]]:
        _ = db
        listed.append(parent)
        return [(f"{parent}_y2025m01", old_month)]

    def spy_drop(db: Session, parent: str, month: date) -> bool:
        _ = db
        asked.append((parent, month))
        return True

    monkeypatch.setattr(partitions, "is_postgres", lambda db: True)
    monkeypatch.setattr(partitions, "month_children", fake_children)
    monkeypatch.setattr(partitions, "drop_month_partition", spy_drop)
    monkeypatch.setenv("BI_DAILY_RETENTION_DAYS", "95")
    get_settings.cache_clear()

    job = job_queue.enqueue(
        db_session, ORG_1, "bi_retention", payload={"builder_version": versions.BUILDER_VERSION}
    )
    db_session.commit()
    claimed = job_queue.claim_next(db_session, utc_now(), ("bi_retention",))
    assert claimed is not None and claimed.id == job.id

    bi_retention.run_bi_retention(db_session, claimed)

    eom = BiFactPositionEom.__tablename__
    assert set(listed) == set(mart_builder.RETENTION_PARENTS)
    assert eom not in listed
    assert asked == [(parent, old_month) for parent in mart_builder.RETENTION_PARENTS]
    assert claimed.progress["status"] == "succeeded"
    assert claimed.progress["retention_days"] == 95
    assert claimed.progress["builder_version"] == versions.BUILDER_VERSION
    assert claimed.progress["dropped"] == [
        f"{parent}_y2025m01" for parent in mart_builder.RETENTION_PARENTS
    ]
    assert not any(name.startswith(eom) for name in claimed.progress["dropped"])
