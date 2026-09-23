"""``bi_query_log``: what every BI read records, and what it must never record.

The log is the reviewable half of the BI authorization story: it is how a
reviewer answers "who asked what, and was it served". So two things are pinned
here with equal force — that a row exists for every decision (including a
denial, which is the row a reviewer most wants), and that the row contains no
filter VALUE (S26). The query is identified by a one-way digest and by member
ids; an obligor's name reaching this table would make the audit trail a second
copy of the data it audits.

The append-only property itself is Postgres behaviour (trigger, revoked
privileges, RESTRICTIVE policies) and is proven in
``tests/db/test_bi_query_log_postgres.py``, never mocked as passing on SQLite.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, PrincipalType, SensitivityScope
from app.models import Bank
from app.models.bi import QUERY_LOG_SURFACES, BiQueryLog
from app.schemas.bi import BiQuery
from app.services.bi import query_log
from tests.api.helpers import ORG_1, USER_1, headers
from tests.api.test_bi_routes import (
    AGGREGATE_ONLY,
    AS_OF,
    BALANCE_BY_BRANCH_QUERY,
    BANK_ID,
    BASE,
    FINGERPRINT,
    OBLIGOR_QUERY,
    Grant,
    call,
    grant_only,
    impersonation_headers,
    seed_bi_mart,
)

#: A surface the vocabulary does not carry, derived so that widening
#: :data:`QUERY_LOG_SURFACES` cannot silently make the refusal test vacuous.
_UNKNOWN_SURFACE = next(
    candidate
    for candidate in ("dashboard", "subscription", "not_a_surface")
    if candidate not in QUERY_LOG_SURFACES
)

#: A query whose filter values are exactly the strings this file looks for in the
#: stored row. Both are real data in the fixture: a sector, and an obligor name.
FILTERED_QUERY: dict[str, Any] = {
    "measures": ["loans.balance_rc"],
    "dimensions": ["counterparty.name"],
    "filters": [
        {"member": "loan.sector", "op": "eq", "values": ["trade"]},
        {"member": "counterparty.name", "op": "in", "values": ["Ada Traders", "Kwesi Holdings"]},
    ],
    "time": {"as_of": AS_OF.isoformat()},
}
SECRET_VALUES = ("trade", "Ada Traders", "Kwesi Holdings")


@pytest.fixture
def mart(db_session: Session) -> Bank:
    return seed_bi_mart(db_session)


@pytest.fixture
def bi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings  # noqa: PLC0415 - cleared after the env is set

    monkeypatch.setenv("BI_ENABLED", "1")
    get_settings.cache_clear()


def rows(db: Session) -> list[BiQueryLog]:
    db.expire_all()
    return list(
        db.scalars(
            select(BiQueryLog)
            .where(BiQueryLog.organization_id == ORG_1)
            .order_by(BiQueryLog.queried_at, BiQueryLog.surface)
        )
    )


def test_a_served_query_is_recorded_with_everything_a_reviewer_needs(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    response = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert response.status_code == 200, response.text
    (row,) = rows(db_session)
    assert row.organization_id == ORG_1
    assert row.bank_id == BANK_ID
    assert row.principal_user_id == USER_1
    assert row.principal_type == PrincipalType.HUMAN.value
    assert row.surface == "query"
    assert row.decision == query_log.DECISION_ALLOWED
    assert row.denied_members == []
    assert row.row_count == 2
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.catalogue_version
    assert row.build_fingerprint == FINGERPRINT
    assert set(row.member_ids) == {"loans.balance_rc", "branch.code"}
    assert row.query_hash == query_log.query_digest(BiQuery(**BALANCE_BY_BRANCH_QUERY))


def test_the_row_carries_no_filter_value(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """S26. The query IS identified — by a digest and by member ids — so two
    different questions are distinguishable without either being readable."""
    response = call(db_client, "POST", "/query", FILTERED_QUERY)
    assert response.status_code == 200, response.text
    (row,) = rows(db_session)
    stored = {
        "member_ids": row.member_ids,
        "denied_members": row.denied_members,
        "query_hash": row.query_hash,
        "surface": row.surface,
        "decision": row.decision,
        "build_fingerprint": row.build_fingerprint,
        "catalogue_version": row.catalogue_version,
    }
    text = repr(stored)
    for secret in SECRET_VALUES:
        assert secret not in text, secret
    assert "loan.sector" in row.member_ids
    assert "counterparty.name" in row.member_ids


def test_a_denied_probe_is_recorded_as_a_denial(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """The row a reviewer is looking for: someone asked for an obligor name they
    hold no sentence for. Nothing was served, and ``row_count`` says so."""
    authv = grant_only(db_session, AGGREGATE_ONLY)
    response = call(
        db_client,
        "POST",
        "/query",
        OBLIGOR_QUERY,
        request_headers=headers(authorization_version=authv),
    )
    assert response.status_code == 403, response.text
    (row,) = rows(db_session)
    assert row.decision == query_log.DECISION_DENIED
    assert row.denied_members == ["counterparty.name"]
    assert row.row_count is None
    assert row.duration_ms is None
    assert row.build_fingerprint is None
    assert "counterparty.name" in row.member_ids


def test_a_revalidated_read_is_recorded_as_serving_nothing(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """A 304 is still a decision: the principal asked, and was told their copy
    still holds. It is logged with no row count because no rows were read."""
    first = call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY)
    assert first.status_code == 200, first.text
    again = db_client.post(
        f"{BASE}/query",
        json=BALANCE_BY_BRANCH_QUERY,
        headers={**headers(), "If-None-Match": first.headers["ETag"]},
    )
    assert again.status_code == 304
    served, revalidated = rows(db_session)
    assert served.row_count == 2
    assert revalidated.decision == query_log.DECISION_ALLOWED
    assert revalidated.row_count is None
    assert revalidated.query_hash == served.query_hash


def test_an_impersonated_session_persists_nothing(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """D-026's own justification: a credential that may not persist anything is
    refused before the log, so "skip the log" is not a code path that exists."""
    response = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=impersonation_headers(),
    )
    assert response.status_code == 403
    assert rows(db_session) == []
    catalogue = db_client.get(f"{BASE}/catalogue", headers=impersonation_headers())
    assert catalogue.status_code == 403
    assert rows(db_session) == []


def test_a_machine_key_persists_nothing(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    from tests.api.helpers import integration_key_headers  # noqa: PLC0415 - issues a real key

    response = call(
        db_client,
        "POST",
        "/query",
        BALANCE_BY_BRANCH_QUERY,
        request_headers=integration_key_headers(BANK_ID),
    )
    assert response.status_code == 401
    assert rows(db_session) == []


MALFORMED_QUERY: dict[str, Any] = {
    "measures": ["loans.nope"],
    "time": {"as_of": AS_OF.isoformat()},
}


def test_a_malformed_query_is_recorded_as_a_refusal_naming_no_member(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """Audit A6-06: the ATTEMPT is charged, not only the decision.

    An id the catalogue does not know is refused before authorization, so no
    member was denied and none is named — but the row exists, because the budget
    is counted over these rows and an unknown-member stream was otherwise the one
    unmetered way to make this plane work. The unknown id is client bytes and
    never reaches the table.
    """
    response = call(db_client, "POST", "/query", MALFORMED_QUERY)
    assert response.status_code == 422
    (row,) = rows(db_session)
    assert row.decision == query_log.DECISION_DENIED
    assert row.member_ids == []
    assert row.denied_members == []
    assert row.row_count is None
    assert row.duration_ms is None
    assert row.build_fingerprint is None
    assert row.surface == "query"
    assert "nope" not in row.query_hash
    assert "loans.nope" not in repr(
        {"member_ids": row.member_ids, "denied": row.denied_members, "hash": row.query_hash}
    )


def test_a_stream_of_malformed_queries_reaches_the_rate_limit(
    db_client: TestClient,
    db_session: Session,
    mart: Bank,
    bi_on: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verification A6-06 asks for: the cheapest way to make BI work is bounded."""
    monkeypatch.setattr(query_log, "RATE_LIMIT_MAX_QUERIES", 2)
    first = call(db_client, "POST", "/query", MALFORMED_QUERY)
    second = call(db_client, "POST", "/query", MALFORMED_QUERY)
    third = call(db_client, "POST", "/query", MALFORMED_QUERY)
    assert [first.status_code, second.status_code] == [422, 422]
    assert third.status_code == 429, third.text
    assert len(rows(db_session)) == 2


def test_each_surface_and_each_page_is_its_own_row(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    authv = grant_only(
        db_session,
        (*AGGREGATE_ONLY, Grant(ModuleScope.RISK, SensitivityScope.CONFIDENTIAL)),
    )
    request_headers = headers(authorization_version=authv)
    assert (
        call(
            db_client,
            "POST",
            "/grid",
            {"query": BALANCE_BY_BRANCH_QUERY, "start_row": 0, "end_row": 1},
            request_headers=request_headers,
        ).status_code
        == 200
    )
    assert (
        call(
            db_client,
            "POST",
            "/grid",
            {"query": BALANCE_BY_BRANCH_QUERY, "start_row": 1, "end_row": 2},
            request_headers=request_headers,
        ).status_code
        == 200
    )
    assert (
        call(
            db_client,
            "POST",
            "/explain",
            {"query": BALANCE_BY_BRANCH_QUERY, "measure": "loans.balance_rc"},
            request_headers=request_headers,
        ).status_code
        == 200
    )
    recorded = rows(db_session)
    assert [row.surface for row in recorded] == ["grid", "grid", "explain"]
    pages = [row.query_hash for row in recorded if row.surface == "grid"]
    assert len(set(pages)) == 2, "two different pages are two different questions"


def test_the_budget_is_measured_over_the_recorded_rows(
    db_client: TestClient, db_session: Session, mart: Bank, bi_on: None
) -> None:
    """D-010: the store is this table, which is why the limit holds across the
    four uvicorn workers a single deployment runs."""
    before = query_log.budget_for(db_session, organization_id=ORG_1, principal_user_id=USER_1)
    assert before.used == 0
    assert call(db_client, "POST", "/query", BALANCE_BY_BRANCH_QUERY).status_code == 200
    db_session.expire_all()
    after = query_log.budget_for(db_session, organization_id=ORG_1, principal_user_id=USER_1)
    assert after.used == 1
    assert after.limit == query_log.RATE_LIMIT_MAX_QUERIES
    assert 1 <= after.retry_after_seconds <= query_log.RATE_LIMIT_WINDOW_SECONDS


def test_the_recorder_refuses_a_surface_outside_the_tables_vocabulary(
    db_session: Session, mart: Bank
) -> None:
    """The CHECK would refuse it; failing in Python names the value.

    ``catalogue`` and ``trust`` are IN the vocabulary since audits A6-01 and
    A6-06 — the trust route is authorized and logged like any other data route,
    and the catalogue read is metered — so the value this asserts on has to be
    one the vocabulary genuinely does not carry. It is derived from
    :data:`QUERY_LOG_SURFACES` rather than written out, so widening the
    vocabulary again cannot quietly make this test vacuous.
    """
    assert _UNKNOWN_SURFACE not in QUERY_LOG_SURFACES
    entry = query_log.QueryRecord(
        organization_id=ORG_1,
        bank_id=BANK_ID,
        principal_user_id=USER_1,
        surface=_UNKNOWN_SURFACE,
        query_hash="0" * 64,
        decision=query_log.DECISION_ALLOWED,
        catalogue_version="1.0.0",
    )
    with pytest.raises(ValueError, match="surface"):
        query_log.record(db_session, entry)
    with pytest.raises(ValueError, match="decision"):
        query_log.record(
            db_session,
            query_log.QueryRecord(
                organization_id=ORG_1,
                bank_id=BANK_ID,
                principal_user_id=USER_1,
                surface="query",
                query_hash="0" * 64,
                decision="maybe",
                catalogue_version="1.0.0",
            ),
        )


def test_the_digest_is_stable_and_question_specific() -> None:
    first = BiQuery(**BALANCE_BY_BRANCH_QUERY)
    again = BiQuery(**BALANCE_BY_BRANCH_QUERY)
    other = BiQuery(**{**BALANCE_BY_BRANCH_QUERY, "limit": 10})
    assert query_log.query_digest(first) == query_log.query_digest(again)
    assert query_log.query_digest(first) != query_log.query_digest(other)
    assert len(query_log.query_digest(first)) == 64
