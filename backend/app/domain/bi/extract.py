"""Pure mart-row extraction for the BI plane (spec: ``docs/bi.md`` §Phase 1 Marts).

Three transforms, one per mart family, each taking plain objects through
narrow Protocols so the builder can hand it ORM rows and a test can hand it a
dataclass. Nothing here opens a session, touches a model or recomputes an
engine figure.

What the caller owns
--------------------
* **Generation.** Every snapshot, position, product, counterparty and event
  passed here is CURRENT GENERATION and of an included validation status
  (``superseded_by IS NULL AND withdrawn_at IS NULL``,
  ``validation_status IN ('accepted', 'warning')``). A superseded row is never
  passed; this module cannot tell and does not check.
* **The reporting currency.** ``base_currency`` is
  ``jurisdictions.base_currency(bank)``; no default, no literal.
* **Classification.** ``classified`` is the loan's entry from
  ``loan_classification.classified_loans(..., record=False)`` — the dispatch
  plane must never let its reads reach a sealed run's parameter provenance.
* **Event attribution.** ``snapshot_match`` is the facility the event names by
  its own ``(source_system, position_source_reference)`` (D-018) and that
  facility's current-generation snapshot on or before the event date, if one
  exists. Never a cross-system guess.

The two FX rules (D-015)
------------------------
A foreign-currency position without an ingested conversion has NO reporting-
currency balance. The **derivation rule** (``fact_derivation._position_row``)
keeps it ``None`` and counts it, because the balance-sheet facts R2/R3
reconcile to exclude it; the **classification rule**
(``loan_classification._load_loan_exposures``) puts it in the book at ``0`` so
it is counted and the NPL denominator R1 reconciles to matches. The mart
carries both: ``balance_rc`` (NULL + ``fx_unconverted``) and
``classification_exposure_rc`` (``0`` for an unconverted loan, NULL for a
non-loan). A position already in the reporting currency needs no conversion —
its own balance IS the reporting-currency amount, the ONE substitution both
rules make.

Attribute keys are the ingestion contract's wire keys (``balance_ghs``,
``ecl_provision_ghs``, …; ``docs/API_INTEGRATION.md`` §3.4). They are read
as-is — the suffix is a load-bearing key, not a currency claim — and land in
``_rc`` columns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol
from uuid import UUID

from app.domain.bi.authority import (
    UNREGISTERED,
    AdvisoryDesignation,
    designation_of,
    resolve_authority,
)
from app.domain.capital.loan_classification import ClassifiedLoan
from app.domain.credit.dpd_bands import dpd_band
from app.domain.gl import pl_mapping
from app.domain.ingestion.optional_position_fields import (
    POSITION_ATTRIBUTE_TEXT_LIMITS,
    over_long_text_attributes,
)
from app.domain.irr.buckets import repricing_bucket
from app.domain.liquidity.ladder import LADDER_HORIZON_DAYS, ladder_bucket_index
from app.domain.positions.families import (
    LOAN_CATEGORY_MAP,
    PAST_DUE_CATEGORY,
    loan_family,
    unclassified_category,
)

Tier = Literal["live", "official"]
AttributionBasis = Literal["snapshot_on_or_before", "no_snapshot", "unmatched"]
MetricUnit = Literal["pct", "ccy", "ratio", "count", "text"]

_ZERO = Decimal("0")
LOAN = "LOAN"

# --- vocabularies shared with the catalogue -----------------------------------


@dataclass(frozen=True, slots=True)
class MaturityBucket:
    """One contractual-ladder bucket: a wire code, a label and its day range."""

    code: str
    label: str
    minimum_days: int
    maximum_days: int | None


def _maturity_buckets() -> tuple[MaturityBucket, ...]:
    """Derived from ``LADDER_HORIZON_DAYS`` so the ladder and the mart agree."""
    buckets: list[MaturityBucket] = []
    lower = 0
    for upper in LADDER_HORIZON_DAYS:
        if upper is None:
            code, label = f"over_{lower - 1}d", f"Over {lower - 1} days"
        elif lower == 0:
            code, label = f"0_{upper}d", f"Up to {upper} days"
        else:
            code, label = f"{lower}_{upper}d", f"{lower}–{upper} days"
        buckets.append(MaturityBucket(code, label, lower, upper))
        if upper is not None:
            lower = upper + 1
    return tuple(buckets)


#: The contractual-maturity ladder's buckets in ladder order.
MATURITY_BUCKETS: tuple[MaturityBucket, ...] = _maturity_buckets()

#: Position types that sit on the contractual ladder (``regulatory_liquidity.
#: _currency_ladders``): on- and off-balance-sheet claims and obligations with a
#: maturity. Guarantees and undrawn commitments are contingent and are not laddered.
LADDERED_POSITION_TYPES: frozenset[str] = frozenset(
    {
        "LOAN",
        "SECURITY_HOLDING",
        "CASH",
        "INTERBANK_PLACEMENT",
        "OTHER_ASSET",
        "DEPOSIT",
        "INTERBANK_BORROWING",
        "OTHER_LIABILITY",
        "DERIVATIVE",
        "FX_HEDGE",
        "INTEREST_RATE_SWAP",
    }
)
#: Deposit account types deemed demand-natured (LMTD ¶5), read from the snapshot.
DEMAND_DEPOSIT_TYPES: frozenset[str] = frozenset({"CURRENT", "CALL", "SAVINGS"})

#: BI product family per non-loan position type. Loans take
#: ``families.loan_family(exposure_category)`` — the IRR/FTP family. For the
#: other types this is a BI grouping keyed on what the snapshot states, NOT the
#: IRR placement (which depends on the behavioural-assumption register and is
#: therefore not a per-row pure function).
POSITION_FAMILIES: dict[str, str] = {
    "SECURITY_HOLDING": "securities",
    "INTERBANK_PLACEMENT": "interbank_placements",
    "INTERBANK_BORROWING": "interbank_borrowings",
    "CASH": "cash",
    "DERIVATIVE": "derivatives",
    "FX_HEDGE": "derivatives",
    "INTEREST_RATE_SWAP": "derivatives",
    "LC_GUARANTEE": "off_balance_sheet",
    "COMMITMENT_UNDRAWN": "off_balance_sheet",
    "OTHER_ASSET": "other_assets",
    "OTHER_LIABILITY": "other_liabilities",
}
#: Deposit family per ``deposit_account_type``; an unstated type is ``other_deposits``.
DEPOSIT_FAMILIES: dict[str, str] = {
    "CURRENT": "demand_deposits",
    "CALL": "demand_deposits",
    "SAVINGS": "savings_deposits",
    "FIXED": "term_deposits",
    "OTHER": "other_deposits",
}
OTHER_DEPOSITS = "other_deposits"

#: Every family a position can land in, for the catalogue's enumeration.
PRODUCT_FAMILY_LABELS: dict[str, str] = {
    "corporate_loans": "Corporate loans",
    "sme_loans": "SME loans",
    "retail_loans": "Retail loans",
    "mortgages": "Mortgages",
    "cre_loans": "Commercial real estate loans",
    "unclassified_loans": "Unclassified loans",
    "demand_deposits": "Demand deposits",
    "savings_deposits": "Savings deposits",
    "term_deposits": "Term deposits",
    "other_deposits": "Other deposits",
    "securities": "Securities",
    "interbank_placements": "Interbank placements",
    "interbank_borrowings": "Interbank borrowings",
    "cash": "Cash and balances",
    "derivatives": "Derivatives and hedges",
    "off_balance_sheet": "Guarantees and commitments",
    "other_assets": "Other assets",
    "other_liabilities": "Other liabilities",
}


# --- input protocols -----------------------------------------------------------


class SnapshotLike(Protocol):
    """A current-generation ``CanonicalPositionSnapshot`` (or a stand-in)."""

    @property
    def id(self) -> UUID: ...
    @property
    def organization_id(self) -> str: ...
    @property
    def bank_id(self) -> str: ...
    @property
    def as_of_date(self) -> date: ...
    @property
    def position_id(self) -> UUID: ...
    @property
    def counterparty_id(self) -> UUID | None: ...
    @property
    def ingestion_batch_id(self) -> UUID | None: ...
    @property
    def balance(self) -> Decimal | None: ...
    @property
    def notional(self) -> Decimal | None: ...
    @property
    def interest_rate(self) -> Decimal | None: ...
    @property
    def rate_type(self) -> str | None: ...
    @property
    def rate_index(self) -> str | None: ...
    @property
    def contractual_maturity(self) -> date | None: ...
    @property
    def next_repricing_date(self) -> date | None: ...
    @property
    def ifrs9_stage(self) -> int | None: ...
    @property
    def encumbered(self) -> bool | None: ...
    @property
    def deposit_account_type(self) -> str | None: ...
    @property
    def behavioral_maturity_months(self) -> int | None: ...
    @property
    def attributes(self) -> Mapping[str, Any] | None: ...


class PositionLike(Protocol):
    @property
    def source_system(self) -> str: ...
    @property
    def source_reference(self) -> str: ...
    @property
    def position_type(self) -> str: ...
    @property
    def currency(self) -> str: ...
    @property
    def origination_date(self) -> date | None: ...


class CounterpartyLike(Protocol):
    @property
    def counterparty_type(self) -> str | None: ...
    @property
    def group_reference(self) -> str | None: ...


class ProductLike(Protocol):
    @property
    def product_code(self) -> str: ...
    @property
    def regulatory_category(self) -> str | None: ...


class GlAccountLike(Protocol):
    @property
    def account_code(self) -> str: ...


class LoanEventLike(Protocol):
    """A current-generation ``CanonicalLoanEvent`` (or a stand-in)."""

    @property
    def id(self) -> UUID: ...
    @property
    def organization_id(self) -> str: ...
    @property
    def bank_id(self) -> str: ...
    @property
    def source_system(self) -> str: ...
    @property
    def source_reference(self) -> str: ...
    @property
    def event_type(self) -> str: ...
    @property
    def event_subtype(self) -> str | None: ...
    @property
    def event_date(self) -> date: ...
    @property
    def position_source_reference(self) -> str: ...
    @property
    def amount(self) -> Decimal: ...
    @property
    def currency(self) -> str: ...
    @property
    def amount_ghs(self) -> Decimal | None: ...


class AttributionSource(Protocol):
    """What an event inherits from the snapshot it is attributed to.

    Satisfied by a :class:`PositionFactRow` and by a built mart row alike.
    """

    @property
    def snapshot_id(self) -> UUID: ...
    @property
    def branch_code(self) -> str | None: ...
    @property
    def product_code(self) -> str | None: ...
    @property
    def product_family(self) -> str | None: ...
    @property
    def counterparty_id(self) -> UUID | None: ...
    @property
    def sector(self) -> str | None: ...


class LiveMetricLike(Protocol):
    """A ``LiveMetric`` row (or a stand-in)."""

    @property
    def organization_id(self) -> str: ...
    @property
    def bank_id(self) -> str: ...
    @property
    def module(self) -> str: ...
    @property
    def metrics(self) -> Mapping[str, Any]: ...
    @property
    def status(self) -> str: ...
    @property
    def source_as_of_date(self) -> date: ...
    @property
    def source_fact_period_id(self) -> UUID | None: ...
    @property
    def computed_from_input_hash(self) -> str | None: ...
    @property
    def engine_version(self) -> str: ...
    @property
    def pipeline_state(self) -> str: ...
    @property
    def computed_at(self) -> datetime: ...


class RunLike(Protocol):
    """A succeeded baseline ``RegulatoryRun`` (or a stand-in)."""

    @property
    def id(self) -> UUID: ...
    @property
    def organization_id(self) -> str: ...
    @property
    def bank_id(self) -> str: ...
    @property
    def module(self) -> str: ...
    @property
    def metrics(self) -> Mapping[str, Any]: ...
    @property
    def status(self) -> str: ...
    @property
    def reporting_period_id(self) -> UUID: ...
    @property
    def input_hash(self) -> str: ...
    @property
    def engine_version(self) -> str: ...
    @property
    def completed_at(self) -> datetime | None: ...


# --- output rows ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PositionFactRow:
    """One ``bi_fact_position_daily`` / ``bi_fact_position_eom`` row.

    ``builder_version`` and ``built_at`` are the builder's, stamped at write time.
    """

    organization_id: str
    bank_id: str
    as_of_date: date
    snapshot_id: UUID
    position_id: UUID
    source_system: str
    source_reference: str
    position_type: str
    currency: str
    balance_native: Decimal
    balance_rc: Decimal | None
    fx_unconverted: bool
    classification_exposure_rc: Decimal | None
    notional_rc: Decimal | None
    interest_rate: Decimal | None
    rate_type: str | None
    rate_index: str | None
    contractual_maturity: date | None
    next_repricing_date: date | None
    maturity_bucket: str | None
    repricing_bucket: str | None
    origination_date: date | None
    vintage_month: date | None
    days_past_due: int | None
    dpd_band: str | None
    ifrs9_stage: int | None
    grade: str | None
    non_performing: bool | None
    classification_basis: str | None
    provision_required_rc: Decimal | None
    provision_held_rc: Decimal | None
    interest_in_suspense_rc: Decimal | None
    collateral_rc: Decimal | None
    collateral_type: str | None
    restructured: bool | None
    deposit_account_type: str | None
    behavioral_maturity_months: Decimal | None
    encumbered: bool | None
    hqla_level: str | None
    branch_code: str | None
    officer_id: str | None
    channel: str | None
    account_status: str | None
    arrears_amount_rc: Decimal | None
    product_code: str | None
    product_family: str | None
    exposure_category: str | None
    counterparty_id: UUID | None
    counterparty_type: str | None
    counterparty_group: str | None
    sector: str | None
    employer: str | None
    gl_account_code: str | None
    ingestion_batch_id: UUID | None


@dataclass(frozen=True, slots=True)
class SnapshotMatch:
    """The facility a loan event names, resolved by the caller (D-018).

    ``position_id`` is the current-generation ``CanonicalPosition`` in the
    event's OWN source system whose ``source_reference`` equals the event's
    ``position_source_reference``. ``snapshot`` is that facility's current-
    generation snapshot on or before the event date, already extracted, or
    ``None`` when the facility had no snapshot by then.
    """

    position_id: UUID
    snapshot: AttributionSource | None


@dataclass(frozen=True, slots=True)
class LoanEventFactRow:
    """One ``bi_fact_loan_event`` row."""

    organization_id: str
    bank_id: str
    event_id: UUID
    event_date: date
    event_type: str
    event_subtype: str | None
    source_system: str
    source_reference: str
    position_source_reference: str
    position_id: UUID | None
    snapshot_id: UUID | None
    amount_native: Decimal
    currency: str
    amount_rc: Decimal | None
    fx_unconverted: bool
    attribution_basis: AttributionBasis
    branch_code: str | None
    product_code: str | None
    product_family: str | None
    counterparty_id: UUID | None
    sector: str | None


@dataclass(frozen=True, slots=True)
class EngineMetricFactRow:
    """One ``bi_fact_engine_metric`` row — a typed copy, never a recomputation."""

    organization_id: str
    bank_id: str
    as_of_date: date
    module: str
    metric_id: str
    tier: Tier
    value: Decimal | None
    unit: MetricUnit
    status: str
    regime: str
    institution_class: str
    advisory_designation: AdvisoryDesignation
    input_hash: str | None
    engine_version: str | None
    pipeline_state: str | None
    reconciliation_blocked: bool
    run_id: UUID | None
    reporting_period_id: UUID | None
    computed_at: datetime | None


# --- scalar readers -----------------------------------------------------------------


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else _ZERO


def _dec_or_none(value: Any) -> Decimal | None:
    """A Decimal, or ``None`` for absent / blank / unparseable input."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _text(value: Any) -> str | None:
    """A non-empty string verbatim (no case folding, no slugging), else ``None``."""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _bounded_text(attributes: Mapping[str, Any], key: str) -> str | None:
    """``_text`` of ``attributes[key]``, or ``None`` when it is longer than the
    mart column it is copied into (``POSITION_ATTRIBUTE_TEXT_LIMITS``).

    Never truncated (audit A360 H3): a branch code cut to 120 characters is a
    DIFFERENT branch, and a code the platform cannot carry is reported as
    absent — the row keeps its place with the column NULL, exactly like an
    unstated attribute — rather than as a near-miss. Written verbatim it would
    fail the whole tenant's build on Postgres (``value too long for type
    character varying``), which SQLite never shows. The builder counts and logs
    the keys it refused (``attribute_text_overflows``); ingestion reports the
    same values at the door (rule ``position_attribute_text_bounds``).
    """
    text = _text(attributes.get(key))
    if text is None or len(text) > POSITION_ATTRIBUTE_TEXT_LIMITS[key]:
        return None
    return text


def attribute_text_overflows(attributes: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The attribute keys of one snapshot whose text the mart columns cannot hold."""
    return tuple(key for key, _length, _limit in over_long_text_attributes(attributes or {}))


#: The scale of every ``*_rc`` money column (``Numeric(28, 6)``).
_RC_QUANTUM = Decimal("0.000001")


def arrears_amount_rc(
    stated: Decimal | None,
    *,
    in_base: bool,
    balance_native: Decimal,
    balance_rc: Decimal | None,
) -> Decimal | None:
    """The stated arrears in the reporting currency, under the position's OWN
    conversion (audit A360 R12).

    A reporting-currency position states arrears in the reporting currency
    already. A foreign-currency position states them in its own currency
    (``docs/API_INTEGRATION.md`` §3.4 gives no converted key) beside a
    ``balance`` in that currency and — when the bank converted it — a
    ``balance_ghs``. Arrears are a SLICE of that same balance at that same date,
    so ``stated × balance_rc / balance_native`` is the bank's own rate applied to
    the bank's own figure, not a rate the platform inferred from anywhere else:
    ``arrears_rc / balance_rc`` equals ``stated / balance`` EXACTLY, which is
    what makes ``loans.arrears_share_pct`` right, and the amount is off by at
    most the rounding the bank applied to ``balance_ghs`` itself. Dropping it
    instead (the previous rule) made R12 report "no loan states an arrears
    amount" for a loan that did, and understated the share by that loan's whole
    balance. An UNCONVERTED position still yields ``None``: there is no rate of
    the bank's to apply, and ``fx_unconverted`` already says why.
    """
    if stated is None:
        return None
    if in_base:
        return stated
    if balance_rc is None or balance_native == 0:
        return None
    return (stated * balance_rc / balance_native).quantize(_RC_QUANTUM)


def _flag(value: Any) -> bool | None:
    """The ingestion contract's boolean spellings; ``None`` when unstated."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def coerce_days_past_due(value: Any) -> int | None:
    """``days_past_due`` as the classification service reads it.

    Int or stringified; blank / unparseable / negative is *not stated* (None),
    never floored to current.
    """
    if value is None or value == "":
        return None
    try:
        days = int(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return days if days >= 0 else None


# --- position rows ---------------------------------------------------------------


def product_exposure_category(regulatory_category: str | None) -> str:
    """The exposure category a loan PRODUCT declares.

    A recognised regulatory category maps through ``LOAN_CATEGORY_MAP``;
    anything else is the named unclassified category (no risk weight, never
    substituted) — ``fact_derivation._classify_loans`` without the stage rule.
    """
    mapped = LOAN_CATEGORY_MAP.get((regulatory_category or "").upper())
    if mapped is None:
        return unclassified_category(regulatory_category)
    return mapped[0]


def exposure_category(*, ifrs9_stage: int | None, regulatory_category: str | None) -> str:
    """The loan's exposure category exactly as ``fact_derivation._classify_loans``.

    Stage 3 is the past-due class whatever the product says; otherwise the
    product's own category.
    """
    if ifrs9_stage == 3:
        return PAST_DUE_CATEGORY[0]
    return product_exposure_category(regulatory_category)


def product_family(
    position_type: str, *, regulatory_category: str | None, deposit_account_type: str | None
) -> str | None:
    """The BI product family for a position (see :data:`POSITION_FAMILIES`).

    A loan's family is its PRODUCT's (``families.loan_family`` over the
    product's category), deliberately without the stage-3 override that the
    exposure category carries: the family is a property of what was sold, the
    exposure class of how it is performing, and a mix chart must not move a
    mortgage into corporate loans the day it is impaired. This is the one place
    BI departs from the IRR family label of a stage-3 loan, and it is what lets
    ``bi_dim_product.product_family`` equal the row's family.
    """
    if position_type == LOAN:
        return loan_family(product_exposure_category(regulatory_category))
    if position_type == "DEPOSIT":
        key = (deposit_account_type or "").upper()
        return DEPOSIT_FAMILIES.get(key, OTHER_DEPOSITS)
    return POSITION_FAMILIES.get(position_type)


def maturity_bucket(
    position_type: str,
    *,
    contractual_maturity: date | None,
    deposit_account_type: str | None,
    as_of: date,
) -> str | None:
    """The contractual-ladder bucket code, or ``None`` for a non-laddered type."""
    if position_type not in LADDERED_POSITION_TYPES:
        return None
    on_demand = position_type == "CASH" or (
        position_type == "DEPOSIT" and (deposit_account_type or "").upper() in DEMAND_DEPOSIT_TYPES
    )
    index = ladder_bucket_index(contractual_maturity, as_of, on_demand=on_demand)
    return MATURITY_BUCKETS[index].code


def vintage_month(origination_date: date | None) -> date | None:
    """First day of the origination month."""
    return origination_date.replace(day=1) if origination_date is not None else None


def position_row(  # noqa: PLR0913 - the contract's signature: one argument per joined entity
    snapshot: SnapshotLike,
    position: PositionLike,
    counterparty: CounterpartyLike | None,
    product: ProductLike | None,
    gl_account: GlAccountLike | None,
    *,
    base_currency: str,
    classified: ClassifiedLoan | None,
) -> PositionFactRow:
    """The ``bi_fact_position_daily`` row for one current-generation snapshot.

    ``classified`` is consulted only for a LOAN; for any other type the four
    classification columns are NULL whatever is passed.
    """
    attributes: Mapping[str, Any] = snapshot.attributes or {}
    as_of = snapshot.as_of_date
    position_type = position.position_type
    is_loan = position_type == LOAN
    in_base = position.currency == base_currency

    balance_native = _dec(snapshot.balance)
    balance_rc = _dec_or_none(attributes.get("balance_ghs"))
    if balance_rc is None and in_base:
        balance_rc = balance_native
    fx_unconverted = balance_rc is None
    classification_exposure_rc: Decimal | None = None
    if is_loan:
        classification_exposure_rc = _ZERO if balance_rc is None else balance_rc
    notional_rc = _dec_or_none(attributes.get("notional_ghs"))
    if notional_rc is None and in_base:
        notional_rc = _dec_or_none(snapshot.notional)

    days_past_due = coerce_days_past_due(attributes.get("days_past_due"))
    regulatory_category = product.regulatory_category if product is not None else None
    category = (
        exposure_category(ifrs9_stage=snapshot.ifrs9_stage, regulatory_category=regulatory_category)
        if is_loan
        else None
    )
    classified_loan = classified if is_loan else None
    months = snapshot.behavioral_maturity_months

    return PositionFactRow(
        organization_id=snapshot.organization_id,
        bank_id=snapshot.bank_id,
        as_of_date=as_of,
        snapshot_id=snapshot.id,
        position_id=snapshot.position_id,
        source_system=position.source_system,
        source_reference=position.source_reference,
        position_type=position_type,
        currency=position.currency,
        balance_native=balance_native,
        balance_rc=balance_rc,
        fx_unconverted=fx_unconverted,
        classification_exposure_rc=classification_exposure_rc,
        notional_rc=notional_rc,
        interest_rate=_dec_or_none(snapshot.interest_rate),
        rate_type=snapshot.rate_type,
        rate_index=snapshot.rate_index,
        contractual_maturity=snapshot.contractual_maturity,
        next_repricing_date=snapshot.next_repricing_date,
        maturity_bucket=maturity_bucket(
            position_type,
            contractual_maturity=snapshot.contractual_maturity,
            deposit_account_type=snapshot.deposit_account_type,
            as_of=as_of,
        ),
        repricing_bucket=repricing_bucket(snapshot, as_of),
        origination_date=position.origination_date,
        vintage_month=vintage_month(position.origination_date),
        days_past_due=days_past_due,
        dpd_band=dpd_band(days_past_due),
        ifrs9_stage=snapshot.ifrs9_stage,
        grade=classified_loan.grade if classified_loan is not None else None,
        non_performing=classified_loan.non_performing if classified_loan is not None else None,
        classification_basis=(
            classified_loan.classification_basis if classified_loan is not None else None
        ),
        provision_required_rc=(
            classified_loan.provision_required_ghs if classified_loan is not None else None
        ),
        provision_held_rc=_dec_or_none(attributes.get("ecl_provision_ghs")),
        interest_in_suspense_rc=_dec_or_none(attributes.get("interest_in_suspense_ghs")),
        collateral_rc=_dec_or_none(attributes.get("crm_collateral_ghs")),
        collateral_type=(
            _bounded_text(attributes, "collateral_type")
            or _bounded_text(attributes, "crm_collateral_class")
        ),
        restructured=_flag(attributes.get("restructured")),
        deposit_account_type=snapshot.deposit_account_type,
        behavioral_maturity_months=Decimal(months) if months is not None else None,
        encumbered=snapshot.encumbered,
        hqla_level=_bounded_text(attributes, "hqla_level"),
        branch_code=_bounded_text(attributes, "branch_id"),
        officer_id=_bounded_text(attributes, "officer_id"),
        channel=_bounded_text(attributes, "channel"),
        account_status=_bounded_text(attributes, "account_status"),
        # Reporting-currency arrears under the position's OWN conversion: the
        # stated figure as-is in the reporting currency, scaled by the bank's own
        # ``balance_ghs / balance`` for a converted foreign-currency facility, and
        # NULL for an unconverted one (``arrears_amount_rc`` says why).
        arrears_amount_rc=arrears_amount_rc(
            _dec_or_none(attributes.get("arrears_amount")),
            in_base=in_base,
            balance_native=balance_native,
            balance_rc=balance_rc,
        ),
        product_code=product.product_code if product is not None else None,
        product_family=product_family(
            position_type,
            regulatory_category=regulatory_category,
            deposit_account_type=snapshot.deposit_account_type,
        ),
        exposure_category=category,
        counterparty_id=snapshot.counterparty_id,
        counterparty_type=counterparty.counterparty_type if counterparty is not None else None,
        counterparty_group=counterparty.group_reference if counterparty is not None else None,
        sector=_bounded_text(attributes, "sector") or _bounded_text(attributes, "industry"),
        employer=_bounded_text(attributes, "employer"),
        gl_account_code=gl_account.account_code if gl_account is not None else None,
        ingestion_batch_id=snapshot.ingestion_batch_id,
    )


# --- loan events -----------------------------------------------------------------


def event_amount_rc(event: LoanEventLike, *, base_currency: str) -> Decimal | None:
    """The event's reporting-unit amount (``regulatory_credit._event_amount_ghs``).

    The ingested conversion when stated; the native amount when the event is
    already in the reporting currency; otherwise ``None`` — unconverted, never
    invented.
    """
    if event.amount_ghs is not None:
        return Decimal(str(event.amount_ghs))
    if event.currency == base_currency:
        return Decimal(str(event.amount))
    return None


def loan_event_row(
    event: LoanEventLike, *, snapshot_match: SnapshotMatch | None, base_currency: str
) -> LoanEventFactRow:
    """The ``bi_fact_loan_event`` row for one current-generation event (D-018).

    ``attribution_basis``: ``unmatched`` when no facility in the event's own
    source system carries its reference (branch / product / counterparty /
    sector NULL); ``no_snapshot`` when the facility exists but had no snapshot
    on or before the event date (position known, attribution NULL);
    ``snapshot_on_or_before`` otherwise.
    """
    amount_rc = event_amount_rc(event, base_currency=base_currency)
    basis: AttributionBasis
    source: AttributionSource | None = None
    position_id: UUID | None = None
    if snapshot_match is None:
        basis = "unmatched"
    elif snapshot_match.snapshot is None:
        basis = "no_snapshot"
        position_id = snapshot_match.position_id
    else:
        basis = "snapshot_on_or_before"
        position_id = snapshot_match.position_id
        source = snapshot_match.snapshot
    return LoanEventFactRow(
        organization_id=event.organization_id,
        bank_id=event.bank_id,
        event_id=event.id,
        event_date=event.event_date,
        event_type=event.event_type,
        event_subtype=event.event_subtype,
        source_system=event.source_system,
        source_reference=event.source_reference,
        position_source_reference=event.position_source_reference,
        position_id=position_id,
        snapshot_id=source.snapshot_id if source is not None else None,
        amount_native=Decimal(str(event.amount)),
        currency=event.currency,
        amount_rc=amount_rc,
        fx_unconverted=amount_rc is None,
        attribution_basis=basis,
        branch_code=source.branch_code if source is not None else None,
        product_code=source.product_code if source is not None else None,
        product_family=source.product_family if source is not None else None,
        counterparty_id=source.counterparty_id if source is not None else None,
        sector=source.sector if source is not None else None,
    )


# --- monthly GL (D-021) -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlMonthlyFactRow:
    """One ``bi_fact_gl_monthly`` row: one P&L account, one calendar month.

    ``ytd_rc`` / ``prior_ytd_rc`` / ``movement_rc`` are the LEDGER's own
    balances (unsigned); ``pl_sign`` is the mapping's sign, so an official line
    is Σ ``pl_sign × ytd_rc`` over its accounts — exactly BSD7's period-to-date
    figure, which R4 proves.
    """

    organization_id: str
    bank_id: str
    month_end: date
    gl_account_code: str
    currency: str
    calendar_month: date
    account_class: str
    ytd_rc: Decimal
    prior_ytd_rc: Decimal | None
    movement_rc: Decimal | None
    missing_prior: bool
    balance_basis: str
    pl_line: str | None
    pl_sign: int | None


def gl_monthly_row(  # noqa: PLR0913 - one keyword per input the month depends on
    generations: Sequence[pl_mapping.Generation],
    *,
    organization_id: str,
    bank_id: str,
    month_end: date,
    fy_start: date,
    account_class: str,
    rule: pl_mapping.MappingRule | None,
    base_currency: str,
) -> GlMonthlyFactRow | None:
    """The monthly figures of ONE account through BSD7's own rules (``pl_mapping``).

    ``generations`` are the account's included, current-generation ledger rows
    with as_of ∈ [``fy_start``, ``month_end``] (any currency; their ``basis``
    field is ignored — the effective basis is the rule's, default ``ytd``).
    ``rule`` is ``pl_mapping.account_rule(...)`` for the account: ``None`` when
    the bank has mapped it to no official line (the row still exists, with
    ``pl_line`` NULL). A rule whose sign is not one the integral ``pl_sign``
    column can carry raises ``pl_mapping.PlSignError`` — the mart refuses the
    account rather than truncating a sign the return would file in full.
    The row's ``currency`` is the LATEST generation's,
    ``''`` for the reporting currency (an unstated currency IS the reporting
    currency, per BSD7's Domestic rule). ``None`` when no generation falls on
    or before ``month_end``.
    """
    basis = (rule.basis if rule is not None else None) or pl_mapping.YTD
    rows = [
        pl_mapping.Generation(row.code, row.as_of, row.currency, row.balance, basis)
        for row in generations
    ]
    figures = pl_mapping.monthly_account_figures(
        rows, month_end=month_end, fy_start=fy_start, basis=basis
    )
    if figures is None:
        return None
    latest = pl_mapping.latest_generation(rows, month_end)
    assert latest is not None  # noqa: S101 - figures exist only when a generation does
    currency = (
        "" if pl_mapping.is_base_currency(latest.currency, base_currency) else str(latest.currency)
    )
    return GlMonthlyFactRow(
        organization_id=organization_id,
        bank_id=bank_id,
        month_end=month_end,
        gl_account_code=latest.code,
        currency=currency,
        calendar_month=pl_mapping.month_start(month_end),
        account_class=account_class,
        ytd_rc=figures.ytd,
        prior_ytd_rc=figures.prior_ytd,
        movement_rc=figures.movement,
        missing_prior=figures.missing_prior,
        balance_basis=basis,
        pl_line=rule.item if rule is not None else None,
        # The register's sign, refused rather than truncated when it is not one
        # the integral column can carry (A5-08; ``pl_mapping.PlSignError``).
        pl_sign=(
            pl_mapping.register_sign_as_int(rule.sign, account_code=latest.code, item=rule.item)
            if rule is not None
            else None
        ),
    )


# --- monthly GL by branch (P5-B) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlBranchAllocation:
    """One row of the bank's ``gl_segment_balances`` register, parsed.

    ``currency`` follows the institution mart's convention: ``''`` for the
    reporting currency, because an unstated ledger currency IS the reporting
    currency (BSD7's Domestic rule). ``ytd`` is the branch's fiscal-year-to-date
    balance on the same convention and sign as the account's own institution
    balance — the register is a breakdown of a figure the bank already sends, not
    a second opinion about it.
    """

    gl_account_code: str
    branch_id: str
    currency: str
    ytd: Decimal


@dataclass(frozen=True, slots=True)
class GlBranchMonthlyFactRow:
    """One ``bi_fact_gl_branch_monthly`` row: one P&L account, one branch, one month."""

    organization_id: str
    bank_id: str
    month_end: date
    gl_account_code: str
    branch_code: str
    currency: str
    calendar_month: date
    account_class: str
    ytd_rc: Decimal
    prior_ytd_rc: Decimal | None
    movement_rc: Decimal | None
    missing_prior: bool
    balance_basis: str
    pl_line: str | None
    pl_sign: int | None
    register_as_of: date


@dataclass(frozen=True, slots=True)
class GlBranchMonthlyResult:
    """The month's branch rows plus everything the build must REPORT rather than absorb."""

    rows: tuple[GlBranchMonthlyFactRow, ...]
    #: Real branch ids the register named, so ``bi_dim_branch`` carries them even
    #: when no position mentions the branch. Excludes the residual key, which the
    #: builder adds to the dimension itself with its own label.
    branch_codes: frozenset[str]
    #: ``(account_code, currency)`` the register named that the institution's P&L
    #: ledger does not carry this month. Excluded from the mart and REPORTED: the
    #: ledger is the authority, so a branch figure with no institution row cannot
    #: be reconciled to anything, and adding it would make the branch total
    #: exceed the ledger. Never silently dropped.
    orphans: tuple[tuple[str, str], ...]
    #: Accounts where the register allocated MORE of the account than the ledger
    #: holds (``|Σ reported| > |institution ytd|``), which makes the residual run
    #: the other way. The residual is still written — the identity is the one
    #: thing that may not break — and the condition is reported.
    over_allocated: tuple[str, ...]


def gl_branch_monthly_rows(
    institution: Sequence[GlMonthlyFactRow],
    *,
    current: Sequence[GlBranchAllocation],
    prior: Sequence[GlBranchAllocation],
    residual_branch_id: str,
    register_as_of: date,
) -> GlBranchMonthlyResult:
    """The branch breakdown of one month's P&L ledger, summing to it exactly.

    ``institution`` is the month's :func:`gl_monthly_row` output — the authority
    for which accounts exist, what each is worth, which BSD7 line it feeds and on
    what basis. ``current`` / ``prior`` are the bank's register rows for this
    month and for the month whose end the institution row's ``prior_ytd_rc``
    describes; a branch with no ``prior`` reading gets ``movement_rc`` NULL, never
    a movement computed against an assumed zero — and so does the residual when the
    prior register did not cover the account at all, because "nothing was allocated
    then" and "no breakdown was sent then" are not the same statement.

    Per (account, currency) the result carries one row per reported branch plus
    one on ``residual_branch_id`` holding ``institution_ytd − Σ reported_ytd``
    (omitted only when it is zero at both readings, where it would carry nothing),
    so ``Σ ytd_rc`` over branches IS the institution's ``ytd_rc``, exactly, however
    partial the allocation. The residual's prior is
    ``institution_prior − Σ prior_ytd over THIS month's branches``, which is what
    makes the same identity hold for ``movement_rc`` whenever no row in the block
    is ``missing_prior``: a branch that left since the prior month has its prior
    balance absorbed by the residual rather than stranded, and a branch that
    joined has no movement, so the block does not claim one.

    An account the register names that the ledger has no P&L row for this month is
    an orphan: reported in :attr:`GlBranchMonthlyResult.orphans`, not written.
    An account the register does not mention at all is not built — it gets no
    rows, so a reader sees which accounts have a breakdown and which do not,
    rather than a table of 100 %-unallocated lines that looks like an answer.
    """
    by_key: dict[tuple[str, str], GlMonthlyFactRow] = {
        (row.gl_account_code, row.currency): row for row in institution
    }
    reported: dict[tuple[str, str], dict[str, Decimal]] = {}
    for item in current:
        key = (item.gl_account_code, item.currency)
        bucket = reported.setdefault(key, {})
        # A register that lists one (account, branch, currency) twice is stating
        # two parts of the same figure; summing is the only reading that keeps the
        # identity, and the whole-register-per-push grain makes it a bank's choice
        # of granularity rather than a conflict.
        bucket[item.branch_id] = bucket.get(item.branch_id, _ZERO) + item.ytd
    prior_by_key: dict[tuple[str, str], dict[str, Decimal]] = {}
    for item in prior:
        key = (item.gl_account_code, item.currency)
        bucket = prior_by_key.setdefault(key, {})
        bucket[item.branch_id] = bucket.get(item.branch_id, _ZERO) + item.ytd

    rows: list[GlBranchMonthlyFactRow] = []
    branch_codes: set[str] = set()
    orphans: list[tuple[str, str]] = []
    over_allocated: list[str] = []

    for key in sorted(reported):
        parent = by_key.get(key)
        if parent is None:
            orphans.append(key)
            continue
        allocations = reported[key]
        priors = prior_by_key.get(key, {})
        total_reported = sum(allocations.values(), _ZERO)
        if abs(total_reported) > abs(parent.ytd_rc):
            over_allocated.append(parent.gl_account_code)
        for branch_id in sorted(allocations):
            branch_codes.add(branch_id)
            ytd = allocations[branch_id]
            branch_prior = priors.get(branch_id)
            rows.append(
                _gl_branch_row(
                    parent,
                    branch_code=branch_id,
                    ytd=ytd,
                    prior=branch_prior,
                    register_as_of=register_as_of,
                )
            )
        # The remainder. Its prior is the institution's prior less the prior
        # readings of THIS month's branches, so a departed branch's prior lands
        # here instead of breaking the movement identity.
        # The remainder's prior is knowable only if the prior register covered this
        # (account, currency) at all. Without it, "nothing was allocated then" and
        # "the breakdown was not sent then" are indistinguishable, and treating the
        # second as the first would report the month a bank STARTED sending the
        # dataset as a large movement out of the unallocated line — an artefact of
        # the feed, presented as a business figure.
        residual_prior = (
            parent.prior_ytd_rc
            - sum((priors[branch] for branch in allocations if branch in priors), _ZERO)
            if parent.prior_ytd_rc is not None and key in prior_by_key
            else None
        )
        residual_ytd = parent.ytd_rc - total_reported
        # A residual that is zero at BOTH readings carries nothing: the account is
        # fully allocated, Σ is already the institution's figure, and the row would
        # only put an empty "unallocated" bar on every chart. It is kept when only
        # one reading is zero, because then it carries a real movement.
        if residual_ytd or residual_prior:
            rows.append(
                _gl_branch_row(
                    parent,
                    branch_code=residual_branch_id,
                    ytd=residual_ytd,
                    prior=residual_prior,
                    register_as_of=register_as_of,
                )
            )
    return GlBranchMonthlyResult(
        rows=tuple(rows),
        branch_codes=frozenset(branch_codes),
        orphans=tuple(orphans),
        over_allocated=tuple(sorted(set(over_allocated))),
    )


def _gl_branch_row(
    parent: GlMonthlyFactRow,
    *,
    branch_code: str,
    ytd: Decimal,
    prior: Decimal | None,
    register_as_of: date,
) -> GlBranchMonthlyFactRow:
    """One branch row, inheriting the account's mapping from the institution row.

    ``pl_line`` / ``pl_sign`` / ``balance_basis`` / ``account_class`` are NOT
    re-resolved per branch: the bank makes one statement about an account, so a
    branch cannot feed a different BSD7 line or carry a different sign than the
    account the return files.
    """
    return GlBranchMonthlyFactRow(
        organization_id=parent.organization_id,
        bank_id=parent.bank_id,
        month_end=parent.month_end,
        gl_account_code=parent.gl_account_code,
        branch_code=branch_code,
        currency=parent.currency,
        calendar_month=parent.calendar_month,
        account_class=parent.account_class,
        ytd_rc=ytd,
        prior_ytd_rc=prior,
        movement_rc=None if prior is None else ytd - prior,
        missing_prior=prior is None,
        balance_basis=parent.balance_basis,
        pl_line=parent.pl_line,
        pl_sign=parent.pl_sign,
        register_as_of=register_as_of,
    )


# --- engine metrics ----------------------------------------------------------------

#: Payload keys the live plane stamps beside its metrics, not figures.
_PAYLOAD_STATE_KEYS: frozenset[str] = frozenset({"reconciliation_status", "availability", "reason"})
RECONCILIATION_KEY = "reconciliation_status"
RECONCILIATION_BLOCKED = "blocked"


def metric_unit(metric_id: str, value: Decimal | None) -> MetricUnit:
    """The formatting unit from the wire key's suffix convention."""
    if value is None:
        return "text"
    if metric_id.endswith("_count") or metric_id == "loan_count":
        return "count"
    if metric_id.endswith("_pct"):
        return "pct"
    if metric_id.endswith("_ghs") or metric_id.endswith("_capital") or "_var_" in metric_id:
        return "ccy"
    return "ratio"


def engine_metric_row(  # noqa: PLR0913 - one keyword per mart column, all required
    *,
    organization_id: str,
    bank_id: str,
    as_of_date: date,
    module: str,
    metric_id: str,
    raw_value: Any,
    tier: Tier,
    regime: str,
    institution_class: str,
    status: str,
    input_hash: str | None,
    engine_version: str | None,
    pipeline_state: str | None,
    reconciliation_blocked: bool,
    run_id: UUID | None,
    reporting_period_id: UUID | None,
    computed_at: datetime | None,
) -> EngineMetricFactRow:
    """One typed copy of one payload metric.

    ``regime`` / ``institution_class`` describe the TENANT the figure was
    computed for (``institution_types.capital_regime`` / ``institution_class``).
    The row's ``regime`` is the resolved authority's own regime — a class-neutral
    figure (an IFRS 9 allowance, an advisory PD band) carries the regime it is
    registered under, so the row's ``(metric_id, regime)`` matches the
    catalogue's engine measure key — and falls back to the tenant's regime when
    nothing resolves, with ``advisory_designation="unregistered"`` (H-009).
    """
    value = _dec_or_none(raw_value) if not isinstance(raw_value, bool) else None
    authority = resolve_authority(metric_id, regime=regime, institution_class=institution_class)
    designation = designation_of(authority) if authority is not None else UNREGISTERED
    return EngineMetricFactRow(
        organization_id=organization_id,
        bank_id=bank_id,
        as_of_date=as_of_date,
        module=module,
        metric_id=metric_id,
        tier=tier,
        value=value,
        unit=metric_unit(metric_id, value),
        status=status,
        regime=authority.regime.value if authority is not None else regime,
        institution_class=institution_class,
        advisory_designation=designation,
        input_hash=input_hash,
        engine_version=engine_version,
        pipeline_state=pipeline_state,
        reconciliation_blocked=reconciliation_blocked,
        run_id=run_id,
        reporting_period_id=reporting_period_id,
        computed_at=computed_at,
    )


def _scalar_metrics(metrics: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """The payload's scalar entries in key order; nested structures are not metrics."""
    return [
        (key, value)
        for key, value in sorted(metrics.items())
        if key not in _PAYLOAD_STATE_KEYS and not isinstance(value, dict | list | tuple)
    ]


def live_metric_rows(
    live: LiveMetricLike, *, regime: str, institution_class: str
) -> tuple[EngineMetricFactRow, ...]:
    """Every scalar metric of one live-module payload as ``tier="live"`` rows.

    ``reconciliation_blocked`` is read from BOTH places the live plane records
    it: ``pipeline_state == "blocked"`` on the row and the
    ``reconciliation_status`` key stamped into the payload only when blocked.
    """
    blocked = (
        live.pipeline_state == RECONCILIATION_BLOCKED
        or live.metrics.get(RECONCILIATION_KEY) == RECONCILIATION_BLOCKED
    )
    return tuple(
        engine_metric_row(
            organization_id=live.organization_id,
            bank_id=live.bank_id,
            as_of_date=live.source_as_of_date,
            module=live.module,
            metric_id=metric_id,
            raw_value=raw,
            tier="live",
            regime=regime,
            institution_class=institution_class,
            status=live.status,
            input_hash=live.computed_from_input_hash,
            engine_version=live.engine_version,
            pipeline_state=live.pipeline_state,
            reconciliation_blocked=blocked,
            run_id=None,
            reporting_period_id=live.source_fact_period_id,
            computed_at=live.computed_at,
        )
        for metric_id, raw in _scalar_metrics(live.metrics)
    )


def official_run_rows(
    run: RunLike, *, as_of_date: date, regime: str, institution_class: str
) -> tuple[EngineMetricFactRow, ...]:
    """Every scalar metric of one sealed baseline run as ``tier="official"`` rows.

    ``as_of_date`` is the run's ``bank_reporting_periods.period_end`` — the
    caller joins it, because a run row carries only the period id. A sealed run
    has no pipeline state and is never reconciliation-blocked: a book that
    fails the identity produces no official run at all.
    """
    return tuple(
        engine_metric_row(
            organization_id=run.organization_id,
            bank_id=run.bank_id,
            as_of_date=as_of_date,
            module=run.module,
            metric_id=metric_id,
            raw_value=raw,
            tier="official",
            regime=regime,
            institution_class=institution_class,
            status=run.status,
            input_hash=run.input_hash,
            engine_version=run.engine_version,
            pipeline_state=None,
            reconciliation_blocked=False,
            run_id=run.id,
            reporting_period_id=run.reporting_period_id,
            computed_at=run.completed_at,
        )
        for metric_id, raw in _scalar_metrics(run.metrics)
    )
