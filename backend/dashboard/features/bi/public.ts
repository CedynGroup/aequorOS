// BI's cross-feature interface: re-exports only, no logic. Features consume BI
// through this module (FEATURE_LAYOUT.md); imports of any other BI module are
// boundary violations.
export { LandingInsightStrip, useKpiExplain } from "./components/InsightStrip";
