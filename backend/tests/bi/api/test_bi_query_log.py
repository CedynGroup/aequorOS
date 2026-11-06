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
``tests/db/test_bi_query_log_postgres_integration.py``, never mocked as passing on SQLite.

This module's other-suite cases live in ``test_bi_query_log_integration.py``.
"""

from __future__ import annotations

from app.bi.schemas.query import BiQuery
from app.bi.service import query_log
from tests.bi.route_data import BALANCE_BY_BRANCH_QUERY


def test_the_digest_is_stable_and_question_specific() -> None:
    first = BiQuery(**BALANCE_BY_BRANCH_QUERY)
    again = BiQuery(**BALANCE_BY_BRANCH_QUERY)
    other = BiQuery(**{**BALANCE_BY_BRANCH_QUERY, "limit": 10})
    assert query_log.query_digest(first) == query_log.query_digest(again)
    assert query_log.query_digest(first) != query_log.query_digest(other)
    assert len(query_log.query_digest(first)) == 64
