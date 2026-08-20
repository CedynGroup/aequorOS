"""Pure stress-testing domain logic (no DB, no FastAPI).

The Phase-1 macro→risk-parameter translation layer (docs/stress.md §3.2), the
Phase-2 enterprise orchestrator + 3-year projection + Appendix II builders
(§3.3–3.4), and the Phase-4 per-risk methods (§3.6): bottom-up credit
(exposure-level PD/LGD/EAD + rating migration + FX revaluation), concentration,
operational-risk scenario simulation, and contingent leverage. Every module is
pure and deterministic so each is unit-testable in isolation and reproducible.
"""

from app.domain.stress.concentration import (
    ConcentrationExposure,
    ConcentrationInputs,
    ConcentrationParams,
    ConcentrationResult,
    FundingPosition,
    compute_concentration,
)
from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    ContingentLeverageParams,
    ContingentLeverageResult,
    DerivativePosition,
    SftPosition,
    compute_contingent_leverage,
)
from app.domain.stress.credit_bottom_up import (
    CRD_EXPOSURE_CLASSES,
    BottomUpCreditInputs,
    BottomUpCreditResult,
    CreditExposure,
    MigrationParams,
    compute_bottom_up_credit,
    result_at_peak,
    result_for_year,
)
from app.domain.stress.management_actions import (
    ActionKind,
    ActionTrigger,
    ManagementAction,
    ManagementActionError,
    ManagementActionPlan,
    ManagementActionsResult,
    PostActionYear,
    ResolvedAction,
    YearActionAggregate,
    apply_management_actions,
    default_action_plan,
)
from app.domain.stress.operational import (
    OPERATIONAL_SCENARIOS,
    OperationalConfig,
    OperationalInputs,
    OperationalResult,
    OperationalSeverity,
    compute_operational,
)
from app.domain.stress.translation import (
    DEFAULT_ELASTICITIES,
    TRANSLATION_MODULES,
    ElasticityTerm,
    MacroPathPoint,
    ShockMapping,
    frac_change,
    signed_peak_delta,
    translate,
)

__all__ = [
    "CRD_EXPOSURE_CLASSES",
    "DEFAULT_ELASTICITIES",
    "OPERATIONAL_SCENARIOS",
    "TRANSLATION_MODULES",
    "ActionKind",
    "ActionTrigger",
    "BottomUpCreditInputs",
    "BottomUpCreditResult",
    "ConcentrationExposure",
    "ConcentrationInputs",
    "ConcentrationParams",
    "ConcentrationResult",
    "ContingentLeverageInputs",
    "ContingentLeverageParams",
    "ContingentLeverageResult",
    "CreditExposure",
    "DerivativePosition",
    "ElasticityTerm",
    "FundingPosition",
    "MacroPathPoint",
    "ManagementAction",
    "ManagementActionError",
    "ManagementActionPlan",
    "ManagementActionsResult",
    "MigrationParams",
    "OperationalConfig",
    "OperationalInputs",
    "OperationalResult",
    "OperationalSeverity",
    "PostActionYear",
    "ResolvedAction",
    "SftPosition",
    "ShockMapping",
    "YearActionAggregate",
    "apply_management_actions",
    "compute_bottom_up_credit",
    "compute_concentration",
    "compute_contingent_leverage",
    "compute_operational",
    "default_action_plan",
    "frac_change",
    "result_at_peak",
    "result_for_year",
    "signed_peak_delta",
    "translate",
]
