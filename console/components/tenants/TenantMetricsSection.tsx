'use client';

import type { ApiError, TenantLiveMetric, TenantMetricsResponse, TenantRegulatoryRun } from '@/lib/api';
import { fmtTs, relTime, shortId, DASH } from '@/lib/format';
import {
  Chip,
  DataTable,
  EmptyState,
  InfoTip,
  SectionCard,
  StatusChip,
  type Column,
} from '@/components/ui';
import { DeepSectionBody } from './DeepSectionBody';
import { summarizeObject, formatJson } from './util';

/**
 * Computed module outputs for one tenant: latest LiveMetric per module (the
 * always-fresh baseline) and the latest immutable RegulatoryRun per family (the
 * filing reference). Both are drawn straight from the payload — module output
 * objects are summarized as `key=value` fragments rather than guessing a
 * headline metric.
 */

const liveColumns: Column<TenantLiveMetric>[] = [
  {
    key: 'module',
    header: 'Module',
    sortable: true,
    sortAccessor: (m) => m.module,
    render: (m) => <span className="font-mono text-caption text-navy">{m.module}</span>,
  },
  {
    key: 'status',
    header: 'Status',
    sortable: true,
    sortAccessor: (m) => m.status,
    render: (m) => <StatusChip value={m.status} />,
  },
  {
    key: 'outputs',
    header: 'Outputs',
    render: (m) => (
      <span
        className="break-words font-mono text-caption text-navy/90"
        title={formatJson(m.metrics)}
      >
        {summarizeObject(m.metrics)}
      </span>
    ),
  },
  {
    key: 'period',
    header: 'Period',
    sortable: true,
    sortAccessor: (m) => m.reporting_period_id,
    render: (m) => (
      <span className="font-mono text-caption text-slate" title={m.reporting_period_id}>
        {shortId(m.reporting_period_id)}
      </span>
    ),
  },
  {
    key: 'computed',
    header: 'Computed',
    align: 'right',
    sortable: true,
    sortAccessor: (m) => m.computed_at,
    render: (m) => (
      <span className="whitespace-nowrap text-caption text-slate" title={fmtTs(m.computed_at)}>
        {relTime(m.computed_at)}
      </span>
    ),
  },
];

const runColumns: Column<TenantRegulatoryRun>[] = [
  {
    key: 'module',
    header: 'Family',
    sortable: true,
    sortAccessor: (r) => r.module,
    render: (r) => <span className="font-mono text-caption text-navy">{r.module}</span>,
  },
  {
    key: 'scenario',
    header: 'Scenario',
    sortable: true,
    sortAccessor: (r) => r.scenario_code,
    render: (r) => <span className="text-caption text-navy/90">{r.scenario_code || DASH}</span>,
  },
  {
    key: 'status',
    header: 'Status',
    sortable: true,
    sortAccessor: (r) => r.status,
    render: (r) => <StatusChip value={r.status} />,
  },
  {
    key: 'error',
    header: 'Error',
    render: (r) =>
      r.error_code ? (
        <Chip tone="crit" mono title={r.error_code}>
          {r.error_code}
        </Chip>
      ) : (
        <span className="text-slate-light">{DASH}</span>
      ),
  },
  {
    key: 'run',
    header: 'Run',
    render: (r) => (
      <span className="font-mono text-caption text-slate" title={`${r.run_id} · hash ${r.input_hash}`}>
        {shortId(r.run_id)}
      </span>
    ),
  },
  {
    key: 'completed',
    header: 'Completed',
    align: 'right',
    sortable: true,
    sortAccessor: (r) => r.completed_at ?? r.started_at ?? '',
    render: (r) =>
      r.completed_at ? (
        <span className="whitespace-nowrap text-caption text-slate" title={fmtTs(r.completed_at)}>
          {relTime(r.completed_at)}
        </span>
      ) : (
        <span className="text-slate-light">{DASH}</span>
      ),
  },
];

export function TenantMetricsSection({
  data,
  loading,
  error,
  reload,
  orgId,
  orgLabel,
}: {
  data: TenantMetricsResponse | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
  orgId: string;
  orgLabel?: string;
}) {
  const live = data?.live_metrics ?? [];
  const runs = data?.regulatory_runs ?? [];

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-1.5">
          Metrics
          <InfoTip label="About metrics">
            Live metrics are re-derived on every ingestion (no filing written); official runs are
            the immutable, signed filing snapshots. Both are shown latest-per-module.
          </InfoTip>
        </span>
      }
      subtitle={`${live.length} live module(s) · ${runs.length} official run(s)`}
      noPadding
      className="mb-5"
    >
      <DeepSectionBody
        loading={loading && !data}
        error={error}
        reload={reload}
        context="Loading metrics"
        orgId={orgId}
        orgLabel={orgLabel}
        showEmpty={live.length === 0 && runs.length === 0}
        empty={
          <EmptyState
            title="No computed metrics"
            description="Module outputs appear here once the tenant has ingested data and the engine has run."
          />
        }
      >
        <div>
          <div className="px-4 pt-3 text-micro font-medium uppercase tracking-wider text-slate">
            Live metrics
          </div>
          {live.length > 0 ? (
            <DataTable
              columns={liveColumns}
              rows={live}
              density="compact"
              initialSort={{ key: 'module', dir: 'asc' }}
              getFilterText={(m) => [m.module, m.status, summarizeObject(m.metrics)].join(' ')}
              filterPlaceholder="Filter modules…"
              emptyMessage="No live metrics."
            />
          ) : (
            <p className="px-4 py-3 text-caption text-slate">No live metrics computed yet.</p>
          )}

          <div className="mt-2 border-t border-border-light px-4 pt-3 text-micro font-medium uppercase tracking-wider text-slate">
            Latest official runs
          </div>
          {runs.length > 0 ? (
            <DataTable
              columns={runColumns}
              rows={runs}
              density="compact"
              initialSort={{ key: 'completed', dir: 'desc' }}
              getFilterText={(r) => [r.module, r.scenario_code, r.status, r.error_code ?? ''].join(' ')}
              filterPlaceholder="Filter runs…"
              emptyMessage="No official runs."
            />
          ) : (
            <p className="px-4 py-3 text-caption text-slate">No official runs minted yet.</p>
          )}
        </div>
      </DeepSectionBody>
    </SectionCard>
  );
}
