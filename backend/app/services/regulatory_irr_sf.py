"""IRRBB Standardised Framework runs: loader, lifecycle, persistence, read model.

A SIBLING of ``app.services.regulatory_irr``, not an extension of it. The
Standardised Framework is a different measurement — nineteen buckets against
nine, six prescribed shapes against the platform's Basel six, per-currency
aggregation with a materiality gate, an outlier test on LOSSES only (D-013),
and behavioural treatment of deposits and prepayments the legacy engine has no
vocabulary for. Extending ``domain/irr/engine.py`` in place would have moved
every existing IRRBB golden and every filed IRRBB return; instead the SF runs
as a new module value (``irr_sf``) on the same immutable run table, and the
legacy engine is byte-identical (P5-DESIGN §1.7).

What this module owns:

* **the book** — canonical position snapshots at the reporting date projected
  onto the governed bucket ladder, non-maturing deposits split out, and the
  per-currency curve, FX rate and banking-book size the materiality test needs;
* **the lifecycle** — queue, run, then persist either the whole result or a
  TYPED failure on the run row (a refusal is data, never an HTTP 500);
* **presentation rounding** (D-062). The domain quantises to six places
  because that is what the hand-checked golden vectors are stated to; anything
  coarser than that belongs here and in the UI, never pushed down into the
  engine, because a domain that rounds for presentation cannot be reconciled
  against a golden.

What it deliberately does NOT own: a single regulatory number. Every
calibration is resolved from the control plane and refused when absent
(D-024). And the automatic-option refusal carries ONE name,
``irrbb_sf_options_unsupported``, on the exception and on the run row alike
(D-061) — two names for one condition is what confuses an examiner reading a
run row against the message the operator saw.

The SF is NOT a live-plane module: it writes no ``live_metrics`` and has no
``compute_live``. An immutable filing measurement is minted on request, not
polled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.irr import standardised as sf
from app.domain.irr import standardised_cash_flows as sfcf
from app.domain.irr import standardised_params as sfp
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    RegulatoryLineItem,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.regulatory_irr_sf import (
    IrrbbSfAssumptionRead,
    IrrbbSfAttemptRead,
    IrrbbSfAttemptsRead,
    IrrbbSfBucketRead,
    IrrbbSfCurrencyScenarioRead,
    IrrbbSfCurrencyScopeRead,
    IrrbbSfDataQualityRead,
    IrrbbSfExclusionRead,
    IrrbbSfLadderRowRead,
    IrrbbSfMandateRead,
    IrrbbSfMeasureRead,
    IrrbbSfMeasuresRead,
    IrrbbSfNmdCategoryRead,
    IrrbbSfParameterRead,
    IrrbbSfRead,
    IrrbbSfRunCreate,
    IrrbbSfScenarioRead,
    IrrbbSfTable7Read,
    IrrbbSfTable8RowRead,
)
from app.schemas.regulatory_liquidity import RegulatoryRunRead
from app.services import (
    filing_reconciliation,
    jurisdictions,
    market_data,
    market_data_sources,
    regulatory_parameters,
)
from app.services.audit import record_event
from app.services.regulatory_irr import tier1_for_period
from app.services.regulatory_liquidity import (  # noqa: PLC2701 - engine completion read
    _read_regulatory_run_execution_result,
)

ENGINE_VERSION = "regulatory-irr-sf-v1.0.0"
INPUT_SCHEMA_VERSION = "irrbb-sf-inputs-v1"
OUTPUT_SCHEMA_VERSION = "irrbb-sf-metrics-v1"
MODULE_IRR_SF = "irr_sf"
#: The framework reports all six prescribed shapes together, so a run is not a
#: choice of scenario. One code names the whole measurement.
SF_SCENARIO_CODE = "standardised_framework"

#: The governed row that decides when the framework becomes mandatory. It is a
#: rule about as-of dates, resolved at TODAY, and the deployment cannot waive
#: it (D-040).
CODE_MANDATORY_FROM = "irrbb_sf_mandatory_from_as_of"

SECTION_LADDER = "irr_sf_ladder"
SECTION_EVE = "irr_sf_eve"
SECTION_NII = "irr_sf_nii"

#: The stored UNIT of a reporting-currency amount. It is a wire key, not a
#: currency claim: ``ck_regulatory_metric_results_unit`` admits exactly
#: ``pct`` / ``ghs`` / ``years``, and every module that persists a money metric
#: — capital, liquidity, FX, legacy IRRBB — writes this same key. The same
#: reading AGENTS.md gives a ``bog_``-prefixed fact category, which means
#: "central-bank reserves" in every jurisdiction.
#:
#: The bank's ACTUAL reporting currency is never inferred from it. It is
#: resolved from the bank through ``jurisdictions.base_currency`` and carried
#: on the snapshot, on ``run.metrics`` and on the read model, so nothing a bank
#: reads is labelled from this constant. Giving the column a real ISO code
#: would need the shared CHECK and the shared ``RegulatoryMetricUnit`` literal
#: widened across every module at once — recorded for the lead, not done here.
METRIC_UNIT_REPORTING_CURRENCY = "ghs"

METRIC_EVE_RISK_MEASURE = "sf_eve_risk_measure"
METRIC_EVE_RISK_MEASURE_PCT_TIER1 = "sf_eve_risk_measure_pct_tier1"
METRIC_MAX_DELTA_NII = "sf_max_delta_nii"

RULE_OUTLIER = "sf_outlier_test"
RULE_ASSUMPTION_DEFAULTS = "sf_assumption_defaults"
RULE_PARAMETERS_PENDING = "sf_parameters_pending"
RULE_CURRENCY_SCOPE = "sf_currency_scope"

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
#: What a stored line item can hold. The authoritative figures keep the
#: domain's six places in ``run.metrics``; the line-item columns are
#: ``Numeric(20, 4)``, so the truncation is performed here deliberately rather
#: than left to the database to do silently.
_LINE_ITEM_QUANTUM = Decimal("0.0001")
_MONTHS_IN_YEAR = Decimal(12)

#: Canonical position types the banking book measures, mapped to the family the
#: governed default cash-flow profile is stated for (App I fn 20-21; the
#: mapping itself is INFERRED and documented here rather than in the engine).
_ASSET_FAMILIES: Mapping[str, str] = {
    "LOAN": "LOAN",
    "SECURITY_HOLDING": "SECURITY_HOLDING",
    "INTERBANK_PLACEMENT": "INTERBANK_PLACEMENT",
}
_LIABILITY_FAMILIES: Mapping[str, str] = {
    "INTERBANK_BORROWING": "INTERBANK_BORROWING",
    "OTHER_LIABILITY": "OTHER_LIABILITY",
}
#: Explicitly out of scope: they carry no interest-rate repricing risk that the
#: framework measures. Named so a reader can tell "excluded by rule" from
#: "dropped because we could not read it".
_OUT_OF_SCOPE_TYPES: frozenset[str] = frozenset(
    {"CASH", "OTHER_ASSET", "LC_GUARANTEE", "COMMITMENT_UNDRAWN"}
)
#: Deposit account types with no contractual maturity date.
_NON_MATURING_ACCOUNTS: frozenset[str] = frozenset({"CURRENT", "CALL", "SAVINGS"})
_TRANSACTIONAL_ACCOUNTS: frozenset[str] = frozenset({"CURRENT", "CALL"})
_RETAIL_COUNTERPARTIES: frozenset[str] = frozenset({"RETAIL_INDIVIDUAL", "SME"})

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")

#: Exclusion markers. Every one of these is a position the framework did NOT
#: measure, counted with the money it represents, because a silently dropped
#: position is indistinguishable from one worth nothing.
EXCLUDED_UNCONVERTED = "unconverted"
EXCLUDED_UNMODELLED_DERIVATIVE = "unmodelled_derivative_notional"
EXCLUDED_OUT_OF_SCOPE = "out_of_scope"
EXCLUDED_NO_BALANCE = "no_balance"

#: A wholesale term deposit with no evidence of a redemption penalty. The
#: engine prices it at the exercise most disadvantageous to the bank, which for
#: a liability is full redemption at par, on the base book and every shocked
#: book alike. The assumption is the LOADER's, not the engine's — nothing
#: upstream carries an early-withdrawal attribute — so it is tallied here, per
#: position, the way every other substitution is.
TALLY_TD_WHOLESALE_DEMANDABLE = "td_wholesale_demandable"

#: Production copy for every marker that reaches a bank-facing surface. A raw
#: marker must never be printed (feedback 2026-08-20: UI copy is production
#: copy).
ASSUMPTION_LABELS: Mapping[str, str] = {
    sfcf.TALLY_PROFILE_AMORTISATION: "Repayment profile taken from the platform default",
    sfcf.TALLY_PROFILE_FREQUENCY: "Payment frequency taken from the platform default",
    sfcf.TALLY_HORIZONLESS: "No maturity or repricing date stated; slotted at the default bucket",
    sfcf.TALLY_ACCRUAL_START_UNKNOWN: "Interest accrual start not stated; accrued from the "
    "reporting date",
    sfcf.TALLY_NO_SPREAD_LEG: "Floating position with no spread stated",
    sf.TALLY_NMD_NO_CORE_ESTIMATE: "Deposit with no core-share estimate; treated as wholly "
    "non-core",
    sf.TALLY_NMD_CORE_WITHOUT_DURATION: "Core deposits with no assigned repricing maturity",
    sf.TALLY_NMD_CORE_CAP_BINDING: "Core share cut back to the supervisory cap",
    sf.TALLY_NMD_HISTORY_SHORT: "Deposit behaviour estimated on less than the expected "
    "observation history",
    sf.TALLY_NO_PREPAYMENT_RATE: "Loan with no prepayment rate; contractual schedule used",
    sf.TALLY_NO_REDEMPTION_RATE: "Term deposit with no early-redemption rate; contractual "
    "schedule used",
    sfcf.TALLY_PAST_DUE: "Principal already past due at the reporting date; placed in the "
    "shortest bucket",
    TALLY_TD_WHOLESALE_DEMANDABLE: "Wholesale term deposit with no evidence of a redemption "
    "penalty; priced as repayable on demand",
}

#: Statements the BOOK earns, on top of the statements the parameters earn.
#: Each one says what an assumption did to the measure, in the direction it did
#: it, because a count on its own does not tell a reader whether the number
#: they are about to file is high or low.
BOOK_STATEMENTS: Mapping[str, str] = {
    sfcf.TALLY_PAST_DUE: (
        "Principal already past due at the reporting date is placed in the shortest "
        "time bucket, because it has no remaining contractual schedule to slot. It is "
        "therefore discounted at the shortest rate and changes very little when rates "
        "move, so for a book with a material non-performing balance this measure "
        "understates the change in economic value. Expected recovery cash flows and "
        "their timing are not modelled: the reported positions carry no performing "
        "status."
    ),
    TALLY_TD_WHOLESALE_DEMANDABLE: (
        "Wholesale term deposits that state no redemption penalty are treated as "
        "repayable on demand at face value, which is the exercise most disadvantageous "
        "to the institution. That treatment prices the base position and every shocked "
        "position alike, so these deposits carry almost no interest-rate sensitivity of "
        "their own and provide no term funding protection against the asset side. "
        "Recording an early-withdrawal penalty on these deposits at ingestion moves them "
        "to their contractual schedule."
    ),
}
EXCLUSION_LABELS: Mapping[str, str] = {
    EXCLUDED_UNCONVERTED: "Position in a currency with no exchange rate",
    EXCLUDED_UNMODELLED_DERIVATIVE: "Derivative with no leg terms stated",
    EXCLUDED_OUT_OF_SCOPE: "Position type outside the banking-book interest-rate measure",
    EXCLUDED_NO_BALANCE: "Position with no balance stated",
}
NMD_CATEGORY_LABELS: Mapping[str, str] = {
    "retail_transactional": "Retail transactional",
    "retail_non_transactional": "Retail non-transactional",
    "wholesale": "Wholesale",
}
MEASURE_LABELS: Mapping[str, str] = {
    sf.MEASURE_ALL: "All six prescribed scenarios",
    sf.MEASURE_MANDATORY: "Mandatory reporting scenarios",
    sf.MEASURE_OUTLIER_SET: "Outlier test scenarios",
}

#: Behavioural reference-row vocabulary (docs/API_INTEGRATION.md). Absent rows
#: are a disclosed assumption, never a substituted number.
#: The reference dataset the behavioural rates arrive on.
DATASET_BEHAVIOURAL = "behavioral_assumptions"

ASSUMPTION_TYPE_PREPAYMENT = "PREPAYMENT_RATE"
ASSUMPTION_TYPE_REDEMPTION = "TERM_DEPOSIT_REDEMPTION_RATE"
ASSUMPTION_TYPE_NMD_CORE = "NMD_CORE_SHARE"
ASSUMPTION_TYPE_NMD_DURATION = "NMD_DURATION"


class SfRunError(Exception):
    """A typed refusal persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class SfMandate:
    """Whether the Standardised Framework is mandatory for an as-of date."""

    mandatory: bool
    mandatory_from: date | None
    as_of: date
    confirmation_status: str
    source_citation: str

    @property
    def statement(self) -> str:
        if self.mandatory_from is None:
            return (
                "No commencement date is governed for the Standardised Framework, so it "
                "is not yet mandatory."
            )
        pending = " This date is pending confirmation with the supervisor." if (
            self.confirmation_status == "pending"
        ) else ""
        if self.mandatory:
            return (
                "The Standardised Framework applies to reporting dates from "
                f"{self.mandatory_from.isoformat()}." + pending
            )
        return (
            "The Standardised Framework becomes mandatory for reporting dates from "
            f"{self.mandatory_from.isoformat()}." + pending
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mandatory": self.mandatory,
            "mandatory_from": None if self.mandatory_from is None else (
                self.mandatory_from.isoformat()
            ),
            "as_of": self.as_of.isoformat(),
            "confirmation_status": self.confirmation_status,
            "source_citation": self.source_citation,
            "statement": self.statement,
        }


@dataclass
class _LadderGroup:
    """One homogeneous portfolio, accumulated across its positions."""

    currency: str
    portfolio: str
    kind: sf.LadderKind
    principal: list[Decimal]
    interest: list[Decimal]
    cpr_weight: Decimal = _ZERO
    cpr_amount: Decimal = _ZERO
    tdrr_weight: Decimal = _ZERO
    tdrr_amount: Decimal = _ZERO


@dataclass
class _CurrencySize:
    assets_native: Decimal = _ZERO
    liabilities_native: Decimal = _ZERO


@dataclass
class _Book:
    inputs: sf.SfInputs
    parameters: dict[str, regulatory_parameters.ResolvedParameter]
    params: sfp.SfParameters
    curves: dict[str, market_data.CurveView]
    fx: dict[str, Decimal]
    instrument_count: int
    exclusions: dict[str, tuple[int, Decimal]]
    ladder_digest: str
    nmd_count: int
    mandate: SfMandate
    snapshot: dict[str, Any] = field(default_factory=dict)


# --- public surface ----------------------------------------------------------


def sf_mandate(
    db: Session, bank: Bank, *, as_of: date, today: date, record: bool = True
) -> SfMandate:
    """Resolve the commencement rule for ``as_of``.

    The governed row is resolved at TODAY, not at the reporting date: it is a
    rule about which reporting dates the framework covers, and back-dating the
    rule itself would let a console edit rewrite whether a past filing was
    required.

    **Resolving a parameter is not free.** Every ``regulatory_parameters``
    resolution is recorded in a per-session ledger that the NEXT run to be
    sealed drains through ``consume_parameter_provenance`` — which is how a row
    resolved during registry scanning once leaked into an unrelated return
    family's parameter digest, inside the very ``content_digest`` an officer
    signs. Inside this module that is safe: the resolution happens in
    :func:`_load_book`, at calculation time, and the run row sealed
    immediately after is the one that should own it.

    A caller that uses this seam OUTSIDE a calculation — a readiness check, a
    framework scan, a UI probe — passes ``record=False``. The row is then read
    through a DISPATCH-plane resolver that never enters the ledger, so a
    readiness question cannot end up inside an unrelated run's
    ``content_digest`` (D-078). Precedence, active window and observability are
    identical on both paths; only the ledger differs.
    """
    row = (
        regulatory_parameters.try_resolve(db, bank, CODE_MANDATORY_FROM, as_of=today)
        if record
        else regulatory_parameters.PrefetchedParameterResolver.load(
            db, bank, as_of_dates=[today], record=False
        ).try_resolve(CODE_MANDATORY_FROM, as_of=today)
    )
    if row is None:
        return SfMandate(
            mandatory=False,
            mandatory_from=None,
            as_of=as_of,
            confirmation_status="",
            source_citation="",
        )
    body = row.value_json or {}
    raw = body.get("date")
    try:
        mandatory_from = date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return SfMandate(
            mandatory=False,
            mandatory_from=None,
            as_of=as_of,
            confirmation_status=row.confirmation_status,
            source_citation=row.source_citation,
        )
    return SfMandate(
        mandatory=as_of >= mandatory_from,
        mandatory_from=mandatory_from,
        as_of=as_of,
        confirmation_status=row.confirmation_status,
        source_citation=row.source_citation,
    )


def latest_sf_run(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> RegulatoryRun | None:
    """The newest SUCCEEDED Standardised Framework run for this exact period.

    The public seam the ICAAP block resolver and the Pillar 2 method bind to,
    so neither has to re-derive what "the current SF run" means.
    """
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == MODULE_IRR_SF,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def latest_sf_attempt(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> RegulatoryRun | None:
    """The newest Standardised Framework run of ANY status for this period.

    :func:`latest_sf_run` answers "is there a result to bind"; this answers
    "was the framework tried, and what happened". They are different questions,
    and conflating them turns a typed refusal — a book with interest-rate
    options, refused under ``irrbb_sf_options_unsupported`` (D-061) — into the
    silence of "no run exists", which reads as "nobody has tried".
    """
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == MODULE_IRR_SF,
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def run_standardised_framework(
    db: Session, ctx: TenantContext, bank_id: str, payload: IrrbbSfRunCreate
) -> RegulatoryRunRead:
    """Mint one immutable Standardised Framework run."""
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    # Every immutable run is filing evidence, so the balance-sheet control
    # gates this mint exactly as it gates capital, liquidity and legacy IRRBB.
    # The 409 is raised BEFORE the run row exists — a refused mint leaves no
    # half-run behind.
    filing_reconciliation.assert_filing_reconciled(
        db, ctx, bank, as_of=period.period_end, period_id=period.id, purpose="official_run"
    )
    return _create_and_execute(db, ctx, bank, period)


def get_standardised_framework(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID,
    *,
    resolved_bank: Bank | None = None,
) -> IrrbbSfRead:
    """The latest Standardised Framework result for one reporting period."""
    bank = resolved_bank or _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    run = latest_sf_run(db, ctx, bank, period)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No Standardised Framework result exists for this reporting date. "
                "Run the framework to produce one."
            ),
        )
    return _read_model(db, ctx, bank, period, run)


# --- the attempt history -----------------------------------------------------
#
# The result read answers "is there a figure to bind". This answers "was the
# framework tried at all, and what happened" — a REFUSED attempt is a ``failed``
# run, so it never reaches the result read, and silence there is
# indistinguishable from nobody having tried. Until this route existed the
# dashboard reconstructed the answer from the generic run registry, which is
# where a defect lived: the registry pages its rows under ``runs`` while the
# screen read ``items``, so a refused framework reported "nobody has run it".

#: Production copy for a run's lifecycle state. No raw enum reaches a reader.
ATTEMPT_STATUS_LABELS: Mapping[str, str] = {
    "queued": "Waiting to run",
    "running": "Running now",
    "succeeded": "Produced a result",
    "failed": "Refused to measure",
}

#: Why the framework would not measure a book, by the engine's own refusal
#: name (D-061). One sentence per code, and an unmapped code still says
#: something truthful rather than printing the token.
REFUSAL_COPY: Mapping[str, str] = {
    sf.SfOptionsUnsupportedError.CODE: (
        "the banking book holds interest-rate options, and the framework's standardised "
        "add-on for them is not available on this platform yet. The measure is refused "
        "rather than reported without those positions, which would understate it."
    ),
}

#: How many attempts one read returns. A page size, not a regulatory quantity.
ATTEMPT_PAGE_SIZE = 10


def refusal_sentence(code: str, message: str) -> str:
    """One sentence a preparer can act on, whatever the refusal was."""
    reason = REFUSAL_COPY.get(code)
    if reason is not None:
        return (
            "The standardised framework could not be measured for this date because "
            f"{reason}"
        )
    detail = message.strip()
    return (
        "The standardised framework could not be measured for this date."
        + (f" {detail}" if detail else "")
    )


def _attempt_read(run: RegulatoryRun) -> IrrbbSfAttemptRead:
    failed = run.status == "failed"
    code = run.error_code or None
    return IrrbbSfAttemptRead(
        run_id=run.id,
        status=run.status,
        status_label=ATTEMPT_STATUS_LABELS.get(run.status, run.status),
        input_hash=run.input_hash or "",
        engine_version=run.engine_version or "",
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        error_code=code,
        refusal_statement=(
            refusal_sentence(code or "", run.error_message or "") if failed else ""
        ),
    )


def _attempts_statement(*, latest: RegulatoryRun | None, has_result: bool) -> str:
    """What the history means, so the screen never has to infer it."""
    if latest is None:
        return (
            "The standardised framework has not been run for this reporting date. "
            "Nothing has been refused — nobody has tried."
        )
    if latest.status in {"queued", "running"}:
        return (
            "A standardised framework run is under way for this reporting date. "
            "The result appears here when it finishes."
        )
    if latest.status == "failed":
        sentence = refusal_sentence(latest.error_code or "", latest.error_message or "")
        if has_result:
            return (
                f"{sentence} An earlier run did produce a result for this date, so the "
                "figures on this page are the earlier ones."
            )
        return sentence
    return "The standardised framework produced a result for this reporting date."


def get_standardised_framework_attempts(  # noqa: PLR0913 - the read names its full scope
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID,
    *,
    limit: int = ATTEMPT_PAGE_SIZE,
    resolved_bank: Bank | None = None,
) -> IrrbbSfAttemptsRead:
    """Every framework attempt at one reporting date, newest first.

    Unlike the result read this never 404s on an empty history: "nobody has run
    it" is an ANSWER, and the one a preparer most needs to tell apart from a
    refusal.
    """
    bank = resolved_bank or _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, reporting_period_id)
    rows = list(
        db.scalars(
            select(RegulatoryRun)
            .where(
                RegulatoryRun.organization_id == ctx.organization_id,
                RegulatoryRun.bank_id == bank.id,
                RegulatoryRun.reporting_period_id == period.id,
                RegulatoryRun.module == MODULE_IRR_SF,
            )
            .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
            .limit(max(1, limit))
        )
    )
    # The seam the ICAAP block resolver and the Pillar 2 method already bind to,
    # rather than "the first row of this page" — so the route and the report
    # cannot disagree about which attempt is the newest one.
    latest = latest_sf_attempt(db, ctx, bank, period)
    result = latest_sf_run(db, ctx, bank, period)
    refusal = next(
        (row for row in rows if row.status == "failed" and row.error_code), None
    )
    return IrrbbSfAttemptsRead(
        bank_id=bank.id,
        reporting_period_id=period.id,
        as_of=period.period_end,
        attempted=latest is not None,
        has_result=result is not None,
        latest=None if latest is None else _attempt_read(latest),
        refusal=None if refusal is None else _attempt_read(refusal),
        attempts=[_attempt_read(row) for row in rows],
        statement=_attempts_statement(latest=latest, has_result=result is not None),
    )


# --- lifecycle ---------------------------------------------------------------


def _create_and_execute(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> RegulatoryRunRead:
    reporting_currency = jurisdictions.base_currency(bank)
    try:
        book = _load_book(db, ctx, bank, period, reporting_currency)
    except SfRunError as exc:
        # The book could not even be assembled, so there is nothing to seal.
        # The refusal is still persisted as a failed run: "we tried, and this
        # is why" is the audit record a supervisor asks for; silence is not.
        snapshot = _empty_snapshot(bank, period, reporting_currency, exc)
        return _execute_refusal(db, ctx, bank, period, snapshot, exc)
    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_IRR_SF,
        scenario_code=SF_SCENARIO_CODE,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_snapshot_hash(book.snapshot),
        inputs=book.snapshot,
        metrics={},
        parameter_provenance=regulatory_parameters.consume_parameter_provenance(db),
        created_by=ctx.actor_user_id,
    )
    db.add(run)
    db.flush()
    _record_started(db, ctx, bank, period, run)
    db.commit()

    run.status = "running"
    run.started_at = datetime.now(UTC)
    db.commit()

    run_id = run.id
    try:
        result = sf.run(book.inputs, book.params)
        _persist_success(db, ctx, run, book, result)
    except sf.SfOptionsUnsupportedError as exc:
        # D-061: ONE name for this condition, on the exception and on the run
        # row alike. There is no mapping table and no second spelling.
        _persist_failure(db, ctx, run_id, SfRunError(exc.code, str(exc), dict(exc.detail)))
    except sfp.SfParameterError as exc:
        _persist_failure(db, ctx, run_id, SfRunError(exc.code, str(exc), dict(exc.detail)))
    except sf.SfError as exc:
        _persist_failure(db, ctx, run_id, SfRunError(exc.code, str(exc), dict(exc.detail)))
    except SfRunError as exc:
        _persist_failure(db, ctx, run_id, exc)
    except HTTPException:
        raise
    except Exception:
        _persist_failure(
            db,
            ctx,
            run_id,
            SfRunError(
                "calculation_error",
                "The Standardised Framework measure could not be calculated.",
                {
                    "corrective_action": (
                        "Review the run inputs and retry. Contact support if it fails again."
                    )
                },
            ),
        )
    db.expire_all()
    return _read_regulatory_run_execution_result(db, ctx, bank, run_id)


def _execute_refusal(  # noqa: PLR0913 - the refused run is its full identity
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    snapshot: dict[str, Any],
    error: SfRunError,
) -> RegulatoryRunRead:
    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_IRR_SF,
        scenario_code=SF_SCENARIO_CODE,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_snapshot_hash(snapshot),
        inputs=snapshot,
        metrics={},
        parameter_provenance=regulatory_parameters.consume_parameter_provenance(db),
        created_by=ctx.actor_user_id,
    )
    db.add(run)
    db.flush()
    _record_started(db, ctx, bank, period, run)
    db.commit()
    run_id = run.id
    _persist_failure(db, ctx, run_id, error)
    db.expire_all()
    return _read_regulatory_run_execution_result(db, ctx, bank, run_id)


def _record_started(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    run: RegulatoryRun,
) -> None:
    record_event(
        db,
        ctx,
        event_type="regulatory_run.started",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "bank_id": str(bank.id),
            "reporting_period_id": str(period.id),
            "module": MODULE_IRR_SF,
            "scenario_code": SF_SCENARIO_CODE,
            "input_hash": run.input_hash,
            "engine_version": ENGINE_VERSION,
        },
    )


def _persist_failure(
    db: Session, ctx: TenantContext, run_id: UUID, error: SfRunError
) -> None:
    db.rollback()
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
        )
    )
    if run is None:  # pragma: no cover - the queued row was committed earlier
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory run not found."
        )
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    run.error_code = error.code
    run.error_message = error.message
    run.error_details = error.details
    record_event(
        db,
        ctx,
        event_type="regulatory_run.failed",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "scenario_code": run.scenario_code,
            "error_code": error.code,
        },
    )
    db.commit()


def _persist_success(
    db: Session, ctx: TenantContext, run: RegulatoryRun, book: _Book, result: sf.SfResult
) -> None:
    run.metrics = _metrics_payload(book, result)
    outlier_status = "red" if result.outlier else "green"
    # Each metric is named with a STRING LITERAL here, not with the module
    # constant beside it. ``tests/domain/authority/test_registry_completeness``
    # reads every persistence site by AST to check that no filed figure reaches
    # the database without a registered authority, and it refuses to guess: a
    # site it cannot resolve fails the gate rather than quietly disappearing
    # from it. The constants stay for the callers, and a test holds the two
    # equal so they cannot drift.
    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        (
            "sf_eve_risk_measure",
            result.measures.outlier_set.measure,
            METRIC_UNIT_REPORTING_CURRENCY,
            None,
            "na",
        ),
        (
            "sf_eve_risk_measure_pct_tier1",
            result.pct_tier1,
            "pct",
            result.outlier_threshold_pct,
            outlier_status,
        ),
        (
            "sf_max_delta_nii",
            _max_delta_nii(result),
            METRIC_UNIT_REPORTING_CURRENCY,
            None,
            "na",
        ),
    ]
    for position, (code, value, unit, threshold_min, metric_status) in enumerate(
        metric_rows, start=1
    ):
        db.add(
            RegulatoryMetricResult(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                metric_code=code,
                metric_value=value,
                unit=unit,
                threshold_min=threshold_min,
                status=metric_status,
                position=position,
            )
        )
    for position, line in enumerate(_line_items(result), start=1):
        section, line_code, description, exposure, weighted = line
        db.add(
            RegulatoryLineItem(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                section=section,
                line_code=line_code,
                description=description,
                exposure_amount=None if exposure is None else _line_amount(exposure),
                rate_pct=None,
                weighted_amount=_line_amount(weighted),
                position=position,
            )
        )
    for position, validation in enumerate(_validations(book, result), start=1):
        rule_code, passed, severity, message = validation
        db.add(
            RegulatoryValidation(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                rule_code=rule_code,
                passed=passed,
                severity=severity,
                message=message,
                position=position,
            )
        )
    run.status = "succeeded"
    run.completed_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="regulatory_run.succeeded",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "input_hash": run.input_hash,
            "scenario_code": run.scenario_code,
            "module": MODULE_IRR_SF,
            "outlier": result.outlier,
        },
    )
    db.commit()


# --- persistence payloads ----------------------------------------------------


def _max_delta_nii(result: sf.SfResult) -> Decimal:
    """The worst earnings loss across the reported shapes, loss-positive."""
    losses = [row.delta_nii for row in result.table8 if row.code in sf.SCENARIOS]
    return max([*losses, _ZERO])


def _metrics_payload(book: _Book, result: sf.SfResult) -> dict[str, Any]:
    """The whole result, as JSON. Decimals are strings — never floats."""
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "as_of": result.as_of.isoformat(),
        "reporting_currency": result.reporting_currency,
        "bucket_keys": list(result.bucket_keys),
        "tier1": str(result.tier1),
        "pct_tier1": str(result.pct_tier1),
        "outlier": result.outlier,
        "outlier_threshold_pct": str(result.outlier_threshold_pct),
        "measures": {
            measure.name: {
                "scenarios": list(measure.scenarios),
                "measure": str(measure.measure),
                "worst_scenario": measure.worst_scenario,
            }
            for measure in (
                result.measures.all_scenarios,
                result.measures.mandatory,
                result.measures.outlier_set,
            )
        },
        "scenarios": [
            {
                "code": scenario.code,
                "label": scenario.label,
                "mandatory": scenario.mandatory,
                "in_outlier_set": scenario.in_outlier_set,
                "loss": str(scenario.loss),
                "net": str(scenario.net),
                "delta_nii": str(scenario.delta_nii),
                "by_currency": [
                    {
                        "currency": row.currency,
                        "eve_base_native": str(row.eve_base_native),
                        "eve_scenario_native": str(row.eve_scenario_native),
                        "delta_eve_native": str(row.delta_eve_native),
                        "delta_eve_reporting": str(row.delta_eve_reporting),
                        "automatic_option_addon_native": str(row.k_ao_native),
                        "delta_nii_native": str(row.delta_nii_native),
                        "delta_nii_reporting": str(row.delta_nii_reporting),
                    }
                    for row in scenario.by_currency
                ],
            }
            for scenario in result.scenarios
        ],
        "currencies": [
            {
                "currency": scope.currency,
                "assets_reporting": str(scope.assets_reporting),
                "liabilities_reporting": str(scope.liabilities_reporting),
                "asset_share_pct": str(scope.asset_share_pct),
                "liability_share_pct": str(scope.liability_share_pct),
                "share_pct": str(scope.share_pct),
                "material": scope.material,
                "fx_to_reporting": str(scope.fx_to_reporting),
            }
            for scope in result.currencies
        ],
        "excluded_currencies": list(result.excluded_currencies),
        "table8": [
            {
                "code": row.code,
                "label": row.label,
                "delta_eve": str(row.delta_eve),
                "delta_eve_net": str(row.delta_eve_net),
                "delta_nii": str(row.delta_nii),
                "delta_eve_prior": None if row.delta_eve_prior is None else str(
                    row.delta_eve_prior
                ),
                "delta_nii_prior": None if row.delta_nii_prior is None else str(
                    row.delta_nii_prior
                ),
            }
            for row in result.table8
        ],
        "table7_quantitative": {
            "average_repricing_maturity_years": str(
                result.table7_quantitative.average_repricing_maturity_years
            ),
            "longest_repricing_maturity_years": str(
                result.table7_quantitative.longest_repricing_maturity_years
            ),
        },
        "nmd_disclosure": [
            {
                "currency": row.currency,
                "category": row.category,
                "balance": str(row.balance),
                "core": str(row.core),
                "non_core": str(row.non_core),
                "core_cap_pct": str(row.core_cap_pct),
                "cap_binding": row.cap_binding,
                "average_core_maturity_years": str(row.average_core_maturity_years),
                "longest_core_maturity_years": str(row.longest_core_maturity_years),
            }
            for row in result.nmd_disclosure
        ],
        "ladder_base": [
            {
                "currency": row.currency,
                "bucket_key": row.bucket_key,
                "principal": str(row.principal),
                "interest": str(row.interest),
                "net": str(row.net),
            }
            for row in result.ladder_base
        ],
        "assumption_tallies": dict(sorted(result.assumption_tallies.items())),
        "exclusions": {
            marker: {"count": count, "amount_reporting": str(amount)}
            for marker, (count, amount) in sorted(book.exclusions.items())
        },
        "instrument_count": book.instrument_count,
        "parameters_pending_confirmation": list(result.parameters_pending_confirmation),
        "representative_parameters": list(result.representative_parameters),
        "statements": [*result.statements, *_book_statements(result.assumption_tallies)],
        "automatic_option_statement": result.k_ao_statement,
        "post_shock_floor": result.post_shock_floor,
        "mandate": book.mandate.to_dict(),
    }


def _book_statements(tallies: Mapping[str, int]) -> list[str]:
    """What the book's own assumptions did to this measure, in order.

    Driven by the tallies rather than declared alongside them, so an assumption
    that was never applied never prints a warning the reader has to discount.
    """
    return [
        statement
        for marker, statement in BOOK_STATEMENTS.items()
        if int(tallies.get(marker, 0)) > 0
    ]


def _line_amount(value: Decimal) -> Decimal:
    return value.quantize(_LINE_ITEM_QUANTUM, rounding=ROUND_HALF_UP)


def _line_items(
    result: sf.SfResult,
) -> list[tuple[str, str, str, Decimal | None, Decimal]]:
    rows: list[tuple[str, str, str, Decimal | None, Decimal]] = []
    for row in result.ladder_base:
        rows.append(
            (
                SECTION_LADDER,
                f"{row.currency}:{row.bucket_key}",
                f"{row.currency} net banking-book cash flow, bucket {row.bucket_key}",
                row.principal,
                row.net,
            )
        )
    for scenario in result.scenarios:
        for currency_row in scenario.by_currency:
            rows.append(
                (
                    SECTION_EVE,
                    f"{currency_row.currency}:{scenario.code}",
                    f"{currency_row.currency} change in economic value — {scenario.label}",
                    currency_row.delta_eve_native,
                    currency_row.delta_eve_reporting,
                )
            )
    for row in result.table8:
        rows.append(
            (
                SECTION_NII,
                row.code,
                f"Change in net interest income — {row.label}",
                None,
                row.delta_nii,
            )
        )
    return rows


def _validations(book: _Book, result: sf.SfResult) -> list[tuple[str, bool, str, str]]:
    rows: list[tuple[str, bool, str, str]] = []
    threshold = _plain(result.outlier_threshold_pct)
    measured = _plain(result.pct_tier1)
    if result.outlier:
        rows.append(
            (
                RULE_OUTLIER,
                False,
                "error",
                f"The economic value measure is {measured}% of Tier 1 capital, above the "
                f"{threshold}% supervisory outlier threshold.",
            )
        )
    else:
        rows.append(
            (
                RULE_OUTLIER,
                True,
                "info",
                f"The economic value measure is {measured}% of Tier 1 capital, below the "
                f"{threshold}% supervisory outlier threshold.",
            )
        )
    applied = sum(count for count in result.assumption_tallies.values() if count > 0)
    if applied:
        named = ", ".join(
            f"{ASSUMPTION_LABELS.get(marker, marker)} ({count})"
            for marker, count in sorted(result.assumption_tallies.items())
            if count > 0
        )
        rows.append(
            (
                RULE_ASSUMPTION_DEFAULTS,
                False,
                "warning",
                f"{applied} modelling defaults were applied because the book did not state "
                f"the terms: {named}.",
            )
        )
    if result.parameters_pending_confirmation:
        labels = ", ".join(
            sfp.PARAMETER_LABELS.get(code, code)
            for code in result.parameters_pending_confirmation
        )
        rows.append(
            (
                RULE_PARAMETERS_PENDING,
                True,
                "info",
                f"These governed inputs are still pending confirmation with the "
                f"supervisor: {labels}.",
            )
        )
    material = [scope.currency for scope in result.currencies if scope.material]
    if result.excluded_currencies:
        rows.append(
            (
                RULE_CURRENCY_SCOPE,
                True,
                "info",
                f"Measured in {', '.join(material) or 'no currency'}. Below the materiality "
                f"threshold and therefore not measured: "
                f"{', '.join(result.excluded_currencies)}.",
            )
        )
    else:
        rows.append(
            (
                RULE_CURRENCY_SCOPE,
                True,
                "info",
                f"Every currency in the banking book is material and measured: "
                f"{', '.join(material) or 'none'}.",
            )
        )
    if book.exclusions:
        named = ", ".join(
            f"{EXCLUSION_LABELS.get(marker, marker)} ({count})"
            for marker, (count, _amount) in sorted(book.exclusions.items())
        )
        rows.append(
            (
                "sf_positions_excluded",
                False,
                "warning",
                f"Positions the framework did not measure: {named}.",
            )
        )
    return rows


def _plain(value: Decimal) -> str:
    """A Decimal as a reader would write it — no exponent, no trailing zeros."""
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


# --- inputs ------------------------------------------------------------------


def _resolve_parameters(
    db: Session, bank: Bank, as_of: date
) -> dict[str, regulatory_parameters.ResolvedParameter]:
    rows: dict[str, regulatory_parameters.ResolvedParameter] = {}
    for code in sfp.REQUIRED_CODES:
        try:
            rows[code] = regulatory_parameters.resolve(db, bank, code, as_of=as_of)
        except regulatory_parameters.RegulatoryParameterError as exc:
            raise SfRunError(
                "missing_parameter",
                str(exc),
                {"param_code": code},
            ) from exc
    return rows


def _load_book(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    reporting_currency: str,
) -> _Book:
    as_of = period.period_end
    rows = _resolve_parameters(db, bank, as_of)
    try:
        params = sfp.parse_parameters(rows)
    except sfp.SfParameterError as exc:
        raise SfRunError(exc.code, str(exc), dict(exc.detail)) from exc

    records = _canonical_records(db, ctx, bank, as_of)
    behaviour = _behavioural_assumptions(db, ctx, bank, as_of)
    scan = _scan_records(records, reporting_currency, params, as_of, behaviour)
    if not scan.instrument_count and not scan.nmds and not scan.options:
        raise SfRunError(
            "no_positions_as_of",
            "The reporting date has no banking-book positions the Standardised "
            "Framework can measure.",
            {"as_of": as_of.isoformat()},
        )
    groups, nmds, options = scan.groups, scan.nmds, scan.options
    tallies, exclusions, sizes = scan.tallies, scan.exclusions, scan.sizes

    fx = _fx_rates(db, bank, sizes, reporting_currency, as_of)
    curves = _curves(db, bank, sizes, as_of)
    currencies = _currency_inputs(sizes, fx, curves, params)
    tier1 = _tier1(db, ctx, bank, period)
    ladders = tuple(_ladder(group) for group in _sorted_groups(groups))

    mandate = sf_mandate(db, bank, as_of=as_of, today=datetime.now(UTC).date())
    inputs = sf.SfInputs(
        as_of=as_of,
        reporting_currency=reporting_currency,
        ladders=ladders,
        nmds=tuple(nmds),
        currencies=currencies,
        tier1=tier1,
        automatic_options=tuple(options),
        tallies=tallies,
    )
    book = _Book(
        inputs=inputs,
        parameters=rows,
        params=params,
        curves=curves,
        fx=fx,
        instrument_count=scan.instrument_count,
        exclusions=exclusions,
        ladder_digest=scan.digest(),
        nmd_count=len(nmds),
        mandate=mandate,
    )
    book.snapshot = _build_snapshot(bank, period, reporting_currency, book)
    return book



@dataclass
class _Scan:
    """What one pass over the canonical book produced.

    Split out of :func:`_load_book` so the classification rules — which
    position types the framework measures, which are excluded by rule, and
    which are refused outright — read as one thing.
    """

    groups: dict[tuple[str, str, str], _LadderGroup] = field(default_factory=dict)
    nmds: list[sf.NmdBalance] = field(default_factory=list)
    options: list[sf.AutomaticOption] = field(default_factory=list)
    tallies: dict[str, int] = field(default_factory=dict)
    exclusions: dict[str, tuple[int, Decimal]] = field(default_factory=dict)
    sizes: dict[str, _CurrencySize] = field(default_factory=dict)
    parts: list[str] = field(default_factory=list)
    instrument_count: int = 0

    def digest(self) -> str:
        """Proof of the exact position set, without storing 167k rows."""
        return hashlib.sha256("\n".join(sorted(self.parts)).encode()).hexdigest()


def _scan_records(
    records: Sequence[tuple[Any, Any, Any, Any]],
    reporting_currency: str,
    params: sfp.SfParameters,
    as_of: date,
    behaviour: Mapping[str, Mapping[str, Decimal]],
) -> _Scan:
    scan = _Scan()
    for snapshot, position, counterparty, product in records:
        currency = (position.currency or reporting_currency).upper()
        balance = _dec_or_none(snapshot.balance)
        if balance is None or balance == _ZERO:
            _count_exclusion(scan.exclusions, EXCLUDED_NO_BALANCE, _ZERO)
            continue
        position_type = position.position_type
        if position_type in _OUT_OF_SCOPE_TYPES:
            _count_exclusion(scan.exclusions, EXCLUDED_OUT_OF_SCOPE, abs(balance))
            continue
        option_type = _option_type(snapshot)
        if option_type is not None:
            # Collected rather than skipped: the engine REFUSES a book with
            # options (DV-010), and it can only refuse what it is told about.
            scan.options.append(
                sf.AutomaticOption(
                    ref=snapshot.source_reference,
                    currency=currency,
                    option_type=option_type,
                    notional=abs(_dec_or_none(snapshot.notional) or balance),
                )
            )
            continue
        if position_type in ("DERIVATIVE", "FX_HEDGE"):
            # Non-option derivatives enter only when the legs are stated;
            # otherwise the notional is excluded and COUNTED, never zero-filled.
            _count_exclusion(scan.exclusions, EXCLUDED_UNMODELLED_DERIVATIVE, abs(balance))
            continue
        family = None if position_type == "INTEREST_RATE_SWAP" else _family_for(
            position_type, snapshot
        )
        non_maturing = position_type == "DEPOSIT" and _is_non_maturing(snapshot)
        if family is None and position_type != "INTEREST_RATE_SWAP" and not non_maturing:
            _count_exclusion(scan.exclusions, EXCLUDED_OUT_OF_SCOPE, abs(balance))
            continue

        scan.instrument_count += 1
        size = scan.sizes.setdefault(currency, _CurrencySize())
        scan.parts.append(
            "|".join(
                [
                    snapshot.source_reference,
                    currency,
                    position_type,
                    str(balance),
                    ""
                    if snapshot.contractual_maturity is None
                    else snapshot.contractual_maturity.isoformat(),
                ]
            )
        )
        if non_maturing:
            scan.nmds.append(
                _nmd_balance(snapshot, counterparty, product, currency, abs(balance), behaviour)
            )
            size.liabilities_native += abs(balance)
            continue
        if position_type == "INTEREST_RATE_SWAP":
            # A swap has no balance-sheet principal; its notional sits on the
            # asset side only so the currency's materiality share reflects it.
            size.assets_native += abs(balance)
            _project_swap(snapshot, currency, abs(balance), params, as_of, scan)
            continue

        side: sfcf.Side = "asset" if position_type in _ASSET_FAMILIES else "liability"
        if side == "asset":
            size.assets_native += abs(balance)
        else:
            size.liabilities_native += abs(balance)
        _project_position(
            snapshot,
            position,
            product,
            counterparty,
            family=str(family),
            side=side,
            currency=currency,
            principal=abs(balance),
            params=params,
            as_of=as_of,
            behaviour=behaviour,
            scan=scan,
        )
    return scan


def _sorted_groups(groups: Mapping[tuple[str, str, str], _LadderGroup]) -> list[_LadderGroup]:
    return [groups[key] for key in sorted(groups)]


def _canonical_records(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> Sequence[Any]:
    """(snapshot, position, counterparty, product) for the reporting date.

    Current-generation only: superseded and withdrawn rows are excluded, and
    the validation status must be one a CALCULATION may read.
    """
    return db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalCounterparty, CanonicalProduct
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()


def _behavioural_assumptions(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, dict[str, Decimal]]:
    """Rate assumptions by type and product code, from the latest batch.

    Reference rows are BATCH-scoped: they carry no supersession chain and no
    validation status of their own, so "current" means the newest ingestion
    batch of the ``behavioral_assumptions`` kind at or before the reporting
    date — the same rule ``fact_derivation`` reads them under. Reading them any
    other way would mix two model generations in one ladder.

    An absent assumption is NOT a zero: the caller tallies it as a disclosed
    default so a reader can see the contractual schedule was used instead.
    """
    batches = db.execute(
        select(
            CanonicalReferenceRow.ingestion_batch_id,
            func.max(CanonicalReferenceRow.created_at),
        )
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank.id,
            CanonicalReferenceRow.dataset_kind == DATASET_BEHAVIOURAL,
            CanonicalReferenceRow.as_of_date <= as_of,
        )
        .group_by(CanonicalReferenceRow.ingestion_batch_id)
    ).all()
    if not batches:
        return {}
    # Postgres has no max(uuid), so the winner is picked here: newest
    # created_at, then the batch UUIDv7 text (time-ordered) as the tie-break.
    batch_id = max(batches, key=lambda row: (row[1], str(row[0])))[0]
    payloads = db.scalars(
        select(CanonicalReferenceRow.payload)
        .where(
            CanonicalReferenceRow.organization_id == ctx.organization_id,
            CanonicalReferenceRow.bank_id == bank.id,
            CanonicalReferenceRow.dataset_kind == DATASET_BEHAVIOURAL,
            CanonicalReferenceRow.ingestion_batch_id == batch_id,
        )
        .order_by(CanonicalReferenceRow.row_index)
    ).all()
    table: dict[str, dict[str, Decimal]] = {}
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        assumption = str(payload.get("assumption_type", "")).strip().upper()
        product_code = str(payload.get("product_code", "")).strip()
        value = _dec_or_none(payload.get("value"))
        if not assumption or not product_code or value is None:
            continue
        table.setdefault(assumption, {})[product_code] = value
    return table


def _family_for(position_type: str, snapshot: Any) -> str | None:
    if position_type in _ASSET_FAMILIES:
        if position_type == "SECURITY_HOLDING" and _is_trading_or_equity(snapshot):
            return None
        return _ASSET_FAMILIES[position_type]
    if position_type in _LIABILITY_FAMILIES:
        if position_type == "OTHER_LIABILITY" and _dec_or_none(snapshot.interest_rate) is None:
            return None
        return _LIABILITY_FAMILIES[position_type]
    if position_type == "DEPOSIT":
        return "DEPOSIT_TERM"
    return None


def _is_trading_or_equity(snapshot: Any) -> bool:
    attributes = snapshot.attributes or {}
    if str(attributes.get("book", "")).strip().lower() == "trading":
        return True
    return str(attributes.get("instrument_class", "")).strip().lower() == "equity"


def _option_type(snapshot: Any) -> str | None:
    attributes = snapshot.attributes or {}
    for key in ("option_type", "optionality", "embedded_option"):
        value = attributes.get(key)
        if value:
            return str(value)
    return None


def _is_non_maturing(snapshot: Any) -> bool:
    account = (snapshot.deposit_account_type or "").upper()
    if account in _NON_MATURING_ACCOUNTS:
        return True
    return account == "" and snapshot.contractual_maturity is None


def _nmd_category(counterparty: Any, snapshot: Any) -> sfp.NmdCategory:
    counterparty_type = "" if counterparty is None else (counterparty.counterparty_type or "")
    if counterparty_type.upper() not in _RETAIL_COUNTERPARTIES:
        return "wholesale"
    account = (snapshot.deposit_account_type or "").upper()
    if account in _TRANSACTIONAL_ACCOUNTS:
        return "retail_transactional"
    return "retail_non_transactional"


def _nmd_balance(  # noqa: PLR0913 - one argument per canonical source of the row
    snapshot: Any,
    counterparty: Any,
    product: Any,
    currency: str,
    balance: Decimal,
    behaviour: Mapping[str, Mapping[str, Decimal]],
) -> sf.NmdBalance:
    product_code = _product_code(snapshot, product)
    core = behaviour.get(ASSUMPTION_TYPE_NMD_CORE, {}).get(product_code)
    duration_months = behaviour.get(ASSUMPTION_TYPE_NMD_DURATION, {}).get(product_code)
    attributes = snapshot.attributes or {}
    history = _dec_or_none(attributes.get("deposit_history_years"))
    return sf.NmdBalance(
        currency=currency,
        category=_nmd_category(counterparty, snapshot),
        product=product_code,
        balance=balance,
        core_estimate=core,
        core_maturity_years=(
            None if duration_months is None else duration_months / _MONTHS_IN_YEAR
        ),
        history_years=history,
    )


def _product_code(snapshot: Any, product: Any) -> str:
    if product is not None and product.product_code:
        return str(product.product_code)
    attributes = snapshot.attributes or {}
    return str(attributes.get("product_code") or snapshot.source_reference)


def _ladder_kind(
    family: str, counterparty: Any, cpr: Decimal | None, tdrr: Decimal | None
) -> sf.LadderKind:
    if family == "LOAN":
        return sf.KIND_PREPAYABLE if cpr is not None else sf.KIND_FIXED
    if family == "DEPOSIT_TERM":
        if tdrr is not None:
            return sf.KIND_TD_RETAIL_REDEEMABLE
        counterparty_type = "" if counterparty is None else (counterparty.counterparty_type or "")
        if counterparty_type.upper() in _RETAIL_COUNTERPARTIES:
            return sf.KIND_FIXED
        # A wholesale term deposit with no early-withdrawal evidence is priced
        # at the exercise most disadvantageous to the bank, on ONE book: the
        # engine settles the exercise once and applies it to the base case and
        # every shocked case alike (F4; see standardised._exercise_policy). The
        # assumption is tallied by the caller, because an absent attribute is
        # not evidence of a redemption penalty.
        return sf.KIND_TD_WHOLESALE_REDEEMABLE
    return sf.KIND_FIXED


def _project_position(  # noqa: PLR0913 - one argument per dimension of the position
    snapshot: Any,
    position: Any,
    product: Any,
    counterparty: Any,
    *,
    family: str,
    side: sfcf.Side,
    currency: str,
    principal: Decimal,
    params: sfp.SfParameters,
    as_of: date,
    behaviour: Mapping[str, Mapping[str, Decimal]],
    scan: _Scan,
) -> None:
    product_code = _product_code(snapshot, product)
    cpr = behaviour.get(ASSUMPTION_TYPE_PREPAYMENT, {}).get(product_code)
    tdrr = behaviour.get(ASSUMPTION_TYPE_REDEMPTION, {}).get(product_code)
    kind = _ladder_kind(family, counterparty, cpr, tdrr)
    if family == "LOAN" and cpr is None:
        _tally(scan.tallies, sf.TALLY_NO_PREPAYMENT_RATE)
    # Two different assumptions hide behind one absent redemption rate, and
    # they are not the same exposure. A RETAIL term deposit with no rate runs
    # to contract. A WHOLESALE one is priced as demandable at par, which is a
    # far stronger assumption and was previously reported under a label saying
    # the contractual schedule had been used (audit W1, 2026-09-20).
    if kind == sf.KIND_TD_WHOLESALE_REDEEMABLE:
        _tally(scan.tallies, TALLY_TD_WHOLESALE_DEMANDABLE)
    elif family == "DEPOSIT_TERM" and tdrr is None:
        _tally(scan.tallies, sf.TALLY_NO_REDEMPTION_RATE)
    attributes = snapshot.attributes or {}
    terms = sfcf.InstrumentTerms(
        ref=snapshot.source_reference,
        family=family,
        currency=currency,
        side=side,
        principal=principal,
        rate_pct=(_dec_or_none(snapshot.interest_rate) or _ZERO) * _HUNDRED,
        rate_type="FLOATING" if (snapshot.rate_type or "FIXED") == "FLOATING" else "FIXED",
        maturity=snapshot.contractual_maturity,
        next_reset=snapshot.next_repricing_date,
        start_date=position.origination_date,
        spread_pct=_spread_pct(snapshot),
        amortisation=_amortisation(attributes),
        frequency_months=_frequency_months(attributes),
    )
    flows = sfcf.project(terms, as_of, params)
    for marker in flows.defaulted:
        _tally(scan.tallies, marker)
    _accumulate(scan, currency, product_code, kind, side, flows, cpr, tdrr, principal)


def _project_swap(  # noqa: PLR0913 - the swap's identity is its own arguments
    snapshot: Any,
    currency: str,
    notional: Decimal,
    params: sfp.SfParameters,
    as_of: date,
    scan: _Scan,
) -> None:
    attributes = snapshot.attributes or {}
    pay_fixed = str(attributes.get("pay_leg", "fixed")).strip().lower() == "fixed"
    terms = sfcf.InstrumentTerms(
        ref=snapshot.source_reference,
        family=sfcf.FAMILY_SWAP_FIXED_LEG,
        currency=currency,
        side="asset",
        principal=notional,
        rate_pct=(_dec_or_none(snapshot.interest_rate) or _ZERO) * _HUNDRED,
        maturity=snapshot.contractual_maturity,
        next_reset=snapshot.next_repricing_date,
    )
    fixed_leg, float_leg = sfcf.swap_legs(
        terms, pay_fixed=pay_fixed, float_reset=snapshot.next_repricing_date
    )
    for leg in (fixed_leg, float_leg):
        flows = sfcf.project(leg, as_of, params)
        for marker in flows.defaulted:
            _tally(scan.tallies, marker)
        _accumulate(
            scan,
            currency,
            f"swap:{leg.family}",
            sf.KIND_FIXED,
            leg.side,
            flows,
            None,
            None,
            notional,
        )


def _accumulate(  # noqa: PLR0913 - the group key plus its weighted rates
    scan: _Scan,
    currency: str,
    portfolio: str,
    kind: sf.LadderKind,
    side: sfcf.Side,
    flows: sfcf.ProjectedFlows,
    cpr: Decimal | None,
    tdrr: Decimal | None,
    principal: Decimal,
) -> None:
    sign = Decimal(1) if side == "asset" else Decimal(-1)
    key = (currency, portfolio, kind)
    group = scan.groups.get(key)
    if group is None:
        size = len(flows.principal)
        group = _LadderGroup(
            currency=currency,
            portfolio=portfolio,
            kind=kind,
            principal=[_ZERO] * size,
            interest=[_ZERO] * size,
        )
        scan.groups[key] = group
    for index, amount in enumerate(flows.principal):
        group.principal[index] += sign * amount
    for index, amount in enumerate(flows.interest):
        group.interest[index] += sign * amount
    if cpr is not None:
        group.cpr_weight += principal
        group.cpr_amount += principal * cpr
    if tdrr is not None:
        group.tdrr_weight += principal
        group.tdrr_amount += principal * tdrr


def _ladder(group: _LadderGroup) -> sf.Ladder:
    outstanding = _running_outstanding(group.principal)
    return sf.Ladder(
        currency=group.currency,
        portfolio=group.portfolio,
        kind=group.kind,
        principal=tuple(group.principal),
        interest=tuple(group.interest),
        outstanding_end=tuple(outstanding),
        cpr0=(
            None
            if group.cpr_weight == _ZERO
            else group.cpr_amount / group.cpr_weight
        ),
        tdrr0=(
            None
            if group.tdrr_weight == _ZERO
            else group.tdrr_amount / group.tdrr_weight
        ),
    )


def _running_outstanding(principal: Sequence[Decimal]) -> list[Decimal]:
    remaining = sum(principal, _ZERO)
    ends: list[Decimal] = []
    for amount in principal:
        remaining -= amount
        ends.append(remaining)
    return ends


def _tally(tallies: dict[str, int], marker: str) -> None:
    tallies[marker] = tallies.get(marker, 0) + 1


def _count_exclusion(
    exclusions: dict[str, tuple[int, Decimal]], marker: str, amount: Decimal
) -> None:
    count, total = exclusions.get(marker, (0, _ZERO))
    exclusions[marker] = (count + 1, total + amount)


def _spread_pct(snapshot: Any) -> Decimal | None:
    spread = _dec_or_none(snapshot.rate_spread)
    return None if spread is None else spread * _HUNDRED


def _amortisation(attributes: Mapping[str, Any]) -> str | None:
    value = attributes.get("amortisation") or attributes.get("amortization")
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in sfp.AMORTISATIONS else None


def _frequency_months(attributes: Mapping[str, Any]) -> int | None:
    value = attributes.get("payment_frequency_months")
    if value is None:
        return None
    parsed = _dec_or_none(value)
    if parsed is None or parsed < _ZERO or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _fx_rates(
    db: Session,
    bank: Bank,
    sizes: Mapping[str, _CurrencySize],
    reporting_currency: str,
    as_of: date,
) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {reporting_currency: Decimal(1)}
    for currency in sorted(sizes):
        if currency == reporting_currency:
            continue
        view = market_data_sources.preferred_fx_spot(
            db,
            bank.organization_id,
            bank.id,
            currency,
            reporting_currency,
            as_of,
        )
        if view is None:
            raise SfRunError(
                "missing_fx_rate",
                f"No exchange rate is available to convert {currency} into "
                f"{reporting_currency} at {as_of.isoformat()}.",
                {"currency": currency, "reporting_currency": reporting_currency},
            )
        rates[currency] = view.rate
    return rates


def _curves(
    db: Session, bank: Bank, sizes: Mapping[str, _CurrencySize], as_of: date
) -> dict[str, market_data.CurveView]:
    curves: dict[str, market_data.CurveView] = {}
    for currency in sorted(sizes):
        view = market_data.get_yield_curve(
            db, bank.organization_id, bank.id, currency, as_of
        )
        if view is not None:
            curves[currency] = view
    return curves


def _currency_inputs(
    sizes: Mapping[str, _CurrencySize],
    fx: Mapping[str, Decimal],
    curves: Mapping[str, market_data.CurveView],
    params: sfp.SfParameters,
) -> tuple[sf.CurrencyInputs, ...]:
    inputs: list[sf.CurrencyInputs] = []
    for currency in sorted(sizes):
        size = sizes[currency]
        rate = fx[currency]
        curve = curves.get(currency)
        zero_cc = () if curve is None else _zero_cc_at_midpoints(curve, params)
        inputs.append(
            sf.CurrencyInputs(
                currency=currency,
                zero_cc=zero_cc,
                fx_to_reporting=rate,
                bb_assets_rep=size.assets_native * rate,
                bb_liabilities_rep=size.liabilities_native * rate,
            )
        )
    return tuple(inputs)


def _zero_cc_at_midpoints(
    curve: market_data.CurveView, params: sfp.SfParameters
) -> tuple[Decimal, ...]:
    """The curve as CONTINUOUSLY-compounded zero rates at each bucket midpoint.

    Market data carries annually-compounded decimal fractions (0.245, never
    24.5), so the conversion is ``ln(1 + r)``. Between quoted tenors the rate
    interpolates linearly; beyond the ends it is held flat, which is the same
    convention the legacy engine's curve reader uses.
    """
    nodes = sorted((Decimal(months), Decimal(rate)) for months, rate in curve.points)
    if not nodes:
        return ()
    out: list[Decimal] = []
    for midpoint in params.midpoints:
        months = midpoint * _MONTHS_IN_YEAR
        out.append(_continuous(_interpolate(nodes, months)))
    return tuple(out)


def _interpolate(nodes: Sequence[tuple[Decimal, Decimal]], months: Decimal) -> Decimal:
    if months <= nodes[0][0]:
        return nodes[0][1]
    if months >= nodes[-1][0]:
        return nodes[-1][1]
    for index in range(1, len(nodes)):
        left_x, left_y = nodes[index - 1]
        right_x, right_y = nodes[index]
        if months <= right_x:
            if right_x == left_x:
                return right_y
            weight = (months - left_x) / (right_x - left_x)
            return left_y + weight * (right_y - left_y)
    return nodes[-1][1]  # pragma: no cover - the loop brackets every interior tenor


def _continuous(annual: Decimal) -> Decimal:
    """``ln(1 + r)``, refusing a rate that would make the log undefined."""
    base = Decimal(1) + annual
    if base <= _ZERO:
        raise SfRunError(
            "invalid_parameter",
            "A published zero rate of -100% or lower cannot be converted to a "
            "continuously compounded rate.",
            {"rate": str(annual)},
        )
    return base.ln()


def _tier1(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> Decimal:
    tier1 = tier1_for_period(db, ctx, bank, period)
    if tier1 <= _ZERO:
        raise SfRunError(
            "tier1_unavailable",
            "The outlier test needs a positive Tier 1 capital figure, and none could be "
            "derived from the capital-component facts.",
            {"as_of": period.period_end.isoformat()},
        )
    return tier1


# --- snapshot ----------------------------------------------------------------


def _build_snapshot(
    bank: Bank, period: BankReportingPeriod, reporting_currency: str, book: _Book
) -> dict[str, Any]:
    """The value-based input snapshot the ``input_hash`` seals.

    Value-based like every other module (AGENTS.md): no row ids, no
    timestamps, no ordering that a re-read could change. The ladders are the
    aggregate, not the 167k positions, and ``instrument_digest`` proves the set
    without storing it.
    """
    inputs = book.inputs
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_IRR_SF,
        "bank_id": str(bank.id),
        "reporting_currency": reporting_currency,
        "jurisdiction_code": bank.jurisdiction_code,
        "as_of_date": period.period_end.isoformat(),
        "reporting_period": {
            "id": str(period.id),
            "label": period.label,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
        "parameters": {
            code: _parameter_snapshot(row) for code, row in sorted(book.parameters.items())
        },
        "currencies": [
            {
                "currency": currency.currency,
                "fx_to_reporting": str(currency.fx_to_reporting),
                "bb_assets": str(currency.bb_assets_rep),
                "bb_liabilities": str(currency.bb_liabilities_rep),
                "curve": _curve_snapshot(book.curves.get(currency.currency), currency.zero_cc),
            }
            for currency in inputs.currencies
        ],
        "ladders": [
            {
                "currency": ladder.currency,
                "portfolio": ladder.portfolio,
                "kind": ladder.kind,
                "principal": [str(value) for value in ladder.principal],
                "interest": [str(value) for value in ladder.interest],
                "outstanding_end": [str(value) for value in ladder.outstanding_end],
                "cpr0": None if ladder.cpr0 is None else str(ladder.cpr0),
                "tdrr0": None if ladder.tdrr0 is None else str(ladder.tdrr0),
            }
            for ladder in inputs.ladders
        ],
        "nmds": [
            {
                "currency": nmd.currency,
                "category": nmd.category,
                "product": nmd.product,
                "balance": str(nmd.balance),
                "core_estimate": None if nmd.core_estimate is None else str(nmd.core_estimate),
                "core_maturity_years": None if nmd.core_maturity_years is None else str(
                    nmd.core_maturity_years
                ),
                "history_years": None if nmd.history_years is None else str(nmd.history_years),
            }
            for nmd in sorted(inputs.nmds, key=lambda row: (row.currency, row.product))
        ],
        "automatic_options": [
            {
                "ref": option.ref,
                "currency": option.currency,
                "option_type": option.option_type,
                "notional": str(option.notional),
            }
            for option in sorted(inputs.automatic_options, key=lambda row: row.ref)
        ],
        "data_quality": {
            "defaulted": dict(sorted(inputs.tallies.items())),
            "excluded": {
                marker: {"count": count, "amount_reporting": str(amount)}
                for marker, (count, amount) in sorted(book.exclusions.items())
            },
            "instrument_count": book.instrument_count,
            "instrument_digest": book.ladder_digest,
        },
    }


def _parameter_snapshot(row: regulatory_parameters.ResolvedParameter) -> Any:
    """The VALUE only. Row ids and timestamps are identity, not value, and live
    in ``run.parameter_provenance`` beside the snapshot (audit D-18)."""
    if row.value_json is not None:
        return row.value_json
    return None if row.value is None else str(row.value)


def _curve_snapshot(
    curve: market_data.CurveView | None, zero_cc: Sequence[Decimal]
) -> dict[str, Any] | None:
    if curve is None:
        return None
    return {
        "name": curve.curve_name,
        "type": curve.curve_type,
        "as_of": curve.as_of_date.isoformat(),
        "compounding": "annual",
        "zero_cc_at_midpoints": [str(value) for value in zero_cc],
    }


def _empty_snapshot(
    bank: Bank, period: BankReportingPeriod, reporting_currency: str, error: SfRunError
) -> dict[str, Any]:
    """The snapshot of a run that could not assemble its book.

    Still value-based and still hashed, so a refusal is as reproducible as a
    result — a reader can tell two refusals apart.
    """
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_IRR_SF,
        "bank_id": str(bank.id),
        "reporting_currency": reporting_currency,
        "jurisdiction_code": bank.jurisdiction_code,
        "as_of_date": period.period_end.isoformat(),
        "reporting_period": {
            "id": str(period.id),
            "label": period.label,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
        "refused": {"code": error.code, "details": error.details or {}},
    }


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# --- read model --------------------------------------------------------------


def _read_model(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    run: RegulatoryRun,
) -> IrrbbSfRead:
    metrics = run.metrics or {}
    run_read = _read_regulatory_run_execution_result(db, ctx, bank, run.id)
    params = _parameters_read(run)
    buckets = _buckets_read(run)
    bucket_labels = {bucket.key: bucket.label for bucket in buckets}
    mandate = metrics.get("mandate") or {}
    return IrrbbSfRead(
        run=run_read,
        as_of=period.period_end,
        reporting_currency=str(metrics.get("reporting_currency") or ""),
        mandate=IrrbbSfMandateRead(
            mandatory=bool(mandate.get("mandatory", False)),
            mandatory_from=_date_or_none(mandate.get("mandatory_from")),
            as_of=period.period_end,
            confirmation_status=str(mandate.get("confirmation_status") or ""),
            source_citation=str(mandate.get("source_citation") or ""),
            statement=str(mandate.get("statement") or ""),
        ),
        parameters=params,
        buckets=buckets,
        currencies=_currencies_read(metrics, run),
        excluded_currencies=list(metrics.get("excluded_currencies") or []),
        scenarios=_scenarios_read(metrics),
        measures=_measures_read(metrics),
        tier1=_dec(metrics.get("tier1")),
        pct_tier1=_dec(metrics.get("pct_tier1")),
        outlier=bool(metrics.get("outlier", False)),
        outlier_threshold_pct=_dec(metrics.get("outlier_threshold_pct")),
        table8=[
            IrrbbSfTable8RowRead(
                code=str(row.get("code")),
                label=str(row.get("label")),
                delta_eve=_dec(row.get("delta_eve")),
                delta_eve_net=_dec(row.get("delta_eve_net")),
                delta_nii=_dec(row.get("delta_nii")),
                delta_eve_prior=_dec_or_none(row.get("delta_eve_prior")),
                delta_nii_prior=_dec_or_none(row.get("delta_nii_prior")),
            )
            for row in metrics.get("table8") or []
        ],
        table7_quantitative=IrrbbSfTable7Read(
            average_repricing_maturity_years=_dec(
                (metrics.get("table7_quantitative") or {}).get(
                    "average_repricing_maturity_years"
                )
            ),
            longest_repricing_maturity_years=_dec(
                (metrics.get("table7_quantitative") or {}).get(
                    "longest_repricing_maturity_years"
                )
            ),
        ),
        nmd_categories=[
            IrrbbSfNmdCategoryRead(
                currency=str(row.get("currency")),
                category=str(row.get("category")),
                label=NMD_CATEGORY_LABELS.get(
                    str(row.get("category")), str(row.get("category"))
                ),
                balance=_dec(row.get("balance")),
                core=_dec(row.get("core")),
                non_core=_dec(row.get("non_core")),
                core_cap_pct=_dec(row.get("core_cap_pct")),
                cap_binding=bool(row.get("cap_binding", False)),
                average_core_maturity_years=_dec(row.get("average_core_maturity_years")),
                longest_core_maturity_years=_dec(row.get("longest_core_maturity_years")),
            )
            for row in metrics.get("nmd_disclosure") or []
        ],
        ladders=[
            IrrbbSfLadderRowRead(
                currency=str(row.get("currency")),
                bucket_key=str(row.get("bucket_key")),
                bucket_label=bucket_labels.get(
                    str(row.get("bucket_key")), str(row.get("bucket_key"))
                ),
                principal=_dec(row.get("principal")),
                interest=_dec(row.get("interest")),
                net=_dec(row.get("net")),
            )
            for row in metrics.get("ladder_base") or []
        ],
        data_quality=IrrbbSfDataQualityRead(
            instrument_count=int(metrics.get("instrument_count") or 0),
            assumptions=[
                IrrbbSfAssumptionRead(
                    marker=marker,
                    label=ASSUMPTION_LABELS.get(marker, marker),
                    count=int(count),
                )
                for marker, count in sorted((metrics.get("assumption_tallies") or {}).items())
                if int(count) > 0
            ],
            exclusions=[
                IrrbbSfExclusionRead(
                    marker=marker,
                    label=EXCLUSION_LABELS.get(marker, marker),
                    count=int(body.get("count") or 0),
                    amount_reporting=_dec(body.get("amount_reporting")),
                )
                for marker, body in sorted((metrics.get("exclusions") or {}).items())
            ],
        ),
        statements=list(metrics.get("statements") or []),
        automatic_option_statement=str(metrics.get("automatic_option_statement") or ""),
        post_shock_floor_statement=_post_shock_statement(
            str(metrics.get("post_shock_floor") or "")
        ),
    )


def _post_shock_statement(code: str) -> str:
    """What was done about a post-shock rate floor, and what is unsettled.

    Corrected 2026-09-20 (audit U-3/R-5). The sentence used to read "The
    framework text prescribes no post-shock rate floor" — a claim about a
    supervisory standard, printed for the filer, that the platform cannot
    support: the guideline the shock tables were transcribed from is not held
    in this system, and the international standardised framework these shapes
    follow does prescribe a floor that rises from a negative value at the
    shortest tenor to zero at the long end. What the platform can state is what
    it DID, and that the point is open. The values themselves are governed; the
    sentence is ours, so the sentence is what changed.
    """
    if code == sf.POST_SHOCK_FLOOR:
        return (
            "No post-shock rate floor was applied: shocked rates are reported exactly as "
            "the prescribed shock shapes produce them, including where a shape takes a "
            "rate below zero. The guideline extract these shapes were transcribed from "
            "carries no floor provision, while the international standardised framework "
            "they follow does prescribe one, so the absence is recorded as an open point "
            "for the supervisor rather than a settled reading. It binds only where a "
            "downward shock would take a low starting rate below zero."
        )
    return ""


def post_shock_floor_statement(code: str) -> str:
    """The post-shock floor statement, for any surface that republishes a run."""
    return _post_shock_statement(code)


def parameter_value_text(raw: Any) -> str:
    """One governed value as the run stored it, for a bank-facing table."""
    return _value_text(raw)


def _parameters_read(run: RegulatoryRun) -> list[IrrbbSfParameterRead]:
    """Every governed input this run consumed, from its own provenance.

    Read from ``parameter_provenance`` rather than re-resolved, so the answer
    is what the run used, not what the console says today.
    """
    snapshot_values = (run.inputs or {}).get("parameters") or {}
    rows: list[IrrbbSfParameterRead] = []
    for entry in run.parameter_provenance or []:
        code = str(entry.get("param_code"))
        if code not in sfp.REQUIRED_CODES and code != CODE_MANDATORY_FROM:
            continue
        citation = str(entry.get("source_citation") or "")
        confirmation = str(entry.get("confirmation_status") or "")
        representative = (
            code in sfp.REPRESENTATIVE_CODES
            or sfp.REPRESENTATIVE_MARKER in citation.upper()
        )
        raw = snapshot_values.get(code, entry.get("value"))
        provenance = sfp.ParameterProvenance(
            code=code,
            label=sfp.PARAMETER_LABELS.get(code, code),
            unit=str(entry.get("unit") or ""),
            confirmation_status=confirmation,
            source_citation=citation,
            representative=representative,
        )
        rows.append(
            IrrbbSfParameterRead(
                code=code,
                label=provenance.label,
                unit=provenance.unit,
                value=_value_text(raw),
                confirmation_status=confirmation,
                source_citation=citation,
                representative=representative,
                pending_confirmation=provenance.pending_confirmation,
                statement=provenance.statement,
            )
        )
    rows.sort(key=lambda row: row.code)
    return rows


def _value_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def _buckets_read(run: RegulatoryRun) -> list[IrrbbSfBucketRead]:
    body = ((run.inputs or {}).get("parameters") or {}).get(sfp.CODE_TIME_BUCKETS) or {}
    entries = body.get("buckets") if isinstance(body, Mapping) else None
    if not isinstance(entries, list):
        return []
    return [
        IrrbbSfBucketRead(
            key=str(entry.get("key")),
            label=str(entry.get("label") or entry.get("key")),
            midpoint_years=_dec(entry.get("midpoint_years")),
        )
        for entry in entries
        if isinstance(entry, Mapping)
    ]


def _currencies_read(
    metrics: Mapping[str, Any], run: RegulatoryRun
) -> list[IrrbbSfCurrencyScopeRead]:
    curve_by_currency: dict[str, Mapping[str, Any]] = {}
    for entry in (run.inputs or {}).get("currencies") or []:
        curve = entry.get("curve")
        if isinstance(curve, Mapping):
            curve_by_currency[str(entry.get("currency"))] = curve
    rows: list[IrrbbSfCurrencyScopeRead] = []
    for entry in metrics.get("currencies") or []:
        currency = str(entry.get("currency"))
        curve = curve_by_currency.get(currency) or {}
        rows.append(
            IrrbbSfCurrencyScopeRead(
                currency=currency,
                assets_reporting=_dec(entry.get("assets_reporting")),
                liabilities_reporting=_dec(entry.get("liabilities_reporting")),
                asset_share_pct=_dec(entry.get("asset_share_pct")),
                liability_share_pct=_dec(entry.get("liability_share_pct")),
                share_pct=_dec(entry.get("share_pct")),
                material=bool(entry.get("material", False)),
                fx_to_reporting=_dec(entry.get("fx_to_reporting")),
                curve_name=_str_or_none(curve.get("name")),
                curve_as_of=_date_or_none(curve.get("as_of")),
                curve_source=_str_or_none(curve.get("type")),
            )
        )
    return rows


def _scenarios_read(metrics: Mapping[str, Any]) -> list[IrrbbSfScenarioRead]:
    return [
        IrrbbSfScenarioRead(
            code=str(entry.get("code")),
            label=str(entry.get("label")),
            mandatory=bool(entry.get("mandatory", False)),
            in_outlier_set=bool(entry.get("in_outlier_set", False)),
            by_currency=[
                IrrbbSfCurrencyScenarioRead(
                    currency=str(row.get("currency")),
                    eve_base_native=_dec(row.get("eve_base_native")),
                    eve_scenario_native=_dec(row.get("eve_scenario_native")),
                    delta_eve_native=_dec(row.get("delta_eve_native")),
                    delta_eve_reporting=_dec(row.get("delta_eve_reporting")),
                    automatic_option_addon_native=_dec(
                        row.get("automatic_option_addon_native")
                    ),
                    delta_nii_native=_dec(row.get("delta_nii_native")),
                    delta_nii_reporting=_dec(row.get("delta_nii_reporting")),
                )
                for row in entry.get("by_currency") or []
            ],
            loss=_dec(entry.get("loss")),
            net=_dec(entry.get("net")),
            delta_nii=_dec(entry.get("delta_nii")),
        )
        for entry in metrics.get("scenarios") or []
    ]


def _measures_read(metrics: Mapping[str, Any]) -> IrrbbSfMeasuresRead:
    bodies = metrics.get("measures") or {}
    return IrrbbSfMeasuresRead(
        all_scenarios=_measure_read(sf.MEASURE_ALL, bodies.get(sf.MEASURE_ALL) or {}),
        mandatory=_measure_read(sf.MEASURE_MANDATORY, bodies.get(sf.MEASURE_MANDATORY) or {}),
        outlier_set=_measure_read(
            sf.MEASURE_OUTLIER_SET, bodies.get(sf.MEASURE_OUTLIER_SET) or {}
        ),
    )


def _measure_read(name: str, body: Mapping[str, Any]) -> IrrbbSfMeasureRead:
    worst = _str_or_none(body.get("worst_scenario"))
    return IrrbbSfMeasureRead(
        name=name,
        label=MEASURE_LABELS.get(name, name),
        scenarios=list(body.get("scenarios") or []),
        measure=_dec(body.get("measure")),
        worst_scenario=worst,
        worst_scenario_label=None if worst is None else sfp.SCENARIO_LABELS.get(worst, worst),
    )


# --- small helpers -----------------------------------------------------------


def _dec(raw: Any) -> Decimal:
    value = _dec_or_none(raw)
    return _ZERO if value is None else value


def _dec_or_none(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def _str_or_none(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    return text or None


def _date_or_none(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(
            Bank.id == bank_id,
            Bank.organization_id == ctx.organization_id,
        )
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == reporting_period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    return period
