// Liquidity's cross-feature interface: re-exports only, no logic. Features
// consume liquidity through this module (FEATURE_LAYOUT.md); imports of any
// other liquidity module are boundary violations.
export { default as IllustrativeBadge } from "./components/IllustrativeBadge";
export { default as RatioTrendChart } from "./components/charts/RatioTrendChart";
export { runComputedAt, runThresholds } from "./components/runData";
