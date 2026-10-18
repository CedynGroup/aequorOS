import {
  CfpContentInputToJSON,
  type CfpContentInput,
} from "@aequoros/risk-service-api";

export function cfpContentToWire(content: CfpContentInput) {
  const wire = CfpContentInputToJSON(content) as Record<string, unknown>;
  return {
    ewi_triggers: wire.ewi_triggers ?? [],
    funding_options: wire.funding_options ?? [],
    action_plans: wire.action_plans ?? [],
    alternative_sources: wire.alternative_sources ?? [],
    escalation_procedures: wire.escalation_procedures ?? [],
    key_relationships: wire.key_relationships ?? [],
    communication_plans: wire.communication_plans ?? [],
    behavioral_liquidity_scenarios:
      content.behavioralLiquidityScenarios?.map((scenario) => ({
        name: scenario.name,
        activation_horizon: scenario.activationHorizon,
        linked_action: scenario.linkedAction,
        deposit_runoff_uplift_pct: scenario.depositRunoffUpliftPct,
        funding_cost_uplift_bps: scenario.fundingCostUpliftBps,
        notes: scenario.notes,
      })) ?? [],
  };
}
