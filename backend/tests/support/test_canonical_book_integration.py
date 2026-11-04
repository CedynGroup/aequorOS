"""Prepared books are opt-in values, with real rebuild and rollback controls."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import get_sessionmaker
from app.identity.service import authorization
from app.models import Bank, BankReportingPeriod, Organization, ParamCapitalThreshold, User
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.canonical_book import (
    CanonicalBookDatabase,
    canonical_book_transaction,
    canonical_test_book,
)

pytestmark = pytest.mark.canonical_book_baseline
_SENTINEL_ORG = "OR-PRESET01"


def test_01_committed_mutations_and_deletions_are_isolated(db_session: Session) -> None:
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    bank.name = "Must roll back"
    db_session.add(Organization(id=_SENTINEL_ORG, name="Must disappear"))
    _ = db_session.execute(delete(ParamCapitalThreshold))
    _ = authorization.invalidate_user_authorization(
        db_session,
        organization_id=DEMO_ORG_ID,
        user_id=DEMO_USER_ID,
        reason="Exercise preset authorization-version isolation",
    )
    db_session.commit()
    assert db_session.get(Organization, _SENTINEL_ORG) is not None
    assert db_session.scalars(select(ParamCapitalThreshold)).all() == []
    assert bank.name == "Must roll back"


def test_02_following_case_sees_the_unchanged_baseline(db_session: Session) -> None:
    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    user = db_session.get(User, DEMO_USER_ID)
    assert bank is not None and bank.name == "Sample Bank Ltd"
    assert user is not None and user.authorization_version == 1
    assert db_session.get(Organization, _SENTINEL_ORG) is None
    assert db_session.scalars(select(ParamCapitalThreshold)).all()


def _book_values(db: Session) -> dict[str, tuple[str, ...]]:
    periods = {row.id: row.period_end for row in db.scalars(select(BankReportingPeriod))}
    values: dict[str, tuple[str, ...]] = {}
    for name, table in Base.metadata.tables.items():
        if name not in {
            "banks",
            "bank_reporting_periods",
            "bank_financial_facts",
            "forecast_assumption_versions",
        } and not name.startswith("param_"):
            continue
        rows = cast(Sequence[Mapping[str, object]], db.execute(select(table)).mappings().all())
        encoded: list[str] = []
        for row in rows:
            payload: dict[str, object] = {}
            for key, value in row.items():
                if key in {"id", "created_at", "updated_at"}:
                    continue
                payload[key] = (
                    periods[value]
                    if key == "reporting_period_id" and isinstance(value, UUID)
                    else value
                )
            encoded.append(json.dumps(payload, sort_keys=True, default=str))
        values[name] = tuple(sorted(encoded))
    return values


def test_preset_matches_a_fresh_real_materialization(db_session: Session) -> None:
    before = _book_values(db_session)
    old_period_ids = set(db_session.scalars(select(BankReportingPeriod.id)))
    summary = materialize_canonical_test_book(db_session)
    db_session.commit()
    assert summary.bank_id == SAMPLE_BANK_ID
    assert old_period_ids.isdisjoint(db_session.scalars(select(BankReportingPeriod.id)))
    assert _book_values(db_session) == before


def test_only_the_first_request_reuses_the_book(db_session: Session) -> None:
    original_ids = set(db_session.scalars(select(BankReportingPeriod.id)))
    _ = canonical_test_book(db_session)
    assert set(db_session.scalars(select(BankReportingPeriod.id))) == original_ids
    with get_sessionmaker()() as another_session:
        _ = canonical_test_book(another_session)
        another_session.commit()
        assert original_ids.isdisjoint(another_session.scalars(select(BankReportingPeriod.id)))


def test_api_and_direct_sessions_use_the_same_outer_transaction(
    db_client: TestClient, db_session: Session
) -> None:
    from app.api import deps  # noqa: PLC0415
    from app.identity.api import auth  # noqa: PLC0415

    _ = db_client
    for factory in (get_sessionmaker, deps.get_sessionmaker, auth.get_sessionmaker):
        with factory()() as session:
            assert session is not db_session
            assert session.get_bind() is db_session.get_bind()
            assert session.join_transaction_mode == "create_savepoint"


def test_fresh_transactions_do_not_keep_orm_objects(
    canonical_book_database: CanonicalBookDatabase,
) -> None:
    with (
        pytest.MonkeyPatch.context() as patch,
        canonical_book_transaction(canonical_book_database, patch) as maker,
        maker() as first,
    ):
        bank = first.get(Bank, SAMPLE_BANK_ID)
        assert bank is not None
        first_connection = first.get_bind()
        bank.name = "Committed by a service"
        first.commit()
    with (
        pytest.MonkeyPatch.context() as patch,
        canonical_book_transaction(canonical_book_database, patch) as maker,
        maker() as second,
    ):
        restored = second.get(Bank, SAMPLE_BANK_ID)
        assert restored is not None
        assert restored is not bank
        assert restored.name == "Sample Bank Ltd"
        assert second.get_bind() is not first_connection
