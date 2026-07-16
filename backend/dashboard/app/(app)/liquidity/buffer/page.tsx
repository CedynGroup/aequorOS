'use client';

import { ShieldCheck } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import HQLAStackChart from '@/components/charts/HQLAStackChart';
import { runComputedAt } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import { useLiquidityDashboard, useRegulatoryRun } from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { CHART_SERIES, seriesColor } from '@/lib/chartTheme';
import { fmtCurrency, fmtPct } from '@/lib/format';

type BufferRow = {
  code: string;
  instrument: string;
  marketValueGHS: number | null;
  haircutPct: number | null;
  weightedGHS: number;
  sharePct: number | null;
  isTotal?: boolean;
};

const columns: Column<BufferRow>[] = [
  {
    key: 'instrument',
    header: 'Instrument',
    width: '38%',
    render: (r) => r.instrument,
  },
  {
    key: 'mv',
    header: 'Market value (GHS)',
    numeric: true,
    render: (r) =>
      r.marketValueGHS === null ? '—' : fmtCurrency(r.marketValueGHS, 'GHS'),
  },
  {
    key: 'haircut',
    header: 'Haircut',
    numeric: true,
    render: (r) =>
      r.haircutPct === null ? '—' : `${r.haircutPct.toFixed(1)}%`,
  },
  {
    key: 'weighted',
    header: 'Post-haircut value',
    numeric: true,
    render: (r) => fmtCurrency(r.weightedGHS, 'GHS'),
  },
  {
    key: 'share',
    header: 'Share',
    numeric: true,
    render: (r) => (r.sharePct === null ? '—' : `${r.sharePct.toFixed(1)}%`),
  },
];

export default function LiquidityBuffer() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;
  const run = latestRun.data;
  const hqlaTotal = num(data?.metrics.hqlaTotalGhs);
  const netOutflows = num(data?.metrics.netOutflows30dGhs);

  const rows: BufferRow[] = (data?.hqlaComposition ?? []).map((line) => {
    const exposure =
      line.exposureAmount === null ? null : num(line.exposureAmount);
    const weighted = num(line.weightedAmount);
    return {
      code: line.lineCode,
      instrument: line.description,
      marketValueGHS: exposure,
      haircutPct:
        exposure !== null && exposure > 0
          ? (1 - weighted / exposure) * 100
          : null,
      weightedGHS: weighted,
      sharePct: hqlaTotal > 0 ? (weighted / hqlaTotal) * 100 : null,
    };
  });

  const stackData = rows.map((r, i) => ({
    level: r.instrument,
    shareGHS: r.weightedGHS,
    pct: r.sharePct === null ? 0 : Math.round(r.sharePct),
    color: seriesColor(i),
  }));

  const allLevel1 = data?.validations.find(
    (v) => v.ruleCode === 'hqla_all_level1'
  );
  const largest = rows.reduce<BufferRow | null>(
    (best, r) => (best === null || r.weightedGHS > best.weightedGHS ? r : best),
    null
  );

  const computedAt = runComputedAt(run);
  const provenance = data ? (
    <span>
      {data.stored
        ? 'Stored baseline run'
        : 'Live computation — run baseline to persist'}
    </span>
  ) : undefined;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Buffer' },
        ]}
        title="Liquidity Buffer"
        subtitle="High quality liquid asset composition · Basel III LCR numerator"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={run ? <RunBadge run={run} /> : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
            {/* Buffer KPIs */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <KpiStat
                label="HQLA stock"
                value={fmtCurrency(hqlaTotal, 'GHS')}
                hint="Post-haircut weighted"
              />
              <KpiStat
                label="Coverage of net outflows"
                value={netOutflows > 0 ? fmtPct((hqlaTotal / netOutflows) * 100, 1) : '—'}
                hint="= LCR for the 30-day horizon"
              />
              <KpiStat
                label="Asset classes held"
                value={rows.length}
                hint={largest ? `Largest: ${largest.instrument}` : undefined}
              />
              <div className="card px-4 py-3.5 flex flex-col gap-1.5 min-w-0">
                <p className="text-micro font-medium text-slate uppercase tracking-wider truncate">
                  Buffer quality
                </p>
                <div className="flex items-center gap-2">
                  <ShieldCheck
                    size={18}
                    className={allLevel1?.passed ? 'text-success' : 'text-warning'}
                    aria-hidden
                  />
                  <StatusPill tone={allLevel1?.passed ? 'success' : 'amber'}>
                    {allLevel1?.passed ? 'All Level 1' : 'Includes < Level 1'}
                  </StatusPill>
                </div>
                {allLevel1 && (
                  <p className="text-caption text-slate leading-snug">
                    {allLevel1.message}
                  </p>
                )}
              </div>
            </div>

            {/* Composition stack + legend */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChartFrame
                className="lg:col-span-2"
                title="HQLA composition"
                subtitle="Post-haircut weighted values by instrument"
                height={Math.max(200, stackData.length * 44 + 40)}
                footer={provenance}
              >
                <HQLAStackChart
                  data={stackData}
                  height={Math.max(200, stackData.length * 44 + 40)}
                />
              </ChartFrame>

              <SectionCard
                title="Share of buffer"
                subtitle={`Total ${fmtCurrency(hqlaTotal, 'GHS')}`}
              >
                <ul className="space-y-2.5 text-caption">
                  {stackData.map((h) => (
                    <li key={h.level} className="flex items-center gap-3">
                      <span
                        className="w-2 h-2 rounded-sm shrink-0"
                        style={{ background: h.color }}
                        aria-hidden
                      />
                      <span className="text-navy flex-1 truncate font-medium">
                        {h.level}
                      </span>
                      <span className="font-mono text-navy tnum shrink-0">
                        {fmtCurrency(h.shareGHS, 'GHS')}
                      </span>
                      <span className="font-mono text-slate tnum w-10 text-right shrink-0">
                        {h.pct}%
                      </span>
                    </li>
                  ))}
                  {stackData.length > CHART_SERIES.length && (
                    <li className="text-slate">
                      Palette cycles beyond {CHART_SERIES.length} instruments.
                    </li>
                  )}
                </ul>
              </SectionCard>
            </div>

            {/* Haircut detail table */}
            <SectionCard
              title="Buffer detail"
              subtitle="Market value vs post-haircut LCR value per instrument"
              noPadding
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={
                <span>
                  Baseline runs carry BoG-eligible instruments at face value;
                  market-value haircuts are applied by the stress engine
                  (hqla_securities_haircut) on the Stress tab.
                </span>
              }
            >
              <DataTable
                columns={columns}
                rows={[
                  ...rows,
                  {
                    code: 'TOTAL',
                    instrument: 'TOTAL HQLA',
                    marketValueGHS: rows.reduce(
                      (s, r) => s + (r.marketValueGHS ?? 0),
                      0
                    ),
                    haircutPct: null,
                    weightedGHS: hqlaTotal,
                    sharePct: rows.length ? 100 : null,
                    isTotal: true,
                  },
                ]}
                totalsRowMatcher={(r) => Boolean(r.isTotal)}
              />
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
