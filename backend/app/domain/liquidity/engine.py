"""Pure regulatory liquidity engine (LCR, NSFR, stress application).

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the bank facts and the active parameter set and
receive fully materialized results with per-category line items. Monetary
amounts quantize to ``MONEY`` (4 dp) and ratio percentages quantize to
``RATIO_PCT`` (6 dp) with ``ROUND_HALF_UP``; status classification always
happens AFTER quantization so stored and displayed values agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

type LiquidityStatus = Literal["green", "amber", "red"]
type LineSection = Literal["hqla", "outflow", "inflow", "asf", "rsf"]

FACT_GROUP_BALANCE_SHEET = "balance_sheet"
FACT_GROUP_LOAN_EXPOSURE = "loan_exposure"
FACT_GROUP_SECURITIES = "securities"
FACT_GROUP_OFF_BALANCE = "off_balance"
FACT_GROUP_LCR_INFLOW = "lcr_inflow"

# The balance sheet carries loans in one aggregate row; the NSFR consumes the
# tie-out validated granular ``loan_exposure`` facts instead, so the aggregate
# is excluded from the RSF base to avoid double counting.
LOANS_GROSS_CATEGORY = "loans_gross"
OFF_BALANCE_RSF_CATEGORY = "off_balance_commitments"
RSF_SECURITIES_CATEGORIES = ("securities_bog_bills", "securities_gog_bonds")

SHOCK_INFLOW_MULTIPLIER = "inflow_multiplier"
SHOCK_HQLA_SECURITIES_HAIRCUT = "hqla_securities_haircut_pct"
SHOCK_RSF_SECURITIES_OVERRIDE = "rsf:securities_weight_override"
SHOCK_RUNOFF_PREFIX = "runoff:"
SHOCK_ASF_PREFIX = "asf:"
# Consumed by the per-currency gap layer (compute_currency_gaps), not the
# fact-based LCR/NSFR — the facts carry no currency dimension. apply_shocks
# accepts it so a scenario can couple cedi depreciation with run-off uplifts
# without tripping the unsupported-shock guard.
SHOCK_FX_DEPRECIATION = "fx_depreciation_pct"
# Behavioural run-off schedule for demand-natured deposits under stress
# (LRMD para 50-54): nmd_runoff:h1..h4 = the percentage of non-maturity
# deposits assumed to flee in each of the first four horizons; the
# remainder is the stable core reporting beyond 12 months. Calibration
# comes from the reviewed behavioural assumptions (the NMD-duration GBM
# via the apply-as-assumptions seam) — the engine never invents lives.
SHOCK_NMD_RUNOFF_PREFIX = "nmd_runoff:"

# --- HQLA composition (Basel III LCR, BCBS 238 §II.A "Stock of HQLA") --------
# The HQLA *taxonomy* — which tiers exist — is structural vocabulary, the same
# kind of thing as a capital tier code, and lives here. The *rates and caps*
# attached to each tier are regulatory numbers and are NEVER written here: they
# arrive on ``LiquidityParams`` from the regulatory-parameter layer, and a tier
# present in the book with no resolved rate fails the calculation closed.
HQLA_LEVEL_1 = "L1"
HQLA_LEVEL_2A = "L2A"
HQLA_LEVEL_2B = "L2B"
#: Every level the engine will accept on a fact. A security flagged as HQLA
#: under anything else is a classification failure, not a Level-1 asset.
HQLA_LEVELS: tuple[str, ...] = (HQLA_LEVEL_1, HQLA_LEVEL_2A, HQLA_LEVEL_2B)
HQLA_LEVEL_2_LEVELS: tuple[str, ...] = (HQLA_LEVEL_2A, HQLA_LEVEL_2B)

#: Parameter keys, used in the fail-closed messages so an operator is told the
#: exact control-plane code to configure.
#:
#: Forensic re-audit 2026-08-22 **NEW-A1-2**: the per-level haircut key used to
#: be built from a ``"hqla_haircut:"`` prefix, so an unresolved Level-2A rate
#: refused with *"No active liquidity parameter covers category
#: 'hqla_haircut:L2A'"* — a code that exists nowhere in the control plane. The
#: stored codes are ``hqla_l1_haircut_pct`` / ``hqla_l2a_haircut_pct`` /
#: ``hqla_l2b_haircut_pct`` (``services.regulatory_parameters.HQLA_HAIRCUT_CODES``,
#: seeded by alembic ``202608220034``). That is the worst possible moment to be
#: wrong: the message fires exactly when an operator is trying to fix the
#: configuration it names. The template below is a naming convention, not a
#: regulatory number, so it belongs here alongside the two cap codes, which
#: already match their stored codes exactly. ``tests/domain/
#: test_liquidity_hqla_haircuts_and_caps.py`` pins it against
#: ``HQLA_HAIRCUT_CODES`` so the two can never drift apart again.
PARAM_HQLA_HAIRCUT_TEMPLATE = "hqla_{level}_haircut_pct"
PARAM_HQLA_LEVEL2_CAP = "hqla_level2_cap_pct"
PARAM_HQLA_LEVEL2B_CAP = "hqla_level2b_cap_pct"


def hqla_haircut_param_code(level: str) -> str:
    """The control-plane parameter code carrying one Basel level's haircut."""
    return PARAM_HQLA_HAIRCUT_TEMPLATE.format(level=level.strip().lower())

#: Synthetic HQLA line codes carrying the cap deductions, so the stock of HQLA
#: is always the sum of its own line items and the deduction is auditable.
LINE_CODE_LEVEL2_CAP = "hqla_level2_cap_adjustment"
LINE_CODE_LEVEL2B_CAP = "hqla_level2b_cap_adjustment"


class MissingParameterError(Exception):
    """A category with a non-zero balance has no active rate/weight parameter."""

    def __init__(self, category: str, message: str | None = None) -> None:
        super().__init__(
            message or f"No active liquidity parameter covers category '{category}'."
        )
        self.category = category


class UnsupportedShockError(Exception):
    """A stress scenario carries a shock key the engine does not understand."""

    def __init__(self, scenario_code: str, shock_key: str) -> None:
        super().__init__(
            f"Stress scenario '{scenario_code}' carries unsupported shock key '{shock_key}'."
        )
        self.scenario_code = scenario_code
        self.shock_key = shock_key


class UnclassifiedHqlaError(Exception):
    """A security is flagged as HQLA under a level the Basel taxonomy does not define.

    Fail-closed replacement for the pre-2026-08-21 behaviour, where any non-null
    ``hqla_level`` was counted at face value and therefore treated as Level 1
    (enterprise audit P0-8). An asset whose liquidity tier cannot be established
    is not a Level-1 asset; it is an unclassified asset, and the LCR cannot be
    computed from it.
    """

    def __init__(self, category: str, level: str | None) -> None:
        super().__init__(
            f"Security '{category}' carries HQLA level {level!r}, which is not one of "
            f"{HQLA_LEVELS}. Classify it before computing the LCR."
        )
        self.category = category
        self.level = level


class LiquidityComputationError(Exception):
    """The supplied facts produce a degenerate ratio (zero denominator)."""


@dataclass(frozen=True)
class LiquidityFact:
    """One bank financial fact, reduced to the fields the liquidity engine uses."""

    fact_group: str
    category: str
    amount: Decimal
    hqla_level: str | None = None
    side: str | None = None
    cash_derived: bool = False


@dataclass(frozen=True)
class LiquidityParams:
    """Active parameter set resolved as of the reporting-period end."""

    outflow_rates: Mapping[str, Decimal]
    inflow_rates: Mapping[str, Decimal]
    asf_weights: Mapping[str, Decimal]
    rsf_weights: Mapping[str, Decimal]
    inflow_cap_pct: Decimal
    lcr_min_pct: Decimal
    lcr_amber_floor_pct: Decimal
    nsfr_min_pct: Decimal
    nsfr_amber_floor_pct: Decimal
    #: HQLA haircut percentage per Basel HQLA level (``"L1"``/``"L2A"``/``"L2B"``),
    #: resolved from the regulatory-parameter layer. REQUIRED and carrying no
    #: default: a level that appears in the book with no resolved haircut raises
    #: ``MissingParameterError`` rather than being weighted at face value.
    hqla_haircut_pct: Mapping[str, Decimal]
    #: Level-2 (2A+2B) cap as a percentage of the stock of HQLA. ``None`` is
    #: permitted only for a book that holds no Level-2 asset at all; a Level-2
    #: holding with an unresolved cap raises ``MissingParameterError``.
    hqla_level2_cap_pct: Decimal | None
    #: Level-2B sub-cap as a percentage of the stock of HQLA. Same rule.
    hqla_level2b_cap_pct: Decimal | None


@dataclass(frozen=True)
class LiquidityLineItem:
    section: LineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class HqlaComposition:
    """The stock of HQLA decomposed by Basel level, after haircuts and caps.

    ``total`` is the filed stock: post-haircut Level 1 + 2A + 2B less the two cap
    adjustments. Every component is reported so a reviewer can see WHY the stock
    is what it is — the pre-2026-08-21 engine reported only a face-value sum.
    """

    level1: Decimal
    level2a: Decimal
    level2b: Decimal
    level2_cap_adjustment: Decimal
    level2b_cap_adjustment: Decimal
    total: Decimal

    @property
    def level2_cap_applied(self) -> bool:
        return self.level2_cap_adjustment > _ZERO

    @property
    def level2b_cap_applied(self) -> bool:
        return self.level2b_cap_adjustment > _ZERO


@dataclass(frozen=True)
class LcrResult:
    hqla_total: Decimal
    hqla_composition: HqlaComposition
    outflows_total: Decimal
    gross_inflows_total: Decimal
    inflow_cap_amount: Decimal
    capped_inflows_total: Decimal
    inflow_cap_applied: bool
    net_outflows_total: Decimal
    lcr_pct: Decimal
    status: LiquidityStatus
    all_hqla_level1: bool
    line_items: tuple[LiquidityLineItem, ...]


@dataclass(frozen=True)
class NsfrResult:
    asf_total: Decimal
    rsf_total: Decimal
    nsfr_pct: Decimal
    status: LiquidityStatus
    line_items: tuple[LiquidityLineItem, ...]


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def classify_ratio(
    value_pct: Decimal, minimum_pct: Decimal, amber_floor_pct: Decimal
) -> LiquidityStatus:
    """Classify an already-quantized ratio percentage against its thresholds."""
    if value_pct >= minimum_pct:
        return "green"
    if value_pct >= amber_floor_pct:
        return "amber"
    return "red"


def compute_lcr(facts: Sequence[LiquidityFact], params: LiquidityParams) -> LcrResult:
    hqla_facts = _sorted(
        fact
        for fact in facts
        if fact.fact_group == FACT_GROUP_SECURITIES and fact.hqla_level is not None
    )
    hqla_items, hqla_composition = _hqla_stock(hqla_facts, params)
    hqla_total = hqla_composition.total

    outflow_facts = _sorted(
        fact
        for fact in facts
        if (fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.side == "liability")
        or fact.fact_group == FACT_GROUP_OFF_BALANCE
    )
    outflow_items = _weighted_items("outflow", outflow_facts, params.outflow_rates)
    outflows_total = money(sum((item.weighted_amount for item in outflow_items), _ZERO))

    inflow_facts = _sorted(fact for fact in facts if fact.fact_group == FACT_GROUP_LCR_INFLOW)
    inflow_items = _weighted_items("inflow", inflow_facts, params.inflow_rates)
    gross_inflows_total = money(sum((item.weighted_amount for item in inflow_items), _ZERO))

    inflow_cap_amount = money(outflows_total * params.inflow_cap_pct / _HUNDRED)
    inflow_cap_applied = gross_inflows_total > inflow_cap_amount
    capped_inflows_total = inflow_cap_amount if inflow_cap_applied else gross_inflows_total
    net_outflows_total = money(outflows_total - capped_inflows_total)
    if net_outflows_total <= _ZERO:
        raise LiquidityComputationError(
            "Net cash outflows must be positive to compute the LCR ratio."
        )

    lcr_pct = ratio_pct(hqla_total / net_outflows_total * _HUNDRED)
    status = classify_ratio(lcr_pct, params.lcr_min_pct, params.lcr_amber_floor_pct)
    return LcrResult(
        hqla_total=hqla_total,
        hqla_composition=hqla_composition,
        outflows_total=outflows_total,
        gross_inflows_total=gross_inflows_total,
        inflow_cap_amount=inflow_cap_amount,
        capped_inflows_total=capped_inflows_total,
        inflow_cap_applied=inflow_cap_applied,
        net_outflows_total=net_outflows_total,
        lcr_pct=lcr_pct,
        status=status,
        all_hqla_level1=all(_hqla_level(fact) == HQLA_LEVEL_1 for fact in hqla_facts),
        line_items=(*hqla_items, *outflow_items, *inflow_items),
    )


def compute_nsfr(facts: Sequence[LiquidityFact], params: LiquidityParams) -> NsfrResult:
    asf_facts = _sorted(
        fact
        for fact in facts
        if fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.side in ("liability", "equity")
    )
    asf_items = _weighted_items("asf", asf_facts, params.asf_weights)
    asf_total = money(sum((item.weighted_amount for item in asf_items), _ZERO))

    rsf_facts = _sorted(
        fact
        for fact in facts
        if (
            fact.fact_group == FACT_GROUP_BALANCE_SHEET
            and fact.side == "asset"
            and fact.category != LOANS_GROSS_CATEGORY
        )
        or fact.fact_group == FACT_GROUP_LOAN_EXPOSURE
    )
    rsf_items = list(_weighted_items("rsf", rsf_facts, params.rsf_weights))

    off_balance_total = money(
        sum(
            (fact.amount for fact in facts if fact.fact_group == FACT_GROUP_OFF_BALANCE),
            _ZERO,
        )
    )
    off_balance_weight = params.rsf_weights.get(OFF_BALANCE_RSF_CATEGORY)
    if off_balance_weight is None and off_balance_total != _ZERO:
        raise MissingParameterError(OFF_BALANCE_RSF_CATEGORY)
    if off_balance_weight is not None:
        rsf_items.append(
            LiquidityLineItem(
                section="rsf",
                line_code=OFF_BALANCE_RSF_CATEGORY,
                description=_describe(OFF_BALANCE_RSF_CATEGORY),
                exposure_amount=off_balance_total,
                rate_pct=off_balance_weight,
                weighted_amount=money(off_balance_total * off_balance_weight / _HUNDRED),
            )
        )
    rsf_total = money(sum((item.weighted_amount for item in rsf_items), _ZERO))
    if rsf_total <= _ZERO:
        raise LiquidityComputationError(
            "Required stable funding must be positive to compute the NSFR ratio."
        )

    nsfr_pct = ratio_pct(asf_total / rsf_total * _HUNDRED)
    status = classify_ratio(nsfr_pct, params.nsfr_min_pct, params.nsfr_amber_floor_pct)
    return NsfrResult(
        asf_total=asf_total,
        rsf_total=rsf_total,
        nsfr_pct=nsfr_pct,
        status=status,
        line_items=(*asf_items, *rsf_items),
    )


def apply_liquidity_stress(
    scenario_code: str,
    facts: Sequence[LiquidityFact],
    params: LiquidityParams,
    shocks: Mapping[str, Decimal],
) -> tuple[tuple[LiquidityFact, ...], LiquidityParams]:
    """Return ``(stressed_facts, stressed_params)`` for one stress scenario.

    Supported shock keys:

    - ``runoff:<category>`` replaces the LCR outflow runoff rate.
    - ``inflow_multiplier`` scales gross LCR inflows (applied to inflow rates).
    - ``hqla_securities_haircut_pct`` haircuts securities-group HQLA facts,
      excluding cash-derived rows (vault cash and BoG excess reserves keep
      their face value; only marketable securities take the haircut).
    - ``asf:<category>`` replaces the NSFR ASF weight.
    - ``rsf:securities_weight_override`` replaces the RSF weight for the
      balance-sheet securities rows. The NSFR is structural, so the override
      applies to UNSTRESSED balance-sheet values (no market-value haircut on
      the RSF side).
    """
    outflow_rates = dict(params.outflow_rates)
    inflow_rates = dict(params.inflow_rates)
    asf_weights = dict(params.asf_weights)
    rsf_weights = dict(params.rsf_weights)
    haircut_pct = _ZERO

    for shock_key, shock_value in shocks.items():
        if shock_key.startswith(SHOCK_RUNOFF_PREFIX):
            outflow_rates[shock_key.removeprefix(SHOCK_RUNOFF_PREFIX)] = shock_value
        elif shock_key == SHOCK_INFLOW_MULTIPLIER:
            inflow_rates = {category: rate * shock_value for category, rate in inflow_rates.items()}
        elif shock_key == SHOCK_HQLA_SECURITIES_HAIRCUT:
            haircut_pct = shock_value
        elif shock_key.startswith(SHOCK_ASF_PREFIX):
            asf_weights[shock_key.removeprefix(SHOCK_ASF_PREFIX)] = shock_value
        elif shock_key == SHOCK_RSF_SECURITIES_OVERRIDE:
            for category in RSF_SECURITIES_CATEGORIES:
                rsf_weights[category] = shock_value
        elif shock_key == SHOCK_FX_DEPRECIATION or shock_key.startswith(
            SHOCK_NMD_RUNOFF_PREFIX
        ):
            continue  # applied by the currency-gap / stressed-ladder layer
        else:
            raise UnsupportedShockError(scenario_code, shock_key)

    haircut_factor = (_HUNDRED - haircut_pct) / _HUNDRED
    stressed_facts = tuple(
        replace(fact, amount=money(fact.amount * haircut_factor))
        if (
            haircut_pct != _ZERO
            and fact.fact_group == FACT_GROUP_SECURITIES
            and fact.hqla_level is not None
            and not fact.cash_derived
        )
        else fact
        for fact in facts
    )
    stressed_params = replace(
        params,
        outflow_rates=outflow_rates,
        inflow_rates=inflow_rates,
        asf_weights=asf_weights,
        rsf_weights=rsf_weights,
    )
    return stressed_facts, stressed_params


def consumed_hqla_levels(facts: Sequence[LiquidityFact]) -> set[str]:
    """The Basel levels :func:`compute_lcr` will actually charge a haircut for.

    The engine's OWN HQLA filter, exposed so a caller sealing a reproducibility
    snapshot records exactly the governed rates the arithmetic will read —
    neither wider nor narrower (forensic re-audit 2026-08-22 D-7 / NEW-A1-1).

    A holding whose level could not be established carries ``hqla_level=None``,
    is filtered out of the stock here and by :func:`_hqla_stock`, and therefore
    consumes no rate. A level outside the Basel taxonomy consumes none either:
    :func:`_hqla_level` raises ``UnclassifiedHqlaError`` on it, so no run — and
    no hash — exists to record one.
    """
    levels = {
        (fact.hqla_level or "").strip().upper()
        for fact in facts
        if fact.fact_group == FACT_GROUP_SECURITIES and fact.hqla_level is not None
    }
    return levels & set(HQLA_LEVELS)


def _hqla_level(fact: LiquidityFact) -> str:
    """The fact's Basel HQLA level, or fail closed.

    Normalised (trim + upper) so ``"l2a"`` is the same tier as ``"L2A"``; anything
    outside :data:`HQLA_LEVELS` raises. Before 2026-08-21 an unrecognised level was
    counted at face value, i.e. silently as Level 1 (enterprise audit P0-8).
    """
    level = (fact.hqla_level or "").strip().upper()
    if level not in HQLA_LEVELS:
        raise UnclassifiedHqlaError(fact.category, fact.hqla_level)
    return level


def _hqla_haircut(params: LiquidityParams, level: str) -> Decimal:
    rate = params.hqla_haircut_pct.get(level)
    if rate is None:
        code = hqla_haircut_param_code(level)
        raise MissingParameterError(code, _hqla_parameter_message(code, level=level))
    return rate


def _required_cap(value: Decimal | None, param_code: str) -> Decimal:
    """A cap percentage that must exist, and must leave head-room to cap against.

    A cap of 100% (or more) would make the Basel Annex-1 ratio form divide by
    zero; that is a mis-configured control plane, not a bank with unlimited
    Level-2 capacity, so it fails closed too.
    """
    if value is None:
        raise MissingParameterError(param_code, _hqla_parameter_message(param_code))
    if value < _ZERO or value >= _HUNDRED:
        raise MissingParameterError(
            param_code,
            f"The HQLA cap {param_code!r} resolved to {value}%, which is outside the "
            "0-100% range the Basel Annex-1 cap arithmetic is defined over. Correct it "
            "in the regulatory-parameter control plane.",
        )
    return value


def _hqla_parameter_message(param_code: str, *, level: str | None = None) -> str:
    """A fail-closed message an operator can act on without reading the source.

    The generic 'no parameter covers category X' text is opaque for the HQLA
    codes, which live in the GLOBAL regulatory-parameter control plane rather
    than a tenant register, and whose most likely cause is a deployment whose
    seed migration has not run yet.
    """
    held = f" The book holds a {level} asset, so this rate is required." if level else ""
    return (
        f"The Basel HQLA parameter {param_code!r} did not resolve, so the LCR cannot be "
        f"computed.{held} An unresolved haircut is NOT treated as zero — that would count "
        "the asset at face value and overstate the LCR. Seed it in the regulatory-parameter "
        "control plane (alembic revision 202608220034 seeds the Basel defaults for the bank "
        "institution class; the operator console manages later generations)."
    )


def _hqla_stock(
    hqla_facts: Sequence[LiquidityFact], params: LiquidityParams
) -> tuple[tuple[LiquidityLineItem, ...], HqlaComposition]:
    """The stock of HQLA: per-level haircuts, then the Level-2 caps (BCBS 238).

    Two controls, both absent before 2026-08-21 (enterprise audit P0-8):

    1. **Haircuts.** Every HQLA holding is weighted at ``1 - haircut(level)``.
       Level 1 is customarily un-haircut, but even the 0% is a *resolved
       parameter*, never an assumption: a level with no resolved rate raises.
    2. **Caps.** Level 2 in aggregate, and Level 2B on its own, may not exceed
       their governed share of the stock. The Annex-1 form is used, expressed in
       terms of the two governed caps so no ratio is written as a literal::

           r_2b_a = cap2b / (100 - cap2b)          # the 15/85 leg
           r_2b_b = cap2b / (100 - cap2)           # the 15/60 leg
           r_2    = cap2  / (100 - cap2)           # the 2/3 leg

           adj_2b = max(L2B - r_2b_a x (L1 + L2A), L2B - r_2b_b x L1, 0)
           adj_2  = max((L2A + L2B - adj_2b) - r_2 x L1, 0)
           stock  = L1 + L2A + L2B - adj_2b - adj_2

       With the Basel caps (40 / 15) those ratios are exactly 15/85, 15/60 and
       2/3.

    **Declared deviation — OPEN, bounded, and currently inert.** Annex 1 applies
    the caps to the *adjusted* amounts: what each pool would be if short-term
    secured funding, secured lending and collateral-swap transactions maturing
    within 30 calendar days were unwound first. This engine applies them to the
    ACTUAL post-haircut amounts.

    *What is missing, precisely.* The unwind needs, per transaction: the leg
    direction (secured funding / secured lending / collateral swap), the Basel
    level of the HQLA on each leg, and a maturity inside 30 days. The canonical
    book carries none of that pairing. Its only secured-financing signals are the
    booleans ``encumbered`` / ``pledged_as_collateral`` and the free-text
    ``encumbrance_reason`` on a position, and ``PositionType`` has no ``REPO`` /
    ``REVERSE_REPO`` / ``SECURITIES_LENDING`` / ``COLLATERAL_SWAP`` member at
    all. Closing this needs a secured-transaction dimension on the securities
    facts — it cannot be inferred from what is ingested today, and inferring it
    would be a modelling claim wearing a citation.

    *What it costs.* For a bank running no 30-day secured book the adjusted and
    actual amounts are identical and the deviation is arithmetically absent. For
    one that does, the cap can bind at a different point than Basel intends, in
    either direction. **Measured read-only against the primary on 2026-08-22:
    zero positions of ANY type carry ``encumbered``, ``pledged_as_collateral``
    or an ``encumbrance_reason``, across all 80,874 current-generation
    SECURITY_HOLDING snapshots and every other position type — so there is
    nothing to unwind, and no filed figure differs.** It is a stated limitation
    ahead of the data, not a silent assumption behind it.

    A book holding no Level-2 asset needs no cap: both adjustments are provably
    zero, so the cap parameters are not required in that case and a Level-1-only
    bank is never blocked on a parameter that cannot bind.
    """
    items: list[LiquidityLineItem] = []
    by_level: dict[str, Decimal] = dict.fromkeys(HQLA_LEVELS, _ZERO)
    for fact in hqla_facts:
        level = _hqla_level(fact)
        haircut = _hqla_haircut(params, level)
        weighted = money(fact.amount * (_HUNDRED - haircut) / _HUNDRED)
        by_level[level] += weighted
        items.append(
            LiquidityLineItem(
                section="hqla",
                line_code=fact.category,
                description=_describe(fact.category),
                exposure_amount=money(fact.amount),
                # An un-haircut line keeps the historic ``None`` (no rate was
                # charged), so every Level-1-only book's persisted line items are
                # byte-identical to before this control landed.
                rate_pct=None if haircut == _ZERO else haircut,
                weighted_amount=weighted,
            )
        )

    level1 = money(by_level[HQLA_LEVEL_1])
    level2a = money(by_level[HQLA_LEVEL_2A])
    level2b = money(by_level[HQLA_LEVEL_2B])
    adjustment_2b = _ZERO
    adjustment_2 = _ZERO
    if level2a + level2b > _ZERO:
        cap2 = _required_cap(params.hqla_level2_cap_pct, PARAM_HQLA_LEVEL2_CAP)
        cap2b = _required_cap(params.hqla_level2b_cap_pct, PARAM_HQLA_LEVEL2B_CAP)
        ratio_2b_a = cap2b / (_HUNDRED - cap2b)
        ratio_2b_b = cap2b / (_HUNDRED - cap2)
        ratio_2 = cap2 / (_HUNDRED - cap2)
        adjustment_2b = money(
            max(
                level2b - ratio_2b_a * (level1 + level2a),
                level2b - ratio_2b_b * level1,
                _ZERO,
            )
        )
        adjustment_2 = money(
            max((level2a + level2b - adjustment_2b) - ratio_2 * level1, _ZERO)
        )

    if adjustment_2b > _ZERO:
        items.append(
            LiquidityLineItem(
                section="hqla",
                line_code=LINE_CODE_LEVEL2B_CAP,
                description="Level 2B cap adjustment",
                exposure_amount=level2b,
                rate_pct=params.hqla_level2b_cap_pct,
                weighted_amount=money(-adjustment_2b),
            )
        )
    if adjustment_2 > _ZERO:
        items.append(
            LiquidityLineItem(
                section="hqla",
                line_code=LINE_CODE_LEVEL2_CAP,
                description="Level 2 cap adjustment",
                exposure_amount=money(level2a + level2b),
                rate_pct=params.hqla_level2_cap_pct,
                weighted_amount=money(-adjustment_2),
            )
        )

    total = money(level1 + level2a + level2b - adjustment_2b - adjustment_2)
    composition = HqlaComposition(
        level1=level1,
        level2a=level2a,
        level2b=level2b,
        level2_cap_adjustment=adjustment_2,
        level2b_cap_adjustment=adjustment_2b,
        total=total,
    )
    return tuple(items), composition


def _weighted_items(
    section: LineSection,
    facts: Sequence[LiquidityFact],
    rates: Mapping[str, Decimal],
) -> tuple[LiquidityLineItem, ...]:
    items: list[LiquidityLineItem] = []
    for fact in facts:
        rate = rates.get(fact.category)
        if rate is None:
            if fact.amount != _ZERO:
                raise MissingParameterError(fact.category)
            continue
        items.append(
            LiquidityLineItem(
                section=section,
                line_code=fact.category,
                description=_describe(fact.category),
                exposure_amount=money(fact.amount),
                rate_pct=rate,
                weighted_amount=money(fact.amount * rate / _HUNDRED),
            )
        )
    return tuple(items)


def _sorted(facts: Iterable[LiquidityFact]) -> tuple[LiquidityFact, ...]:
    return tuple(sorted(facts, key=lambda fact: (fact.fact_group, fact.category)))


def _describe(category: str) -> str:
    return category.replace("_", " ").title().replace("Bog", "BoG").replace("Gog", "GoG")


@dataclass(frozen=True)
class CurrencyGap:
    """Per-currency contractual liquidity gap across the five LMTD horizons."""

    currency: str
    assets: tuple[Decimal, ...]
    liabilities: tuple[Decimal, ...]
    net: tuple[Decimal, ...]
    cumulative: tuple[Decimal, ...]
    assets_total: Decimal
    liabilities_total: Decimal
    net_total: Decimal
    stressed_liabilities_total: Decimal
    stressed_net_total: Decimal


@dataclass(frozen=True)
class CurrencyGapResult:
    """FRM 16-17 per-currency gaps + FX funding-mismatch metrics.

    ``fx_depreciation_pct`` scales the cedi value of NON-base-currency
    liabilities (a depreciation raises what FX funding costs in cedi terms);
    base-currency figures never move. Deterministic Decimal arithmetic only.
    """

    gaps: tuple[CurrencyGap, ...]
    fx_assets_total: Decimal
    fx_liabilities_total: Decimal
    fx_funding_gap: Decimal
    fx_share_of_liabilities_pct: Decimal
    stressed_fx_liabilities_total: Decimal
    stressed_fx_funding_gap: Decimal
    fx_depreciation_pct: Decimal


def compute_currency_gaps(
    ladders: dict[str, dict[str, list[Decimal]]],
    base_currency: str,
    fx_depreciation_pct: Decimal = _ZERO,
) -> CurrencyGapResult:
    """Per-currency contractual gaps with an FX-depreciation overlay.

    ``ladders``: currency → {"assets": [...], "liabilities": [...]} across the
    five LMTD horizons, in cedi equivalents. Currencies are processed in
    sorted order so results are canonical regardless of input ordering.
    """
    factor = (Decimal("100") + fx_depreciation_pct) / Decimal("100")
    gaps: list[CurrencyGap] = []
    fx_assets = fx_liabilities = stressed_fx_liabilities = _ZERO
    total_liabilities = _ZERO
    for currency in sorted(ladders):
        ladder = ladders[currency]
        assets = tuple(Decimal(str(value)) for value in ladder.get("assets", []))
        liabilities = tuple(Decimal(str(value)) for value in ladder.get("liabilities", []))
        net = tuple(a - b for a, b in zip(assets, liabilities, strict=True))
        cumulative: list[Decimal] = []
        running = _ZERO
        for value in net:
            running += value
            cumulative.append(running)
        assets_total = sum(assets, _ZERO)
        liabilities_total = sum(liabilities, _ZERO)
        is_base = currency == base_currency
        stressed_liabilities_total = (
            liabilities_total if is_base else money(liabilities_total * factor)
        )
        gaps.append(
            CurrencyGap(
                currency=currency,
                assets=assets,
                liabilities=liabilities,
                net=net,
                cumulative=tuple(cumulative),
                assets_total=assets_total,
                liabilities_total=liabilities_total,
                net_total=assets_total - liabilities_total,
                stressed_liabilities_total=stressed_liabilities_total,
                stressed_net_total=assets_total - stressed_liabilities_total,
            )
        )
        total_liabilities += liabilities_total
        if not is_base:
            fx_assets += assets_total
            fx_liabilities += liabilities_total
            stressed_fx_liabilities += stressed_liabilities_total

    fx_share = (
        ratio_pct(fx_liabilities / total_liabilities * Decimal("100"))
        if total_liabilities > _ZERO
        else _ZERO
    )
    return CurrencyGapResult(
        gaps=tuple(gaps),
        fx_assets_total=fx_assets,
        fx_liabilities_total=fx_liabilities,
        fx_funding_gap=fx_assets - fx_liabilities,
        fx_share_of_liabilities_pct=fx_share,
        stressed_fx_liabilities_total=stressed_fx_liabilities,
        stressed_fx_funding_gap=fx_assets - stressed_fx_liabilities,
        fx_depreciation_pct=fx_depreciation_pct,
    )


@dataclass(frozen=True)
class StressedLadder:
    """LRMD ¶50: contractual flows combined with behavioural NMD run-off."""

    currency: str
    contractual_liabilities: tuple[Decimal, ...]
    stressed_liabilities: tuple[Decimal, ...]
    stressed_net: tuple[Decimal, ...]
    stressed_cumulative: tuple[Decimal, ...]
    demand_deposits: Decimal
    stable_core: Decimal


def compute_stressed_ladder(
    ladders: dict[str, dict[str, list[Decimal]]],
    demand_by_currency: dict[str, Decimal],
    runoff_schedule: dict[int, Decimal],
) -> tuple[StressedLadder, ...]:
    """Behaviourally-modified stress ladder (LRMD ¶50–54).

    Contractually, demand-natured deposits sit entirely in the first horizon.
    Under the reviewed behavioural schedule, ``runoff_schedule[i]`` percent of
    them flee in horizon ``i`` (1-based, horizons 1–4) and the remainder is
    the stable core reporting beyond 12 months. Assets stay contractual —
    ¶51's conservatism (later inflows, earlier outflows) is encoded in the
    schedule's calibration, not manufactured here.
    """
    results: list[StressedLadder] = []
    for currency in sorted(ladders):
        ladder = ladders[currency]
        liabilities = [Decimal(str(v)) for v in ladder.get("liabilities", [])]
        assets = [Decimal(str(v)) for v in ladder.get("assets", [])]
        horizons = len(liabilities)
        demand = demand_by_currency.get(currency, _ZERO)
        stressed = list(liabilities)
        stressed[0] -= demand
        fled_total = _ZERO
        for horizon, pct in sorted(runoff_schedule.items()):
            if 1 <= horizon <= horizons - 1:
                fled = money(demand * pct / _HUNDRED)
                stressed[horizon - 1] += fled
                fled_total += fled
        stable_core = demand - fled_total
        stressed[-1] += stable_core
        net = [a - b for a, b in zip(assets, stressed, strict=True)]
        cumulative: list[Decimal] = []
        running = _ZERO
        for value in net:
            running += value
            cumulative.append(running)
        results.append(
            StressedLadder(
                currency=currency,
                contractual_liabilities=tuple(liabilities),
                stressed_liabilities=tuple(stressed),
                stressed_net=tuple(net),
                stressed_cumulative=tuple(cumulative),
                demand_deposits=demand,
                stable_core=stable_core,
            )
        )
    return tuple(results)
