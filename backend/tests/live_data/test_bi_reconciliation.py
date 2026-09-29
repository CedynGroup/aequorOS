"""BI reconciliation over the REAL database (read-only; see conftest).

Named in ``docs/bi.md`` §Verification ("an opt-in read-only
``tests/live_data/test_bi_reconciliation.py`` runs against the primary") and,
until audit A360-7 noticed, absent. What it certifies is the BI plane's one
promise: the marts agree with the figures the platform already files. The
hermetic suite proves that on fixture banks; this proves it on the book.

Four questions, each about the LATEST built as-of date per bank:

1. **Is what is being served the latest build, or a stale one?** Finding H2: a
   failed rebuild rolls the mart back to the previous rows and leaves the previous
   ``bi_reconciliation_results`` committed, so the trust badge stays green over
   figures the platform no longer holds. The latest ``bi_mart_builds`` row per
   scope must not be ``failed``.
2. **Was every check recorded?** One ``bi_reconciliation_results`` row per storable
   check id, or the badge is computed over a partial set.
3. **Is any comparison check red?** R1–R5 compare the mart to the live engine, the
   live balance sheet, BSD7A and the canonical row count within named tolerances;
   red means the served figure differs from the filed one.
4. **Does re-evaluating the checks NOW agree with what the build stored?** The
   checks are re-run through the platform's own ``reconciliation.evaluate`` on
   this read-only session. A different status or figure means the canonical book
   or the live plane has moved since the mart was built and no rebuild followed —
   the served figure is stale even though the stored badge says otherwise.

Skips, with the reason named, only when NO BI mart has ever been built here:
``BI_MART_ENQUEUE_ENABLED`` ships off and is set in no deployment, so on a
database that has never run a build there is nothing to reconcile and the suite
must say so rather than pass over an empty table.

Uses the BYPASSRLS worker URL so every tenant's marts are visible; with a
tenant-scoped role and ``LIVE_DATA_ORG_ID`` it certifies that one tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank
from app.models.bi import MART_BUILD_SCOPES
from app.services.bi.reconciliation import RED, STORABLE_CHECK_IDS, CheckResult, evaluate

#: The comparison checks: mart against a figure the platform files.
COMPARISON_CHECK_IDS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5")
#: ``bi_reconciliation_results`` stores ``Numeric(28, 6)``.
_STORED_QUANTUM = Decimal("0.000001")


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
            "deployment; there is nothing to reconcile yet"
        )
    return [BuiltSlice(str(r.organization_id), str(r.bank_id), r.as_of) for r in rows]


def _stored(db: Session, built: BuiltSlice) -> dict[str, dict[str, object]]:
    rows = db.execute(
        text(
            """
            SELECT check_id, status, lhs, rhs, difference, tolerance, detail, builder_version
              FROM bi_reconciliation_results
             WHERE organization_id = :org AND bank_id = :bank AND as_of_date = :as_of
            """
        ),
        {"org": built.organization_id, "bank": built.bank_id, "as_of": built.as_of},
    ).all()
    return {
        str(row.check_id): {
            "status": str(row.status),
            "lhs": row.lhs,
            "rhs": row.rhs,
            "difference": row.difference,
            "tolerance": row.tolerance,
            "detail": row.detail,
            "builder_version": row.builder_version,
        }
        for row in rows
    }


def _q(value: Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(value).quantize(_STORED_QUANTUM)


def test_the_latest_build_of_every_scope_succeeded(
    live_db: Session, built_slices: list[BuiltSlice]
) -> None:
    """Finding H2's steady state: a latest build that FAILED means the mart holds
    the previous rows and the stored badge describes them, not the book."""
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
        "under the previous trust badge:\n  " + "\n  ".join(stale)
    )


def test_every_storable_check_is_recorded_for_the_latest_built_date(
    live_db: Session, built_slices: list[BuiltSlice]
) -> None:
    incomplete: list[str] = []
    for built in built_slices:
        stored = set(_stored(live_db, built))
        missing = sorted(set(STORABLE_CHECK_IDS) - stored)
        extra = sorted(stored - set(STORABLE_CHECK_IDS))
        if missing or extra:
            incomplete.append(f"{built.label}: missing {missing}, unexpected {extra}")
    assert not incomplete, (
        "the reconciliation record is partial for these built dates, so the trust badge "
        "is computed over fewer checks than the platform defines:\n  " + "\n  ".join(incomplete)
    )


def test_no_comparison_check_is_red_for_the_latest_built_date(
    live_db: Session, built_slices: list[BuiltSlice]
) -> None:
    """Red on R1–R5 means the mart disagrees with a figure the platform files."""
    reds: list[str] = []
    for built in built_slices:
        stored = _stored(live_db, built)
        for check_id in COMPARISON_CHECK_IDS:
            row = stored.get(check_id)
            if row is None:
                continue  # completeness is the previous test's finding
            if row["status"] == RED:
                reds.append(
                    f"{built.label} {check_id}: mart {row['lhs']} vs platform {row['rhs']} "
                    f"(difference {row['difference']}, tolerance {row['tolerance']}) "
                    f"{row['detail']!s:.200}"
                )
    assert not reds, (
        "the BI mart does not reconcile to the platform's own figures on these dates:\n  "
        + "\n  ".join(reds)
    )


def test_re_evaluating_the_comparison_checks_now_agrees_with_what_the_build_stored(
    live_db: Session, built_slices: list[BuiltSlice]
) -> None:
    """The platform's own checks, re-run on the current book against the current mart.

    A stored GREEN that re-evaluates RED (or a moved figure) means canonical data
    or the live plane changed after the mart was built and the rebuild that should
    have followed did not — the badge describes a book that no longer exists.
    ``evaluate`` reads only; the session is server-side read-only regardless.
    """
    drift: list[str] = []
    for built in built_slices:
        bank = live_db.get(Bank, built.bank_id)
        assert bank is not None, f"{built.label}: bank row not visible to this session"
        assert bank.organization_id == built.organization_id, built.label
        stored = _stored(live_db, built)
        fresh: dict[str, CheckResult] = evaluate(
            live_db, TenantContext(organization_id=built.organization_id), bank, built.as_of
        )
        live_db.rollback()  # end the read transaction; nothing to commit
        for check_id in COMPARISON_CHECK_IDS:
            was, now = stored.get(check_id), fresh.get(check_id)
            if was is None or now is None:
                continue
            if was["status"] != now.status:
                drift.append(
                    f"{built.label} {check_id}: stored {was['status']} "
                    f"(lhs {was['lhs']}, rhs {was['rhs']}) but re-evaluates {now.status} "
                    f"(lhs {now.lhs}, rhs {now.rhs}) — {now.detail!s:.160}"
                )
                continue
            for side in ("lhs", "rhs"):
                if _q(was[side]) != _q(getattr(now, side)):  # type: ignore[arg-type]
                    drift.append(
                        f"{built.label} {check_id}.{side}: stored {was[side]} but re-evaluates "
                        f"{getattr(now, side)}"
                    )
    assert not drift, (
        "the reconciliation checks no longer produce what the build stored — the book or "
        "the live plane moved after the mart was built and it was not rebuilt:\n  "
        + "\n  ".join(drift)
    )


def test_the_module_imports_the_platforms_checks_not_a_copy() -> None:
    """The point of the file: one definition of R1–R5, the platform's."""
    assert evaluate.__module__ == "app.services.bi.reconciliation"
    assert set(COMPARISON_CHECK_IDS) <= set(STORABLE_CHECK_IDS)
