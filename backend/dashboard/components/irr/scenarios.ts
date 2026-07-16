/**
 * Shared display metadata for the IRRBB workspace.
 *
 * Scenario codes come from the backend `IrrScenarioCode` enum (baseline plus
 * the six Basel IRRBB shocks the engine runs); descriptions summarize the
 * shock shape documented in `app/domain/irr/engine.py`. Display-only — no
 * regulatory math happens client-side.
 */

export const SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  parallel_up_200: 'Parallel +200bp',
  parallel_down_200: 'Parallel −200bp',
  short_up_250: 'Short +250bp',
  short_down_250: 'Short −250bp',
  steepener: 'Steepener',
  flattener: 'Flattener',
};

/** Shock-shape summaries for the standard Basel IRRBB scenario set. */
export const SCENARIO_DESCRIPTIONS: Record<string, string> = {
  baseline: 'Base zero-coupon curve, no shock applied',
  parallel_up_200: 'Parallel upward shift of the full curve',
  parallel_down_200: 'Parallel downward shift of the full curve',
  short_up_250: 'Short-end rates shocked up, long end anchored',
  short_down_250: 'Short-end rates shocked down, long end anchored',
  steepener: 'Short rates down, long rates up — curve steepens',
  flattener: 'Short rates up, long rates down — curve flattens',
};

export function scenarioLabel(code: string): string {
  return SCENARIO_LABELS[code] ?? code;
}

export function scenarioDescription(code: string): string | undefined {
  return SCENARIO_DESCRIPTIONS[code];
}

/**
 * The engine's nine ordered repricing buckets (`IRR_BUCKETS`), used to keep
 * ladder ordering stable even if the payload order ever changes.
 */
export const BUCKET_ORDER = [
  'overnight',
  '1-7d',
  '8-30d',
  '1-3m',
  '3-6m',
  '6-12m',
  '1-3y',
  '3-5y',
  '5y+',
] as const;

export function bucketRank(bucket: string): number {
  const idx = (BUCKET_ORDER as readonly string[]).indexOf(bucket);
  return idx === -1 ? BUCKET_ORDER.length : idx;
}
