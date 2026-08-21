"""Concentration stress (docs/stress.md §1.7 AppI ¶26-29, §3.6, Phase 4 item 2).

Appendix I's concentration method — a genuine STRESS, not the Large-Exposures
*reporting* the platform already produces. It measures how concentrated the book
is along every prescribed dimension and turns that into an incremental loss and a
Pillar-2 concentration charge feeding Appendix II Table 5:

- **single-name** — the largest counterparty / connected group defaults
  (idiosyncratic shock, AppI ¶27); connected groups are formed by a shared
  ``group_key`` (the connected-counterparty pattern ``le_generation.py`` uses:
  the canonical ``group_reference``, else the counterparty stands alone);
- **sectoral / geographical / product / collateral-type** — asset-side
  concentration measured by the Herfindahl-Hirschman Index (HHI = Σ shareᵢ²)
  over each dimension, with a stress loss on the most concentrated bucket;
- **funding-source** — liability-side concentration (largest funder withdraws),
  the liquidity leg of AppI ¶29.

On + off balance are both included (the caller folds off-balance CCF-weighted
exposure into ``ead``). Pure, deterministic, Decimal-only.

**The Pillar-2 credit-concentration charge** (the Table 5 slot) is a documented
granularity add-on: ``base_credit_rwa × (name_charge_coeff × name_HHI +
sector_charge_coeff × sector_HHI)`` — the two material concentration channels the
CRD add-on covers. Every coefficient has an ``overrides`` seam
(``ConcentrationParams``) exactly like the Phase-1 translation elasticities (¶45).

Graceful zero: with no exposures every dimension is empty, the incremental loss
and the Pillar-2 charge are zero, and ``largest_group_key`` is ``None`` — a bank
with no material concentration is correctly resilient, not erroring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

MONEY = Decimal("0.0001")
_PCT = Decimal("0.0001")
_HHI = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

# The asset-side dimensions measured by HHI (single-name is handled separately
# because its stress is a full default, not a granularity add-on).
DIM_SINGLE_NAME = "single_name"
DIM_SECTOR = "sector"
DIM_GEOGRAPHY = "geography"
DIM_PRODUCT = "product"
DIM_COLLATERAL = "collateral"
DIM_FUNDING = "funding"


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ConcentrationParams:
    """Documented concentration-stress coefficients (¶45 overrides seam)."""

    #: LGD applied to the single largest name on default (downturn LGD).
    single_name_lgd_pct: Decimal = Decimal("60")
    #: Default probability ascribed to the single largest name (idiosyncratic
    #: shock — a full default is 100%).
    single_name_default_pct: Decimal = Decimal("100")
    #: Loss rate applied to the most concentrated sector under a sector shock.
    sector_stress_loss_pct: Decimal = Decimal("10")
    #: Pillar-2 charge coefficients on each dimension's HHI (of base credit RWA).
    name_charge_coeff: Decimal = Decimal("0.5")
    sector_charge_coeff: Decimal = Decimal("0.5")
    #: Fraction of the single largest funding source that withdraws under stress.
    funding_outflow_pct: Decimal = Decimal("100")


DEFAULT_CONCENTRATION_PARAMS = ConcentrationParams()


@dataclass(frozen=True)
class ConcentrationExposure:
    """One asset-side exposure with its concentration dimensions.

    ``group_key`` is the connected-group / single-name identity; the optional
    dimension keys are ``None`` when the source does not classify them (that
    exposure simply does not contribute to that dimension's HHI).
    """

    exposure_id: str
    ead: Decimal
    group_key: str
    sector: str | None = None
    geography: str | None = None
    product: str | None = None
    collateral_type: str | None = None
    on_balance: bool = True


@dataclass(frozen=True)
class FundingPosition:
    """One funding source (depositor / wholesale provider) — liability side."""

    source_key: str
    amount: Decimal


@dataclass(frozen=True)
class ConcentrationInputs:
    """The full concentration-stress input set."""

    exposures: Sequence[ConcentrationExposure] = field(default_factory=tuple)
    funding: Sequence[FundingPosition] = field(default_factory=tuple)
    base_credit_rwa: Decimal = _ZERO
    params: ConcentrationParams = DEFAULT_CONCENTRATION_PARAMS


@dataclass(frozen=True)
class DimensionConcentration:
    dimension: str
    hhi: Decimal
    bucket_count: int
    top_key: str | None
    top_share_pct: Decimal
    top_amount: Decimal
    incremental_loss: Decimal
    pillar2_charge: Decimal


@dataclass(frozen=True)
class ConcentrationResult:
    dimensions: tuple[DimensionConcentration, ...]
    total_incremental_loss: Decimal
    pillar2_concentration_charge: Decimal
    largest_group_key: str | None
    largest_group_exposure: Decimal
    largest_funding_source: str | None
    largest_funding_outflow: Decimal

    def dimension(self, name: str) -> DimensionConcentration | None:
        return next((dim for dim in self.dimensions if dim.dimension == name), None)

    def serialize(self) -> dict[str, object]:
        return {
            "total_incremental_loss": str(self.total_incremental_loss),
            "pillar2_concentration_charge": str(self.pillar2_concentration_charge),
            "largest_group_key": self.largest_group_key,
            "largest_group_exposure": str(self.largest_group_exposure),
            "largest_funding_source": self.largest_funding_source,
            "largest_funding_outflow": str(self.largest_funding_outflow),
            "dimensions": [
                {
                    "dimension": dim.dimension,
                    "hhi": str(dim.hhi),
                    "bucket_count": dim.bucket_count,
                    "top_key": dim.top_key,
                    "top_share_pct": str(dim.top_share_pct),
                    "top_amount": str(dim.top_amount),
                    "incremental_loss": str(dim.incremental_loss),
                    "pillar2_charge": str(dim.pillar2_charge),
                }
                for dim in self.dimensions
            ],
        }


def _bucketize(
    exposures: Sequence[ConcentrationExposure], key: str
) -> tuple[dict[str, Decimal], Decimal]:
    """Aggregate EAD by one dimension key (``None`` values are skipped)."""
    buckets: dict[str, Decimal] = {}
    total = _ZERO
    for exposure in exposures:
        value = getattr(exposure, key)
        if value is None:
            continue
        buckets[value] = buckets.get(value, _ZERO) + exposure.ead
        total += exposure.ead
    return buckets, total


def _hhi_and_top(
    buckets: dict[str, Decimal], total: Decimal
) -> tuple[Decimal, str | None, Decimal]:
    """(HHI, top bucket key, top bucket amount) for one dimension."""
    if total <= _ZERO or not buckets:
        return _ZERO, None, _ZERO
    hhi = sum(((amount / total) ** 2 for amount in buckets.values()), _ZERO)
    top_key, top_amount = max(buckets.items(), key=lambda kv: (kv[1], kv[0]))
    return hhi.quantize(_HHI), top_key, top_amount


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator * _HUNDRED).quantize(_PCT) if denominator > _ZERO else _ZERO


def compute_concentration(inputs: ConcentrationInputs) -> ConcentrationResult:
    """Measure concentration on every dimension and derive the Pillar-2 charge."""
    params = inputs.params
    exposures = list(inputs.exposures)

    # --- single-name / connected-group (idiosyncratic default) ---------------
    groups, group_total = _bucketize(exposures, "group_key")
    name_hhi, largest_group, largest_group_ead = _hhi_and_top(groups, group_total)
    single_name_loss = money(
        largest_group_ead
        * params.single_name_default_pct
        / _HUNDRED
        * params.single_name_lgd_pct
        / _HUNDRED
    )
    name_charge = money(inputs.base_credit_rwa * params.name_charge_coeff * name_hhi)
    single_name_dim = DimensionConcentration(
        dimension=DIM_SINGLE_NAME,
        hhi=name_hhi,
        bucket_count=len(groups),
        top_key=largest_group,
        top_share_pct=_pct(largest_group_ead, group_total),
        top_amount=money(largest_group_ead),
        incremental_loss=single_name_loss,
        pillar2_charge=name_charge,
    )

    # --- sectoral (sector-wide loss on the most concentrated sector) ---------
    sector_buckets, sector_total = _bucketize(exposures, "sector")
    sector_hhi, top_sector, top_sector_ead = _hhi_and_top(sector_buckets, sector_total)
    sector_loss = money(top_sector_ead * params.sector_stress_loss_pct / _HUNDRED)
    sector_charge = money(inputs.base_credit_rwa * params.sector_charge_coeff * sector_hhi)
    sector_dim = DimensionConcentration(
        dimension=DIM_SECTOR,
        hhi=sector_hhi,
        bucket_count=len(sector_buckets),
        top_key=top_sector,
        top_share_pct=_pct(top_sector_ead, sector_total),
        top_amount=money(top_sector_ead),
        incremental_loss=sector_loss,
        pillar2_charge=sector_charge,
    )

    # --- geography / product / collateral (HHI granularity, informational) ---
    informational = tuple(
        _informational_dimension(exposures, dimension, key)
        for dimension, key in (
            (DIM_GEOGRAPHY, "geography"),
            (DIM_PRODUCT, "product"),
            (DIM_COLLATERAL, "collateral_type"),
        )
    )

    # --- funding-source (largest funder withdraws) ---------------------------
    funding_buckets: dict[str, Decimal] = {}
    for position in inputs.funding:
        funding_buckets[position.source_key] = (
            funding_buckets.get(position.source_key, _ZERO) + position.amount
        )
    funding_total = sum(funding_buckets.values(), _ZERO)
    funding_hhi, top_funder, top_funder_amt = _hhi_and_top(funding_buckets, funding_total)
    funding_outflow = money(top_funder_amt * params.funding_outflow_pct / _HUNDRED)
    funding_dim = DimensionConcentration(
        dimension=DIM_FUNDING,
        hhi=funding_hhi,
        bucket_count=len(funding_buckets),
        top_key=top_funder,
        top_share_pct=_pct(top_funder_amt, funding_total),
        top_amount=money(top_funder_amt),
        incremental_loss=_ZERO,  # a funding shock is a liquidity outflow, not a P&L loss
        pillar2_charge=_ZERO,
    )

    dimensions = (single_name_dim, sector_dim, *informational, funding_dim)
    # The asset-side dimensions contribute an incremental credit loss; the
    # single-name and sectoral channels are the material ones (the informational
    # HHI dimensions do not add a separate loss to avoid double-counting names).
    total_incremental_loss = money(single_name_loss + sector_loss)
    pillar2_charge = money(name_charge + sector_charge)
    return ConcentrationResult(
        dimensions=dimensions,
        total_incremental_loss=total_incremental_loss,
        pillar2_concentration_charge=pillar2_charge,
        largest_group_key=largest_group,
        largest_group_exposure=money(largest_group_ead),
        largest_funding_source=top_funder,
        largest_funding_outflow=funding_outflow,
    )


def _informational_dimension(
    exposures: Sequence[ConcentrationExposure], dimension: str, key: str
) -> DimensionConcentration:
    buckets, total = _bucketize(exposures, key)
    hhi, top_key, top_amount = _hhi_and_top(buckets, total)
    return DimensionConcentration(
        dimension=dimension,
        hhi=hhi,
        bucket_count=len(buckets),
        top_key=top_key,
        top_share_pct=_pct(top_amount, total),
        top_amount=money(top_amount),
        incremental_loss=_ZERO,
        pillar2_charge=_ZERO,
    )
