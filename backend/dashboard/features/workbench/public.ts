// Workbench's cross-feature interface: re-exports only, no logic. Features
// consume workbench through this module (FEATURE_LAYOUT.md); imports of any
// other workbench module are boundary violations.
export { default as RunModuleScenarios } from "./components/RunModuleScenarios";
export {
  default as ScenarioMetrics,
  scenarioDelta,
  scenarioLabel,
  scenarioRisk,
  scenarioValue,
} from "./components/ScenarioMetrics";
export { default as ScenarioWorkbench } from "./components/ScenarioWorkbench";
