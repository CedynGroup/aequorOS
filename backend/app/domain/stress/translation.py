"""Macro → risk-parameter translation (docs/stress.md §3.2, Phase 1).

The satellite step the platform lacked: map a governed scenario's macro-variable
paths (Appendix III Table 6) onto the EXACT per-module shock keys each engine
consumes (¶38(e) "stressed risk factors translate into internally consistent
risk parameters"; ¶48 scenario → PD/LGD). This is the load-bearing seam the
Phase 2 enterprise orchestrator calls once per module.

Design (¶45 "justify all overlays … with challenge/validation"):
- The mapping is a **linear elasticity register** — a documented, resolvable,
  overridable set of coefficients keyed ``module → shock_key → macro terms``.
  It is a plain in-code register with an ``overrides`` seam rather than a new
  effective-dated DB table, matching the repo's house pattern for methodology
  coefficients (pure ``app/domain/curves/``; the ECL/CRM engines' "code defaults
  + optional register override"). A pure domain module cannot read the DB, and
  Phase 1 needs no governance surface on the coefficients themselves; the
  ``overrides`` argument is where a governed register or the ``ai_engine.md``
  behavioural satellite models plug in later.
- Deltas are taken at the **peak stress year** (the year with the largest
  |stress − base|), because a stress parameter reflects the most severe point of
  the scenario. Rates are decimal fractions, so coefficients fold in the unit
  conversion (×10000 for a fraction→basis-points shock, ×100 for a
  fraction→percent shock).
- A shock is emitted only when it materially deviates from its neutral value (0
  for an additive shock, 1.0 for a multiplier). A base scenario (stress == base)
  therefore translates to ``{}`` — no stress, which is correct.

The output keys are validated to be a subset of the engines' ``SHOCK_VOCABULARY``
by ``tests/domain/test_stress_translation.py`` (kept there so this module stays
pure — no import of the FastAPI-coupled ``scenario_catalog`` service).

Elasticity provenance (AppI/AppIII risk-driver logic):
- ``interest_rate`` → 1:1 parallel yield-curve / FTP-curve shift (standard IRRBB
  passthrough); a duration≈3.5 MTM haircut on HQLA securities; front-loaded NMD
  runoff (depositors chase yield).
- ``inflation`` → additional NMD runoff (real-balance erosion).
- ``gog_yield`` → ~0.5 passthrough into the marginal funding spread (retail
  funding reprices slower than the sovereign).
- ``fx_usd_ghs`` depreciation → 1:1 FX shock and liquidity FX-gap depreciation.
- ``gdp_growth`` down → lower loan-repayment inflows; higher PD/LGD.
- ``unemployment`` up → higher PD. ``gse_index`` down → higher LGD (collateral).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.authority.outcomes import NotComputable, OutcomeState, outcome

# --- Emitted shock keys (mirror the engine/service constants) ----------------
# Kept as local literals so this module imports nothing from the service layer;
# the alignment test proves they equal the live vocabulary.
_NMD_RUNOFF_PREFIX = "nmd_runoff:"  # app/domain/liquidity/engine.SHOCK_NMD_RUNOFF_PREFIX
_INFLOW_MULTIPLIER = "inflow_multiplier"  # …SHOCK_INFLOW_MULTIPLIER
_HQLA_HAIRCUT = "hqla_securities_haircut_pct"  # …SHOCK_HQLA_SECURITIES_HAIRCUT
_FX_DEPRECIATION = "fx_depreciation_pct"  # …SHOCK_FX_DEPRECIATION
_ECL_PD_MULTIPLIER = "ecl_pd_multiplier"  # regulatory_capital.ECL_CONDITIONING_KEYS
_ECL_LGD_MULTIPLIER = "ecl_lgd_multiplier"  # regulatory_capital.ECL_CONDITIONING_KEYS
_PARALLEL_BP = "parallel_bp"  # app/domain/irr/engine.SHOCK_PARALLEL_BP
_GHS_USD_SHOCK_PCT = "ghs_usd_shock_pct"  # regulatory_fx.SHOCK_DEPRECIATION
_CURVE_SHIFT_BP = "curve_shift_bp"  # regulatory_ftp.SHOCK_CURVE_SHIFT_BP
_FUNDING_ADD_BPS = "funding_spread_add_bps"  # regulatory_ftp.SHOCK_FUNDING_ADD_BP

TRANSLATION_MODULES: tuple[str, ...] = ("liquidity", "capital", "irr", "fx", "ftp")

_DELTA = "delta"  # signed (stress − base), fraction/level units
_FRAC_CHANGE = "frac_change"  # (stress − base) / base, dimensionless
_ADDITIVE = "additive"  # value = Σ contributions
_MULTIPLIER = "multiplier"  # value = 1 + Σ contributions


@dataclass(frozen=True)
class MacroPathPoint:
    """One authored macro path point (mirrors ``MacroScenarioPath``)."""

    variable: str
    year_index: int
    base_value: Decimal
    stress_value: Decimal


#: Which direction of a macro move is ADVERSE for a given term. This is the
#: fix for the peak-selection defect the 2026-08-20 enterprise audit found: the
#: peak used to be the largest-MAGNITUDE move in either direction, while the
#: consumer floors the resulting shock at neutral. A scenario with +30% cedi
#: depreciation in year 1 and −35% appreciation in year 3 therefore selected the
#: appreciation, floored it to zero, and applied NO FX stress at all — a scenario
#: made MORE severe in one year silently became benign. A term that declares its
#: adverse direction takes its peak over the adverse moves only.
ADVERSE_UP = "up"  # a RISE in the variable is the stress (rates, inflation, unemployment, FX)
ADVERSE_DOWN = "down"  # a FALL is the stress (GDP, equity index)
#: ``None`` = both directions are genuinely stressful and the shock is signed
#: (the IRRBB parallel shift: a rate fall is a real EVE stress, not a no-op).
ADVERSE_EITHER = None


@dataclass(frozen=True)
class ElasticityTerm:
    """One driver's linear contribution to a shock.

    ``coefficient`` folds in the unit conversion (e.g. 10000 for a
    fraction→basis-points shock), so ``driver_value × coefficient`` is already in
    the shock's own units.

    ``adverse`` declares which direction of the macro move stresses the bank
    (:data:`ADVERSE_UP` / :data:`ADVERSE_DOWN`), so the peak is taken over the
    adverse moves rather than the largest-magnitude move of either sign. ``None``
    keeps the signed largest-magnitude selection, for shocks where both
    directions are real.
    """

    variable: str
    driver: str  # _DELTA | _FRAC_CHANGE
    coefficient: Decimal
    adverse: str | None = ADVERSE_EITHER


@dataclass(frozen=True)
class ShockMapping:
    """How one shock key is computed from a set of macro drivers."""

    shock_key: str
    kind: str  # _ADDITIVE | _MULTIPLIER
    terms: tuple[ElasticityTerm, ...]
    floor: Decimal | None = None
    cap: Decimal | None = None
    # Extra metadata, ignored by the maths — provenance for readers/audits.
    rationale: str = field(default="")

    @property
    def neutral(self) -> Decimal:
        return Decimal(1) if self.kind == _MULTIPLIER else Decimal(0)


def _peak(values: Sequence[Decimal], adverse: str | None) -> Decimal:
    """The peak of ``values`` in the declared adverse direction (0 when none).

    ``ADVERSE_UP`` takes the largest strictly-positive value, ``ADVERSE_DOWN``
    the most negative, and ``None`` the largest magnitude of either sign (ties
    resolving to the larger signed value, so the result stays order-independent
    and a pure function of the input set).
    """
    if adverse == ADVERSE_UP:
        adverse_values = [v for v in values if v > 0]
        return max(adverse_values) if adverse_values else Decimal(0)
    if adverse == ADVERSE_DOWN:
        adverse_values = [v for v in values if v < 0]
        return min(adverse_values) if adverse_values else Decimal(0)
    if not values:
        return Decimal(0)
    return max(values, key=lambda v: (abs(v), v))


def signed_peak_delta(
    paths: Sequence[MacroPathPoint], variable: str, *, adverse: str | None = ADVERSE_EITHER
) -> Decimal:
    """Peak signed ``stress − base`` across years (0 if the variable is absent).

    With ``adverse`` unset this is the largest-magnitude delta of either sign —
    correct for a signed shock like the IRRBB parallel shift. With ``adverse``
    set, the peak is taken over the adverse moves ONLY, so a benign year can
    never mask a stressed one in a shock that floors at neutral.
    """
    return _peak([p.stress_value - p.base_value for p in paths if p.variable == variable], adverse)


def frac_change(
    paths: Sequence[MacroPathPoint], variable: str, *, adverse: str | None = ADVERSE_EITHER
) -> Decimal:
    """Peak fractional change ``(stress − base) / base`` across years (0 if absent).

    ``adverse`` has the same meaning as in :func:`signed_peak_delta`.

    **A zero base refuses** (audit 2026-08-22 D-8). A fractional change against a
    zero base is undefined, and the year used to be *skipped*: an ``fx_usd_ghs``
    or ``gse_index`` path authored with a zero base contributed nothing, so the
    peak was taken over the remaining years — or, when every year had a zero
    base, came back as a flat ``0`` and the shock read as neutral. An exchange
    rate or an index level of zero is a broken path, not an unstressed one.
    """
    points = [point for point in paths if point.variable == variable]
    undefined = tuple(
        f"macro:{variable}@y{point.year_index}" for point in points if point.base_value == 0
    )
    if undefined:
        raise NotComputable(
            outcome(
                OutcomeState.DATA_QUALITY_BLOCK,
                metric_id=f"macro_driver:{variable}",
                reason=(
                    f"The macro variable '{variable}' is authored with a zero base value, "
                    "so its fractional stress-vs-base change is undefined. A zero base "
                    "used to be skipped, which silently dropped that year from the peak "
                    "(and translated the whole driver to no stress when every year was "
                    "zero). Author a real base level for the variable."
                ),
                items=undefined,
                context={"variable": variable},
            )
        )
    return _peak(
        [(point.stress_value - point.base_value) / point.base_value for point in points],
        adverse,
    )


def require_macro_variables(
    paths: Sequence[MacroPathPoint],
    variables: Sequence[str],
    *,
    metric_id: str,
    context: Mapping[str, object] | None = None,
) -> None:
    """Refuse unless ``paths`` carries every one of ``variables``.

    The reusable form of the :func:`translate` completeness guard, for the macro
    drivers a consumer reads **outside** the elasticity register — the capital
    path composition's FX/rate/GDP peaks, the projection's per-year plan
    perturbation, the bottom-up book's FX revaluation. Those reads went through
    :func:`signed_peak_delta` / :func:`frac_change` directly, which answer ``0``
    for an absent variable, so a scenario that did not carry them produced a
    complete, unstressed result (audit 2026-08-22 D-8).

    A driver the institution considers irrelevant is authored FLAT
    (``stress_value == base_value``) — an explicit zero, not an absent one.
    """
    present = {point.variable for point in paths}
    missing = tuple(name for name in variables if name not in present)
    if not missing:
        return
    raise NotComputable(
        outcome(
            OutcomeState.MISSING_REQUIRED_INPUT,
            metric_id=metric_id,
            reason=(
                "The scenario does not carry every macro variable this calculation "
                "reads. A missing variable reads as a zero delta and would silently "
                "leave the result unstressed; author it flat (stress equals base) if "
                "it is genuinely not part of the scenario."
            ),
            items=tuple(f"macro:{name}" for name in missing),
            context={**dict(context or {}), "missing": list(missing)},
        )
    )


def _driver_value(paths: Sequence[MacroPathPoint], term: ElasticityTerm) -> Decimal:
    if term.driver == _FRAC_CHANGE:
        return frac_change(paths, term.variable, adverse=term.adverse)
    return signed_peak_delta(paths, term.variable, adverse=term.adverse)


def _shock_value(paths: Sequence[MacroPathPoint], mapping: ShockMapping) -> Decimal:
    contribution = sum(
        (_driver_value(paths, term) * term.coefficient for term in mapping.terms),
        Decimal(0),
    )
    value = mapping.neutral + contribution if mapping.kind == _MULTIPLIER else contribution
    if mapping.floor is not None and value < mapping.floor:
        value = mapping.floor
    if mapping.cap is not None and value > mapping.cap:
        value = mapping.cap
    return value


def _t(
    variable: str, driver: str, coefficient: str, adverse: str | None = ADVERSE_EITHER
) -> ElasticityTerm:
    return ElasticityTerm(
        variable=variable, driver=driver, coefficient=Decimal(coefficient), adverse=adverse
    )


# --- The default elasticity register -----------------------------------------
# Coefficients are round, defensible linear elasticities; see the module
# docstring for the AppI/AppIII provenance of each driver.
DEFAULT_ELASTICITIES: dict[str, tuple[ShockMapping, ...]] = {
    "liquidity": (
        # Front-loaded NMD runoff term structure: a market-rate rise pulls
        # deposits out (yield chasing) and inflation erodes real balances; the
        # shortest bucket runs off hardest. Coefficients are % runoff per unit
        # fraction of the driver (e.g. 200 ⇒ 2% runoff per 1pp rate rise).
        ShockMapping(
            f"{_NMD_RUNOFF_PREFIX}h1",
            _ADDITIVE,
            (
                _t("interest_rate", _DELTA, "200", ADVERSE_UP),
                _t("inflation", _DELTA, "100", ADVERSE_UP),
            ),
            floor=Decimal(0),
            cap=Decimal(100),
            rationale="30d NMD runoff: 2%/pp rate + 1%/pp inflation.",
        ),
        ShockMapping(
            f"{_NMD_RUNOFF_PREFIX}h2",
            _ADDITIVE,
            (
                _t("interest_rate", _DELTA, "150", ADVERSE_UP),
                _t("inflation", _DELTA, "75", ADVERSE_UP),
            ),
            floor=Decimal(0),
            cap=Decimal(100),
        ),
        ShockMapping(
            f"{_NMD_RUNOFF_PREFIX}h3",
            _ADDITIVE,
            (
                _t("interest_rate", _DELTA, "100", ADVERSE_UP),
                _t("inflation", _DELTA, "50", ADVERSE_UP),
            ),
            floor=Decimal(0),
            cap=Decimal(100),
        ),
        ShockMapping(
            f"{_NMD_RUNOFF_PREFIX}h4",
            _ADDITIVE,
            (
                _t("interest_rate", _DELTA, "60", ADVERSE_UP),
                _t("inflation", _DELTA, "30", ADVERSE_UP),
            ),
            floor=Decimal(0),
            cap=Decimal(100),
        ),
        # Loan-repayment inflows fall in a downturn; a stress can only reduce
        # inflows (cap 1.0) and never zeroes them (floor 0.1). 4.0 ⇒ a 5pp GDP
        # contraction cuts inflows 20%.
        ShockMapping(
            _INFLOW_MULTIPLIER,
            _MULTIPLIER,
            (_t("gdp_growth", _DELTA, "4.0", ADVERSE_DOWN),),
            floor=Decimal("0.1"),
            cap=Decimal(1),
            rationale="Inflow multiplier: −20% per 5pp GDP contraction.",
        ),
        # Duration≈3.5 MTM haircut on HQLA securities from a rate rise
        # (350 ⇒ 3.5% per 1pp). A rate fall floors at 0 (no haircut).
        ShockMapping(
            _HQLA_HAIRCUT,
            _ADDITIVE,
            (_t("interest_rate", _DELTA, "350", ADVERSE_UP),),
            floor=Decimal(0),
            cap=Decimal(100),
            rationale="HQLA haircut: modified-duration 3.5 × Δyield.",
        ),
        # Cedi depreciation feeds the liquidity FX-gap layer (1:1).
        ShockMapping(
            _FX_DEPRECIATION,
            _ADDITIVE,
            (_t("fx_usd_ghs", _FRAC_CHANGE, "100", ADVERSE_UP),),
            floor=Decimal(0),
            rationale="FX depreciation %: 1:1 USD→GHS.",
        ),
    ),
    # Capital emits only the ECL PD/LGD conditioning multipliers — the cleanly
    # macro-derivable credit-risk parameters (¶48). The absolute quarterly
    # capital path (RWA growth, income, credit loss, FX RWA multiplier) is
    # balance-sheet-dependent and is composed by the Phase 2 orchestrator, so it
    # is deliberately NOT emitted here (and these two keys are exempt from the
    # engine's four-key completeness rule).
    "capital": (
        # PD rises as GDP falls (−3.0) and unemployment rises (+2.0). Floor 1.0
        # (a benign scenario never lowers PD below baseline).
        ShockMapping(
            _ECL_PD_MULTIPLIER,
            _MULTIPLIER,
            (
                _t("gdp_growth", _DELTA, "-3.0", ADVERSE_DOWN),
                _t("unemployment", _DELTA, "2.0", ADVERSE_UP),
            ),
            floor=Decimal(1),
            cap=Decimal(5),
            rationale="PD: +15% per 5pp GDP fall, +6% per 3pp unemployment rise.",
        ),
        # LGD rises as GDP falls (collateral values, −1.5) and equities fall
        # (−0.4 on the fractional GSE change).
        ShockMapping(
            _ECL_LGD_MULTIPLIER,
            _MULTIPLIER,
            (
                _t("gdp_growth", _DELTA, "-1.5", ADVERSE_DOWN),
                _t("gse_index", _FRAC_CHANGE, "-0.4", ADVERSE_DOWN),
            ),
            floor=Decimal(1),
            cap=Decimal(3),
            rationale="LGD: collateral erosion from GDP + equity decline.",
        ),
    ),
    # 1:1 market-rate passthrough into a parallel yield-curve shift (bp).
    "irr": (
        ShockMapping(
            _PARALLEL_BP,
            _ADDITIVE,
            (_t("interest_rate", _DELTA, "10000"),),
            rationale="Parallel curve shift: 1:1 with the market rate.",
        ),
    ),
    # Cedi depreciation → NOP revaluation shock (%).
    "fx": (
        ShockMapping(
            _GHS_USD_SHOCK_PCT,
            _ADDITIVE,
            (_t("fx_usd_ghs", _FRAC_CHANGE, "100", ADVERSE_UP),),
            floor=Decimal(0),
            rationale="GHS/USD depreciation shock, 1:1.",
        ),
    ),
    "ftp": (
        # FTP curve tracks the market rate 1:1.
        ShockMapping(
            _CURVE_SHIFT_BP,
            _ADDITIVE,
            (_t("interest_rate", _DELTA, "10000"),),
            rationale="FTP curve shift: 1:1 with the market rate.",
        ),
        # Marginal funding spread widens with the sovereign yield at ~0.5
        # passthrough (retail funding reprices slower). Floor 0 (a yield fall
        # does not narrow the modelled stress spread).
        ShockMapping(
            _FUNDING_ADD_BPS,
            _ADDITIVE,
            (_t("gog_yield", _DELTA, "5000", ADVERSE_UP),),
            floor=Decimal(0),
            rationale="Funding spread add: 0.5 × Δ sovereign yield.",
        ),
    ),
}


def _register_for(
    module: str, overrides: Mapping[str, Sequence[ShockMapping]] | None
) -> Sequence[ShockMapping]:
    """The module's ACTIVE elasticity register — never an empty one.

    Fail-closed one layer above P0-9 (audit 2026-08-22 D-23). An override register
    of ``{"capital": ()}`` used to resolve to no mappings at all, and every entry
    point treated that as a complete answer: :func:`required_variables` reported
    the module needs nothing, :func:`missing_variables` reported nothing missing,
    and :func:`translate` returned ``{}`` — a module that was never stressed
    presenting as stressed with a neutral result, which is the exact shape P0-9
    closed for an empty scenario. An elasticity register that does not resolve is
    a governance failure, not a module with no sensitivities: a module genuinely
    insensitive to the macro is expressed as mappings with zero coefficients, an
    explicit zero rather than an absent one.
    """
    if module not in TRANSLATION_MODULES:
        msg = f"Unknown stress module '{module}'. Expected one of {TRANSLATION_MODULES}."
        raise ValueError(msg)
    register: dict[str, Sequence[ShockMapping]] = dict(DEFAULT_ELASTICITIES)
    if overrides is not None:
        register.update(overrides)
    mappings = register.get(module, ())
    if not mappings:
        raise NotComputable(
            outcome(
                OutcomeState.POLICY_UNRESOLVED,
                metric_id=f"stress_shocks:{module}",
                reason=(
                    f"No elasticity register resolves for the '{module}' module, so it "
                    "cannot be stressed. An empty register would translate to no shocks "
                    "at all and silently leave the module unstressed; author the "
                    "mappings with zero coefficients if the module is genuinely "
                    "insensitive to the macro."
                ),
                items=(f"register:elasticities:{module}",),
                context={"module": module},
            )
        )
    return mappings


def required_variables(
    module: str, overrides: Mapping[str, Sequence[ShockMapping]] | None = None
) -> tuple[str, ...]:
    """Every macro variable the module's active elasticity register reads.

    This IS the module's input contract. A scenario that does not carry all of
    these cannot drive the module — see :func:`translate`.
    """
    return tuple(
        sorted(
            {
                term.variable
                for mapping in _register_for(module, overrides)
                for term in mapping.terms
            }
        )
    )


def missing_variables(
    scenario_paths: Sequence[MacroPathPoint],
    module: str,
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None,
) -> tuple[str, ...]:
    """The required variables the supplied paths do not carry (empty = complete)."""
    present = {point.variable for point in scenario_paths}
    return tuple(
        variable
        for variable in required_variables(module, overrides)
        if variable not in present
    )


def translate(
    scenario_paths: Sequence[MacroPathPoint],
    module: str,
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None,
) -> dict[str, Decimal]:
    """Translate a scenario's macro paths into one module's shock dict.

    Pure and deterministic. Returns ``{shock_key: Decimal}`` containing only the
    shocks that materially deviate from their neutral value; a base scenario
    yields ``{}``. Every returned key belongs to that module's engine vocabulary.

    ``overrides`` replaces the default mappings for a module (the seam a governed
    elasticity register or a behavioural satellite model plugs into) — pass
    ``{"liquidity": (ShockMapping(...), ...)}`` to override just liquidity.

    **Fail-closed on an incomplete scenario** (enterprise audit P0-9). Before
    2026-08-21 an absent macro variable read as a zero delta, so an empty path
    set — a scenario that failed to load, or whose ``variable`` strings were
    mis-keyed — translated to ``{}``: every multiplier neutral, every engine
    unstressed, and a complete, well-formed outcome reporting that the bank
    survives. A configuration failure was indistinguishable from a resilient
    bank. Both conditions now raise
    :class:`~app.domain.authority.outcomes.NotComputable` carrying
    ``MISSING_REQUIRED_INPUT``:

    * ``scenario_paths`` is empty;
    * the module's elasticity register names a macro variable the paths do not
      carry. A variable a bank considers irrelevant is authored FLAT
      (``stress_value == base_value``), which contributes exactly zero — an
      explicit zero, not an absent one; or
    * the module's elasticity register itself does not resolve — the same seam
      one layer up (audit 2026-08-22 D-23), carrying ``POLICY_UNRESOLVED``. See
      :func:`_register_for`.
    """
    mappings = _register_for(module, overrides)
    if not scenario_paths:
        raise NotComputable(
            outcome(
                OutcomeState.MISSING_REQUIRED_INPUT,
                metric_id=f"stress_shocks:{module}",
                reason=(
                    f"The scenario carries no macro-variable paths, so the '{module}' "
                    "module cannot be stressed. An empty path set is a configuration "
                    "failure, not an unstressed scenario."
                ),
                items=tuple(f"macro:{name}" for name in required_variables(module, overrides)),
                context={"module": module},
            )
        )
    missing = missing_variables(scenario_paths, module, overrides)
    if missing:
        raise NotComputable(
            outcome(
                OutcomeState.MISSING_REQUIRED_INPUT,
                metric_id=f"stress_shocks:{module}",
                reason=(
                    f"The scenario does not carry every macro variable the '{module}' "
                    "elasticity register reads. A missing variable would translate to a "
                    "zero delta and silently unstress the module; author it flat "
                    "(stress == base) if it is genuinely not part of the scenario."
                ),
                items=tuple(f"macro:{name}" for name in missing),
                context={"module": module, "missing": list(missing)},
            )
        )
    result: dict[str, Decimal] = {}
    for mapping in mappings:
        value = _shock_value(scenario_paths, mapping)
        if value != mapping.neutral:
            result[mapping.shock_key] = value
    return result
