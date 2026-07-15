'use client';

import { useState } from 'react';
import { FileBarChart2 } from 'lucide-react';
import type { RegulatoryModule } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody } from '@/components/ui/Card';
import StatusPill from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import { useRegulatoryRuns } from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, labelize, num, shortId } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

const MODULE_FILTERS: { code: RegulatoryModule | null; label: string }[] = [
  { code: null, label: 'All modules' },
  { code: 'liquidity', label: 'Liquidity' },
  { code: 'capital', label: 'Capital' },
  { code: 'forecast', label: 'Forecast' },
  { code: 'optimizer', label: 'Optimizer' },
  { code: 'whatif', label: 'What-if' },
];

/** Pull the module's headline metric out of the raw metrics dict, if present. */
function headlineMetric(
  module: string | null,
  metrics: Record<string, unknown>
): string | null {
  const pct = (key: string, label: string): string | null => {
    const value = metrics[key];
    return typeof value === 'string' ? `${label} ${fmtPct(num(value), 2)}` : null;
  };
  switch (module) {
    case 'liquidity':
      return pct('lcr_pct', 'LCR');
    case 'capital':
      return pct('car_pct', 'CAR');
    case 'forecast': {
      const summary = metrics['summary'] as
        | { avg_roe_pct?: string }
        | undefined;
      if (summary?.avg_roe_pct) {
        return `Avg ROE ${fmtPct(num(summary.avg_roe_pct), 2)}`;
      }
      return pct('avg_roe_pct', 'Avg ROE');
    }
    case 'optimizer': {
      const top = metrics['top'] as
        | { summary?: { avg_roe_pct?: string } }[]
        | undefined;
      const best = Array.isArray(top) ? top[0]?.summary?.avg_roe_pct : undefined;
      return best ? `Best avg ROE ${fmtPct(num(best), 2)}` : null;
    }
    case 'whatif': {
      const year5 = metrics['year5'] as
        | { car_pct?: { delta?: string } }
        | undefined;
      const delta = year5?.car_pct?.delta;
      return delta
        ? `ΔCAR Y5 ${num(delta) >= 0 ? '+' : ''}${num(delta).toFixed(2)} pp`
        : null;
    }
    default:
      return null;
  }
}

export default function ReportsPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const [moduleFilter, setModuleFilter] = useState<RegulatoryModule | null>(null);
  const runsQuery = useRegulatoryRuns(bankId, {
    module: moduleFilter ?? undefined,
    limit: 50,
  });
  const runs = runsQuery.data?.runs ?? [];

  return (
    <>
      <PageHeader
        title="Reports Library"
        subtitle="Immutable calculation runs · Full audit trail"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <div className="px-8 py-6 space-y-6">
        {/* Module filter chips */}
        <div className="flex items-center gap-2 flex-wrap">
          {MODULE_FILTERS.map((filter) => {
            const active =
              filter.code === moduleFilter ||
              (filter.code === null && moduleFilter === null);
            return (
              <button
                key={filter.label}
                type="button"
                onClick={() => setModuleFilter(filter.code)}
                className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                  active
                    ? 'bg-navy text-white border-navy'
                    : 'bg-white text-slate border-border hover:text-navy hover:border-navy/30'
                }`}
              >
                {filter.label}
              </button>
            );
          })}
        </div>

        <Card>
          <CardBody className="p-0">
            <QueryBoundary
              isLoading={runsQuery.isLoading}
              error={runsQuery.error}
              onRetry={() => runsQuery.refetch()}
              skeleton={
                <p className="px-5 py-4 text-body text-slate">Loading runs…</p>
              }
            >
              {runs.length === 0 ? (
                <p className="px-5 py-4 text-body text-slate">
                  No persisted runs
                  {moduleFilter ? ` for ${labelize(moduleFilter)}` : ''} yet —
                  every calculation run from the Liquidity, Basel Capital, and
                  Forecasting modules appears here.
                </p>
              ) : (
                <ul className="divide-y divide-border-light">
                  {runs.map((run) => {
                    const headline = headlineMetric(
                      run.module,
                      (run.metrics ?? {}) as Record<string, unknown>
                    );
                    return (
                      <li
                        key={run.id}
                        className="px-5 py-3.5 flex items-center gap-4 hover:bg-surface-alt"
                      >
                        <span className="w-9 h-9 rounded bg-action-light text-action inline-flex items-center justify-center shrink-0">
                          <FileBarChart2 size={16} aria-hidden />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-body font-medium text-navy truncate">
                            {run.module ? labelize(run.module) : 'Run'} ·{' '}
                            {labelize(run.scenarioCode)} · period{' '}
                            {run.periodLabel}
                          </p>
                          <p className="text-caption text-slate truncate">
                            {headline ? `${headline} · ` : ''}
                            <span className="font-mono">
                              {run.engineVersion}
                            </span>{' '}
                            ·{' '}
                            <span className="font-mono">
                              {shortId(run.inputHash, 10)}
                            </span>
                          </p>
                        </div>
                        <span className="font-mono text-caption text-slate w-32 shrink-0 tabular-nums text-right">
                          {fmtTimestamp(run.createdAt)}
                        </span>
                        <StatusPill
                          tone={run.status === 'succeeded' ? 'success' : 'critical'}
                          className="shrink-0"
                        >
                          {labelize(run.status)}
                        </StatusPill>
                      </li>
                    );
                  })}
                </ul>
              )}
            </QueryBoundary>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
