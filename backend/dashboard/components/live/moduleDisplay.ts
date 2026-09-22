/**
 * Shared display metadata for the live engine's modules: human labels,
 * dashboard routes, the Command Center's default card order, and the one
 * headline metric surfaced in the live status card. Live/freshness payloads
 * carry raw snake_case metric keys, so the headline lookup reads those keys
 * directly.
 *
 * `PRIMARY_METRIC` is mirrored by `_PRIMARY_METRIC_KEY` in
 * `backend/app/services/window_analytics.py` and by `METRIC_LABELS` in
 * `components/home/WindowAnalysis.tsx`; parity tests read this file's source,
 * so keep every entry in the shape `module: { key: "...", label: "..." }`.
 */

import type { LiveModule } from "@aequoros/risk-service-api";
// Relative imports keep this module runnable under the node test build
// (tsconfig.test.json emits CommonJS without rewriting the `@/` alias).
import { num } from "../../lib/api/values";
import { fmtPct } from "../../lib/format";
// Type-only: the two axes below are exactly the ones `SemanticDelta` renders.
import type { DeltaDirection, DeltaFavorability } from "../ui/DeltaBadge";

export const LIVE_MODULE_LABELS: Record<LiveModule, string> = {
  liquidity: "Liquidity",
  capital: "Capital",
  credit: "Credit",
  irr: "Interest Rate Risk",
  fx: "FX Risk",
  ftp: "Transfer Pricing",
  rating: "Credit Assessment",
  forecast: "Balance Sheet Forecast",
};

export const LIVE_MODULE_HREFS: Record<LiveModule, string> = {
  liquidity: "/liquidity",
  capital: "/basel",
  credit: "/credit",
  irr: "/irr/limits",
  fx: "/fx/limits",
  ftp: "/ftp/products",
  rating: "/markets",
  forecast: "/forecasting",
};

/**
 * Command Center card order. Role lenses permute it and never drop a module:
 * a lens that omits one hides a live engine from the people it is meant for.
 */
export const DEFAULT_MODULE_ORDER: LiveModule[] = [
  "liquidity",
  "capital",
  "credit",
  "irr",
  "fx",
  "ftp",
  "rating",
  "forecast",
];

export type LivePrimaryMetric = { label: string; value: string };

// The single most representative metric per module, by its raw payload key.
// Every key is a computed METRIC the engine registers as such; a limit
// (`eve_limit_pct`) is a parameter and never belongs here — it made the IRR
// pulse sparkline a flat line with a zero delta.
const PRIMARY_METRIC: Record<LiveModule, { key: string; label: string }> = {
  liquidity: { key: "lcr_pct", label: "LCR" },
  capital: { key: "car_pct", label: "CAR" },
  credit: { key: "npl_ratio_pct", label: "NPL ratio" },
  irr: { key: "worst_eve_change_pct_tier1", label: "ΔEVE / Tier 1" },
  fx: { key: "nop_pct_tier1", label: "NOP / Tier 1" },
  ftp: { key: "portfolio_nim_pct", label: "Portfolio NIM" },
  rating: { key: "pit_pd_upper_pct", label: "PIT PD upper band" },
  forecast: { key: "year5_car_pct", label: "Year-5 CAR" },
};

/**
 * Modules whose headline is judged on MAGNITUDE. The IRR engine selects the
 * worst ΔEVE scenario by |Δ| and classifies the breach on |Δ|, but stores the
 * SIGNED value (a loss is negative), so a move from −8% to −12% is adverse
 * even though the number fell. The displayed value stays signed, exactly as
 * the IRR module page shows it; only the better/worse comparison uses |Δ|.
 */
const MAGNITUDE_JUDGED_MODULES: ReadonlySet<LiveModule> = new Set<LiveModule>([
  "irr",
]);

/** Raw payload key of a module's headline metric (snapshot ladder reads it). */
export function livePrimaryMetricKey(module: LiveModule): string {
  return PRIMARY_METRIC[module].key;
}

/**
 * Change in a module's headline metric between two readings, in the sense the
 * engine judges it: a signed difference for most ratios, |next| − |prev| where
 * the breach rule is on magnitude. Positive means the figure grew;
 * `ADVERSE_WHEN_UP` decides whether growth is adverse. This is the TONE input
 * only — never print it, because for IRR its sign can contradict the signed
 * series the card shows (see `livePrimaryMetricChange`).
 */
export function livePrimaryMetricDelta(
  module: LiveModule,
  next: number,
  prev: number,
): number {
  return MAGNITUDE_JUDGED_MODULES.has(module)
    ? Math.abs(next) - Math.abs(prev)
    : next - prev;
}

/**
 * Modules whose headline GROWING (as `livePrimaryMetricDelta` measures it) is
 * the adverse outcome: a rising NPL ratio, |ΔEVE|, NOP or PD band. Everywhere
 * else growth is favourable (LCR, CAR, NIM, projected CAR).
 */
const ADVERSE_WHEN_UP: ReadonlySet<LiveModule> = new Set<LiveModule>([
  "credit",
  "irr",
  "fx",
  "rating",
]);

export type LiveMetricChange = {
  /** Signed `next − prev` — the figure the badge prints. */
  change: number;
  /** Sign of `change`: the glyph, so it never contradicts the headline/spark. */
  direction: DeltaDirection;
  /** The engine's judgement of the move: the badge colour. */
  favorability: DeltaFavorability;
};

/**
 * A headline change with its two presentation axes kept apart. DIRECTION
 * (glyph and printed figure) follows the stored series, exactly as the
 * headline and sparkline do; TONE follows the engine's own rule through
 * `livePrimaryMetricDelta` and `ADVERSE_WHEN_UP`. For every module but IRR
 * the two agree; for IRR they can differ, which is the point: −8% → −12% is a
 * red ▼ −4.00 pts (the series fell, the exposure grew), −12% → −8% a green
 * ▲ +4.00 pts. Printing |next| − |prev| instead put a ▲ under a falling spark.
 */
export function livePrimaryMetricChange(
  module: LiveModule,
  next: number,
  prev: number,
): LiveMetricChange {
  const change = next - prev;
  const judged = livePrimaryMetricDelta(module, next, prev);
  const grew = judged > 0;
  const growthIsAdverse = ADVERSE_WHEN_UP.has(module);
  return {
    change,
    direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
    favorability:
      judged === 0
        ? "neutral"
        : grew !== growthIsAdverse
          ? "favorable"
          : "adverse",
  };
}

/** toFixed that never renders negative zero ("-0.00" → "0.00"). */
export function fixed(value: number, decimals: number): string {
  const rendered = value.toFixed(decimals);
  return Number(rendered) === 0 ? (0).toFixed(decimals) : rendered;
}

/** Badge text for a headline change: explicit sign, two decimals, unit. */
export function liveMetricChangeText(change: number, suffix = " pts"): string {
  return `${change > 0 ? "+" : ""}${fixed(change, 2)}${suffix}`;
}

/** Headline metric for a module's live block, or null when unavailable. */
export function livePrimaryMetric(
  module: LiveModule,
  metrics: Record<string, unknown> | null | undefined,
): LivePrimaryMetric | null {
  const spec = PRIMARY_METRIC[module];
  if (!spec || !metrics) return null;
  const raw = metrics[spec.key];
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== "string" && typeof raw !== "number") return null;
  return { label: spec.label, value: fmtPct(num(raw), 2) };
}
