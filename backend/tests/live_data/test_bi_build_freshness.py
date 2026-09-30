"""BI build freshness over the REAL database (read-only; see conftest).

One question, about the LATEST built as-of date per bank: **is what is being
served the latest build, or a stale one?** A failed rebuild rolls the mart back
to the previous rows (audit A360 H2), so a latest ``bi_mart_builds`` row that is
``failed`` means readers are being served figures the bank's own book has moved
past. That is a statement about the bank's OWN data being current — nothing here
grades a BI figure against the returns the platform files; that verdict left BI
on 2026-09-29 (migration ``202609290080``).

Skips, with the reason named, only when NO BI mart has ever been built here:
``BI_MART_ENQUEUE_ENABLED`` ships off and is set in no deployment, so on a
database that has never run a build there is nothing to be stale and the suite
must say so rather than pass over an empty table.

Uses the BYPASSRLS worker URL so every tenant's marts are visible; with a
tenant-scoped role and ``LIVE_DATA_ORG_ID`` it certifies that one tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.bi import MART_BUILD_SCOPES


@dataclass(frozen=True)
class BuiltSlice:
    organization_id: str
    bank_id: str
    as_of: date

    @property
    def label(self) -> str:
        return f"{self.bank_id}@{self.as_of.isoformat()}"


@pytest.fixture(scope="module")
def built_slices(live_db: Session) -> list[BuiltSlice]:
    """The latest as-of date with a succeeded ``positions`` build, per bank."""
    rows = live_db.execute(
        text(
            """
            SELECT organization_id, bank_id, max(as_of_date) AS as_of
              FROM bi_mart_builds
             WHERE status = 'succeeded' AND scope = 'positions'
             GROUP BY organization_id, bank_id
             ORDER BY organization_id, bank_id
            """
        )
    ).all()
    if not rows:
        pytest.skip(
            "no BI mart has ever been built on this database (bi_mart_builds holds no "
            "succeeded positions build) — BI_MART_ENQUEUE_ENABLED is off in every "
            "deployment; there is nothing to be stale yet"
        )
    return [BuiltSlice(str(r.organization_id), str(r.bank_id), r.as_of) for r in rows]


def test_the_latest_build_of_every_scope_succeeded(
    live_db: Session, built_slices: list[BuiltSlice]
) -> None:
    """Finding H2's steady state: a latest build that FAILED means the mart holds
    the previous rows, and every surface is serving a book the bank has moved past."""
    stale: list[str] = []
    for built in built_slices:
        rows = live_db.execute(
            text(
                """
                SELECT DISTINCT ON (scope) scope, status, started_at, error
                  FROM bi_mart_builds
                 WHERE organization_id = :org AND bank_id = :bank AND as_of_date = :as_of
                 ORDER BY scope, started_at DESC
                """
            ),
            {"org": built.organization_id, "bank": built.bank_id, "as_of": built.as_of},
        ).all()
        assert rows, f"{built.label}: no build rows at all for a date the fixture selected"
        unknown = sorted({str(r.scope) for r in rows} - set(MART_BUILD_SCOPES))
        assert not unknown, f"{built.label}: build scopes the model does not name: {unknown}"
        stale.extend(
            f"{built.label} {row.scope}: {row.status} at {row.started_at} — {row.error!s:.160}"
            for row in rows
            if str(row.status) == "failed"
        )
    assert not stale, (
        "the latest build of these scopes failed, so the mart serves the PREVIOUS rows "
        "as if they were current:\n  " + "\n  ".join(stale)
    )
