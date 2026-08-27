"""Deterministic SQL-shape contracts for the regulatory calendar.

These are count/shape budgets, not wall-clock performance tests: remote
Postgres latency varies, while one package query and at most one submission-
event query is a stable property the obligation horizon must not amplify.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import Connection, Engine, event
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage, RegulatorySubmissionEvent
from app.schemas.regulatory_reporting import (
    ReportingObligationListRead,
    ReportingObligationRead,
)
from app.services.regulatory_reporting import calendar
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    ISOLATED_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

_AS_OF = date(2026, 4, 5)
_MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)


@contextmanager
def _capture_sql(engine: Engine | Connection) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _measure[T](db: Session, operation: Callable[[], T]) -> tuple[T, list[str]]:
    db.expire_all()
    with _capture_sql(db.get_bind()) as statements:
        result = operation()
    return result, statements


def _table_selects(statements: list[str], table: str) -> list[str]:
    return [statement for statement in statements if f" from {table} " in statement]


def _submitted_package(obligation: ReportingObligationRead) -> RegulatoryPackage:
    return RegulatoryPackage(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        return_family=obligation.return_family,
        return_code=obligation.return_code,
        reporting_date=obligation.reporting_date,
        frequency=obligation.frequency,
        basis="solo",
        status="submitted",
        version=1,
        snapshot={},
        source_runs=[],
        generated_by=DEMO_USER_ID,
    )


def test_obligation_query_shape_is_constant_as_horizon_grows(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    seed = calendar.list_obligations(db_session, _MAKER, SAMPLE_BANK_ID, 3, as_of=_AS_OF)
    # Exercise the optional pending-reupload lookup with enough packages that a
    # per-package regression would be obvious. Every package gets one latest
    # submitted event carrying the downtime flag.
    lcr = next(
        item
        for item in seed.obligations
        if item.return_code == "LCR-NSFR" and item.reporting_date == date(2026, 3, 31)
    )
    selected = [lcr, *(item for item in seed.obligations if item != lcr)][:20]
    packages = [_submitted_package(item) for item in selected]
    db_session.add_all(packages)
    db_session.flush()
    db_session.add_all(
        [
            RegulatorySubmissionEvent(
                organization_id=DEMO_ORG_ID,
                package_id=package.id,
                channel="email",
                event="submitted",
                external_ref=f"EMAIL-{index}",
                detail={"pending_orass_reupload": True},
            )
            for index, package in enumerate(packages)
        ]
    )
    db_session.commit()

    short, short_sql = _measure(
        db_session,
        lambda: calendar.list_obligations(db_session, _MAKER, SAMPLE_BANK_ID, 3, as_of=_AS_OF),
    )
    long, long_sql = _measure(
        db_session,
        lambda: calendar.list_obligations(db_session, _MAKER, SAMPLE_BANK_ID, 12, as_of=_AS_OF),
    )

    assert len(long.obligations) > len(short.obligations)
    # One current-package SELECT and one batched submitted-event SELECT at
    # either horizon. The whole service path remains a small constant budget.
    for statements in (short_sql, long_sql):
        assert len(_table_selects(statements, "regulatory_packages")) == 1
        assert len(_table_selects(statements, "regulatory_submission_events")) == 1
        assert len(statements) <= 9
    assert len(short_sql) == len(long_sql)

    short_anchors, short_anchor_sql = _measure(
        db_session,
        lambda: calendar.list_return_anchors(
            db_session, _MAKER, SAMPLE_BANK_ID, "LCR-NSFR", 3, as_of=_AS_OF
        ),
    )
    long_anchors, long_anchor_sql = _measure(
        db_session,
        lambda: calendar.list_return_anchors(
            db_session, _MAKER, SAMPLE_BANK_ID, "LCR-NSFR", 12, as_of=_AS_OF
        ),
    )
    assert len(long_anchors.anchors) > len(short_anchors.anchors)
    for statements in (short_anchor_sql, long_anchor_sql):
        assert len(_table_selects(statements, "regulatory_packages")) == 1
        assert len(_table_selects(statements, "regulatory_submission_events")) == 1
        assert len(statements) <= 9
    assert len(short_anchor_sql) == len(long_anchor_sql)


def test_calendar_links_only_the_current_solo_package(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    seed: ReportingObligationListRead = calendar.list_obligations(
        db_session, _MAKER, SAMPLE_BANK_ID, 1, as_of=_AS_OF
    )
    target = next(
        item
        for item in seed.obligations
        if item.return_code == "LCR-NSFR" and item.reporting_date == date(2026, 3, 31)
    )
    solo = _submitted_package(target)
    consolidated = _submitted_package(target)
    consolidated.basis = "consolidated"
    consolidated.status = "generated"
    consolidated.version = 7
    db_session.add_all([solo, consolidated])
    db_session.commit()

    result = calendar.list_obligations(db_session, _MAKER, SAMPLE_BANK_ID, 1, as_of=_AS_OF)
    linked = next(
        item
        for item in result.obligations
        if item.return_code == target.return_code and item.reporting_date == target.reporting_date
    )
    assert linked.basis == "solo"
    assert linked.package_id == solo.id
    assert linked.package_status == "submitted"
    assert linked.package_version == 1


def test_calendar_bank_lookup_remains_tenant_isolated(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    stranger = TenantContext(organization_id=ISOLATED_ORG_ID, actor_user_id=DEMO_USER_ID)

    with pytest.raises(HTTPException) as exc_info:
        calendar.list_obligations(db_session, stranger, SAMPLE_BANK_ID, 12, as_of=_AS_OF)

    assert exc_info.value.status_code == 404
