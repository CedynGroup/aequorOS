'use client';

import { useState } from 'react';
import { Droplets, ShieldCheck } from 'lucide-react';
import type { BehavioralLiquiditySegmentRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SdiModuleContext from '@/components/sdi/SdiModuleContext';
import { useBankContext } from '@/components/shell/BankContext';
import { useBehavioralLiquidity } from '@/lib/api/hooks';
import { fmtCurrency, fmtPct } from '@/lib/format';

const DIMENSIONS = [
  { key: 'product', label: 'Product' },
  { key: 'customer_segment', label: 'Customer segment' },
  { key: 'concentration_group', label: 'Concentration group' },
  { key: 'branch', label: 'Branch' },
] as const;

function pct(value: number | null | undefined): string {
  return value == null ? '-' : fmtPct(Number(value), 1);
}

const columns: Column<BehavioralLiquiditySegmentRead>[] = [
  { key: 'segment', header: 'Segment', render: (row) => row.segment },
  { key: 'balance', header: 'Current deposits', numeric: true, render: (row) => fmtCurrency(row.currentBalanceGhs) },
  { key: 'runoff', header: 'Worst observed runoff', numeric: true, render: (row) => pct(row.observedMonthlyRunoffPct) },
  { key: 'withdrawal', header: 'Latest withdrawal', numeric: true, render: (row) => pct(row.latestWithdrawalPct) },
  { key: 'attrition', header: 'Position attrition', numeric: true, render: (row) => pct(row.positionAttritionPct) },
  { key: 'seasonality', header: 'Seasonal deviation', numeric: true, render: (row) => pct(row.seasonalDeviationPct) },
  { key: 'beta', header: 'Deposit beta', numeric: true, render: (row) => row.depositBeta == null ? '-' : Number(row.depositBeta).toFixed(2) },
  { key: 'lag', header: 'Repricing lag', numeric: true, render: (row) => row.repricingLagMonths == null ? '-' : `${row.repricingLagMonths} mo` },
  {
    key: 'status',
    header: 'Evidence',
    render: (row) => <StatusPill tone={row.dataStatus === 'ready' ? 'success' : row.dataStatus === 'partial' ? 'amber' : 'pending'}>{row.dataStatus.replace('_', ' ')}</StatusPill>,
  },
];

export default function BehavioralLiquidityPage() {
  const { bank } = useBankContext();
  const [dimension, setDimension] = useState<(typeof DIMENSIONS)[number]['key']>('product');
  const query = useBehavioralLiquidity(bank?.id);
  const report = query.data;
  const rows = report?.segments.filter((segment) => segment.dimension === dimension) ?? [];
  const worstRunoff = rows.reduce<number | null>((worst, row) => row.observedMonthlyRunoffPct == null ? worst : worst === null || row.observedMonthlyRunoffPct > worst ? row.observedMonthlyRunoffPct : worst, null);
  const pricingReady = rows.filter((row) => row.depositBeta != null && row.repricingLagMonths != null).length;

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Modules', href: '/' }, { label: 'Behavioral Models', href: '/behavioral' }, { label: 'Liquidity Behavior' }]}
        title="Behavioral Liquidity"
        subtitle="Observed deposit runoff, withdrawal, attrition, seasonality, and pricing response from canonical history"
      />
      <SdiModuleContext title="SDI liquidity behavior">
        These are observed deposit behaviors, not a supervisory liquidity-stress result. CFP overlays require an approved scenario linked to a documented action.
      </SdiModuleContext>
      <QueryBoundary isLoading={query.isLoading} error={query.error} onRetry={() => query.refetch()}>
        {report && (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              <KpiStat label="Source as of" value={report.asOfDate === 'unavailable' ? '-' : report.asOfDate} hint="Latest canonical deposit snapshot" />
              <KpiStat label="Worst observed runoff" value={pct(worstRunoff)} status={worstRunoff !== null && worstRunoff > 10 ? 'warn' : 'ok'} hint="Largest observed month-on-month balance decline" />
              <KpiStat label="Pricing-ready segments" value={`${pricingReady} / ${rows.length}`} status={pricingReady === rows.length && rows.length > 0 ? 'ok' : 'warn'} hint="Deposit beta and repricing lag both evidenced" />
              <KpiStat label="Approved CFP overlays" value={String(report.scenarios.length)} status={report.scenarios.length > 0 ? 'ok' : undefined} hint="Each linked to a CFP action" />
            </div>

            <SectionCard title="Observed deposit behavior" subtitle="Switch dimensions to compare behavior across the same canonical history.">
              <div className="flex flex-wrap gap-2">
                {DIMENSIONS.map((entry) => (
                  <button key={entry.key} type="button" onClick={() => setDimension(entry.key)} className={`px-3 py-1.5 text-caption font-medium rounded-md border ${dimension === entry.key ? 'bg-action-light text-action border-action/30' : 'border-border text-slate hover:text-navy hover:bg-surface'}`}>
                    {entry.label}
                  </button>
                ))}
              </div>
            </SectionCard>

            {rows.length === 0 ? (
              <EmptyState Icon={Droplets} title="No deposit behavior evidence for this dimension" description="Ingest dated deposit snapshots with the relevant product, counterparty, group, or branch identifiers to calculate these metrics." />
            ) : (
              <SectionCard title={`${DIMENSIONS.find((entry) => entry.key === dimension)?.label} behavior detail`} subtitle="Position attrition is a source-reference count proxy; it is not asserted to be customer churn." noPadding>
                <DataTable columns={columns} rows={rows} density="compact" maxHeight={520} stickyHeader />
              </SectionCard>
            )}

            <SectionCard title="Evidence notes" subtitle="Missing inputs are retained as named limits, never filled with a generic model value.">
              <div className="space-y-3">
                {rows.filter((row) => row.reasons.length > 0).map((row) => (
                  <div key={`${row.dimension}-${row.segment}`} className="border-b border-border-light pb-3 last:border-0 last:pb-0">
                    <p className="text-body font-medium text-navy">{row.segment}</p>
                    {row.reasons.map((reason) => <p key={reason} className="mt-1 text-caption text-slate">{reason}</p>)}
                  </div>
                ))}
                {!rows.some((row) => row.reasons.length > 0) && <p className="text-body text-success">All displayed metrics meet their evidence requirements.</p>}
              </div>
            </SectionCard>

            <SectionCard title="CFP-linked behavioral overlays" subtitle="Approved planning overlays are not applied to regulatory liquidity metrics until a governing SDI methodology is adopted." noPadding>
              {report.scenarios.length > 0 ? (
                <DataTable columns={[
                  { key: 'name', header: 'Scenario', render: (row) => row.name },
                  { key: 'horizon', header: 'Activation horizon', render: (row) => row.activationHorizon.replaceAll('_', ' ') },
                  { key: 'runoff', header: 'Runoff uplift', numeric: true, render: (row) => `${Number(row.depositRunoffUpliftPct).toFixed(1)}%` },
                  { key: 'cost', header: 'Funding-cost uplift', numeric: true, render: (row) => `${Number(row.fundingCostUpliftBps).toFixed(0)} bps` },
                  { key: 'action', header: 'Linked action', render: (row) => row.linkedAction },
                ]} rows={report.scenarios} density="compact" />
              ) : <EmptyState Icon={ShieldCheck} title="No approved CFP behavioral overlays" description="Add a behavioral scenario to the audited CFP draft and link it to one of the plan's documented actions." />}
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}