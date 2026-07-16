'use client';

/**
 * Official Runs registry — the governance console's audit trail. A filterable
 * table of every persisted regulatory run (module, scenario, status, input
 * hash, engine version, created-at) grouped into per-day batches. Rows link
 * to the owning module's dashboard; the section footer carries the latest
 * run's RunBadge provenance.
 */

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowUpRight, FileBarChart2 } from 'lucide-react';
import type {
  RegulatoryModule,
  RegulatoryRunSummaryRead,
} from '@aequoros/risk-service-api';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import RunBadge from '@/components/ui/RunBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { fmtDateUTC, fmtTimestamp, labelize, shortId } from '@/lib/api/values';
import {
  groupRunsByDay,
  MODULE_HREFS,
  MODULE_LABELS,
  useOfficialRunsRegistry,
} from './hooks';

const MODULE_FILTERS: { code: RegulatoryModule | null; label: string }[] = [
  { code: null, label: 'All modules' },
  { code: 'liquidity', label: 'Liquidity' },
  { code: 'capital', label: 'Capital' },
  { code: 'irr', label: 'IRRBB' },
  { code: 'fx', label: 'FX' },
  { code: 'ftp', label: 'FTP' },
  { code: 'forecast', label: 'Forecast' },
  { code: 'optimizer', label: 'Optimizer' },
  { code: 'whatif', label: 'What-if' },
];

type StatusFilter = 'all' | 'succeeded' | 'failed';

export default function RunsRegistry({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const router = useRouter();
  const [moduleFilter, setModuleFilter] = useState<RegulatoryModule | null>(
    null
  );
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const { query, runs, total } = useOfficialRunsRegistry(bankId, moduleFilter);

  const filtered = useMemo(
    () =>
      statusFilter === 'all'
        ? runs
        : runs.filter((run) => run.status === statusFilter),
    [runs, statusFilter]
  );

  const groups = useMemo(() => groupRunsByDay(filtered), [filtered]);

  const latest = filtered[0];

  const columns: Column<RegulatoryRunSummaryRead>[] = [
    {
      key: 'module',
      header: 'Module',
      render: (run) => (
        <span className="inline-flex items-center gap-1.5 font-medium text-navy whitespace-nowrap">
          {run.module ? MODULE_LABELS[run.module] ?? labelize(run.module) : '—'}
          <ArrowUpRight
            size={12}
            className="text-slate-light group-hover:text-action transition-colors"
            aria-hidden
          />
        </span>
      ),
    },
    {
      key: 'scenario',
      header: 'Scenario',
      render: (run) => (
        <span className="text-navy/85">{labelize(run.scenarioCode)}</span>
      ),
    },
    {
      key: 'period',
      header: 'Period',
      render: (run) => (
        <span className="font-mono text-caption text-slate">
          {run.periodLabel}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (run) => (
        <StatusPill tone={run.status === 'succeeded' ? 'success' : 'critical'}>
          {labelize(run.status)}
        </StatusPill>
      ),
    },
    {
      key: 'hash',
      header: 'Input hash',
      render: (run) => (
        <span
          className="inline-flex items-center gap-1.5"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <span className="font-mono text-caption text-slate">
            {shortId(run.inputHash, 10)}
          </span>
          <CopyButton text={run.inputHash} label="input hash" />
        </span>
      ),
    },
    {
      key: 'engine',
      header: 'Engine',
      render: (run) => (
        <span className="font-mono text-caption text-slate whitespace-nowrap">
          {run.engineVersion}
        </span>
      ),
    },
    {
      key: 'created',
      header: 'Created',
      align: 'right',
      render: (run) => (
        <span className="font-mono text-caption text-slate tnum whitespace-nowrap">
          {fmtTimestamp(run.createdAt)}
        </span>
      ),
    },
  ];

  return (
    <SectionCard
      title="Official runs registry"
      subtitle={`Immutable calculation runs with full provenance · ${total} on record`}
      noPadding
      actions={
        <label className="inline-flex items-center gap-2 text-caption text-slate">
          Status
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="bg-surface-raised border border-border rounded px-2 py-1.5 text-caption text-navy"
            aria-label="Filter runs by status"
          >
            <option value="all">All</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
          </select>
        </label>
      }
      runBadge={latest ? <RunBadge run={latest} /> : undefined}
      footer={
        latest ? (
          <span className="whitespace-nowrap">Latest run provenance</span>
        ) : undefined
      }
    >
      {/* Module filter chips */}
      <div
        className="flex items-center gap-2 flex-wrap px-5 py-3 border-b border-border-light"
        role="group"
        aria-label="Filter runs by module"
      >
        {MODULE_FILTERS.map((filter) => {
          const active = filter.code === moduleFilter;
          return (
            <button
              key={filter.label}
              type="button"
              aria-pressed={active}
              onClick={() => setModuleFilter(filter.code)}
              className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                active
                  ? 'btn-primary border-transparent'
                  : 'bg-surface-raised text-slate border-border hover:text-navy hover:border-navy/30'
              }`}
            >
              {filter.label}
            </button>
          );
        })}
      </div>

      <QueryBoundary
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        skeleton={<SkeletonTable rows={6} />}
      >
        {filtered.length === 0 ? (
          <div className="p-5">
            <EmptyState
              Icon={FileBarChart2}
              title="No persisted runs yet"
              description={
                moduleFilter || statusFilter !== 'all'
                  ? 'No runs match the current filters — clear them or run a scenario batch from a module dashboard.'
                  : 'Every calculation run from the Liquidity, Basel Capital, IRRBB, FX, FTP, and Forecasting modules is registered here with its engine version and input hash.'
              }
            />
          </div>
        ) : (
          <div>
            {groups.map((group) => (
              <section key={group.key} aria-label={`Runs on ${group.label}`}>
                <div className="flex items-baseline justify-between gap-3 px-5 py-2 bg-surface border-y border-border-light first:border-t-0">
                  <p className="text-caption font-medium text-navy">
                    {group.label}
                  </p>
                  <p className="text-micro uppercase tracking-wider text-slate">
                    {group.runs.length} run{group.runs.length === 1 ? '' : 's'}
                  </p>
                </div>
                <DataTable
                  columns={columns}
                  rows={group.runs}
                  density="compact"
                  onRowClick={(run) => {
                    const href = run.module ? MODULE_HREFS[run.module] : null;
                    if (href) router.push(href);
                  }}
                />
              </section>
            ))}
          </div>
        )}
      </QueryBoundary>
    </SectionCard>
  );
}
