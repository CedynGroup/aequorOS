"""Capital's interface for other features: re-exports only, no logic."""

# pyright: reportPrivateUsage=false
# The underscored service helpers cross the boundary under public aliases;
# this module is their only sanctioned re-export surface.

from app.capital.schemas.capital_plan import (
    CapitalFloorRead,
    CapitalPlanContent,
    CapitalPlanPut,
    CapitalPlanRead,
    CapitalPlanSummaryRead,
    IlaapSnapshotRead,
)
from app.capital.schemas.sdi import (
    CapitalCheckRead,
    DelinquencyBucketRead,
    LiquidityMonitoringRead,
    LoanGradeBucketRead,
    ModuleReadinessRead,
    PortfolioAtRiskRead,
    ProvisionsHeldRead,
    SdiCapitalChecksRead,
    SdiCounterbalancingCapacityRead,
    SdiFundingConcentrationRead,
    SdiFundingProviderRead,
    SdiLoanClassificationRead,
    SdiMaturityBucketRead,
    SdiReadinessRead,
)
from app.capital.service import capital_plan, regulatory_capital, sdi_capital
from app.capital.service.regulatory_capital import (
    _CAPITAL_FACT_GROUPS as capital_fact_groups,  # noqa: PLC2701 - parity-test seam
)
from app.capital.service.regulatory_capital import (
    ECL_CONDITIONING_KEYS,
    CapitalRunError,
    OfficialCapitalPosition,
    official_capital_position,
    run_all_capital_scenarios,
)
from app.capital.service.regulatory_capital import (
    _compute_inline as compute_inline,  # noqa: PLC2701 - dashboard-trend parity
)
from app.capital.service.regulatory_capital import (
    _decimal_metrics as run_metrics,  # noqa: PLC2701 - dashboard-trend parity
)
from app.capital.service.regulatory_capital import (
    _latest_succeeded_baseline_run as latest_succeeded_baseline_run,  # noqa: PLC2701
)

# isort: split
# sdi_readiness is bound last: it initializes after every lighter re-export
# above. (sdi_views cannot be re-exported here at all — it reaches reporting's
# le_generation, which imports generation, which imports this module.)
from app.capital.service import sdi_readiness  # noqa: E402 - deliberate order

__all__ = [
    "CapitalCheckRead",
    "CapitalFloorRead",
    "CapitalPlanContent",
    "CapitalPlanPut",
    "CapitalPlanRead",
    "CapitalPlanSummaryRead",
    "CapitalRunError",
    "DelinquencyBucketRead",
    "ECL_CONDITIONING_KEYS",
    "IlaapSnapshotRead",
    "LiquidityMonitoringRead",
    "LoanGradeBucketRead",
    "ModuleReadinessRead",
    "OfficialCapitalPosition",
    "PortfolioAtRiskRead",
    "ProvisionsHeldRead",
    "SdiCapitalChecksRead",
    "SdiCounterbalancingCapacityRead",
    "SdiFundingConcentrationRead",
    "SdiFundingProviderRead",
    "SdiLoanClassificationRead",
    "SdiMaturityBucketRead",
    "SdiReadinessRead",
    "capital_fact_groups",
    "capital_plan",
    "compute_inline",
    "latest_succeeded_baseline_run",
    "official_capital_position",
    "regulatory_capital",
    "run_all_capital_scenarios",
    "run_metrics",
    "sdi_capital",
    "sdi_readiness",
]
