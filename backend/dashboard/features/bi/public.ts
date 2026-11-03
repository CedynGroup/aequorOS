// BI's cross-feature interface: re-exports only, no logic. Features consume BI
// through this module (FEATURE_LAYOUT.md); imports of any other BI module are
// boundary violations.
export { default as EChart, type BiEChartsOption } from "./components/EChart";
export { LandingInsightStrip, useKpiExplain } from "./components/InsightStrip";
export { seriesColor, useChartTokens } from "./components/echartsTheme";
