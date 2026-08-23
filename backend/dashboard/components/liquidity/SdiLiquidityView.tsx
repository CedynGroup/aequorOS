'use client';

import PageHeader from '@/components/ui/PageHeader';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { fmtCurrency, fmtPct } from '@/lib/format';
import { num } from '@/lib/api/values';
import {
  type SdiLiquidityRatio,
  type SdiLiquidityReserve,
  type SdiMaturityBucket,
  useSdiLiquidityPosition,
} from '@/components/basel/sdiHooks';

function kpiStatus(status: string): KpiStatus {
  return status === 'below_minimum' ? 'crit' : status === 'not_computable' ? 'warn' : 'ok';
}

function statusTone(status: string): 'success' | 'critical' | 'amber' | 'slate' {
  return status === 'below_minimum'
    ? 'critical'
    : status === 'not_computable'
    ? 'amber'
    : 'success';
}

function statusLabel(status: string): string {
  return status === 'below_minimum'
    ? 'Below floor'
    : status === 'not_computable'
    ? 'Not computable'
    : 'Within floor';
}

const ratioColumns: Column<SdiLiquidityRatio>[] = [
  { key: 'label', header: 'Prudential ratio', render: (row) => row.label },
  {
    key: 'value',
    header: 'Current',
    numeric: true,
    render: (row) => (row.value_pct === null ? '—' : fmtPct(num(row.value_pct), 2)),
  },
  {
    key: 'floor',
    header: 'SDI floor',
    numeric: true,
    render: (row) => fmtPct(num(row.threshold_pct), 2),
  },
  {
    key: 'status',
    header: 'Status',
    render: (row) => <StatusPill tone={statusTone(row.status)}>{statusLabel(row.status)}</StatusPill>,
  },
];

const reserveColumns: Column<SdiLiquidityReserve>[] = [
  { key: 'label', header: 'Reserve check', render: (row) => row.label },
  {
    key: 'value',
    header: 'Current',
    numeric: true,
    render: (row) => (row.value_pct === null ? '—' : fmtPct(num(row.value_pct), 2)),
  },
  {
    key: 'floor',
    header: 'Minimum',
    numeric: true,
    render: (row) => fmtPct(num(row.threshold_pct), 2),
  },
  {
    key: 'status',
    header: 'Status',
    render: (row) => <StatusPill tone={statusTone(row.status)}>{statusLabel(row.status)}</StatusPill>,
  },
];

const maturityColumns: Column<SdiMaturityBucket>[] = [
  { key: 'label', header: 'Maturity bucket', render: (row) => row.label },
  {
    key: 'net',
    header: 'Net mismatch',
    numeric: true,
    render: (row) => fmtCurrency(num(row.net_mismatch_ghs)),
  },
  {
    key: 'cumulative',
    header: 'Cumulative mismatch',
    numeric: true,
    render: (row) =>
      row.cumulative_mismatch_ghs === null ? '—' : fmtCurrency(num(row.cumulative_mismatch_ghs)),
  },
];

export default function SdiLiquidityView({ bankId }: { bankId: string | undefined }) {
  const position = useSdiLiquidityPosition(bankId);
  const ratios = position.data?.ratios ?? [];
  const reserves = position.data?.reserves ?? [];
  const breached = [...ratios, ...reserves].filter((row) => row.status === 'below_minimum').length;
  const ready = position.data?.readiness.filter((row) => row.status === 'ready').length ?? 0;
  const readiness = position.data?.readiness ?? [];

  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Liquidity"
        subtitle="Liquidity monitoring for a specialised deposit-taking institution, against the Liquidity Monitoring Tools Directive (exposure draft, Feb 2026 — stated effective 1 Jan 2027)."
      />

      <QueryBoundary
        isLoading={position.isLoading}
        error={position.error}
        onRetry={() => position.refetch()}
      >
        {position.data ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiStat
                label="Ratio breaches"
                value={String(breached)}
                status={breached > 0 ? 'crit' : 'ok'}
                hint="LMTD Table 1 and liquidity-reserve checks"
              />
              <KpiStat
                label="Table 1 ratios"
                value={String(ratios.length)}
                status="ok"
                hint="Current period against active SDI floors"
              />
              <KpiStat
                label="Data-ready controls"
                value={`${ready} / ${readiness.length}`}
                status={ready === readiness.length ? 'ok' : 'warn'}
                hint="Position data completeness for liquidity controls"
              />
              <KpiStat
                label="Source as of"
                value={position.data.as_of}
                status="ok"
                hint="Latest accepted canonical position snapshot"
              />
            </div>

            <SectionCard
              title="LMTD Table 1 prudential ratios"
              subtitle="Draft-directive liquidity ratios for this SDI, measured against the active threshold for each ratio. The directive is not yet in force; Board-adopted levels govern until it is."
              noPadding
            >
              <DataTable columns={ratioColumns} rows={ratios} density="compact" />
            </SectionCard>

            <SectionCard
              title="Liquidity reserves"
              subtitle="Primary reserve and cumulative primary-plus-secondary reserve as a share of deposit liabilities."
              noPadding
            >
              <DataTable columns={reserveColumns} rows={reserves} density="compact" />
            </SectionCard>

            <SectionCard
              title="Contractual maturity mismatch"
              subtitle="Net and cumulative contractual cash-flow mismatch by maturity bucket."
              noPadding
            >
              <DataTable columns={maturityColumns} rows={position.data.maturity_ladder} density="compact" />
            </SectionCard>

            <SectionCard
              title="Liquidity data readiness"
              subtitle="Data gaps are shown explicitly so unavailable controls are never mistaken for compliant ones."
            >
              <div className="space-y-3">
                {readiness.map((row) => (
                  <div key={row.module} className="flex items-start justify-between gap-4 border-b border-border-light pb-3 last:border-0 last:pb-0">
                    <div>
                      <p className="text-body font-medium text-navy">{row.module.replaceAll('_', ' ')}</p>
                      {row.reasons.map((reason) => (
                        <p key={reason} className="mt-1 text-caption text-slate">{reason}</p>
                      ))}
                    </div>
                    <StatusPill tone={row.status === 'ready' ? 'success' : row.status === 'partial' ? 'amber' : 'critical'}>
                      {row.status}
                    </StatusPill>
                  </div>
                ))}
              </div>
            </SectionCard>
          </>
        ) : null}
      </QueryBoundary>
    </div>
  );
}