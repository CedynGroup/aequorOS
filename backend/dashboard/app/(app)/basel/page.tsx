'use client';

import Link from 'next/link';
import { FileText, Info, Loader2, PlayCircle } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat, { type KpiStatus } from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import Sparkline from '@/components/ui/Sparkline';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DonutChart from '@/components/charts/DonutChart';
import RatioTrendChart from '@/components/liquidity/charts/RatioTrendChart';
import CapitalWaterfallChart from '@/components/basel/charts/CapitalWaterfallChart';
import { runComputedAt, runMetricThreshold } from '@/components/liquidity/runData';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCapitalDashboard,
  useCreateRegulatoryRun,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate, num, statusTone } from '@/lib/api/values';
import { seriesColor } from '@/lib/chartTheme';
import { fmtCurrency, fmtPct, regShort } from '@/lib/format';

function kpiStatus(status: 'green' | 'amber' | 'red' | string): KpiStatus {
  return status === 'red' ? 'crit' : status === 'amber' ? 'warn' : 'ok';
}

export default function BaselOverview() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useCapitalDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = dashboard.data;
  const run = latestRun.data;
  const carMin = num(data?.buffers.carMinPct ?? '10');
  const carEarlyWarning = num(data?.buffers.carEarlyWarningPct ?? '10.5');
  const carCritical = num(data?.buffers.carCriticalPct ?? '9');
  const tier1Min = runMetricThreshold(run, 'tier1_ratio_pct');
  const cet1Min = runMetricThreshold(run, 'cet1_ratio_pct');
  const leverageMin = runMetricThreshold(run, 'leverage_ratio_pct');

  const totalRwa = num(data?.metrics.totalRwaGhs);
  const rwaSlices = data
    ? [
        {
          name: 'Credit risk',
          value: num(data.rwaComposition.creditRwaGhs),
          color: seriesColor(0),
        },
        {
          name: 'Operational risk',
          value: num(data.rwaComposition.operationalRwaGhs),
          color: seriesColor(1),
        },
        {
          name: 'Market risk',
          value: num(data.rwaComposition.marketRwaGhs),
          color: seriesColor(2),
        },
      ]
    : [];

  const carTrend = (data?.trend ?? []).map((p) => num(p.carPct));
  const carDelta =
    carTrend.length >= 2
      ? carTrend[carTrend.length - 1] - carTrend[carTrend.length - 2]
      : undefined;
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);
  const compliantCount = carTrend.filter((v) => v >= carMin).length;

  const structure = data?.capitalStructure;
  const cet1Gross = structure
    ? structure.cet1Components.reduce((s, c) => s + num(c.weightedAmount), 0)
    : 0;
  const deductions = structure
    ? structure.cet1Deductions.reduce(
        (s, c) => s + Math.abs(num(c.weightedAmount)),
        0
      )
    : 0;

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
          { label: 'Basel Capital' },
          { label: 'Overview' },
        ]}
        title="Basel Capital"
        subtitle={`Capital Adequacy Ratio · Tier 1 / Tier 2 · ${regShort()} CRD framework`}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="capital"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            {run && <RunBadge run={run} />}
            <Link
              href="/submissions/returns?code=BSD2"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileText size={13} aria-hidden />
              Official returns →
            </Link>
            <button
              type="button"
              disabled={runBaseline.isPending || !periodId}
              onClick={() =>
                periodId &&
                runBaseline.mutate({
                  module: 'capital',
                  reportingPeriodId: periodId,
                  scenarioCode: 'baseline',
                })
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {runBaseline.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <PlayCircle size={13} aria-hidden />
              )}
              Run baseline
            </button>
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run baseline to
                  persist an auditable regulatory run.
                </p>
              </div>
            )}

            {/* Headline ratios */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiStat
                label="Capital Adequacy Ratio"
                value={num(data.metrics.carPct).toFixed(2)}
                unit="%"
                status={kpiStatus(data.metrics.carStatus)}
                delta={carDelta}
                sparkline={<Sparkline data={carTrend} />}
                hint={`${regShort()} minimum ${carMin.toFixed(1)}%`}
              />
              <KpiStat
                label="Tier 1 ratio"
                value={num(data.metrics.tier1RatioPct).toFixed(2)}
                unit="%"
                status={kpiStatus(data.metrics.tier1Status)}
                hint={
                  tier1Min !== null
                    ? `Regulatory minimum ${tier1Min.toFixed(1)}%`
                    : 'CET1 + AT1 / RWA'
                }
              />
              <KpiStat
                label="CET1 ratio"
                value={num(data.metrics.cet1RatioPct).toFixed(2)}
                unit="%"
                status={kpiStatus(data.metrics.cet1Status)}
                hint={
                  cet1Min !== null
                    ? `Regulatory minimum ${cet1Min.toFixed(1)}%`
                    : 'Common equity Tier 1 / RWA'
                }
              />
              <KpiStat
                label="Leverage ratio"
                value={num(data.metrics.leverageRatioPct).toFixed(2)}
                unit="%"
                status={kpiStatus(data.metrics.leverageStatus)}
                hint={
                  leverageMin !== null
                    ? `Regulatory minimum ${leverageMin.toFixed(1)}%`
                    : 'Tier 1 / total exposures'
                }
              />
            </div>

            {/* Regulatory floors — CAR & companions are floor limits */}
            <SectionCard
              title="Regulatory floors"
              subtitle={`${regShort()} CRD minimums — compliant while each ratio stays above its floor`}
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={provenance}
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
                <LimitBar
                  label="CAR"
                  value={num(data.metrics.carPct)}
                  limit={carMin}
                  warnAt={carEarlyWarning}
                  direction="above"
                  unit="%"
                  limitLabel={`${regShort()} minimum`}
                  warnLabel={data.buffers.carEarlyWarningLabel || 'Early warning'}
                  format={(v) => v.toFixed(1)}
                />
                <LimitBar
                  label="Tier 1 ratio"
                  value={num(data.metrics.tier1RatioPct)}
                  limit={tier1Min ?? 8}
                  warnAt={tier1Min ?? 8}
                  direction="above"
                  unit="%"
                  limitLabel={
                    tier1Min !== null ? 'Regulatory minimum' : 'Assumed minimum'
                  }
                  format={(v) => v.toFixed(1)}
                />
                <LimitBar
                  label="CET1 ratio"
                  value={num(data.metrics.cet1RatioPct)}
                  limit={cet1Min ?? 6.5}
                  warnAt={cet1Min ?? 6.5}
                  direction="above"
                  unit="%"
                  limitLabel={
                    cet1Min !== null ? 'Regulatory minimum' : 'Assumed minimum'
                  }
                  format={(v) => v.toFixed(1)}
                />
                <LimitBar
                  label="Leverage ratio"
                  value={num(data.metrics.leverageRatioPct)}
                  limit={leverageMin ?? 6}
                  warnAt={leverageMin ?? 6}
                  direction="above"
                  unit="%"
                  limitLabel={
                    leverageMin !== null
                      ? 'Regulatory minimum'
                      : 'Assumed minimum'
                  }
                  format={(v) => v.toFixed(1)}
                />
              </div>
            </SectionCard>

            {/* Trend + RWA composition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChartFrame
                className="lg:col-span-2"
                title="CAR — reporting-period trend"
                subtitle={`CAR and Tier 1 across ${carTrend.length} reporting periods`}
                height={260}
                actions={
                  <StatusPill tone="success">
                    Compliant {compliantCount} of {carTrend.length}
                  </StatusPill>
                }
                footer={
                  hasInlineTrendPoints ? (
                    <span>
                      Hollow points are computed inline — run baseline on those
                      periods to persist them.
                    </span>
                  ) : (
                    <span>All trend points are stored baseline runs.</span>
                  )
                }
              >
                <RatioTrendChart
                  data={(data.trend ?? []).map((p) => ({
                    label: p.label,
                    primary: num(p.carPct),
                    secondary: num(p.tier1RatioPct),
                    stored: p.stored,
                  }))}
                  threshold={carMin}
                  thresholdLabel={`${regShort()} min`}
                  redFloor={carEarlyWarning}
                  redFloorLabel="Early warning"
                  primaryLabel="CAR"
                  secondaryLabel="Tier 1"
                  yMin={Math.floor(Math.min(carCritical, ...carTrend) - 2)}
                  height={260}
                />
              </ChartFrame>

              <SectionCard
                title="RWA composition"
                subtitle={`Total ${fmtCurrency(totalRwa)}`}
              >
                <div className="space-y-4">
                  <DonutChart
                    data={rwaSlices}
                    centerLabel="Total RWA"
                    centerValue={fmtCurrency(totalRwa)}
                    format="ccy-m"
                  />
                  <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                    {rwaSlices.map((s) => (
                      <li key={s.name} className="flex items-center gap-3">
                        <span
                          className="w-2 h-2 rounded-sm shrink-0"
                          style={{ background: s.color }}
                          aria-hidden
                        />
                        <span className="text-navy/85 flex-1">{s.name}</span>
                        <span className="font-mono text-navy tnum">
                          {fmtCurrency(s.value)}
                        </span>
                        <span className="font-mono text-slate w-12 text-right tnum">
                          {totalRwa > 0
                            ? `${((s.value / totalRwa) * 100).toFixed(1)}%`
                            : '—'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </SectionCard>
            </div>

            {/* Capital waterfall */}
            {structure && (
              <ChartFrame
                title="Capital waterfall"
                subtitle="CET1 components → deductions → AT1 → Tier 2 → total qualifying capital"
                height={280}
                footer={
                  <span>
                    CET1 {fmtCurrency(num(structure.cet1CapitalGhs))} ·
                    Tier 1 {fmtCurrency(num(structure.tier1CapitalGhs))} ·
                    Total {fmtCurrency(num(structure.totalCapitalGhs))} ·{' '}
                    {fmtPct(num(data.metrics.carPct), 2)} of RWA
                  </span>
                }
              >
                <CapitalWaterfallChart
                  cet1Gross={cet1Gross}
                  deductions={deductions}
                  at1={num(structure.at1CapitalGhs)}
                  tier2={num(structure.tier2CapitalGhs)}
                  total={num(structure.totalCapitalGhs)}
                  height={280}
                />
              </ChartFrame>
            )}

            {/* Regulatory buffers */}
            <SectionCard
              title="Regulatory buffer status"
              subtitle={`${regShort()} CRD thresholds for the Capital Adequacy Ratio`}
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={provenance}
            >
              <div className="grid grid-cols-2 md:grid-cols-5 gap-5">
                <BufferCell
                  label={`${regShort()} minimum CAR`}
                  value={carMin}
                  note="Hard regulatory floor"
                />
                <BufferCell
                  label="Early warning"
                  value={carEarlyWarning}
                  note={data.buffers.carEarlyWarningLabel}
                />
                <BufferCell
                  label="Critical floor"
                  value={carCritical}
                  note="Supervisory intervention level"
                />
                <BufferCell
                  label="Current CAR"
                  value={num(data.buffers.currentCarPct)}
                  note="As of this reporting period"
                  emphasis={statusTone(data.metrics.carStatus)}
                />
                <BufferCell
                  label="Headroom"
                  value={num(data.buffers.headroomPp)}
                  suffix=" pp"
                  note={`Above the ${regShort()} minimum`}
                  emphasis={statusTone(data.metrics.carStatus)}
                />
              </div>
            </SectionCard>

            {/* Validations */}
            <SectionCard
              title="Validations"
              subtitle="Regulatory rule evaluation for this period"
              noPadding
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={provenance}
            >
              <ValidationList validations={data.validations} />
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

function BufferCell({
  label,
  value,
  suffix = '%',
  note,
  emphasis,
}: {
  label: string;
  value: number;
  suffix?: string;
  note?: string;
  emphasis?: string;
}) {
  const valueColor =
    emphasis === 'breach' || emphasis === 'critical'
      ? 'text-critical'
      : emphasis === 'approaching' || emphasis === 'amber'
      ? 'text-warning'
      : emphasis
      ? 'text-success'
      : 'text-navy';
  return (
    <div className="space-y-1">
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className={`font-mono text-h1 tnum ${valueColor}`}>
        {value.toFixed(2)}
        {suffix}
      </p>
      {note && <p className="text-caption text-slate leading-snug">{note}</p>}
    </div>
  );
}
