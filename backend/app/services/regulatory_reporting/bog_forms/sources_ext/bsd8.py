"""BSD8 resolvers — advances by BoG classification bucket + the 50-largest annexure.

FORM BSD8 (Advances Subject to Adverse Classification) asks for the loan book
in the Guide's five categories — Current / OLEM / Substandard / Doubtful /
Loss — as a monthly MOVEMENT schedule (previous balance + new advances +
interest + revaluation − recoveries − write-offs ± reclassification = current
balance), then the cumulative FX share, revaluation gains, interest in
suspense, allowable security and provisions per bucket, and a per-customer
annexure of the 50 largest adversely classified advances.

Everything here AGGREGATES existing canonical state — LOAN position snapshots,
their counterparties/products and documented attribute conventions — it never
derives a classification by heuristics:

* **Classification** (the column). Per LOAN position, in this order:

  1. the ``bog_classification`` attribute when the source states it (snapshot
     attributes overlaid on counterparty attributes; values ``current`` /
     ``olem`` / ``substandard`` / ``doubtful`` / ``loss``, case- and
     punctuation-insensitive; ``other loans especially mentioned`` is OLEM);
  2. otherwise the platform's ONLY classification attribute, the typed
     ``ifrs9_stage`` column, read as a PROXY at the Guide's delinquency
     backstops: stage 1 → Current (0–<30 days), stage 2 → OLEM (30–<90 days
     / watch-list — the same 30-day backstop and the same qualitative
     "undue credit risk" test), stage 3 → NON-PERFORMING (≥ 90 days), which
     the Guide splits into Substandard / Doubtful / Loss by 90/180/360 days —
     a split the stage cannot state;
  3. neither → UNCLASSIFIED.

  A bucket value is returned only when EVERY loan's membership in that bucket
  is decidable: an unclassified loan makes all five buckets ``None`` (the cell
  exports blank, ``input_required``); a stage-3-only loan makes the three NPL
  buckets ``None`` while Current/OLEM still resolve (a stage-3 loan is
  definitely neither). Nothing is guessed, nothing is silently dropped.

* **Amounts** are cedi equivalents (Guide: foreign-currency advances are
  reported converted): ``balance_ghs`` attribute when the source supplies it,
  the raw balance for base-currency loans, otherwise the platform's preferred
  FX spot at period end (raw balance if no spot) — the BSD4 convention.
* **Provisions**: Σ ``ecl_provision_ghs`` (the position-level allowance the
  capital/ECL engines consume); **interest in suspense**: Σ
  ``interest_in_suspense_ghs``; **allowable security**: Σ ``crm_collateral_ghs``
  restricted to the cash / government-stock collateral classes (Guide
  "Security": cash, government stocks, readily realisable securities, deposit
  liens). Each of these is ``None`` — input_required — when NO loan in the
  book carries the attribute (the source does not supply it), never zero.
* **Scope**: own books, current generation of snapshots (latest as-of on or
  before the cut-off per position); ``as_of="previous"`` cuts off at the day
  before the reporting period starts (row 1 "Previous balance").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.canonical import (
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
)
from app.services import jurisdictions, market_data_sources

from ..sources import ResolveContext, resolver

BUCKETS: tuple[str, ...] = ("current", "olem", "substandard", "doubtful", "loss")
NPL_BUCKETS: frozenset[str] = frozenset({"substandard", "doubtful", "loss"})
#: Stage-3 loans without an explicit BoG classification: non-performing, split unknown.
NPL_UNSPLIT = "npl"
#: Official column captions (BSD8 row 6) used for the annexure "Classification" text.
BUCKET_LABEL: dict[str, str] = {
    "current": "Current",
    "olem": "Olem",
    "substandard": "Substandard",
    "doubtful": "Doubtful",
    "loss": "Loss",
}
#: Documented attribute keys read here (docs/bog_returns/bsd8_line_map.md).
ATTR_CLASSIFICATION = "bog_classification"
ATTR_PROVISION = "ecl_provision_ghs"
ATTR_INTEREST_IN_SUSPENSE = "interest_in_suspense_ghs"
ATTR_COLLATERAL_VALUE = "crm_collateral_ghs"
ATTR_COLLATERAL_CLASS = "crm_collateral_class"
#: Guide "Security": cash and government stocks are the unambiguous
#: cash / near-cash collateral classes of the CRM haircut register.
DEFAULT_ALLOWABLE_COLLATERAL_CLASSES: tuple[str, ...] = ("CASH", "SOVEREIGN_DEBT")
#: Off-balance-sheet position types counted as a customer's OBS liability.
OBS_POSITION_TYPES: tuple[str, ...] = ("LC_GUARANTEE", "COMMITMENT_UNDRAWN")

_ZERO = Decimal("0")
_STAGE_BUCKET: dict[int, str] = {1: "current", 2: "olem", 3: NPL_UNSPLIT}
_CLASSIFICATION_ALIASES: dict[str, str] = {
    "current": "current",
    "olem": "olem",
    "otherloansespeciallymentioned": "olem",
    "especiallymentioned": "olem",
    "substandard": "substandard",
    "doubtful": "doubtful",
    "loss": "loss",
}


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def normalise_classification(value: Any) -> str | None:
    """``"Sub-standard"`` → ``"substandard"``; unknown / empty → None."""
    if value in (None, ""):
        return None
    key = "".join(ch for ch in str(value).lower() if ch.isalnum())
    return _CLASSIFICATION_ALIASES.get(key)


@dataclass(frozen=True)
class Loan:
    snapshot: CanonicalPositionSnapshot
    position: CanonicalPosition
    counterparty: CanonicalCounterparty | None
    product: CanonicalProduct | None
    amount_ghs: Decimal
    #: merged attributes: counterparty attributes overlaid by snapshot attributes
    attrs: dict[str, Any]
    #: "current" | "olem" | "substandard" | "doubtful" | "loss" | NPL_UNSPLIT | None
    bucket: str | None

    @property
    def customer_key(self) -> UUID:
        return self.counterparty.id if self.counterparty is not None else self.position.id


def bucket_of(stage: int | None, attrs: dict[str, Any]) -> str | None:
    """Explicit ``bog_classification`` wins; else the IFRS 9 stage proxy; else None."""
    explicit = normalise_classification(attrs.get(ATTR_CLASSIFICATION))
    if explicit is not None:
        return explicit
    return _STAGE_BUCKET.get(stage) if stage is not None else None


def _merged_attrs(
    snapshot: CanonicalPositionSnapshot, counterparty: CanonicalCounterparty | None
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if counterparty is not None and isinstance(counterparty.attributes, dict):
        merged.update(counterparty.attributes)
    if isinstance(snapshot.attributes, dict):
        merged.update(snapshot.attributes)
    return merged


def _spot(rc: ResolveContext, currency: str, base: str) -> Decimal | None:
    key = f"bsd8:spot:{currency}"
    if key in rc.cache:
        cached = rc.cache[key]
        return cached if isinstance(cached, Decimal) else None
    rate: Decimal | None = None
    try:
        view = market_data_sources.preferred_fx_spot(
            rc.db, rc.ctx.organization_id, rc.bank.id, currency, base, rc.period.period_end
        )
        rate = Decimal(str(view.rate)) if view is not None else None
    except Exception:  # noqa: BLE001 — no spot ⇒ raw balance (documented fallback)
        rate = None
    rc.cache[key] = rate if rate is not None else False
    return rate


def _amount_ghs(
    rc: ResolveContext,
    raw_ghs: Any,
    raw: Any,
    currency: str,
    base: str,
) -> Decimal:
    explicit = _dec(raw_ghs)
    if explicit is not None:
        return explicit
    amount = _dec(raw) or _ZERO
    if currency == base:
        return amount
    rate = _spot(rc, currency, base)
    return amount * rate if rate is not None else amount


def _cutoff(rc: ResolveContext, as_of: str) -> date:
    if as_of == "previous":
        return rc.period.period_start - timedelta(days=1)
    if as_of == "current":
        return rc.period.period_end
    msg = f"bsd8: unknown as_of {as_of!r} (expected 'current' or 'previous')"
    raise ValueError(msg)


def load_loans(rc: ResolveContext, as_of: str = "current") -> list[Loan]:
    """Current-generation LOAN snapshots (latest as-of ≤ cut-off per position)
    with position / counterparty / product, cedi equivalents and the
    classification bucket attached. Memoised per cut-off."""
    cutoff = _cutoff(rc, as_of)
    key = f"bsd8:loans:{cutoff.isoformat()}"
    cached = rc.cache.get(key)
    if isinstance(cached, list):
        return cached
    base = jurisdictions.base_currency(rc.bank)
    snap = CanonicalPositionSnapshot
    latest = (
        select(snap.position_id.label("pid"), func.max(snap.as_of_date).label("as_of"))
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.as_of_date <= cutoff,
            snap.superseded_by.is_(None),
        )
        .group_by(snap.position_id)
        .subquery()
    )
    stmt = (
        select(snap, CanonicalPosition, CanonicalCounterparty, CanonicalProduct)
        .join(latest, (latest.c.pid == snap.position_id) & (latest.c.as_of == snap.as_of_date))
        .join(CanonicalPosition, CanonicalPosition.id == snap.position_id)
        .outerjoin(
            CanonicalCounterparty,
            (CanonicalCounterparty.id == snap.counterparty_id)
            & (CanonicalCounterparty.organization_id == snap.organization_id),
        )
        .outerjoin(
            CanonicalProduct,
            (CanonicalProduct.id == snap.product_id)
            & (CanonicalProduct.organization_id == snap.organization_id),
        )
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.superseded_by.is_(None),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.position_type == "LOAN",
        )
        .order_by(snap.source_reference)
    )
    loans: list[Loan] = []
    for row in rc.db.execute(stmt).all():
        snapshot, position, counterparty, product = row[0], row[1], row[2], row[3]
        attrs = _merged_attrs(snapshot, counterparty)
        loans.append(
            Loan(
                snapshot=snapshot,
                position=position,
                counterparty=counterparty,
                product=product,
                amount_ghs=_amount_ghs(
                    rc, attrs.get("balance_ghs"), snapshot.balance, position.currency, base
                ),
                attrs=attrs,
                bucket=bucket_of(snapshot.ifrs9_stage, attrs),
            )
        )
    rc.cache[key] = loans
    return loans


def _load_obs_by_customer(rc: ResolveContext) -> dict[UUID, Decimal]:
    """Σ cedi notional of LC/guarantee + undrawn-commitment positions per
    counterparty (current generation, latest as-of ≤ period end). Memoised."""
    key = "bsd8:obs"
    cached = rc.cache.get(key)
    if isinstance(cached, dict):
        return cached
    base = jurisdictions.base_currency(rc.bank)
    snap = CanonicalPositionSnapshot
    latest = (
        select(snap.position_id.label("pid"), func.max(snap.as_of_date).label("as_of"))
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.as_of_date <= rc.period.period_end,
            snap.superseded_by.is_(None),
        )
        .group_by(snap.position_id)
        .subquery()
    )
    stmt = (
        select(snap, CanonicalPosition)
        .join(latest, (latest.c.pid == snap.position_id) & (latest.c.as_of == snap.as_of_date))
        .join(CanonicalPosition, CanonicalPosition.id == snap.position_id)
        .where(
            snap.organization_id == rc.ctx.organization_id,
            snap.bank_id == rc.bank.id,
            snap.superseded_by.is_(None),
            snap.counterparty_id.is_not(None),
            CanonicalPosition.superseded_by.is_(None),
            CanonicalPosition.position_type.in_(list(OBS_POSITION_TYPES)),
        )
    )
    totals: dict[UUID, Decimal] = {}
    for row in rc.db.execute(stmt).all():
        snapshot, position = row[0], row[1]
        attrs = snapshot.attributes if isinstance(snapshot.attributes, dict) else {}
        raw = snapshot.notional if snapshot.notional is not None else snapshot.balance
        amount = _amount_ghs(rc, attrs.get("notional_ghs"), raw, position.currency, base)
        cp = snapshot.counterparty_id
        if cp is not None:
            totals[cp] = totals.get(cp, _ZERO) + amount
    rc.cache[key] = totals
    return totals


# ---------------------------------------------------------------------------
# summary sheet: one bucket × one measure
# ---------------------------------------------------------------------------


def _in_scope(loan: Loan, params: dict[str, Any], base: str) -> bool:
    cpt = params.get("counterparty_types")
    if cpt and (loan.counterparty is None or loan.counterparty.counterparty_type not in cpt):
        return False
    currency = params.get("currency")
    if currency == "GHS" and loan.position.currency != base:
        return False
    return not (currency == "FX" and loan.position.currency == base)


def _book_carries(loans: list[Loan], attribute: str) -> bool:
    return any(loan.attrs.get(attribute) not in (None, "") for loan in loans)


def _undecidable(loans: list[Loan], classification: str) -> bool:
    """True when some loan's membership in ``classification`` cannot be stated."""
    if any(loan.bucket is None for loan in loans):
        return True
    return classification in NPL_BUCKETS and any(loan.bucket == NPL_UNSPLIT for loan in loans)


def _measure(loan: Loan, measure: str, params: dict[str, Any]) -> Decimal:  # noqa: PLR0911
    if measure == "balance":
        return loan.amount_ghs
    if measure == "provision":
        return _dec(loan.attrs.get(ATTR_PROVISION)) or _ZERO
    if measure == "interest_in_suspense":
        return _dec(loan.attrs.get(ATTR_INTEREST_IN_SUSPENSE)) or _ZERO
    if measure == "security":
        classes = {
            str(c).upper()
            for c in params.get("collateral_classes", DEFAULT_ALLOWABLE_COLLATERAL_CLASSES)
        }
        collateral_class = str(loan.attrs.get(ATTR_COLLATERAL_CLASS) or "").upper()
        if collateral_class not in classes:
            return _ZERO
        return _dec(loan.attrs.get(ATTR_COLLATERAL_VALUE)) or _ZERO
    msg = f"bsd8.bucket: unknown measure {measure!r}"
    raise ValueError(msg)


_MEASURE_ATTRIBUTE: dict[str, str] = {
    "provision": ATTR_PROVISION,
    "interest_in_suspense": ATTR_INTEREST_IN_SUSPENSE,
    "security": ATTR_COLLATERAL_VALUE,
}


@resolver("bsd8.bucket")
def _bucket(rc: ResolveContext, params: dict[str, Any]) -> Decimal | int | None:
    """One classification bucket × one measure over the LOAN book.

    params — ``classification`` (default: the cell's column key — the BSD8 line
    map names its value columns current/olem/substandard/doubtful/loss),
    ``measure`` = balance (default) | provision | interest_in_suspense |
    security | customer_count, ``as_of`` = current (default) | previous,
    optional ``counterparty_types``, ``currency`` = GHS | FX | all,
    ``collateral_classes`` (measure=security).
    """
    classification = str(params.get("classification") or rc.column).lower()
    if classification not in BUCKETS:
        msg = f"bsd8.bucket: unknown classification {classification!r} (expected {BUCKETS})"
        raise ValueError(msg)
    measure = str(params.get("measure", "balance"))
    loans = load_loans(rc, str(params.get("as_of", "current")))
    if _undecidable(loans, classification):
        return None
    attribute = _MEASURE_ATTRIBUTE.get(measure)
    if attribute is not None and not _book_carries(loans, attribute):
        return None  # the source does not supply it — input_required, never zero
    base = jurisdictions.base_currency(rc.bank)
    members = [
        loan for loan in loans if loan.bucket == classification and _in_scope(loan, params, base)
    ]
    if measure == "customer_count":
        return len({loan.customer_key for loan in members})
    return sum((_measure(loan, measure, params) for loan in members), _ZERO)


# ---------------------------------------------------------------------------
# annexure: the 50 largest adversely classified advances (one row per facility)
# ---------------------------------------------------------------------------

ANNEXURE_FIELDS: tuple[str, ...] = (
    "name",
    "branch",
    "sector",
    "facility",
    "expiry_date",
    "capital",
    "interest",
    "obs",
    "security_value",
    "security_type",
    "classification",
    "provision",
    "comments",
)


def ranked_adverse_loans(rc: ResolveContext) -> list[Loan] | None:
    """Adversely classified LOAN facilities (OLEM and worse), largest cedi
    balance first (source reference breaks ties). ``None`` when the book has
    an unclassified loan — the list cannot then be stated. Memoised."""
    key = "bsd8:annexure"
    if key in rc.cache:
        cached = rc.cache[key]
        return cached if isinstance(cached, list) else None
    loans = load_loans(rc, "current")
    if any(loan.bucket is None for loan in loans):
        rc.cache[key] = False
        return None
    adverse = [loan for loan in loans if loan.bucket not in (None, "current")]
    adverse.sort(key=lambda loan: (-loan.amount_ghs, loan.snapshot.source_reference))
    rc.cache[key] = adverse
    return adverse


def _annexure_field(  # noqa: PLR0911, PLR0912
    rc: ResolveContext, ranked: list[Loan], index: int, field_name: str
) -> Decimal | str | None:
    loan = ranked[index]
    attrs = loan.attrs
    if field_name == "name":
        return loan.counterparty.name if loan.counterparty is not None else None
    if field_name == "branch":
        branch = attrs.get("branch_id")
        return str(branch) if branch not in (None, "") else None
    if field_name == "sector":
        sector = attrs.get("sector")
        return str(sector) if sector not in (None, "") else None
    if field_name == "facility":
        if loan.product is not None:
            return loan.product.name or loan.product.product_code
        return None
    if field_name == "expiry_date":
        maturity = loan.snapshot.contractual_maturity
        return maturity.isoformat() if maturity is not None else None
    if field_name == "capital":
        return loan.amount_ghs
    if field_name == "interest":
        return None  # interest due — accruals sub-ledger; not in the canonical balance
    if field_name == "obs":
        if loan.counterparty is None:
            return None
        # a customer's OBS liability is shown once, against its first-listed facility
        first = next(i for i, other in enumerate(ranked) if other.customer_key == loan.customer_key)
        if first != index:
            return _ZERO
        return _load_obs_by_customer(rc).get(loan.counterparty.id, _ZERO)
    if field_name == "security_value":
        return _dec(attrs.get(ATTR_COLLATERAL_VALUE))
    if field_name == "security_type":
        collateral_class = attrs.get(ATTR_COLLATERAL_CLASS)
        return str(collateral_class) if collateral_class not in (None, "") else None
    if field_name == "classification":
        return BUCKET_LABEL.get(loan.bucket or "")  # NPL_UNSPLIT → None: bank must split
    if field_name == "provision":
        return _dec(attrs.get(ATTR_PROVISION))
    if field_name == "comments":
        return None
    msg = f"bsd8.annexure: unknown field {field_name!r} (expected {ANNEXURE_FIELDS})"
    raise ValueError(msg)


@resolver("bsd8.annexure")
def _annexure(rc: ResolveContext, params: dict[str, Any]) -> Decimal | str | None:
    """Row ``rank`` (1-based) of the 50-largest annexure, field = the cell's
    column key (or explicit ``field``): name · branch · sector · facility ·
    expiry_date · capital · interest · obs · security_value · security_type ·
    classification · provision · comments.

    ``None`` (input_required) when the book cannot be classified or for a
    detail the source does not carry on a listed advance; ``""`` (a mapped,
    positively EMPTY cell) past the end of the list — the platform knows there
    is no such advance, so nothing is required of the bank there."""
    rank = int(params["rank"])
    field_name = str(params.get("field") or rc.column)
    if field_name not in ANNEXURE_FIELDS:
        msg = f"bsd8.annexure: unknown field {field_name!r} (expected {ANNEXURE_FIELDS})"
        raise ValueError(msg)
    ranked = ranked_adverse_loans(rc)
    if ranked is None:
        return None
    if rank < 1 or rank > len(ranked):
        return ""
    return _annexure_field(rc, ranked, rank - 1, field_name)
