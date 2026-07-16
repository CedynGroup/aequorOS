'use client';

import Link from 'next/link';
import { FileText, Info, Loader2, PlayCircle } from 'lucide-react';
import type { RegulatoryRunRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import RatioGauge from '@/components/ui/RatioGauge';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import DonutChart from '@/components/charts/DonutChart';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCapitalDashboard,
  useCreateRegulatoryRun,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate, num, statusTone } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';

const RWA_COLORS = {
  credit: '#0A2540',
  operational: '#1F6CE0',
  market: '#2D7FF9',
} as const;

function metricThreshold(
  run: RegulatoryRunRead | undefined,
  metricCode: string
): number | null {
  const result = run?.metricResults.find((m) => m.metricCode === metricCode);
  return result?.thresholdMin === null || result?.thresholdMin === undefined
    ? null
    : num(result.thresholdMin);
}

export default function BaselDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useCapitalDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = dashboard.data;
  const carMin = num(data?.buffers.carMinPct ?? '10');
  const carEarlyWarning = num(data?.buffers.carEarlyWarningPct ?? '10.5');
  const tier1Min = metricThreshold(latestRun.data, 'tier1_ratio_pct');
  const cet1Min = metricThreshold(latestRun.data, 'cet1_ratio_pct');
  const leverageMin = metricThreshold(latestRun.data, 'leverage_ratio_pct');

  const totalRwa = num(data?.metrics.totalRwaGhs);
  const rwaSlices = data
    ? [
        {
          name: 'Credit risk',
          value: num(data.rwaComposition.creditRwaGhs),
          color: RWA_COLORS.credit,
        },
        {
          name: 'Operational risk',
          value: num(data.rwaComposition.operationalRwaGhs),
          color: RWA_COLORS.operational,
        },
        {
          name: 'Market risk',
          value: num(data.rwaComposition.marketRwaGhs),
          color: RWA_COLORS.market,
        },
      ]
    : [];

  const trendPoints = (data?.trend ?? []).map((p) => ({
    month: p.label,
    value: num(p.carPct),
    stored: p.stored,
  }));
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);
  const compliantCount = trendPoints.filter((p) => p.value >= carMin).length;

  const structure = data?.capitalStructure;
  const structureCells = structure
    ? [
        { label: 'CET1 capital', value: num(structure.cet1CapitalGhs) },
        { label: 'AT1 capital', value: num(structure.at1CapitalGhs) },
        { label: 'Tier 1 capital', value: num(structure.tier1CapitalGhs) },
        { label: 'Tier 2 capital', value: num(structure.tier2CapitalGhs) },
        { label: 'Total capital', value: num(structure.totalCapitalGhs) },
      ]
    : [];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital' },
          { label: 'Capital Dashboard' },
        ]}
        title="Basel Capital"
        subtitle="Capital Adequacy Ratio · Tier 1 / Tier 2 · BoG CRD framework"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="capital"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            {latestRun.data && <RunBadge run={latestRun.data} />}
            <Link
              href="/basel/submissions"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileText size={13} aria-hidden />
              Generate BoG return
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
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
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

            {/* Top row: CAR gauge + Tier 1 / CET1 / Leverage KPIs */}
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
              <div className="lg:col-span-2">
                <RatioGauge
                  label="Capital Adequacy Ratio"
                  value={num(data.metrics.carPct)}
                  threshold={carMin}
                  internalBuffer={carEarlyWarning}
                  bufferLabel="Early warning"
                  status={statusTone(data.metrics.carStatus)}
                  decimals={2}
                />
              </div>
              <KPICard
                label="Tier 1 ratio"
                value={num(data.metrics.tier1RatioPct)}
                suffix="%"
                decimals={2}
                status={statusTone(data.metrics.tier1Status)}
                footer={
                  tier1Min !== null
                    ? `Regulatory minimum ${tier1Min.toFixed(1)}%`
                    : 'CET1 + AT1 / RWA'
                }
              />
              <KPICard
                label="CET1 ratio"
                value={num(data.metrics.cet1RatioPct)}
                suffix="%"
                decimals={2}
                status={statusTone(data.metrics.cet1Status)}
                footer={
                  cet1Min !== null
                    ? `Regulatory minimum ${cet1Min.toFixed(1)}%`
                    : 'Common equity Tier 1 / RWA'
                }
              />
              <KPICard
                label="Leverage ratio"
                value={num(data.metrics.leverageRatioPct)}
                suffix="%"
                decimals={2}
                status={statusTone(data.metrics.leverageStatus)}
                footer={
                  leverageMin !== null
                    ? `Regulatory minimum ${leverageMin.toFixed(1)}%`
                    : 'Tier 1 / total exposures'
                }
              />
            </div>

            {/* 12-period trend + RWA composition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <Card className="lg:col-span-2">
                <CardHeader
                  title="CAR — 12-period trend"
                  subtitle="Reporting-period CAR across the trailing year"
                  action={
                    <StatusPill tone="success">
                      Compliant {compliantCount} of {trendPoints.length}
                    </StatusPill>
                  }
                />
                <CardBody>
                  <RatioHistoryChart
                    data={trendPoints}
                    threshold={carMin}
                    internalBuffer={carEarlyWarning}
                    yMin={Math.floor(carMin - 2)}
                    yMax={Math.ceil(
                      Math.max(...trendPoints.map((p) => p.value), carMin) + 2
                    )}
                    color="#0E8A4F"
                    label="CAR"
                  />
                  {hasInlineTrendPoints && (
                    <p className="mt-2 text-caption text-slate">
                      Hollow points are computed inline — run baseline on those
                      periods to persist them.
                    </p>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="RWA composition"
                  subtitle={`Total ${fmtCurrency(totalRwa, 'GHS')}`}
                />
                <CardBody className="space-y-4">
                  <DonutChart
                    data={rwaSlices}
                    centerLabel="Total RWA"
                    centerValue={fmtCurrency(totalRwa, 'GHS')}
                    format="ghs-m"
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
                        <span className="font-mono text-navy tabular-nums">
                          {fmtCurrency(s.value, 'GHS')}
                        </span>
                        <span className="font-mono text-slate w-12 text-right tabular-nums">
                          {totalRwa > 0
                            ? `${((s.value / totalRwa) * 100).toFixed(1)}%`
                            : '—'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            </div>

            {/* Regulatory buffers */}
            <Card>
              <CardHeader
                title="Regulatory buffer status"
                subtitle="BoG CRD thresholds for the Capital Adequacy Ratio"
              />
              <CardBody className="grid grid-cols-2 md:grid-cols-5 gap-5">
                <BufferCell
                  label="BoG minimum CAR"
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
                  value={num(data.buffers.carCriticalPct)}
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
                  note="Above the BoG minimum"
                  emphasis={statusTone(data.metrics.carStatus)}
                />
              </CardBody>
            </Card>

            {/* Capital structure summary strip */}
            <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-5 gap-6">
              {structureCells.map((cell) => (
                <div key={cell.label}>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    {cell.label}
                  </p>
                  <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
                    {fmtCurrency(cell.value, 'GHS')}
                  </p>
                </div>
              ))}
            </div>

            {/* Validations */}
            <Card>
              <CardHeader
                title="Validations"
                subtitle="Regulatory rule evaluation for this period"
              />
              <CardBody className="p-0">
                <ValidationList validations={data.validations} />
              </CardBody>
            </Card>
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
      <p className={`font-mono text-h1 tabular-nums ${valueColor}`}>
        {value.toFixed(2)}
        {suffix}
      </p>
      {note && <p className="text-caption text-slate leading-snug">{note}</p>}
    </div>
  );
}
