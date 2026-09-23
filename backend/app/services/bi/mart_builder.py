"""The BI mart builder: one bank, one business date, every ``bi_*`` mart.

Entrypoints (``.ai/bi_contracts.md`` §Interfaces): :func:`refresh_bank_as_of`
(job ``bi_mart_refresh``), :func:`backfill_step` (``bi_mart_backfill``),
:func:`apply_retention` (``bi_retention``) and :func:`fingerprint_for`.

What a build IS
---------------
A **typed projection** of what the platform already holds — never a
recomputation. Position rows are current-generation snapshots run through the
pure ``app/domain/bi/extract.py``; loan grades come from
``loan_classification.classified_loans(..., record=False)`` (the same grid the
credit engine uses, read WITHOUT entering the calculation plane's parameter
ledger); engine metrics are COPIED from ``live_metrics`` and the latest
succeeded baseline ``regulatory_runs``; the monthly GL mart is computed by the
same ``app/domain/gl/pl_mapping.py`` functions BSD7 files with (D-021). The
builder reads canonical, live and regulatory tables and writes ONLY ``bi_*``
tables; it never calls ``derive_facts``.

How a build runs
----------------
1. **Fingerprint.** :func:`fingerprint_for` hashes the canonical inputs of the
   date (the current-generation, included snapshot set: count, latest
   ``ingested_at``, a stable digest of ids; the loan events dated that day; the
   fiscal-year-to-date GL rows the monthly mart reads), the live plane's
   ``computed_from_input_hash`` per module when the live plane IS at this date,
   the latest succeeded baseline run per module for the reporting period ending
   on the date, the approved classification-grid parameters, and
   ``BUILDER_VERSION`` + ``CATALOGUE_VERSION``. A succeeded ``bi_mart_builds``
   row with the same fingerprint for EVERY scope skips the build.
2. **Partitions, first and committed on their own.** On Postgres the month /
   year children a build writes into are ensured through the migration's
   definer functions BEFORE the slice transaction opens, because creating a
   child locks the parent (``partitions.py`` says why) and because rows that
   have already landed in DEFAULT block the child forever.
3. **One savepoint for every scope.** Inside ``db.begin_nested()``: delete the
   ``(bank, as_of)`` slice of each mart, stream the snapshots with
   ``yield_per`` and ORM predicates only (``is_current_generation``), extract,
   bulk-insert in chunks, roll the day's aggregates up from the inserted rows
   (additive sums, never distinct counts), write the month-end copy when
   ``as_of`` is the bank's last date with data in its month (D-014), attribute
   the day's loan events (D-018), rebuild the calendar month's GL rows, copy the
   engine metrics, Type-1-upsert the dimensions and rebuild the per-bank
   calendar, then grade the result (``reconciliation.evaluate``). A failure
   anywhere rolls the savepoint back — the marts are never half-written —
   marks every scope's build row ``failed`` with the error, commits THAT (the
   job layer rolls its session back on the way to ``failed``, so nothing else
   would survive) and re-raises for the queue to classify.
4. ``bi_mart_builds`` gets one row per scope with the fingerprint, row counts
   and timings; :class:`BuildOutcome` carries the same plus the trust badge.

Conventions the marts rely on
-----------------------------
* ``built_at`` is the build's start instant on every row it wrote.
* ``bi_fact_engine_metric.computed_at`` is NOT NULL; a sealed run's
  ``completed_at`` is nullable, so it is coalesced to ``built_at`` and the row's
  ``run_id`` says which run it quotes.
* The **live tier is the current edge only**: live rows are copied when the
  live plane's ``source_as_of_date`` equals the build's ``as_of``, and live rows
  for any OTHER date are removed on every build, so the live tier always equals
  ``live_metrics`` and never accumulates a false history (official history is
  the sealed runs).
* The **month-end fact holds one row per position per calendar month**: a
  build for the month's last date replaces the whole month's EOM slice, and a
  build for any other date removes that date's EOM rows (it is no longer the
  month-end).
* ``bi_fact_gl_monthly`` is keyed by the month's last GL date; a build for any
  date in a month rebuilds that whole calendar month, so it is the same row
  set whichever date triggered it.
* Retention (:func:`apply_retention`) names ONLY the two monthly DAILY parents
  (D-039): the month-end fact is kept forever, the loan-event fact is history
  (a write-off in 2019 is still a write-off), and ``bi_query_log`` is an audit
  trail the query path owns. On SQLite it is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, distinct, func, insert, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.bi import extract
from app.domain.bi.catalogue import CATALOGUE_VERSION
from app.domain.capital import loan_classification as classification_engine
from app.domain.gl import pl_mapping
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.domain.positions.families import LOAN_CATEGORY_MAP, loan_family
from app.models import (
    Bank,
    BankReportingPeriod,
    BiAggPositionDaily,
    BiDimBranch,
    BiDimCounterparty,
    BiDimDate,
    BiDimGlAccount,
    BiDimProduct,
    BiFactEngineMetric,
    BiFactGlMonthly,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
    BiMartBuild,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalLoanEvent,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    LiveMetric,
    Outlet,
    RegulatoryRun,
)
from app.models.bi import MART_BUILD_SCOPES, UNASSIGNED_REGION, UNMAPPED_BRANCH_NAME
from app.models.canonical import is_current_generation
from app.services import institution_types, jurisdictions, loan_classification
from app.services import regulatory_parameters as rp
from app.services.bi import partitions, reconciliation
from app.services.bi.versions import BUILDER_VERSION

logger = logging.getLogger(__name__)

#: Rows per streamed batch and per bulk INSERT.
STREAM_BATCH = 5000
INSERT_CHUNK = 5000

#: The sealed-run scenario every module names its filing run by
#: (``regulatory_capital.BASELINE_SCENARIO`` and its siblings; pinned by test).
BASELINE_SCENARIO = "baseline"

#: The reference dataset the branch dimension is keyed on and the payload keys
#: the code reads (D-019: ``business_unit_id`` / ``business_unit_name`` are
#: canonical, ``unit_id`` / ``name`` the documented aliases); ``region`` is the
#: optional declared field of D-020.
BUSINESS_UNITS_KIND = "business_units"
_UNIT_ID_KEYS = ("business_unit_id", "unit_id")
_UNIT_NAME_KEYS = ("business_unit_name", "name")
_UNIT_REGION_KEY = "region"

#: The daily parents retention may name. NEVER ``bi_fact_position_eom`` (D-039).
RETENTION_PARENTS: tuple[str, ...] = (
    BiFactPositionDaily.__tablename__,
    BiAggPositionDaily.__tablename__,
)

#: The reference datasets a build reads (the branch register and the CoA →
#: BSD7 mapping); their latest batch enters the fingerprint.
REFERENCE_KINDS: tuple[str, ...] = (BUSINESS_UNITS_KIND, pl_mapping.MAPPING_KIND)

#: Classification-grid inputs that enter the fingerprint beside the class
#: grid codes: the Notice ¶12 cure counts ``_restructure_holds`` reads.
_RESTRUCTURE_PARAM_CODES = ("restructure_cure_payments", "restructure_cure_payments_semi_annual")

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)

BuildStatus = Literal["succeeded", "skipped"]


@dataclass(frozen=True)
class BuildOutcome:
    status: BuildStatus
    fingerprint: str
    row_counts: dict[str, int]
    trust: dict[str, str]


class BankNotFoundError(LookupError):
    """The bank is not this organization's."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bank(db: Session, organization_id: str, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        raise BankNotFoundError(f"Bank {bank_id} not found for organization {organization_id}.")
    return bank


def month_bounds(day: date) -> tuple[date, date]:
    """First and last calendar day of ``day``'s month."""
    return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])


def _snapshot_scope(organization_id: str, bank_id: str) -> tuple[Any, ...]:
    """The included, current-generation snapshot population of one bank."""
    return (
        CanonicalPositionSnapshot.organization_id == organization_id,
        CanonicalPositionSnapshot.bank_id == bank_id,
        *is_current_generation(CanonicalPositionSnapshot),
        CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
    )


def _event_scope(organization_id: str, bank_id: str) -> tuple[Any, ...]:
    return (
        CanonicalLoanEvent.organization_id == organization_id,
        CanonicalLoanEvent.bank_id == bank_id,
        *is_current_generation(CanonicalLoanEvent),
        CanonicalLoanEvent.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
    )


def _gl_scope(organization_id: str, bank_id: str) -> tuple[Any, ...]:
    return (
        CanonicalGlAccount.organization_id == organization_id,
        CanonicalGlAccount.bank_id == bank_id,
        *is_current_generation(CanonicalGlAccount),
        CanonicalGlAccount.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
    )


def snapshot_dates(db: Session, organization_id: str, bank_id: str) -> list[date]:
    """Every business date the bank holds included, current-generation snapshots for."""
    return sorted(
        db.scalars(
            select(distinct(CanonicalPositionSnapshot.as_of_date)).where(
                *_snapshot_scope(organization_id, bank_id)
            )
        )
    )


def last_snapshot_date_in_month(
    db: Session, organization_id: str, bank_id: str, day: date
) -> date | None:
    """The bank's last date with data in ``day``'s calendar month (D-014)."""
    first, last = month_bounds(day)
    return db.scalar(
        select(func.max(CanonicalPositionSnapshot.as_of_date)).where(
            *_snapshot_scope(organization_id, bank_id),
            CanonicalPositionSnapshot.as_of_date >= first,
            CanonicalPositionSnapshot.as_of_date <= last,
        )
    )


def latest_reference_batch(
    db: Session, organization_id: str, bank_id: str, kind: str, as_of: date
) -> tuple[date, UUID] | None:
    """``(as_of, batch id)`` of the latest ``kind`` dataset on/before ``as_of`` —
    the same "latest as-of, then latest batch within it" rule as
    ``bog_forms.sources.reference_rows`` (a corrected re-push replaces)."""
    scope = (
        CanonicalReferenceRow.organization_id == organization_id,
        CanonicalReferenceRow.bank_id == bank_id,
        CanonicalReferenceRow.dataset_kind == kind,
    )
    latest = db.scalar(
        select(func.max(CanonicalReferenceRow.as_of_date)).where(
            *scope, CanonicalReferenceRow.as_of_date <= as_of
        )
    )
    if latest is None:
        return None
    latest_batch = db.scalar(
        select(CanonicalReferenceRow.ingestion_batch_id)
        .where(*scope, CanonicalReferenceRow.as_of_date == latest)
        .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
        .limit(1)
    )
    return (latest, latest_batch) if latest_batch is not None else None


def reference_rows(
    db: Session, organization_id: str, bank_id: str, kind: str, as_of: date
) -> list[dict[str, Any]]:
    """The payload rows of :func:`latest_reference_batch`'s dataset, in row order."""
    found = latest_reference_batch(db, organization_id, bank_id, kind, as_of)
    if found is None:
        return []
    latest, latest_batch = found
    scope = (
        CanonicalReferenceRow.organization_id == organization_id,
        CanonicalReferenceRow.bank_id == bank_id,
        CanonicalReferenceRow.dataset_kind == kind,
    )
    return [
        dict(row or {})
        for row in db.scalars(
            select(CanonicalReferenceRow.payload)
            .where(
                *scope,
                CanonicalReferenceRow.as_of_date == latest,
                CanonicalReferenceRow.ingestion_batch_id == latest_batch,
            )
            .order_by(CanonicalReferenceRow.row_index)
        )
    ]


def _insert_chunks(db: Session, model: type, rows: Sequence[dict[str, Any]]) -> int:
    for start in range(0, len(rows), INSERT_CHUNK):
        db.execute(insert(model), list(rows[start : start + INSERT_CHUNK]))
    return len(rows)


def _upsert_type1(  # noqa: PLR0913 - model, tenant keys, natural key, rows
    db: Session,
    model: type,
    *,
    organization_id: str,
    bank_id: str,
    key: tuple[str, ...],
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """Type-1 upsert on the natural key: overwrite attributes, never delete."""
    existing = {
        tuple(getattr(row, column) for column in key): row
        for row in db.scalars(
            select(model).where(model.organization_id == organization_id, model.bank_id == bank_id)
        )
    }
    written = 0
    for values in rows:
        natural = tuple(values[column] for column in key)
        current = existing.get(natural)
        if current is None:
            current = model(**values)
            db.add(current)
            existing[natural] = current
        else:
            for column, value in values.items():
                setattr(current, column, value)
        written += 1
    db.flush()
    return written


def _stable_digest(values: Iterable[Any]) -> str:
    return hashlib.sha256("\n".join(sorted(str(value) for value in values)).encode()).hexdigest()


def _row(orm_row: object) -> Any:
    """An ORM row handed to one of ``extract``'s Protocols.

    The protocols read plain attributes and every mapped column satisfies them
    at runtime; the type checker sees ``Mapped[...]`` descriptors instead and
    cannot, so the hand-off is typed away here, once.
    """
    return orm_row


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


def _generation_digest(db: Session, model: type, *predicates: Any) -> dict[str, Any]:
    """Count, latest ingestion instant and a stable digest of the row ids."""
    count, ingested = db.execute(
        select(func.count(model.id), func.max(model.ingested_at)).where(*predicates)
    ).one()
    ids = db.scalars(select(model.id).where(*predicates)).all()
    return {
        "count": int(count or 0),
        "ingested_at": ingested.isoformat() if ingested is not None else None,
        "ids": _stable_digest(ids),
    }


def _grid_parameter_codes(institution_class: str) -> tuple[str, ...]:
    return (
        *classification_engine.param_codes_for_class(institution_class),
        *_RESTRUCTURE_PARAM_CODES,
    )


def _latest_baseline_runs(
    db: Session, organization_id: str, bank_id: str, period_id: UUID
) -> list[RegulatoryRun]:
    """The newest succeeded baseline run per module for one reporting period."""
    rows = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == organization_id,
            RegulatoryRun.bank_id == bank_id,
            RegulatoryRun.reporting_period_id == period_id,
            RegulatoryRun.scenario_code == BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.module, RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    )
    latest: dict[str, RegulatoryRun] = {}
    for run in rows:
        latest.setdefault(run.module, run)
    return [latest[module] for module in sorted(latest)]


def _period_for(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> BankReportingPeriod | None:
    return db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == organization_id,
            BankReportingPeriod.bank_id == bank_id,
            BankReportingPeriod.period_end == as_of,
        )
    )


def fingerprint_for(db: Session, *, organization_id: str, bank_id: str, as_of: date) -> str:
    """Value-based fingerprint of everything a build for ``(bank, as_of)`` reads.

    See the module docstring for the inputs. Three deliberate widths: the GL
    input spans the fiscal year to the month's end (a re-pushed prior month
    moves this month's ``prior_ytd_rc``); the dimension inputs — the branch
    register, the CoA mapping and the outlet register — enter by their latest
    batch / change instant, so a re-pushed register rebuilds the dimensions;
    and the grid parameters are resolved through
    ``regulatory_parameters.seed_values`` — the ONE read path that neither
    records consumption nor logs a pending value.
    """
    bank = _bank(db, organization_id, bank_id)
    institution_class = institution_types.institution_class(db, bank)
    fy_start = pl_mapping.fiscal_year_start(as_of, pl_mapping.DEFAULT_FISCAL_YEAR_START_MONTH)
    _first, month_last = month_bounds(as_of)

    live = [
        {
            "module": row.module,
            "input_hash": row.computed_from_input_hash,
            "engine_version": row.engine_version,
            "pipeline_state": row.pipeline_state,
            "status": row.status,
        }
        for row in db.scalars(
            select(LiveMetric)
            .where(
                LiveMetric.organization_id == organization_id,
                LiveMetric.bank_id == bank_id,
                LiveMetric.source_as_of_date == as_of,
            )
            .order_by(LiveMetric.module)
        )
    ]
    period = _period_for(db, organization_id, bank_id, as_of)
    official = (
        [
            {
                "module": run.module,
                "run_id": str(run.id),
                "input_hash": run.input_hash,
                "engine_version": run.engine_version,
            }
            for run in _latest_baseline_runs(db, organization_id, bank_id, period.id)
        ]
        if period is not None
        else []
    )
    parameters = {
        code: (
            {
                "value": str(resolved.decimal),
                "unit": resolved.unit,
                "confirmation_status": resolved.confirmation_status,
            }
            if resolved is not None
            else None
        )
        for code, resolved in rp.seed_values(
            db, bank, _grid_parameter_codes(institution_class), as_of=as_of
        ).items()
    }
    references = {}
    for kind in REFERENCE_KINDS:
        found = latest_reference_batch(db, organization_id, bank_id, kind, as_of)
        references[kind] = (
            {"as_of": found[0].isoformat(), "batch": str(found[1])} if found else None
        )
    outlet_count, outlets_changed = db.execute(
        select(func.count(Outlet.id), func.max(Outlet.updated_at)).where(
            Outlet.organization_id == organization_id, Outlet.bank_id == bank_id
        )
    ).one()
    payload = {
        "builder_version": BUILDER_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "as_of": as_of.isoformat(),
        "references": references,
        "outlets": {
            "count": int(outlet_count or 0),
            "updated_at": outlets_changed.isoformat() if outlets_changed is not None else None,
        },
        "snapshots": _generation_digest(
            db,
            CanonicalPositionSnapshot,
            *_snapshot_scope(organization_id, bank_id),
            CanonicalPositionSnapshot.as_of_date == as_of,
        ),
        "events": _generation_digest(
            db,
            CanonicalLoanEvent,
            *_event_scope(organization_id, bank_id),
            CanonicalLoanEvent.event_date == as_of,
        ),
        "gl": _generation_digest(
            db,
            CanonicalGlAccount,
            *_gl_scope(organization_id, bank_id),
            CanonicalGlAccount.as_of_date >= fy_start,
            CanonicalGlAccount.as_of_date <= month_last,
        ),
        "live": live,
        "official": official,
        "parameters": parameters,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------------
# build records
# ---------------------------------------------------------------------------


def _build_rows(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> dict[str, BiMartBuild]:
    return {
        row.scope: row
        for row in db.scalars(
            select(BiMartBuild).where(
                BiMartBuild.organization_id == organization_id,
                BiMartBuild.bank_id == bank_id,
                BiMartBuild.as_of_date == as_of,
            )
        )
    }


def _already_built(
    db: Session, organization_id: str, bank_id: str, as_of: date, fingerprint: str
) -> bool:
    rows = _build_rows(db, organization_id, bank_id, as_of)
    return all(
        (row := rows.get(scope)) is not None
        and row.status == "succeeded"
        and row.fingerprint == fingerprint
        for scope in MART_BUILD_SCOPES
    )


def _mark_running(  # noqa: PLR0913 - the build identity plus its stamp
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    fingerprint: str,
    started_at: datetime,
) -> dict[str, BiMartBuild]:
    rows = _build_rows(db, organization_id, bank_id, as_of)
    for scope in MART_BUILD_SCOPES:
        row = rows.get(scope)
        if row is None:
            row = BiMartBuild(
                organization_id=organization_id, bank_id=bank_id, as_of_date=as_of, scope=scope
            )
            db.add(row)
            rows[scope] = row
        row.fingerprint = fingerprint
        row.status = "running"
        row.builder_version = BUILDER_VERSION
        row.started_at = started_at
        row.finished_at = None
        row.row_counts = {}
        row.error = None
    db.flush()
    return rows


# ---------------------------------------------------------------------------
# positions (daily fact, aggregate, month-end)
# ---------------------------------------------------------------------------

_POSITION_FIELDS = tuple(f.name for f in fields(extract.PositionFactRow))
_AGG_GRAIN = (
    "position_type",
    "product_family",
    "branch_code",
    "currency",
    "ifrs9_stage",
    "dpd_band",
    "grade",
    "deposit_account_type",
)


@dataclass
class _AggCell:
    row_count: int = 0
    balance_rc_sum: Decimal = _ZERO
    classification_exposure_rc_sum: Decimal = _ZERO
    non_performing_exposure_rc_sum: Decimal = _ZERO
    provision_required_rc_sum: Decimal = _ZERO
    provision_held_rc_sum: Decimal = _ZERO
    collateral_rc_sum: Decimal = _ZERO
    rate_x_balance_rc_sum: Decimal = _ZERO
    fx_unconverted_count: int = 0

    def add(self, row: extract.PositionFactRow) -> None:
        self.row_count += 1
        if row.balance_rc is not None:
            self.balance_rc_sum += row.balance_rc
            if row.interest_rate is not None:
                self.rate_x_balance_rc_sum += row.interest_rate * row.balance_rc
        if row.classification_exposure_rc is not None:
            self.classification_exposure_rc_sum += row.classification_exposure_rc
            if row.non_performing:
                self.non_performing_exposure_rc_sum += row.classification_exposure_rc
        if row.provision_required_rc is not None:
            self.provision_required_rc_sum += row.provision_required_rc
        if row.provision_held_rc is not None:
            self.provision_held_rc_sum += row.provision_held_rc
        if row.collateral_rc is not None:
            self.collateral_rc_sum += row.collateral_rc
        if row.fx_unconverted:
            self.fx_unconverted_count += 1


@dataclass
class _PositionPass:
    """What the position stream leaves behind for the dimensions."""

    branch_codes: set[str] = field(default_factory=set)
    product_families: dict[str, str | None] = field(default_factory=dict)
    products: dict[str, CanonicalProduct] = field(default_factory=dict)
    counterparties: dict[UUID, CanonicalCounterparty] = field(default_factory=dict)
    aggregates: dict[tuple[Any, ...], _AggCell] = field(
        default_factory=lambda: defaultdict(_AggCell)
    )
    rows: int = 0


def _stream_snapshots(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> Iterator[Any]:
    stmt = (
        select(
            CanonicalPositionSnapshot,
            CanonicalPosition,
            CanonicalCounterparty,
            CanonicalProduct,
            CanonicalGlAccount,
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .outerjoin(
            CanonicalGlAccount, CanonicalPositionSnapshot.gl_account_id == CanonicalGlAccount.id
        )
        .where(
            *_snapshot_scope(organization_id, bank_id),
            CanonicalPositionSnapshot.as_of_date == as_of,
        )
        .order_by(CanonicalPositionSnapshot.id)
        .execution_options(yield_per=STREAM_BATCH)
    )
    yield from db.execute(stmt)


def _has_loans(db: Session, organization_id: str, bank_id: str, as_of: date) -> bool:
    return (
        db.scalar(
            select(func.count(CanonicalPositionSnapshot.id))
            .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
            .where(
                *_snapshot_scope(organization_id, bank_id),
                CanonicalPositionSnapshot.as_of_date == as_of,
                CanonicalPosition.position_type == extract.LOAN,
            )
        )
        or 0
    ) > 0


def _date_slice(model: Any, organization_id: str, bank_id: str, as_of: date) -> tuple[Any, ...]:
    """The ``(bank, as_of)`` slice of a mart keyed on ``as_of_date``."""
    return (
        model.organization_id == organization_id,
        model.bank_id == bank_id,
        model.as_of_date == as_of,
    )


def _position_values(row: extract.PositionFactRow, built_at: datetime) -> dict[str, Any]:
    values = {name: getattr(row, name) for name in _POSITION_FIELDS}
    values["builder_version"] = BUILDER_VERSION
    values["built_at"] = built_at
    return values


def _build_positions(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    *,
    base_currency: str,
    built_at: datetime,
    row_counts: dict[str, int],
) -> _PositionPass:
    organization_id, bank_id = ctx.organization_id, bank.id
    is_month_end = last_snapshot_date_in_month(db, organization_id, bank_id, as_of) == as_of
    first, last = month_bounds(as_of)

    # The slice replace: this date's rows in every position mart. The month-end
    # copy is replaced for the WHOLE month when this date is its last date
    # (an earlier date's copy is no longer the month-end), and just for this
    # date otherwise (it is not the month-end any more, if it ever was).
    db.execute(
        delete(BiFactPositionDaily).where(
            *_date_slice(BiFactPositionDaily, organization_id, bank_id, as_of)
        )
    )
    db.execute(
        delete(BiAggPositionDaily).where(
            *_date_slice(BiAggPositionDaily, organization_id, bank_id, as_of)
        )
    )
    if is_month_end:
        db.execute(
            delete(BiFactPositionEom).where(
                BiFactPositionEom.organization_id == organization_id,
                BiFactPositionEom.bank_id == bank_id,
                BiFactPositionEom.as_of_date >= first,
                BiFactPositionEom.as_of_date <= last,
            )
        )
    else:
        db.execute(
            delete(BiFactPositionEom).where(
                *_date_slice(BiFactPositionEom, organization_id, bank_id, as_of)
            )
        )

    classified: Mapping[UUID, classification_engine.ClassifiedLoan] = {}
    if _has_loans(db, organization_id, bank_id, as_of):
        # record=False, never the default: a dispatch-plane read must not enter
        # a sealed run's parameter provenance (D-023).
        classified = loan_classification.classified_loans(db, ctx, bank, as_of, record=False)

    result = _PositionPass()
    pending: list[dict[str, Any]] = []
    eom_rows = 0
    for snapshot, position, counterparty, product, gl_account in _stream_snapshots(
        db, organization_id, bank_id, as_of
    ):
        row = extract.position_row(
            snapshot,
            position,
            counterparty,
            product,
            gl_account,
            base_currency=base_currency,
            classified=classified.get(snapshot.id),
        )
        pending.append(_position_values(row, built_at))
        result.rows += 1
        result.aggregates[tuple(getattr(row, column) for column in _AGG_GRAIN)].add(row)
        if row.branch_code is not None:
            result.branch_codes.add(row.branch_code)
        if row.product_code is not None:
            result.product_families.setdefault(row.product_code, row.product_family)
            if product is not None:
                result.products.setdefault(row.product_code, product)
        if counterparty is not None:
            result.counterparties.setdefault(counterparty.id, counterparty)
        if len(pending) >= INSERT_CHUNK:
            _insert_chunks(db, BiFactPositionDaily, pending)
            if is_month_end:
                eom_rows += _insert_chunks(db, BiFactPositionEom, pending)
            pending = []
    if pending:
        _insert_chunks(db, BiFactPositionDaily, pending)
        if is_month_end:
            eom_rows += _insert_chunks(db, BiFactPositionEom, pending)

    aggregate_rows = [
        {
            "as_of_date": as_of,
            "id": uuid4(),
            "organization_id": organization_id,
            "bank_id": bank_id,
            **dict(zip(_AGG_GRAIN, grain, strict=True)),
            "row_count": cell.row_count,
            "balance_rc_sum": cell.balance_rc_sum,
            "classification_exposure_rc_sum": cell.classification_exposure_rc_sum,
            "non_performing_exposure_rc_sum": cell.non_performing_exposure_rc_sum,
            "provision_required_rc_sum": cell.provision_required_rc_sum,
            "provision_held_rc_sum": cell.provision_held_rc_sum,
            "collateral_rc_sum": cell.collateral_rc_sum,
            "rate_x_balance_rc_sum": cell.rate_x_balance_rc_sum,
            "fx_unconverted_count": cell.fx_unconverted_count,
            "builder_version": BUILDER_VERSION,
            "built_at": built_at,
        }
        for grain, cell in result.aggregates.items()
    ]
    _insert_chunks(db, BiAggPositionDaily, aggregate_rows)

    row_counts["bi_fact_position_daily"] = result.rows
    row_counts["bi_agg_position_daily"] = len(aggregate_rows)
    row_counts["bi_fact_position_eom"] = eom_rows
    return result


# ---------------------------------------------------------------------------
# loan events (D-018)
# ---------------------------------------------------------------------------

_EVENT_FIELDS = tuple(f.name for f in fields(extract.LoanEventFactRow))


def _build_events(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    base_currency: str,
    built_at: datetime,
) -> int:
    db.execute(
        delete(BiFactLoanEvent).where(
            BiFactLoanEvent.organization_id == organization_id,
            BiFactLoanEvent.bank_id == bank_id,
            BiFactLoanEvent.event_date == as_of,
        )
    )
    events = db.scalars(
        select(CanonicalLoanEvent)
        .where(*_event_scope(organization_id, bank_id), CanonicalLoanEvent.event_date == as_of)
        .order_by(CanonicalLoanEvent.id)
    ).all()
    if not events:
        return 0

    # The facility an event names, by its OWN (source_system, reference) —
    # never a cross-system guess.
    references = {event.position_source_reference for event in events}
    positions = {
        (position.source_system, position.source_reference): position
        for position in db.scalars(
            select(CanonicalPosition).where(
                CanonicalPosition.organization_id == organization_id,
                CanonicalPosition.bank_id == bank_id,
                *is_current_generation(CanonicalPosition),
                CanonicalPosition.source_reference.in_(references),
            )
        )
    }
    latest: dict[UUID, tuple[Any, Any, Any]] = {}
    if positions:
        rows = db.execute(
            select(CanonicalPositionSnapshot, CanonicalCounterparty, CanonicalProduct)
            .outerjoin(
                CanonicalCounterparty,
                CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
            )
            .outerjoin(
                CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id
            )
            .where(
                *_snapshot_scope(organization_id, bank_id),
                CanonicalPositionSnapshot.position_id.in_([p.id for p in positions.values()]),
                CanonicalPositionSnapshot.as_of_date <= as_of,
            )
            .order_by(
                CanonicalPositionSnapshot.position_id, CanonicalPositionSnapshot.as_of_date.desc()
            )
        )
        for snapshot, counterparty, product in rows:
            latest.setdefault(snapshot.position_id, (snapshot, counterparty, product))

    values: list[dict[str, Any]] = []
    for event in events:
        position = positions.get((event.source_system, event.position_source_reference))
        match: extract.SnapshotMatch | None = None
        if position is not None:
            found = latest.get(position.id)
            source = (
                extract.position_row(
                    found[0],
                    _row(position),
                    found[1],
                    found[2],
                    None,
                    base_currency=base_currency,
                    classified=None,
                )
                if found is not None
                else None
            )
            match = extract.SnapshotMatch(position_id=position.id, snapshot=source)
        row = extract.loan_event_row(_row(event), snapshot_match=match, base_currency=base_currency)
        record = {name: getattr(row, name) for name in _EVENT_FIELDS}
        record["builder_version"] = BUILDER_VERSION
        record["built_at"] = built_at
        values.append(record)
    return _insert_chunks(db, BiFactLoanEvent, values)


# ---------------------------------------------------------------------------
# monthly GL (D-021)
# ---------------------------------------------------------------------------


_GL_FIELDS = tuple(f.name for f in fields(extract.GlMonthlyFactRow))


@dataclass(frozen=True)
class _GlRow:
    code: str
    as_of: date
    currency: str | None
    balance: Decimal
    account_class: str
    tag: str | None


def _gl_rows_for_fiscal_year(
    db: Session, organization_id: str, bank_id: str, fy_start: date, upper: date
) -> list[_GlRow]:
    """Every included, current-generation P&L ledger row with a balance in
    ``[fy_start, upper]`` — exactly BSD7's ``_selected_generations`` population
    before the line selection."""
    rows = db.execute(
        select(
            CanonicalGlAccount.account_code,
            CanonicalGlAccount.as_of_date,
            CanonicalGlAccount.currency,
            CanonicalGlAccount.balance,
            CanonicalGlAccount.account_class,
            CanonicalGlAccount.attributes[pl_mapping.LINE_ATTRIBUTE].as_string(),
        ).where(
            *_gl_scope(organization_id, bank_id),
            CanonicalGlAccount.account_class.in_(pl_mapping.PL_ACCOUNT_CLASSES),
            CanonicalGlAccount.balance.is_not(None),
            CanonicalGlAccount.as_of_date >= fy_start,
            CanonicalGlAccount.as_of_date <= upper,
        )
    ).all()
    return [
        _GlRow(str(code), as_of, currency, Decimal(balance), str(account_class), tag or None)
        for code, as_of, currency, balance, account_class, tag in rows
    ]


def gl_month_end(db: Session, organization_id: str, bank_id: str, day: date) -> date | None:
    """The last P&L ledger date with data in ``day``'s calendar month."""
    first, last = month_bounds(day)
    return db.scalar(
        select(func.max(CanonicalGlAccount.as_of_date)).where(
            *_gl_scope(organization_id, bank_id),
            CanonicalGlAccount.account_class.in_(pl_mapping.PL_ACCOUNT_CLASSES),
            CanonicalGlAccount.balance.is_not(None),
            CanonicalGlAccount.as_of_date >= first,
            CanonicalGlAccount.as_of_date <= last,
        )
    )


def _build_gl_monthly(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    base_currency: str,
    built_at: datetime,
) -> int:
    first, _last = month_bounds(as_of)
    db.execute(
        delete(BiFactGlMonthly).where(
            BiFactGlMonthly.organization_id == organization_id,
            BiFactGlMonthly.bank_id == bank_id,
            BiFactGlMonthly.calendar_month == first,
        )
    )
    month_end = gl_month_end(db, organization_id, bank_id, as_of)
    if month_end is None:
        return 0
    start_month = pl_mapping.DEFAULT_FISCAL_YEAR_START_MONTH
    fy_start = pl_mapping.fiscal_year_start(month_end, start_month)
    mapping = pl_mapping.coa_mapping_from_rows(
        reference_rows(db, organization_id, bank_id, pl_mapping.MAPPING_KIND, month_end)
    )
    by_account: dict[str, list[_GlRow]] = defaultdict(list)
    for row in _gl_rows_for_fiscal_year(db, organization_id, bank_id, fy_start, month_end):
        by_account[row.code].append(row)

    values: list[dict[str, Any]] = []
    for code in sorted(by_account):
        rows = by_account[code]
        current = max(rows, key=lambda row: row.as_of)
        row = extract.gl_monthly_row(
            [
                pl_mapping.Generation(item.code, item.as_of, item.currency, item.balance, "")
                for item in rows
            ],
            organization_id=organization_id,
            bank_id=bank_id,
            month_end=month_end,
            fy_start=fy_start,
            account_class=current.account_class,
            rule=pl_mapping.account_rule(code, current.tag, mapping),
            base_currency=base_currency,
        )
        if row is None:
            continue
        record = {name: getattr(row, name) for name in _GL_FIELDS}
        record["builder_version"] = BUILDER_VERSION
        record["built_at"] = built_at
        values.append(record)
    return _insert_chunks(db, BiFactGlMonthly, values)


# ---------------------------------------------------------------------------
# engine metrics (copied, never recomputed)
# ---------------------------------------------------------------------------

_ENGINE_FIELDS = tuple(f.name for f in fields(extract.EngineMetricFactRow))


def _engine_values(row: extract.EngineMetricFactRow, built_at: datetime) -> dict[str, Any]:
    values = {name: getattr(row, name) for name in _ENGINE_FIELDS}
    # The column is NOT NULL; a sealed run's ``completed_at`` may be absent.
    values["computed_at"] = row.computed_at or built_at
    values["builder_version"] = BUILDER_VERSION
    values["built_at"] = built_at
    return values


def _build_engine(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    regime: str,
    institution_class: str,
    built_at: datetime,
) -> int:
    db.execute(
        delete(BiFactEngineMetric).where(
            BiFactEngineMetric.organization_id == organization_id,
            BiFactEngineMetric.bank_id == bank_id,
            BiFactEngineMetric.as_of_date == as_of,
        )
    )
    lives = db.scalars(
        select(LiveMetric)
        .where(LiveMetric.organization_id == organization_id, LiveMetric.bank_id == bank_id)
        .order_by(LiveMetric.module)
    ).all()
    # The live tier is the current edge: remove live rows for any date the live
    # plane is no longer at, so the tier never accumulates a false history.
    live_dates = {live.source_as_of_date for live in lives}
    stale = delete(BiFactEngineMetric).where(
        BiFactEngineMetric.organization_id == organization_id,
        BiFactEngineMetric.bank_id == bank_id,
        BiFactEngineMetric.tier == "live",
    )
    if live_dates:
        stale = stale.where(BiFactEngineMetric.as_of_date.not_in(sorted(live_dates)))
    db.execute(stale)

    rows: list[extract.EngineMetricFactRow] = []
    for live in lives:
        if live.source_as_of_date == as_of:
            rows.extend(
                extract.live_metric_rows(
                    _row(live), regime=regime, institution_class=institution_class
                )
            )
    period = _period_for(db, organization_id, bank_id, as_of)
    if period is not None:
        for run in _latest_baseline_runs(db, organization_id, bank_id, period.id):
            rows.extend(
                extract.official_run_rows(
                    _row(run), as_of_date=as_of, regime=regime, institution_class=institution_class
                )
            )
    return _insert_chunks(db, BiFactEngineMetric, [_engine_values(row, built_at) for row in rows])


# ---------------------------------------------------------------------------
# dimensions (Type 1)
# ---------------------------------------------------------------------------


def _first_text(payload: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _build_dim_branch(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    branch_codes: set[str],
    built_at: datetime,
) -> int:
    outlets = {
        outlet.name.strip().casefold(): outlet
        for outlet in db.scalars(
            select(Outlet)
            .where(Outlet.organization_id == organization_id, Outlet.bank_id == bank_id)
            .order_by(Outlet.id)
        )
    }
    rows: dict[str, dict[str, Any]] = {}
    for payload in reference_rows(db, organization_id, bank_id, BUSINESS_UNITS_KIND, as_of):
        code = _first_text(payload, _UNIT_ID_KEYS)
        name = _first_text(payload, _UNIT_NAME_KEYS)
        if code is None or name is None:
            continue
        outlet = outlets.get(name.casefold())
        rows[code] = {
            "organization_id": organization_id,
            "bank_id": bank_id,
            "branch_code": code,
            "name": name,
            "region": _first_text(payload, (_UNIT_REGION_KEY,)) or UNASSIGNED_REGION,
            "outlet_id": outlet.id if outlet is not None else None,
            "outlet_type": outlet.outlet_type if outlet is not None else None,
            "status": outlet.status if outlet is not None else None,
            "mapped": True,
            "builder_version": BUILDER_VERSION,
            "built_at": built_at,
        }
    for code in sorted(branch_codes - rows.keys()):
        rows[code] = {
            "organization_id": organization_id,
            "bank_id": bank_id,
            "branch_code": code,
            "name": UNMAPPED_BRANCH_NAME,
            "region": UNASSIGNED_REGION,
            "outlet_id": None,
            "outlet_type": None,
            "status": None,
            "mapped": False,
            "builder_version": BUILDER_VERSION,
            "built_at": built_at,
        }
    return _upsert_type1(
        db,
        BiDimBranch,
        organization_id=organization_id,
        bank_id=bank_id,
        key=("branch_code",),
        rows=rows.values(),
    )


def _latest_current_rows(
    db: Session, model: type, organization_id: str, bank_id: str, as_of: date
) -> list[Any]:
    """Included, current-generation rows of ``model`` on/before ``as_of``, newest first."""
    return list(
        db.scalars(
            select(model)
            .where(
                model.organization_id == organization_id,
                model.bank_id == bank_id,
                *is_current_generation(model),
                model.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
                model.as_of_date <= as_of,
            )
            .order_by(model.as_of_date.desc(), model.id)
        )
    )


def _product_family_of(product: CanonicalProduct, observed: Mapping[str, str | None]) -> str | None:
    if product.product_code in observed:
        return observed[product.product_code]
    category = (product.regulatory_category or "").upper()
    mapped = LOAN_CATEGORY_MAP.get(category)
    return loan_family(mapped[0]) if mapped is not None else None


def _build_dim_product(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    seen: _PositionPass,
    built_at: datetime,
) -> int:
    products: dict[str, CanonicalProduct] = {}
    for product in _latest_current_rows(db, CanonicalProduct, organization_id, bank_id, as_of):
        products.setdefault(product.product_code, product)
    for code, product in seen.products.items():
        products.setdefault(code, product)
    rows = [
        {
            "organization_id": organization_id,
            "bank_id": bank_id,
            "product_code": code,
            "name": product.name,
            "product_family": _product_family_of(product, seen.product_families),
            "regulatory_category": product.regulatory_category,
            "risk_weight_code": product.risk_weight_code,
            "builder_version": BUILDER_VERSION,
            "built_at": built_at,
        }
        for code, product in sorted(products.items())
    ]
    return _upsert_type1(
        db,
        BiDimProduct,
        organization_id=organization_id,
        bank_id=bank_id,
        key=("product_code",),
        rows=rows,
    )


def _build_dim_counterparty(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    seen: _PositionPass,
    built_at: datetime,
) -> int:
    parties: dict[UUID, CanonicalCounterparty] = dict(seen.counterparties)
    for party in _latest_current_rows(db, CanonicalCounterparty, organization_id, bank_id, as_of):
        parties.setdefault(party.id, party)
    rows = [
        {
            "organization_id": organization_id,
            "bank_id": bank_id,
            "counterparty_id": party.id,
            "source_reference": party.source_reference,
            "name": party.name,
            "counterparty_type": party.counterparty_type,
            "group_reference": party.group_reference,
            "country_code": party.country_code,
            "rating": party.rating,
            "builder_version": BUILDER_VERSION,
            "built_at": built_at,
        }
        for party in sorted(parties.values(), key=lambda p: str(p.id))
    ]
    return _upsert_type1(
        db,
        BiDimCounterparty,
        organization_id=organization_id,
        bank_id=bank_id,
        key=("counterparty_id",),
        rows=rows,
    )


def _build_dim_gl_account(
    db: Session, organization_id: str, bank_id: str, as_of: date, *, built_at: datetime
) -> int:
    accounts: dict[str, CanonicalGlAccount] = {}
    by_id: dict[UUID, str] = {}
    for account in _latest_current_rows(db, CanonicalGlAccount, organization_id, bank_id, as_of):
        by_id[account.id] = account.account_code
        accounts.setdefault(account.account_code, account)
    mapping = pl_mapping.coa_mapping_from_rows(
        reference_rows(db, organization_id, bank_id, pl_mapping.MAPPING_KIND, as_of)
    )
    rows = []
    for code, account in sorted(accounts.items()):
        rule = pl_mapping.account_rule(
            code, (account.attributes or {}).get(pl_mapping.LINE_ATTRIBUTE), mapping
        )
        rows.append(
            {
                "organization_id": organization_id,
                "bank_id": bank_id,
                "account_code": code,
                "name": account.name,
                "account_class": account.account_class,
                "parent_account_code": (
                    by_id.get(account.parent_account_id) if account.parent_account_id else None
                ),
                "pl_line": rule.item if rule is not None else None,
                "builder_version": BUILDER_VERSION,
                "built_at": built_at,
            }
        )
    return _upsert_type1(
        db,
        BiDimGlAccount,
        organization_id=organization_id,
        bank_id=bank_id,
        key=("account_code",),
        rows=rows,
    )


def _build_dim_date(db: Session, organization_id: str, bank_id: str, *, built_at: datetime) -> int:
    """The bank's calendar from its first to its last date with data.

    ``is_last_in_*`` flags the LAST DATE WITH DATA in each month / quarter /
    year (D-014), which is what fixes the semi-additive grain for a bank that
    feeds last-business-day books. Fiscal fields follow BSD7's default fiscal
    year (``pl_mapping``).
    """
    db.execute(
        delete(BiDimDate).where(
            BiDimDate.organization_id == organization_id, BiDimDate.bank_id == bank_id
        )
    )
    dates = snapshot_dates(db, organization_id, bank_id)
    if not dates:
        return 0
    start_month = pl_mapping.DEFAULT_FISCAL_YEAR_START_MONTH
    has_data = set(dates)
    last_in_month: dict[tuple[int, int], date] = {}
    last_in_quarter: dict[tuple[int, int], date] = {}
    last_in_year: dict[int, date] = {}
    for day in dates:  # ascending, so the last assignment wins
        last_in_month[(day.year, day.month)] = day
        last_in_quarter[(day.year, (day.month - 1) // 3)] = day
        last_in_year[day.year] = day
    values: list[dict[str, Any]] = []
    day = dates[0]
    while day <= dates[-1]:
        quarter_month = ((day.month - 1) // 3) * 3 + 1
        values.append(
            {
                "organization_id": organization_id,
                "bank_id": bank_id,
                "date": day,
                "has_data": day in has_data,
                "is_last_in_month": last_in_month.get((day.year, day.month)) == day,
                "is_last_in_quarter": last_in_quarter.get((day.year, (day.month - 1) // 3)) == day,
                "is_last_in_year": last_in_year.get(day.year) == day,
                "calendar_month": day.replace(day=1),
                "calendar_quarter": date(day.year, quarter_month, 1),
                "calendar_year": day.year,
                "fiscal_year": pl_mapping.fiscal_year(day, start_month),
                "fiscal_quarter": pl_mapping.fiscal_quarter(day, start_month),
                "builder_version": BUILDER_VERSION,
                "built_at": built_at,
            }
        )
        day += timedelta(days=1)
    return _insert_chunks(db, BiDimDate, values)


def _build_dims(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    *,
    seen: _PositionPass,
    built_at: datetime,
    row_counts: dict[str, int],
) -> None:
    row_counts["bi_dim_branch"] = _build_dim_branch(
        db, organization_id, bank_id, as_of, branch_codes=seen.branch_codes, built_at=built_at
    )
    row_counts["bi_dim_product"] = _build_dim_product(
        db, organization_id, bank_id, as_of, seen=seen, built_at=built_at
    )
    row_counts["bi_dim_counterparty"] = _build_dim_counterparty(
        db, organization_id, bank_id, as_of, seen=seen, built_at=built_at
    )
    row_counts["bi_dim_gl_account"] = _build_dim_gl_account(
        db, organization_id, bank_id, as_of, built_at=built_at
    )
    row_counts["bi_dim_date"] = _build_dim_date(db, organization_id, bank_id, built_at=built_at)


# ---------------------------------------------------------------------------
# the entrypoints
# ---------------------------------------------------------------------------

#: Which ``row_counts`` keys each build scope reports.
_SCOPE_TABLES: dict[str, tuple[str, ...]] = {
    "positions": ("bi_fact_position_daily", "bi_agg_position_daily", "bi_fact_position_eom"),
    "events": ("bi_fact_loan_event",),
    "gl": ("bi_fact_gl_monthly",),
    "engine": ("bi_fact_engine_metric",),
    "dims": (
        "bi_dim_branch",
        "bi_dim_product",
        "bi_dim_counterparty",
        "bi_dim_gl_account",
        "bi_dim_date",
    ),
}


def _build_scopes(  # noqa: PLR0913 - one build carries its whole identity
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    *,
    built_at: datetime,
    row_counts: dict[str, int],
    timings: dict[str, float],
) -> dict[str, reconciliation.CheckResult]:
    """Every scope in order, inside the caller's savepoint; the R-check results."""
    organization_id, bank_id = ctx.organization_id, bank.id
    base_currency = jurisdictions.base_currency(bank)
    regime = institution_types.capital_regime(db, bank)
    institution_class = institution_types.institution_class(db, bank)

    clock = time.monotonic()
    seen = _build_positions(
        db, ctx, bank, as_of, base_currency=base_currency, built_at=built_at, row_counts=row_counts
    )
    timings["positions"] = time.monotonic() - clock

    clock = time.monotonic()
    row_counts["bi_fact_loan_event"] = _build_events(
        db, organization_id, bank_id, as_of, base_currency=base_currency, built_at=built_at
    )
    timings["events"] = time.monotonic() - clock

    clock = time.monotonic()
    row_counts["bi_fact_gl_monthly"] = _build_gl_monthly(
        db, organization_id, bank_id, as_of, base_currency=base_currency, built_at=built_at
    )
    timings["gl"] = time.monotonic() - clock

    clock = time.monotonic()
    row_counts["bi_fact_engine_metric"] = _build_engine(
        db,
        organization_id,
        bank_id,
        as_of,
        regime=regime,
        institution_class=institution_class,
        built_at=built_at,
    )
    timings["engine"] = time.monotonic() - clock

    clock = time.monotonic()
    _build_dims(
        db, organization_id, bank_id, as_of, seen=seen, built_at=built_at, row_counts=row_counts
    )
    timings["dims"] = time.monotonic() - clock

    results = reconciliation.evaluate(db, ctx, bank, as_of)
    reconciliation.persist(
        db,
        results,
        organization_id=organization_id,
        bank_id=bank_id,
        as_of=as_of,
        builder_version=BUILDER_VERSION,
        evaluated_at=utc_now(),
    )
    return results


def refresh_bank_as_of(
    db: Session, *, organization_id: str, bank_id: str, as_of: date, reason: str
) -> BuildOutcome:
    """(Re)build every mart for ``(bank, as_of)``; skip on an unchanged fingerprint.

    The module docstring is the contract. ``reason`` is the enqueue site's
    stated cause; ``bi_mart_builds`` carries no column for it (contract row
    set), so it is logged with the outcome rather than persisted.
    """
    bank = _bank(db, organization_id, bank_id)
    ctx = TenantContext(organization_id=organization_id)
    fingerprint = fingerprint_for(db, organization_id=organization_id, bank_id=bank_id, as_of=as_of)
    if _already_built(db, organization_id, bank_id, as_of, fingerprint):
        logger.info(
            "bi.mart_builder.skipped bank=%s as_of=%s reason=%s fingerprint=%s",
            bank_id,
            as_of,
            reason,
            fingerprint[:12],
        )
        return BuildOutcome(
            "skipped",
            fingerprint,
            {},
            reconciliation.trust_for(db, organization_id, bank_id, as_of),
        )

    # DDL first, on its own (see partitions.py for the lock and DEFAULT hazards).
    if partitions.ensure_for_build(db, as_of=as_of):
        db.commit()

    started_at = utc_now()
    build_rows = _mark_running(db, organization_id, bank_id, as_of, fingerprint, started_at)
    row_counts: dict[str, int] = {}
    timings: dict[str, float] = {}
    try:
        with db.begin_nested():
            results = _build_scopes(
                db, ctx, bank, as_of, built_at=started_at, row_counts=row_counts, timings=timings
            )
        finished_at = utc_now()
        for scope, row in build_rows.items():
            row.status = "succeeded"
            row.finished_at = finished_at
            row.row_counts = {
                **{table: row_counts.get(table, 0) for table in _SCOPE_TABLES[scope]},
                "elapsed_ms": int(timings.get(scope, 0.0) * 1000),
            }
        db.flush()
    except Exception as exc:
        finished_at = utc_now()
        error = f"{type(exc).__name__}: {exc}"[:2000]
        for row in build_rows.values():
            row.status = "failed"
            row.finished_at = finished_at
            row.error = error
        # The job layer rolls its session back on the way to ``failed``; the
        # failure record must not go with it.
        db.commit()
        logger.exception(
            "bi.mart_builder.failed bank=%s as_of=%s reason=%s", bank_id, as_of, reason
        )
        raise

    trust = reconciliation.trust_of(results)
    logger.info(
        "bi.mart_builder.succeeded bank=%s as_of=%s reason=%s rows=%s trust=%s",
        bank_id,
        as_of,
        reason,
        row_counts,
        trust.get("overall"),
    )
    return BuildOutcome("succeeded", fingerprint, dict(row_counts), trust)


def backfill_step(  # noqa: PLR0913 - the contract's signature
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    cursor: date,
    until: date,
    budget_seconds: int,
) -> date | None:
    """Build the bank's snapshot dates from ``cursor`` DOWN to ``until`` (inclusive)
    until the time budget is spent; the next unprocessed date, or ``None`` when
    every date down to ``until`` is built.

    Each date's build is committed on its own, so a hop that dies mid-way has
    lost nothing but the date it was on — and that date's fingerprint makes the
    retry a skip for every date already built.
    """
    _bank(db, organization_id, bank_id)
    dates = [
        day
        for day in reversed(snapshot_dates(db, organization_id, bank_id))
        if until <= day <= cursor
    ]
    started = time.monotonic()
    for index, day in enumerate(dates):
        refresh_bank_as_of(
            db, organization_id=organization_id, bank_id=bank_id, as_of=day, reason="backfill"
        )
        db.commit()
        if time.monotonic() - started >= budget_seconds:
            remaining = dates[index + 1 :]
            return remaining[0] if remaining else None
    return None


def apply_retention(
    db: Session, *, retention_days: int, today: date | None = None
) -> Sequence[str]:
    """Drop month children of the two DAILY parents older than ``retention_days``.

    A child is dropped when its whole month ends before ``today − retention_days``.
    Only :data:`RETENTION_PARENTS` are named — never the month-end fact (D-039),
    the loan-event fact or the query log — and only through the migration's
    definer function. No-op on SQLite. Returns the dropped children's names.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive.")
    if not partitions.is_postgres(db):
        return ()
    cutoff = (today or utc_now().date()) - timedelta(days=retention_days)
    dropped: list[str] = []
    for parent in RETENTION_PARENTS:
        for child, month in partitions.month_children(db, parent):
            _first, last = month_bounds(month)
            if last < cutoff and partitions.drop_month_partition(db, parent, month):
                dropped.append(child)
    return tuple(dropped)


__all__ = [
    "BASELINE_SCENARIO",
    "BUILDER_VERSION",
    "RETENTION_PARENTS",
    "BankNotFoundError",
    "BuildOutcome",
    "apply_retention",
    "backfill_step",
    "fingerprint_for",
    "gl_month_end",
    "last_snapshot_date_in_month",
    "latest_reference_batch",
    "month_bounds",
    "reference_rows",
    "refresh_bank_as_of",
    "snapshot_dates",
]
