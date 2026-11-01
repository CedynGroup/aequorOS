// Home's cross-feature interface: re-exports only, no logic. Features consume
// home through this module (FEATURE_LAYOUT.md); imports of any other home
// module are boundary violations.
export { default as BreachBanner } from "./components/BreachBanner";
export { default as PulseWall } from "./components/PulseWall";
export { usePulseCards } from "./components/pulse";
