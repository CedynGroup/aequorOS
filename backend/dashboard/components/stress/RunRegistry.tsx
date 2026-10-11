'use client';

/**
 * Persistent, re-openable run registry (docs/stress.md §4 item 5). Mirrors the
 * forecast run registry: EVERY immutable enterprise-stress run is listed with
 * its headline outcome, each re-openable by id, and a quarter picker diffs the
 * same scenarios across reporting periods.
 *
 * The backend exposes the full run history (GET …/enterprise-stress/runs) as
 * lightweight summaries; a row re-opens its immutable run (GET …/runs/{id}) to
 * the full projection + Appendix II. Every row carries its `input_hash`
 * (reproducibility).
 */

import { useMemo, useState } from 'react';
import { FileClock } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { num, fmtDateUTC, shortId } from '@/lib/api/values';
import { useEnterpriseStressRunHistory, useReopenEnterpriseStressRun } from './hooks';
import type {
  EnterpriseStressRead,
  EnterpriseStressRunSummary,
  MacroScenarioSummary,
} from './types';
import type { BankReportingPeriodRead } from '@aequoros/risk-service-api';

type Row = {
  run: EnterpriseStressRunSummary;
  code: string;
  compareErosion: number | null;
};

export default function RunRegistry({
  bankId,
  periodId,
  periods,
  onOpen,
  activeRunId,
}: {
  bankId: string | undefined;
  periodId: string | undefined;
  approvedScenarios: MacroScenarioSummary[];
  periods: BankReportingPeriodRead[];
  onOpen: (run: EnterpriseStressRead) => void;
  activeRunId?: string;
}) {
  const [comparePeriodId, setComparePeriodId] = useState('');

  const registry = useEnterpriseStressRunHistory(bankId, periodId);
  const compareRegistry = useEnterpriseStressRunHistory(bankId, comparePeriodId || undefined);
  const reopen = useReopenEnterpriseStressRun(bankId);

  const rows: Row[] = useMemo(() => {
    // Diff each run against the latest run of the same scenario in the compare quarter.
    const compareByCode = new Map<string, number>();
    for (const r of compareRegistry.data ?? []) {
      if (!compareByCode.has(r.scenario_code)) {
        compareByCode.set(r.scenario_code, num(r.car_erosion_pp));
      }
    }
    return (registry.data ?? []).map((run) => {
      const compare = comparePeriodId && compareByCode.has(run.scenario_code)
        ? num(run.car_erosion_pp) - (compareByCode.get(run.scenario_code) as number)
        : null;
      return { run, code: run.scenario_code, compareErosion: compare };
    });
  }, [registry.data, compareRegistry.data, comparePeriodId]);

  const handleOpen = async (summary: EnterpriseStressRunSummary): Promise<void> => {
    const full: EnterpriseStressRead = await reopen.mutateAsync(summary.run_id);
    onOpen(full);
  };

  const columns: Column<Row>[] = [
    {
      key: 'scenario',
      header: 'Scenario',
      render: (r) => (
        <div className="min-w-0">
          <div className="text-navy font-medium truncate">{r.run.scenario_code}</div>
          <div className="text-micro text-slate-light">hash {shortId(r.run.input_hash, 10)}</div>
        </div>
      ),
    },
    {
      key: 'car',
      header: 'Stressed CAR',
      numeric: true,
      render: (r) => `${num(r.run.stressed_car_end_pct).toFixed(2)}%`,
    },
    {
      key: 'erosion',
      header: 'CAR erosion',
      numeric: true,
      render: (r) => (
        <span className="text-critical">{num(r.run.car_erosion_pp).toFixed(2)} pp</span>
      ),
    },
    ...(comparePeriodId
      ? [
          {
            key: 'diff',
            header: 'Δ vs quarter',
            numeric: true,
            render: (r: Row) =>
              r.compareErosion === null ? (
                <span className="text-slate-light">—</span>
              ) : (
                <span className={r.compareErosion > 0 ? 'text-critical' : 'text-success'}>
                  {r.compareErosion > 0 ? '+' : ''}
                  {r.compareErosion.toFixed(2)} pp
                </span>
              ),
          } as Column<Row>,
        ]
      : []),
    {
      key: 'status',
      header: 'Minima',
      render: (r) => (
        <StatusPill tone={r.run.stress_stays_above_all_minima ? 'success' : 'critical'}>
          {r.run.stress_stays_above_all_minima ? 'Above all' : `Breach Y${r.run.first_breach_year ?? '—'}`}
        </StatusPill>
      ),
    },
    {
      key: 'when',
      header: 'Run',
      render: (r) => <span className="text-caption text-slate">{fmtDateUTC(new Date(r.run.created_at))}</span>,
    },
  ];

  return (
    <SectionCard
      title="Run registry"
      subtitle="Immutable enterprise-stress runs — re-openable, reproducible, quarter-diffable"
      noPadding
      actions={
        periods.length > 1 ? (
          <select
            className="rounded-md border border-border-light bg-transparent px-2 py-1 text-caption text-navy"
            value={comparePeriodId}
            onChange={(e) => setComparePeriodId(e.target.value)}
            aria-label="Compare against quarter"
          >
            <option value="">Diff vs quarter…</option>
            {periods
              .filter((p) => p.id !== periodId)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {fmtDateUTC(new Date(p.periodEnd))}
                </option>
              ))}
          </select>
        ) : null
      }
    >
      <QueryBoundary isLoading={registry.isLoading} error={registry.error} onRetry={() => registry.refetch()}>
        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState
              Icon={FileClock}
              title="No stress runs for this quarter yet"
              description="Approve a scenario and run the enterprise stress test — each run is persisted immutably and appears here, re-openable and reproducible from its input hash."
            />
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            density="compact"
            emphasizeTotals={false}
            onRowClick={(r) => { void handleOpen(r.run); }}
            rowClassName={(r) => (r.run.run_id === activeRunId ? 'bg-action-light/40' : '')}
          />
        )}
      </QueryBoundary>
    </SectionCard>
  );
}
