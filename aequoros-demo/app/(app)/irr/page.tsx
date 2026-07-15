'use client';

import { Info, Loader2, Zap } from 'lucide-react';
import type { IrrEveScenarioRead, IrrGapBucketRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import SignedBarChart, { type SignedPoint } from '@/components/charts/SignedBarChart';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useIrrDashboard,
  useRegulatoryRun,
  useRunAllIrrScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

const SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  parallel_up_200: 'Parallel +200bp',
  parallel_down_200: 'Parallel −200bp',
  short_up_250: 'Short +250bp',
  short_down_250: 'Short −250bp',
  steepener: 'Steepener',
  flattener: 'Flattener',
};

function scenarioLabel(code: string): string {
  return SCENARIO_LABELS[code] ?? code;
}

export default function IrrDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useIrrDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runAll = useRunAllIrrScenarios(bankId);

  const data = dashboard.data;
  const m = data?.metrics;

  const eveLimit = num(m?.eveLimitPct ?? '15');
  const worstPct = num(m?.worstEveChangePctTier1);

  const gapRows = data?.gapTable ?? [];
  const eveRows = data?.eveScenarios ?? [];

  const gapBars: SignedPoint[] = gapRows.map((g) => ({
    label: g.bucket,
    value: num(g.gapGhs),
  }));
  const eveBars: SignedPoint[] = eveRows.map((e) => ({
    label: scenarioLabel(e.scenarioCode),
    value: num(e.deltaEveGhs),
  }));

  const trendPoints = (data?.trend ?? []).map((p) => ({
    month: p.label,
    value: num(p.worstEveChangePctTier1),
    stored: p.stored,
  }));
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);

  const runAllButton = (
    <button
      type="button"
      disabled={runAll.isPending || !periodId}
      onClick={() => periodId && runAll.mutate({ reportingPeriodId: periodId })}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
    >
      {runAll.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <Zap size={13} aria-hidden />
      )}
      Run all scenarios
    </button>
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk' },
          { label: 'IRRBB Dashboard' },
        ]}
        title="Interest Rate Risk"
        subtitle="Banking book IRRBB · Repricing gap · EVE & EaR sensitivity · BoG CRD"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            {latestRun.data && <RunBadge run={latestRun.data} />}
            {runAllButton}
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && m && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run all
                  scenarios to persist auditable regulatory runs for the six
                  Basel IRRBB shocks.
                </p>
              </div>
            )}

            {/* Gauges */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <RatioGauge
                label="Worst ΔEVE / Tier 1"
                value={worstPct}
                threshold={eveLimit}
                status={statusTone(m.eveStatus)}
                decimals={2}
                thresholdLabel="Supervisory limit"
                higherIsBetter={false}
              />
              <StatCard
                label="Duration gap"
                value={`${num(m.durationGap).toFixed(2)} yrs`}
                note={`Assets ${num(m.assetDuration).toFixed(2)}y · Liabilities ${num(
                  m.liabilityDuration
                ).toFixed(2)}y`}
              />
              <StatCard
                label="12-month cumulative gap"
                value={fmtCurrency(num(m.cumulative12mGapGhs), 'GHS')}
                note={
                  num(m.cumulative12mGapGhs) < 0
                    ? 'Liability-sensitive over 12 months'
                    : 'Asset-sensitive over 12 months'
                }
                tone={num(m.cumulative12mGapGhs) < 0 ? 'amber' : undefined}
              />
            </div>

            {/* Repricing gap */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card>
                <CardHeader
                  title="Repricing gap by tenor bucket"
                  subtitle="Rate-sensitive assets less liabilities per BoG CRD bucket"
                />
                <CardBody className="p-0">
                  <GapTable rows={gapRows} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader
                  title="Period gap by bucket"
                  subtitle="Negative buckets are liability-sensitive"
                />
                <CardBody>
                  <SignedBarChart
                    data={gapBars}
                    layout="vertical"
                    height={320}
                    positiveColor="#0A2540"
                    negativeColor="#C97C00"
                    format="ghs-m"
                    valueLabel="Period gap"
                    categoryWidth={72}
                  />
                </CardBody>
              </Card>
            </div>

            {/* EVE by scenario */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card>
                <CardHeader
                  title="Economic Value of Equity by scenario"
                  subtitle={`Base EVE ${fmtCurrency(num(m.eveBaseGhs), 'GHS')} · Tier 1 ${fmtCurrency(
                    num(m.tier1Ghs),
                    'GHS'
                  )}`}
                />
                <CardBody className="p-0">
                  <EveTable rows={eveRows} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader
                  title="ΔEVE by scenario"
                  subtitle="Change in economic value under the six Basel IRRBB shocks"
                />
                <CardBody>
                  <SignedBarChart
                    data={eveBars}
                    layout="vertical"
                    height={320}
                    positiveColor="#0E8A4F"
                    negativeColor="#B3261E"
                    format="ghs-m"
                    valueLabel="ΔEVE"
                    categoryWidth={110}
                  />
                </CardBody>
              </Card>
            </div>

            {/* Earnings at Risk */}
            <Card>
              <CardHeader
                title="Earnings at Risk (EaR)"
                subtitle={`Twelve-month ΔNII under ±200bp parallel shocks · Base NII ${fmtCurrency(
                  num(m.niiBaseGhs),
                  'GHS'
                )}`}
              />
              <CardBody className="grid grid-cols-2 gap-6">
                <StatCard
                  label="ΔNII — rates +200bp"
                  value={fmtCurrencySigned(num(m.earUp200Ghs), 'GHS')}
                  note="Net interest income sensitivity, upward shock"
                  tone={num(m.earUp200Ghs) < 0 ? 'amber' : undefined}
                />
                <StatCard
                  label="ΔNII — rates −200bp"
                  value={fmtCurrencySigned(num(m.earDown200Ghs), 'GHS')}
                  note="Net interest income sensitivity, downward shock"
                  tone={num(m.earDown200Ghs) < 0 ? 'amber' : undefined}
                />
              </CardBody>
            </Card>

            {/* Trend */}
            <Card>
              <CardHeader
                title="Worst ΔEVE / Tier 1 — 12-period trend"
                subtitle="Trailing-year path of the worst-case EVE sensitivity"
                action={
                  <StatusPill tone={statusTone(m.eveStatus)}>
                    {fmtPct(worstPct, 2)} vs {eveLimit}% limit
                  </StatusPill>
                }
              />
              <CardBody>
                {trendPoints.length > 0 ? (
                  <>
                    <RatioHistoryChart
                      data={trendPoints}
                      threshold={eveLimit}
                      yMin={0}
                      yMax={Math.ceil(
                        Math.max(...trendPoints.map((p) => p.value), eveLimit) + 2
                      )}
                      color="#0A2540"
                      label="Worst ΔEVE/Tier 1"
                    />
                    {hasInlineTrendPoints && (
                      <p className="mt-2 text-caption text-slate">
                        Hollow points are computed inline — run all scenarios
                        on those periods to persist them.
                      </p>
                    )}
                  </>
                ) : (
                  <EmptyState
                    Icon={Zap}
                    title="No trend history"
                    description="Run all scenarios to build the per-period EVE sensitivity trend."
                    action={runAllButton}
                  />
                )}
              </CardBody>
            </Card>

            {/* Validations */}
            <Card>
              <CardHeader
                title="Validations"
                subtitle="IRRBB rule evaluation for this period"
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

function GapTable({ rows }: { rows: IrrGapBucketRead[] }) {
  const columns: Column<IrrGapBucketRead>[] = [
    { key: 'bucket', header: 'Tenor bucket', render: (r) => r.bucket, width: '24%' },
    {
      key: 'rsa',
      header: 'RSA',
      numeric: true,
      render: (r) => fmtCurrency(num(r.rsaGhs), 'GHS'),
    },
    {
      key: 'rsl',
      header: 'RSL',
      numeric: true,
      render: (r) => fmtCurrency(num(r.rslGhs), 'GHS'),
    },
    {
      key: 'gap',
      header: 'Period gap',
      numeric: true,
      render: (r) => {
        const v = num(r.gapGhs);
        return (
          <span className={v < 0 ? 'text-warning font-medium' : undefined}>
            {fmtCurrencySigned(v, 'GHS')}
          </span>
        );
      },
    },
    {
      key: 'cum',
      header: 'Cumulative gap',
      numeric: true,
      render: (r) => {
        const v = num(r.cumulativeGapGhs);
        return (
          <span className={v < 0 ? 'text-warning font-medium' : undefined}>
            {fmtCurrencySigned(v, 'GHS')}
          </span>
        );
      },
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function EveTable({ rows }: { rows: IrrEveScenarioRead[] }) {
  const columns: Column<IrrEveScenarioRead>[] = [
    {
      key: 'scenario',
      header: 'Scenario',
      render: (r) => scenarioLabel(r.scenarioCode),
      width: '28%',
    },
    {
      key: 'eve',
      header: 'EVE',
      numeric: true,
      render: (r) => fmtCurrency(num(r.eveGhs), 'GHS'),
    },
    {
      key: 'delta',
      header: 'ΔEVE',
      numeric: true,
      render: (r) => {
        const v = num(r.deltaEveGhs);
        return (
          <span className={v < 0 ? 'text-critical font-medium' : undefined}>
            {fmtCurrencySigned(v, 'GHS')}
          </span>
        );
      },
    },
    {
      key: 'pct',
      header: 'ΔEVE / Tier 1',
      numeric: true,
      render: (r) => {
        const v = num(r.deltaEvePctTier1);
        return (
          <span className={r.breach ? 'text-critical font-medium' : undefined}>
            {fmtPct(v, 2)}
          </span>
        );
      },
    },
    {
      key: 'status',
      header: 'Status',
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.breach ? 'breach' : 'compliant'}>
          {r.breach ? 'Breach' : 'Within limit'}
        </StatusPill>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function StatCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: 'amber' | 'critical';
}) {
  const valueColor =
    tone === 'critical'
      ? 'text-critical'
      : tone === 'amber'
      ? 'text-warning'
      : 'text-navy';
  return (
    <div className="card p-5 h-full flex flex-col gap-2">
      <p className="text-caption font-medium text-slate uppercase tracking-wider">
        {label}
      </p>
      <p className={`font-mono text-h1 tabular-nums ${valueColor}`}>{value}</p>
      {note && <p className="text-caption text-slate leading-snug mt-auto">{note}</p>}
    </div>
  );
}
