"""Named source resolvers that feed official BoG input cells.

A :class:`~spec.LineSpec` names a resolver + params; the engine calls it once
per input cell with the cell's column context (``domestic`` / ``foreign`` /
``total`` / other), so a single line map can fill a Domestic/Foreign row from
one declaration — the resolver applies the Guide's currency rule (Domestic =
payable in cedis; Foreign = payable in a foreign currency, converted).

Resolvers read ONLY existing platform state: canonical facts (the value-based,
hashed regulatory inputs every engine consumes), canonical positions/snapshots
(granular splits by product, counterparty, maturity, currency), succeeded
regulatory runs (capital, liquidity, FX engines), and other forms' computed
cells (the "FROM BSD2" dependencies). Nothing here computes a BoG figure by a
new rule — that would be inventing a line.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
)
from app.models.regulatory import BankFinancialFact

type Column = str  # domestic | foreign | total | <sheet-specific>


@dataclass
class ResolveContext:
    db: Session
    ctx: TenantContext
    bank: Bank
    period: BankReportingPeriod
    column: Column
    #: computed values of dependency forms: {form_code: {(sheet, ref): value}}
    dependencies: dict[str, dict[tuple[str, str], Any]] = field(default_factory=dict)
    #: mutable scratch shared across one form computation (memoised queries)
    cache: dict[str, Any] = field(default_factory=dict)


type Resolver = Callable[[ResolveContext, dict[str, Any]], Decimal | float | int | str | None]

_REGISTRY: dict[str, Resolver] = {}


def resolver(name: str) -> Callable[[Resolver], Resolver]:
    def register(fn: Resolver) -> Resolver:
        if name in _REGISTRY:
            msg = f"duplicate resolver {name!r}"
            raise ValueError(msg)
        _REGISTRY[name] = fn
        return fn

    return register


def get_resolver(name: str) -> Resolver:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        msg = f"unknown source resolver {name!r} (known: {sorted(_REGISTRY)})"
        raise KeyError(msg) from exc


def registered_resolvers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# ---------------------------------------------------------------------------
# currency rule (Guide, General Notes §2)
# ---------------------------------------------------------------------------


def _currency_predicate(column: Column, currency_col: Any, base_currency: str) -> Any | None:
    """Domestic = the bank's base currency; Foreign = everything else; Total = no filter."""
    if column == "domestic":
        return currency_col == base_currency
    if column == "foreign":
        return currency_col != base_currency
    return None


# ---------------------------------------------------------------------------
# constants / pass-through
# ---------------------------------------------------------------------------


@resolver("constant")
def _constant(_rc: ResolveContext, params: dict[str, Any]) -> Any:
    """A fixed value (e.g. a statutory ratio the template expects as an input)."""
    return params.get("value")


# ---------------------------------------------------------------------------
# canonical facts (bank_facts): fact_group × category × currency → Σ amount
# ---------------------------------------------------------------------------


@resolver("facts.sum")
def _facts_sum(rc: ResolveContext, params: dict[str, Any]) -> Decimal:
    """Σ ``amount`` over facts in ``group`` whose ``category`` ∈ ``categories``
    for the reporting period, split by the column's currency rule (override
    with ``currency="all"|"GHS"|"FX"``)."""
    group = params["group"]
    categories: Iterable[str] = params.get("categories", ())
    stmt = select(func.coalesce(func.sum(BankFinancialFact.amount), 0)).where(
        BankFinancialFact.organization_id == rc.ctx.organization_id,
        BankFinancialFact.bank_id == rc.bank.id,
        BankFinancialFact.reporting_period_id == rc.period.id,
        BankFinancialFact.fact_group == group,
    )
    if categories:
        stmt = stmt.where(BankFinancialFact.category.in_(list(categories)))
    currency = params.get("currency")
    if currency == "GHS":
        stmt = stmt.where(BankFinancialFact.currency == rc.bank.currency)
    elif currency == "FX":
        stmt = stmt.where(BankFinancialFact.currency != rc.bank.currency)
    elif currency != "all":
        pred = _currency_predicate(rc.column, BankFinancialFact.currency, rc.bank.currency)
        if pred is not None:
            stmt = stmt.where(pred)
    value = rc.db.scalar(stmt)
    sign = Decimal(str(params.get("sign", 1)))
    return Decimal(value or 0) * sign


# ---------------------------------------------------------------------------
# canonical positions (current generation) with counterparty/product filters
# ---------------------------------------------------------------------------


def _attribute_equals(column: Any, key: str, value: Any) -> Any:
    """JSON attribute equality that tolerates how the value was ingested.

    Ingested attributes arrive typed (``91``, ``true``) OR as text (``"91"``,
    ``"true"``) depending on the source adapter, and JSON numbers/booleans do
    not compare equal to their text form on SQLite (json_extract is typed) nor
    booleans on Postgres — so match the typed accessor OR the text form.
    """
    text = column[key].as_string()
    if isinstance(value, bool):
        return or_(column[key].as_boolean().is_(value), text == str(value).lower())
    if isinstance(value, int):
        return or_(column[key].as_integer() == value, text == str(value))
    if isinstance(value, float):
        return or_(column[key].as_float() == value, text == str(value))
    return text == str(value)


@resolver("positions.sum")
def _positions_sum(rc: ResolveContext, params: dict[str, Any]) -> Decimal:  # noqa: PLR0912
    """Σ snapshot ``balance`` (or ``notional``) over the CURRENT generation of
    positions matching:

    - ``position_types``: e.g. ["LOAN"], ["DEPOSIT"], ["SECURITY_HOLDING"]
    - ``counterparty_types``: canonical counterparty types (bank, sovereign, …)
    - ``resident``: True/False (Guide: non-resident = foreign assets/liabilities)
    - ``regulatory_categories``: canonical product regulatory categories
    - ``product_codes``: explicit product codes
    - ``attribute_eq``: {"key": value} typed equality on snapshot.attributes (JSON)
    - ``counterparty_types_not``: exclude these counterparty types
    - ``measure``: "balance" (default) | "notional"

    Currency follows the column rule unless ``currency`` overrides. Only
    positions whose snapshot ``as_of_date`` is the latest on/before period end
    are counted (one snapshot per position).
    """
    # Amounts in CEDIS: the canonical cedi value of a position lives in
    # snapshot.attributes["balance_ghs"] (what fact_derivation / LMT use); fall
    # back to the native balance where absent. The Guide's Foreign column is
    # "converted into cedis" — never a sum of mixed native currencies.
    is_notional = params.get("measure") == "notional"
    native = (
        CanonicalPositionSnapshot.notional if is_notional else CanonicalPositionSnapshot.balance
    )
    ghs_attr = "notional_ghs" if is_notional else "balance_ghs"
    measure = func.coalesce(CanonicalPositionSnapshot.attributes[ghs_attr].as_float(), native)
    latest = (
        select(
            CanonicalPositionSnapshot.position_id.label("pid"),
            func.max(CanonicalPositionSnapshot.as_of_date).label("as_of"),
        )
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.as_of_date <= rc.period.period_end,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
        )
        .group_by(CanonicalPositionSnapshot.position_id)
        .subquery()
    )
    stmt = (
        select(func.coalesce(func.sum(measure), 0))
        .select_from(CanonicalPositionSnapshot)
        .join(
            latest,
            (latest.c.pid == CanonicalPositionSnapshot.position_id)
            & (latest.c.as_of == CanonicalPositionSnapshot.as_of_date),
        )
        .join(CanonicalPosition, CanonicalPosition.id == CanonicalPositionSnapshot.position_id)
        .where(
            CanonicalPositionSnapshot.organization_id == rc.ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == rc.bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.withdrawn_at.is_(None),
        )
    )
    if types := params.get("position_types"):
        stmt = stmt.where(CanonicalPosition.position_type.in_(list(types)))
    if (
        params.get("counterparty_types") is not None
        or params.get("resident") is not None
        or params.get("country_codes") is not None
    ):
        stmt = stmt.join(
            CanonicalCounterparty,
            CanonicalCounterparty.id == CanonicalPositionSnapshot.counterparty_id,
        )
        if cpt := params.get("counterparty_types"):
            stmt = stmt.where(CanonicalCounterparty.counterparty_type.in_(list(cpt)))
        if params.get("resident") is not None:
            stmt = stmt.where(CanonicalCounterparty.resident.is_(bool(params["resident"])))
        if countries := params.get("country_codes"):
            stmt = stmt.where(CanonicalCounterparty.country_code.in_(list(countries)))
    if params.get("regulatory_categories") or params.get("product_codes"):
        stmt = stmt.join(
            CanonicalProduct, CanonicalProduct.id == CanonicalPositionSnapshot.product_id
        )
        if cats := params.get("regulatory_categories"):
            stmt = stmt.where(CanonicalProduct.regulatory_category.in_(list(cats)))
        if codes := params.get("product_codes"):
            stmt = stmt.where(CanonicalProduct.product_code.in_(list(codes)))
    if params.get("encumbered") is not None:
        stmt = stmt.where(CanonicalPositionSnapshot.encumbered.is_(bool(params["encumbered"])))
    for key, value in (params.get("attribute_eq") or {}).items():
        stmt = stmt.where(_attribute_equals(CanonicalPositionSnapshot.attributes, key, value))
    if excluded := params.get("counterparty_types_not"):
        # exclude these counterparty types (positions without a counterparty stay)
        sub = select(CanonicalCounterparty.id).where(
            CanonicalCounterparty.counterparty_type.in_(list(excluded))
        )
        stmt = stmt.where(
            or_(
                CanonicalPositionSnapshot.counterparty_id.is_(None),
                CanonicalPositionSnapshot.counterparty_id.not_in(sub),
            )
        )
    currency = params.get("currency")
    if currency == "GHS":
        stmt = stmt.where(CanonicalPosition.currency == rc.bank.currency)
    elif currency == "FX":
        stmt = stmt.where(CanonicalPosition.currency != rc.bank.currency)
    elif currency != "all":
        pred = _currency_predicate(rc.column, CanonicalPosition.currency, rc.bank.currency)
        if pred is not None:
            stmt = stmt.where(pred)
    value = rc.db.scalar(stmt)
    sign = Decimal(str(params.get("sign", 1)))
    return Decimal(value or 0) * sign


# ---------------------------------------------------------------------------
# succeeded regulatory-run metrics (capital / liquidity / fx / … engines)
# ---------------------------------------------------------------------------


@resolver("run.metric")
def _run_metric(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """A metric from the latest SUCCEEDED run of ``module`` / ``scenario`` for
    the period: ``metrics[metric]``. None when no run exists (the cell stays
    blank and the line is flagged input_required until the engine runs)."""
    key = f"run:{params['module']}:{params.get('scenario', 'baseline')}"
    run = rc.cache.get(key)
    if run is None:
        run = rc.db.scalar(
            select(RegulatoryRun)
            .where(
                RegulatoryRun.organization_id == rc.ctx.organization_id,
                RegulatoryRun.bank_id == rc.bank.id,
                RegulatoryRun.reporting_period_id == rc.period.id,
                RegulatoryRun.module == params["module"],
                RegulatoryRun.scenario_code == params.get("scenario", "baseline"),
                RegulatoryRun.status == "succeeded",
            )
            .order_by(RegulatoryRun.created_at.desc())
            .limit(1)
        )
        rc.cache[key] = run or False
    if not run:
        return None
    metrics = run.metrics or {}
    raw = metrics.get(params["metric"])
    if raw is None:
        return None
    return Decimal(str(raw)) * Decimal(str(params.get("scale", 1)))


# ---------------------------------------------------------------------------
# cross-form dependency: another form's computed cell (the "FROM BSD2" links)
# ---------------------------------------------------------------------------


@resolver("form.cell")
def _form_cell(rc: ResolveContext, params: dict[str, Any]) -> Any:
    """``{"form": "BSD2", "sheet": "BSD2", "ref": "D68"}`` → that computed value.
    None (input_required) when the dependency form has not been computed."""
    dep = rc.dependencies.get(params["form"])
    if dep is None:
        return None
    return dep.get((params["sheet"], params["ref"]))


# ---------------------------------------------------------------------------
# reference datasets (canonical_reference_rows): the BoG data-gap registers
# (gl_mapping_bsd7, subsidiaries, tariff_schedule, capital_expenditure,
# atm_operations, remittance_flows, teller_withdrawals, interest_accruals) —
# uploaded through the app or pushed through the API, rows preserved verbatim
# under the kind. Readers take the LATEST as_of_date on/before period end.
# ---------------------------------------------------------------------------


def reference_rows(rc: ResolveContext, kind: str) -> list[dict[str, Any]]:
    """All payload rows of ``kind`` from the latest ingestion (as_of ≤ period
    end) for the bank; memoised per form computation. Empty list when the
    dataset was never ingested (callers return None → input_required).

    Reference rows are batch-scoped (no supersession chain), so "latest" is
    the latest as_of_date on/before period end AND, within it, the most
    recently ingested BATCH: a corrected re-push of a register / sub-ledger for
    the same date replaces the earlier one instead of being added to it.
    """
    key = f"refs:{kind}"
    if key in rc.cache:
        return rc.cache[key]
    scope = (
        CanonicalReferenceRow.organization_id == rc.ctx.organization_id,
        CanonicalReferenceRow.bank_id == rc.bank.id,
        CanonicalReferenceRow.dataset_kind == kind,
    )
    latest = rc.db.scalar(
        select(func.max(CanonicalReferenceRow.as_of_date)).where(
            *scope, CanonicalReferenceRow.as_of_date <= rc.period.period_end
        )
    )
    rows: list[dict[str, Any]] = []
    if latest is not None:
        latest_batch = rc.db.scalar(
            select(CanonicalReferenceRow.ingestion_batch_id)
            .where(*scope, CanonicalReferenceRow.as_of_date == latest)
            .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
            .limit(1)
        )
        for row in rc.db.scalars(
            select(CanonicalReferenceRow)
            .where(
                *scope,
                CanonicalReferenceRow.as_of_date == latest,
                CanonicalReferenceRow.ingestion_batch_id == latest_batch,
            )
            .order_by(CanonicalReferenceRow.row_index)
        ):
            payload = dict(row.payload or {})
            payload.setdefault("_as_of_date", latest.isoformat())
            payload.setdefault("_row_index", row.row_index)
            rows.append(payload)
    rc.cache[key] = rows
    return rows


def _row_matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    for field_name, wanted in filters.items():
        have = row.get(field_name)
        if isinstance(wanted, (list, tuple, set)):
            if str(have) not in {str(w) for w in wanted}:
                return False
        elif str(have).strip().lower() != str(wanted).strip().lower():
            return False
    return True


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (ArithmeticError, ValueError):
        return None


def _row_in_column_currency(rc: ResolveContext, currency: Any) -> bool:
    """Guide §2 for a reference row: Domestic = the bank's base currency (a row
    with no stated currency is a base-currency row); Foreign = any other; any
    other column key = no currency filter."""
    if rc.column not in ("domestic", "foreign"):
        return True
    text = str(currency or "").strip().upper()
    is_base = not text or text == str(rc.bank.currency).upper()
    return is_base if rc.column == "domestic" else not is_base


@resolver("refs.sum")
def _refs_sum(rc: ResolveContext, params: dict[str, Any]) -> Decimal | None:
    """Σ ``value_field`` over rows of ``kind`` matching ``filters`` (case-
    insensitive equality; a list means IN). None when the dataset was never
    ingested (input_required) — but 0 when it exists and no row matches.
    ``currency_field`` (optional) names the row field holding the ISO currency:
    when set, a Domestic/Foreign column keeps only the rows on its side of the
    Guide's currency rule (other column keys sum every currency)."""
    rows = reference_rows(rc, params["kind"])
    if not rows:
        return None
    currency_field = params.get("currency_field")
    total = Decimal(0)
    for row in rows:
        if not _row_matches(row, params.get("filters") or {}):
            continue
        if currency_field and not _row_in_column_currency(rc, row.get(str(currency_field))):
            continue
        value = _to_decimal(row.get(params["value_field"]))
        if value is not None:
            total += value
    return total * Decimal(str(params.get("sign", 1)))


@resolver("refs.count")
def _refs_count(rc: ResolveContext, params: dict[str, Any]) -> int | None:
    """Number of rows of ``kind`` matching ``filters`` (None if never ingested)."""
    rows = reference_rows(rc, params["kind"])
    if not rows:
        return None
    return sum(1 for row in rows if _row_matches(row, params.get("filters") or {}))


@resolver("refs.field")
def _refs_field(rc: ResolveContext, params: dict[str, Any]) -> Any:
    """One field of the ``rank``-th matching row (0-based ``index`` after an
    optional ``order_by`` field, descending numerically when ``desc``) — for
    per-row schedules (largest withdrawals, subsidiary rosters, tariff rows).
    None when the dataset was never ingested or the row does not exist."""
    rows = [
        r
        for r in reference_rows(rc, params["kind"])
        if _row_matches(r, params.get("filters") or {})
    ]
    if not rows:
        return None
    if order_by := params.get("order_by"):
        rows.sort(
            key=lambda r: _to_decimal(r.get(order_by)) or Decimal(0),
            reverse=bool(params.get("desc", True)),
        )
    index = int(params.get("index", 0))
    if index >= len(rows):
        return None
    value = rows[index].get(params["field"])
    if params.get("numeric", False):
        return _to_decimal(value)
    return value
