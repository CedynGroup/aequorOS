"""Market data pull jobs: handler execution, §10 error policy, tick scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.market_data import base as market_data_base
from app.adapters.market_data.base import CredentialSet, MarketDataAdapter, MarketDataPullResult
from app.adapters.market_data.credential_manager import EncryptedDbVault, build_vault_path
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.scope_taxonomy import DataScope
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import AuditEvent, Bank, Job
from app.models.market_data import MarketDataConnection
from app.services import job_queue, scheduler
from app.services.market_data_jobs import (
    enqueue_due_market_data_pulls,
    run_market_data_pull,
)
from tests.api.helpers import ORG_1

MASTER_KEY = "unit-test-master-key"
CREDENTIALS = {"api_key": "bbg-key-1234", "api_secret": "must-never-leak"}
AS_OF = date(2026, 7, 15)
# A Wednesday at noon UTC: inside business hours, before the 17:00 EOD slot.
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class FakeAdapter:
    """Test-local registry adapter: records pulls, optionally raising."""

    calls: ClassVar[list[dict[str, Any]]] = []
    raises: ClassVar[MarketDataError | None] = None

    def __init__(self, *, db: Session, bank: Bank, bank_slug: str) -> None:
        self.db = db
        self.bank = bank
        self.bank_slug = bank_slug

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.raises = None

    def pull(  # noqa: PLR0913 - mirrors the MarketDataAdapter contract
        self,
        credentials: CredentialSet,
        scopes: list[DataScope],
        as_of_date: date,
        institution_id: str,
        batch_id: str,
    ) -> MarketDataPullResult:
        FakeAdapter.calls.append(
            {
                "credentials": credentials,
                "scopes": list(scopes),
                "as_of_date": as_of_date,
                "institution_id": institution_id,
                "batch_id": batch_id,
                "bank_slug": self.bank_slug,
            }
        )
        if FakeAdapter.raises is not None:
            raise FakeAdapter.raises
        return MarketDataPullResult(
            batch_id="fake-batch-001",
            institution_id=institution_id,
            scopes_pulled=list(scopes),
            canonical_records_produced=3,
            quota_consumed=2,
            raw_storage_location="raw://test",
            canonical_storage_location="canonical-db://market_data",
            pulled_at=utc_now(),
            warnings=[],
            errors=[],
        )


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeAdapter]]:
    """Register FakeAdapter as the bloomberg vendor; restored on teardown."""
    FakeAdapter.reset()
    monkeypatch.setitem(
        market_data_base._MARKET_DATA_REGISTRY,  # noqa: SLF001 - test seam
        "bloomberg",
        cast("type[MarketDataAdapter]", FakeAdapter),
    )
    yield FakeAdapter
    FakeAdapter.reset()


def _bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Pull Test Bank",
        short_name="PTB",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    return bank


def _connection(  # noqa: PLR0913 - fixture knob per connection attribute
    db_session: Session,
    bank: Bank,
    *,
    vendor: str = "bloomberg",
    status: str = "ACTIVE",
    scopes: list[str] | None = None,
    schedule: dict[str, str] | None = None,
) -> MarketDataConnection:
    connection = MarketDataConnection(
        organization_id=ORG_1,
        bank_id=bank.id,
        vendor=vendor,
        display_name=f"{vendor} terminal",
        status=status,
        vault_path=build_vault_path(bank.id, vendor),
        scopes=scopes if scopes is not None else ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"],
        schedule=schedule if schedule is not None else {"YIELD_CURVE": "END_OF_DAY"},
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def _store_credentials(
    db_session: Session, bank: Bank, monkeypatch: pytest.MonkeyPatch, vendor: str = "bloomberg"
) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()
    vault = EncryptedDbVault(db_session, master_key=MASTER_KEY)
    vault.store(
        organization_id=ORG_1,
        bank_id=bank.id,
        vendor=vendor,
        credentials=CREDENTIALS,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
    )


def _pull_job(db_session: Session, connection: MarketDataConnection) -> Job:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "market_data_pull",
        bank_id=connection.bank_id,
        payload={"connection_id": str(connection.id), "as_of_date": AS_OF.isoformat()},
    )
    db_session.commit()
    return job


def _error(code: BankFacingErrorCode) -> MarketDataError:
    return MarketDataError(
        render_bank_facing(
            code, vendor="Bloomberg", timestamp="2026-07-15T00:00:00Z", scope="YIELD_CURVE_GHS"
        ),
        internal_detail="HTTP 401 from vendor (never bank-facing)",
    )


def test_run_market_data_pull_executes_and_records_success(
    db_session: Session,
    fake_adapter: type[FakeAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_credentials(db_session, bank, monkeypatch)
    job = _pull_job(db_session, connection)

    run_market_data_pull(db_session, job)

    assert connection.last_pull_status == "succeeded"
    assert connection.last_pull_at is not None
    assert job.progress["batch_id"] == "fake-batch-001"
    assert job.progress["scopes_pulled"] == ["YIELD_CURVE_GHS", "FX_SPOT_USD_GHS"]
    assert job.progress["canonical_records_produced"] == 3

    call = fake_adapter.calls[0]
    # Credentials round-trip through the encrypted vault for one pull cycle.
    assert call["credentials"].credentials == CREDENTIALS
    assert call["credentials"].vendor == "bloomberg"
    # Scopes default to the connection's authorized set; adapters mint batches.
    assert call["scopes"] == [DataScope.YIELD_CURVE_GHS, DataScope.FX_SPOT_USD_GHS]
    assert call["as_of_date"] == AS_OF
    assert call["batch_id"] == ""
    assert call["institution_id"] == str(bank.id)
    get_settings.cache_clear()


def test_credential_error_marks_connection_invalid_without_retry(
    db_session: Session,
    fake_adapter: type[FakeAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_credentials(db_session, bank, monkeypatch)
    job = _pull_job(db_session, connection)
    fake_adapter.raises = _error(BankFacingErrorCode.CREDENTIAL_INVALID)

    # No exception: the job completes so the worker never retries it.
    run_market_data_pull(db_session, job)

    assert connection.status == "INVALID"
    assert connection.last_pull_status == "failed"
    assert job.progress["status"] == "failed_no_retry"
    assert job.progress["error_code"] == "CREDENTIAL_INVALID"
    # The internal vendor detail never reaches the job's bank-visible surface.
    assert "HTTP 401" not in job.progress["error"]
    transition = db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "market_data_connection.status_changed")
    )
    assert transition is not None
    assert transition.details["to"] == "INVALID"
    get_settings.cache_clear()


def test_expired_and_revoked_codes_map_to_their_statuses(
    db_session: Session,
    fake_adapter: type[FakeAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_credentials(db_session, bank, monkeypatch)

    fake_adapter.raises = _error(BankFacingErrorCode.CREDENTIAL_EXPIRED)
    run_market_data_pull(db_session, _pull_job(db_session, connection))
    assert connection.status == "EXPIRED"

    connection.status = "ACTIVE"
    db_session.commit()
    fake_adapter.raises = _error(BankFacingErrorCode.CREDENTIAL_REVOKED)
    run_market_data_pull(db_session, _pull_job(db_session, connection))
    assert connection.status == "REVOKED"
    get_settings.cache_clear()


def test_retryable_error_reraises_for_worker_backoff(
    db_session: Session,
    fake_adapter: type[FakeAdapter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_credentials(db_session, bank, monkeypatch)
    job = _pull_job(db_session, connection)
    fake_adapter.raises = _error(BankFacingErrorCode.VENDOR_UNAVAILABLE)

    with pytest.raises(MarketDataError):
        run_market_data_pull(db_session, job)

    # The failure mark is committed before the re-raise; the credential
    # lifecycle status is untouched — the vendor is down, not the credential.
    assert connection.last_pull_status == "failed"
    assert connection.status == "ACTIVE"
    get_settings.cache_clear()


def test_enqueue_due_pulls_is_inert_when_disabled(db_session: Session) -> None:
    bank = _bank(db_session)
    _connection(db_session, bank)

    assert enqueue_due_market_data_pulls(db_session, ORG_1, now=NOW) == []
    assert db_session.scalar(select(Job).where(Job.job_type == "market_data_pull")) is None


def test_enqueue_due_pulls_enqueues_coalesced_jobs(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKET_DATA_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    connection = _connection(db_session, bank)  # never pulled: due immediately

    first = enqueue_due_market_data_pulls(db_session, ORG_1, now=NOW)
    second = enqueue_due_market_data_pulls(db_session, ORG_1, now=NOW)
    db_session.commit()

    assert len(first) == 1
    assert len(second) == 1
    assert second[0].id == first[0].id  # coalesced, not duplicated
    job = first[0]
    assert job.payload["connection_id"] == str(connection.id)
    assert job.payload["scopes"] == ["FX_SPOT_USD_GHS", "YIELD_CURVE_GHS"]
    assert job.payload["as_of_date"] == NOW.date().isoformat()
    assert job.coalesce_key == f"md_pull:{connection.id}:{NOW.date().isoformat()}"
    get_settings.cache_clear()


def test_enqueue_due_pulls_skips_not_due_and_manual_connections(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKET_DATA_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    # Pulled moments ago: the next END_OF_DAY slot (17:00) has not arrived.
    recent = _connection(db_session, bank, scopes=["YIELD_CURVE_GHS"])
    recent.last_pull_at = NOW - timedelta(minutes=5)
    # Manual upload is push-based and never scheduled.
    _connection(db_session, bank, vendor="manual_upload", scopes=["YIELD_CURVE_GHS"])
    db_session.flush()

    assert enqueue_due_market_data_pulls(db_session, ORG_1, now=NOW) == []
    get_settings.cache_clear()


def test_credential_health_transitions_persist(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKET_DATA_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    expiring = _connection(db_session, bank, scopes=["YIELD_CURVE_GHS"])
    expiring.credential_expires_at = NOW + timedelta(days=10)
    expired = _connection(db_session, bank, vendor="refinitiv", scopes=["YIELD_CURVE_GHS"])
    expired.credential_expires_at = NOW - timedelta(days=1)
    db_session.flush()

    enqueued = enqueue_due_market_data_pulls(db_session, ORG_1, now=NOW)

    # EXPIRING_SOON still pulls (credentials authenticate until expiry);
    # EXPIRED blocks scheduled pulls per §10.3.
    assert expiring.status == "EXPIRING_SOON"
    assert expired.status == "EXPIRED"
    assert [job.payload["connection_id"] for job in enqueued] == [str(expiring.id)]
    get_settings.cache_clear()


def test_scheduler_tick_enqueues_due_pulls_when_enabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKET_DATA_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    connection = _connection(db_session, bank, scopes=["YIELD_CURVE_GHS"])
    job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    pulls = list(db_session.scalars(select(Job).where(Job.job_type == "market_data_pull")))
    assert len(pulls) == 1
    assert pulls[0].payload["connection_id"] == str(connection.id)
    # Official runs stay disabled; the tick still self-perpetuates.
    assert tick.progress == {"official_runs_enqueued": [], "market_data_pulls_enqueued": 1}
    queued_ticks = list(
        db_session.scalars(
            select(Job).where(Job.job_type == "scheduled_tick", Job.status == "queued")
        )
    )
    assert len(queued_ticks) == 1
    get_settings.cache_clear()


def test_scheduler_tick_enqueues_no_pulls_when_disabled(db_session: Session) -> None:
    bank = _bank(db_session)
    _connection(db_session, bank, scopes=["YIELD_CURVE_GHS"])
    tick = job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()

    scheduler.run_tick(db_session, tick)

    assert tick.progress.get("status") == "inert"
    assert db_session.scalar(select(Job).where(Job.job_type == "market_data_pull")) is None
