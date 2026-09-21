"""Read model for the IRRBB Standardised Framework.

A sibling of ``app/schemas/regulatory_irr.py``: the legacy Basel-six read model
is untouched, because the Standardised Framework is a NEW scenario set with a
different vocabulary (nineteen buckets, six prescribed shapes, per-currency
aggregation, an outlier test on losses only) rather than a revision of the old
one.

Two rules this module exists to enforce:

* **No raw enum reaches the browser.** Every code that has production copy
  carries its ``label`` beside it — scenarios, deposit categories, time
  buckets, assumption markers and the measure sets.
* **A representative or unconfirmed parameter says so in the payload**, not
  only in the UI. ``IrrbbSfParameterRead.statement`` is the engine's own
  sentence, so the API, the PDF and the screen cannot disagree about whether a
  number is a published supervisory value (D-024, D-039).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.regulatory_liquidity import RegulatoryRunRead


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IrrbbSfRunCreate(ClosedModel):
    """The only input a Standardised Framework run takes.

    No ``scenario_code``: the framework prescribes all six shapes and reports
    them together, so choosing one would be a choice the regulator has already
    made.
    """

    reporting_period_id: UUID


class IrrbbSfParameterRead(ClosedModel):
    """One governed input, with everything a reader needs to judge it."""

    code: str
    label: str
    unit: str
    value: str
    confirmation_status: str
    source_citation: str
    representative: bool
    pending_confirmation: bool
    #: Production copy: "… pending confirmation with the supervisor.",
    #: "… representative only — not a published supervisory value.", or
    #: "… confirmed."
    statement: str


class IrrbbSfMandateRead(ClosedModel):
    """Whether the Standardised Framework is mandatory for this as-of date."""

    mandatory: bool
    mandatory_from: date | None
    as_of: date
    confirmation_status: str
    source_citation: str
    statement: str


class IrrbbSfCurrencyScenarioRead(ClosedModel):
    currency: str
    eve_base_native: Decimal
    eve_scenario_native: Decimal
    delta_eve_native: Decimal
    delta_eve_reporting: Decimal
    automatic_option_addon_native: Decimal
    delta_nii_native: Decimal
    delta_nii_reporting: Decimal


class IrrbbSfScenarioRead(ClosedModel):
    code: str
    label: str
    mandatory: bool
    in_outlier_set: bool
    by_currency: list[IrrbbSfCurrencyScenarioRead]
    #: Losses only: a currency that gains under this shape contributes zero.
    loss: Decimal
    #: The arithmetic sum, gains included — shown beside the loss so a reader
    #: can see what the loss-only rule cost.
    net: Decimal
    delta_nii: Decimal


class IrrbbSfMeasureRead(ClosedModel):
    name: str
    label: str
    scenarios: list[str]
    measure: Decimal
    worst_scenario: str | None
    worst_scenario_label: str | None


class IrrbbSfMeasuresRead(ClosedModel):
    all_scenarios: IrrbbSfMeasureRead
    mandatory: IrrbbSfMeasureRead
    outlier_set: IrrbbSfMeasureRead


class IrrbbSfCurrencyScopeRead(ClosedModel):
    currency: str
    assets_reporting: Decimal
    liabilities_reporting: Decimal
    asset_share_pct: Decimal
    liability_share_pct: Decimal
    share_pct: Decimal
    material: bool
    fx_to_reporting: Decimal
    curve_name: str | None
    curve_as_of: date | None
    curve_source: str | None


class IrrbbSfTable8RowRead(ClosedModel):
    code: str
    label: str
    delta_eve: Decimal
    delta_eve_net: Decimal
    delta_nii: Decimal
    delta_eve_prior: Decimal | None
    delta_nii_prior: Decimal | None


class IrrbbSfNmdCategoryRead(ClosedModel):
    currency: str
    category: str
    label: str
    balance: Decimal
    core: Decimal
    non_core: Decimal
    core_cap_pct: Decimal
    cap_binding: bool
    average_core_maturity_years: Decimal
    longest_core_maturity_years: Decimal


class IrrbbSfTable7Read(ClosedModel):
    average_repricing_maturity_years: Decimal
    longest_repricing_maturity_years: Decimal


class IrrbbSfBucketRead(ClosedModel):
    key: str
    label: str
    midpoint_years: Decimal


class IrrbbSfLadderRowRead(ClosedModel):
    currency: str
    bucket_key: str
    bucket_label: str
    principal: Decimal
    interest: Decimal
    net: Decimal


class IrrbbSfAssumptionRead(ClosedModel):
    """One modelling default, and how often it was applied.

    A representative assumption used forty times is a different exposure from
    one used once, so the count travels with the label (D-060 item 5).
    """

    marker: str
    label: str
    count: int


class IrrbbSfExclusionRead(ClosedModel):
    marker: str
    label: str
    count: int
    amount_reporting: Decimal


class IrrbbSfDataQualityRead(ClosedModel):
    instrument_count: int
    assumptions: list[IrrbbSfAssumptionRead]
    exclusions: list[IrrbbSfExclusionRead]


class IrrbbSfRead(ClosedModel):
    """The full Standardised Framework view for one reporting period."""

    run: RegulatoryRunRead
    as_of: date
    reporting_currency: str
    mandate: IrrbbSfMandateRead
    parameters: list[IrrbbSfParameterRead]
    buckets: list[IrrbbSfBucketRead]
    currencies: list[IrrbbSfCurrencyScopeRead]
    excluded_currencies: list[str]
    scenarios: list[IrrbbSfScenarioRead]
    measures: IrrbbSfMeasuresRead
    tier1: Decimal
    pct_tier1: Decimal
    outlier: bool
    outlier_threshold_pct: Decimal
    table8: list[IrrbbSfTable8RowRead]
    table7_quantitative: IrrbbSfTable7Read
    nmd_categories: list[IrrbbSfNmdCategoryRead]
    ladders: list[IrrbbSfLadderRowRead]
    data_quality: IrrbbSfDataQualityRead
    statements: list[str]
    #: Why the automatic-option add-on is what it is. Silence would read
    #: identically as "no options" and as "options not modelled" (DV-010).
    automatic_option_statement: str
    #: Production copy for the fact that the framework text prescribes no
    #: post-shock rate floor. A reader must not have to infer that from the
    #: numbers, and must not meet the raw code that carries it.
    post_shock_floor_statement: str


# --- the attempt history -----------------------------------------------------


class IrrbbSfAttemptRead(ClosedModel):
    """One Standardised Framework run at a reporting date, whatever it did.

    A REFUSED attempt is a ``failed`` run, so it never reaches the result read
    — and silence there is indistinguishable from "nobody has tried". This is
    the summary that tells the two apart, and it carries the engine's own
    sentence rather than the raw refusal code.
    """

    run_id: UUID
    status: str
    #: Production copy for ``status``. No raw enum reaches the browser.
    status_label: str
    input_hash: str
    engine_version: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    #: The engine's ONE refusal name (D-061), carried unmapped for machines.
    error_code: str | None = None
    #: One sentence a preparer can act on. Empty when the attempt did not fail.
    refusal_statement: str = ""


class IrrbbSfAttemptsRead(ClosedModel):
    """Was the framework tried at this reporting date, and what happened.

    ``latest`` is the seam :func:`app.services.regulatory_irr_sf.latest_sf_attempt`
    answers — the newest run of ANY status. ``has_result`` is the different
    question the result route answers, carried beside it so a screen never has
    to infer one from the other.
    """

    bank_id: str
    reporting_period_id: UUID
    as_of: date
    attempted: bool
    has_result: bool
    latest: IrrbbSfAttemptRead | None = None
    #: The newest attempt that refused, when one is on record.
    refusal: IrrbbSfAttemptRead | None = None
    attempts: list[IrrbbSfAttemptRead]
    #: What the history means for this reporting date, in one sentence.
    statement: str
