/**
 * Read-side helpers over stored RegulatoryRunRead payloads shared by the
 * liquidity and Basel capital workspaces. Display plumbing only — no
 * regulatory math beyond re-reading what the engine persisted.
 */

import type { RegulatoryRunRead } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';

/** A scalar metric from the run's snake_case metrics dict. */
export function runMetric(
  run: RegulatoryRunRead | undefined,
  key: string
): number | null {
  const value = run?.metrics?.[key];
  if (value === undefined || value === null) return null;
  return num(value as string);
}

/** Traffic-light status of a named metric result. */
export function runMetricStatus(
  run: RegulatoryRunRead | undefined,
  metricCode: string
): string | null {
  return (
    run?.metricResults.find((m) => m.metricCode === metricCode)?.status ?? null
  );
}

/** threshold_min of a named metric result (e.g. lcr_pct → 100). */
export function runMetricThreshold(
  run: RegulatoryRunRead | undefined,
  metricCode: string
): number | null {
  const result = run?.metricResults.find((m) => m.metricCode === metricCode);
  return result?.thresholdMin === null || result?.thresholdMin === undefined
    ? null
    : num(result.thresholdMin);
}

/**
 * The active threshold parameter set snapshotted into the run inputs
 * (`inputs.parameters.thresholds_pct`): lcr_min, lcr_amber_floor, nsfr_min,
 * lcr_inflow_cap_pct (+ capital equivalents on capital runs).
 */
export function runThresholds(
  run: RegulatoryRunRead | undefined
): Record<string, number> {
  const parameters = (
    run?.inputs as
      | { parameters?: { thresholds_pct?: Record<string, string> } }
      | undefined
  )?.parameters;
  const raw = parameters?.thresholds_pct ?? {};
  return Object.fromEntries(
    Object.entries(raw).map(([key, value]) => [key, num(value)])
  );
}

/**
 * Completion timestamp of a run as a Date (the generated client surfaces
 * completed_at as an ISO string | null), falling back to created_at.
 */
export function runComputedAt(
  run:
    | { completedAt?: string | null; createdAt?: Date }
    | undefined
): Date | undefined {
  if (!run) return undefined;
  if (run.completedAt) {
    const parsed = new Date(run.completedAt);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return run.createdAt;
}

/** Sum of weighted amounts in a run line-item section (outflow, inflow, …). */
export function runSectionTotal(
  run: RegulatoryRunRead | undefined,
  section: string
): number | null {
  if (!run) return null;
  const lines = run.lineItems.filter((line) => line.section === section);
  if (!lines.length) return null;
  return lines.reduce((sum, line) => sum + num(line.weightedAmount), 0);
}
