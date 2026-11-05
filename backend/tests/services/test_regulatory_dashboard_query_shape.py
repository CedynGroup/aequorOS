"""Query-shape characterization for the five detailed regulatory dashboards.

This module's other-suite cases live in
``test_regulatory_dashboard_query_shape_integration.py``.
"""

from __future__ import annotations

__all__ = ["_dashboard_statement"]


def _dashboard_statement(statement: str) -> str | None:
    normalized = " ".join(statement.lower().split())
    # Transaction and RLS plumbing differ by backend and test isolation mode.
    # Keep the cross-dialect audit count focused on application queries.
    if normalized.startswith(
        (
            "select set_config('app.organization_id',",
            "select set_config('app.bank_id',",
            "savepoint ",
            "release savepoint ",
            "rollback to savepoint ",
        )
    ):
        return None
    return normalized


def test_dashboard_query_count_ignores_postgres_rls_session_setup() -> None:
    assert (
        _dashboard_statement("SELECT set_config('app.organization_id', %(organization_id)s, true)")
        is None
    )
    assert _dashboard_statement("SELECT set_config('app.bank_id', %(bank_id)s, true)") is None
    assert _dashboard_statement("SAVEPOINT sa_savepoint_1") is None
    assert _dashboard_statement("RELEASE SAVEPOINT sa_savepoint_1") is None
    assert _dashboard_statement("ROLLBACK TO SAVEPOINT sa_savepoint_1") is None
    assert _dashboard_statement(" SELECT banks.id\nFROM banks ") == ("select banks.id from banks")
