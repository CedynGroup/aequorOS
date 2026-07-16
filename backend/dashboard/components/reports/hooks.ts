'use client';

/**
 * Reports-module hooks and pure helpers, layered over lib/api/client and the
 * shared lib/api/hooks. New governance-console reads live here so the shared
 * hooks file stays untouched; everything reuses the 'reg-runs' query-key
 * prefix (via useRegulatoryRuns) so pipeline mutations elsewhere invalidate
 * the registry too.
 */

import { useMemo } from 'react';
import type {
  RegulatoryModule,
  RegulatoryRunSummaryRead,
} from '@aequoros/risk-service-api';
import { useRegulatoryRuns } from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

/** Where each regulatory module's dashboard lives. */
export const MODULE_HREFS: Record<string, string> = {
  liquidity: '/liquidity',
  capital: '/basel',
  irr: '/irr',
  fx: '/fx',
  ftp: '/ftp',
  forecast: '/forecasting',
  optimizer: '/forecasting',
  whatif: '/forecasting',
};

/** Human labels for the run registry's module column and filters. */
export const MODULE_LABELS: Record<string, string> = {
  liquidity: 'Liquidity',
  capital: 'Basel Capital',
  irr: 'IRRBB',
  fx: 'FX Risk',
  ftp: 'FTP',
  forecast: 'Forecasting',
  optimizer: 'Optimizer',
  whatif: 'What-if',
};

/** A same-day batch of runs, newest day first. */
export type RunDayGroup = {
  /** UTC day key, e.g. "2026-07-14". */
  key: string;
  /** Display label, e.g. "14 Jul 2026". */
  label: string;
  runs: RegulatoryRunSummaryRead[];
};

/**
 * Group runs into per-day batches (runs are minted in batches — run-all
 * scenario sweeps and official-run mints land seconds apart, so the calendar
 * day is the natural registry grouping). Input is API order (newest first);
 * order is preserved within groups.
 */
export function groupRunsByDay(
  runs: RegulatoryRunSummaryRead[]
): RunDayGroup[] {
  const groups: RunDayGroup[] = [];
  const index = new Map<string, RunDayGroup>();
  for (const run of runs) {
    const key = run.createdAt.toISOString().slice(0, 10);
    let group = index.get(key);
    if (!group) {
      group = { key, label: fmtDateUTC(run.createdAt), runs: [] };
      index.set(key, group);
      groups.push(group);
    }
    group.runs.push(run);
  }
  return groups;
}

/**
 * The official-runs registry read: a wide page of regulatory runs with an
 * optional module filter. Reuses the shared 'reg-runs' query key so pipeline
 * mutations elsewhere invalidate this view too.
 */
export function useOfficialRunsRegistry(
  bankId: string | undefined,
  moduleFilter: RegulatoryModule | null
) {
  const query = useRegulatoryRuns(bankId, {
    module: moduleFilter ?? undefined,
    limit: 100,
  });
  const runs = useMemo(() => query.data?.runs ?? [], [query.data]);
  return { query, runs, total: query.data?.total ?? 0 };
}

/**
 * Latest successful run per module — provenance (engine version, input hash,
 * created-at) for board-pack module blocks and the Settings About panel.
 */
export function useLatestRunsByModule(bankId: string | undefined) {
  const query = useRegulatoryRuns(bankId, { limit: 100 });
  const byModule = useMemo(() => {
    const map = new Map<string, RegulatoryRunSummaryRead>();
    for (const run of query.data?.runs ?? []) {
      if (run.status !== 'succeeded' || !run.module) continue;
      // API order is newest-first; keep the first hit per module.
      if (!map.has(run.module)) map.set(run.module, run);
    }
    return map;
  }, [query.data]);
  return { query, byModule };
}
