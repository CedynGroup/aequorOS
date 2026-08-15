import { DASH } from '@/lib/format';

/** Narrow an unknown JSON value to a plain object (or null). */
export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Render an unknown pipeline value as display text (DASH when absent). */
export function show(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

/** Preferred display order for the AEQ.* curve family. */
export const CURVE_ORDER = [
  'AEQ.GHS.SOV.ZERO',
  'AEQ.GHS.SOV.FWD',
  'AEQ.GHS.OIS',
  'AEQ.GHS.CORP',
];

/** Preferred display order for rate treatments in the grouped rates table. */
export const TREATMENT_ORDER = [
  'pass_through',
  'windowed',
  'derived',
  'research_override',
  'research_spread',
  'research_adjustment',
];
