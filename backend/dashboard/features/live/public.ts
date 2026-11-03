// Live's cross-feature interface: re-exports only, no logic. Features consume
// live through this module (FEATURE_LAYOUT.md); imports of any other live
// module are boundary violations.
export { default as LiveEngineNote } from "./components/LiveEngineNote";
export { default as UnreconciledBookBanner } from "./components/UnreconciledBookBanner";
export {
  DEFAULT_MODULE_ORDER,
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
  fixed,
  liveMetricChangeText,
  livePrimaryMetricChange,
  livePrimaryMetricIsAdvisory,
  livePrimaryMetricKey,
  type LiveMetricChange,
} from "./components/moduleDisplay";
