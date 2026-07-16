'use client';

import { Info, Loader2, Zap } from 'lucide-react';
import type {
  FxCurrencyPositionRead,
  FxHedgeRead,
  FxScenarioNopRead,
  FxStandaloneVarRead,
} from '@aequoros/risk-service-api';
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
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useFxDashboard,
  useRegulatoryRun,
  useRunAllFxScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

const SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  mild_depreciation: 'Mild depreciation',
  severe_depreciation: 'Severe depreciation',
  cedi_crisis: 'Cedi crisis',
};

function scenarioLabel(code: string): string {
  return SCENARIO_LABELS[code] ?? code;
}

export default function FxDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useFxDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runAll = useRunAllFxScenarios(bankId);

  const data = dashboard.data;
  const m = data?.metrics;

  const aggregateLimit = num(m?.nopAggregateLimitPct ?? '20');
  const singleLimit = num(m?.nopSingleLimitPct ?? '10');

  const positions = data?.positions ?? [];
  const positionBars: SignedPoint[] = positions.map((p) => ({
    label: p.currency,
    value: num(p.netGhs),
  }));

  const trendPoints = (data?.trend ?? []).map((p) => ({
    month: p.label,
    value: num(p.nopPctTier1),
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
          { label: 'FX Risk' },
          { label: 'Net Open Position' },
        ]}
        title="FX Risk"
        subtitle="Net open position · VaR · Hedge effectiveness · BoG NOP framework"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="fx"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
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
                  scenarios to persist auditable regulatory runs for the cedi
                  depreciation shocks.
                </p>
              </div>
            )}

            {/* NOP gauge + single-currency + aggregate breakdown */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <RatioGauge
                label="Aggregate NOP / Tier 1"
                value={num(m.nopPctTier1)}
                threshold={aggregateLimit}
                status={statusTone(m.nopStatus)}
                decimals={2}
                thresholdLabel="Aggregate NOP limit"
                higherIsBetter={false}
              />
              <StatCard
                label={`Largest single currency (${m.singleCcyMaxCurrency})`}
                value={fmtPct(num(m.singleCcyMaxPct), 2)}
                note={`Single-currency limit ${singleLimit}% of Tier 1`}
                tone={
                  m.singleCcyStatus === 'red'
                    ? 'critical'
                    : m.singleCcyStatus === 'amber'
                    ? 'amber'
                    : undefined
                }
              />
              <StatCard
                label="Net open position"
                value={fmtCurrency(num(m.nopGhs), 'GHS')}
                note={`Long ${fmtCurrency(num(m.sumLongGhs), 'GHS')} · Short ${fmtCurrency(
                  num(m.sumShortGhs),
                  'GHS'
                )}`}
              />
            </div>

            {/* Per-currency positions */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card>
                <CardHeader
                  title="Position by currency"
                  subtitle="Net open position in GHS equivalent, % of Tier 1 capital"
                />
                <CardBody className="p-0">
                  <PositionTable rows={positions} singleLimit={singleLimit} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader
                  title="Net position by currency"
                  subtitle="Long positions positive, short positions negative"
                />
                <CardBody>
                  <SignedBarChart
                    data={positionBars}
                    layout="vertical"
                    height={300}
                    positiveColor="#0A2540"
                    negativeColor="#C97C00"
                    format="ghs-m"
                    valueLabel="Net GHS"
                    categoryWidth={64}
                  />
                </CardBody>
              </Card>
            </div>

            {/* Value at Risk */}
            <Card>
              <CardHeader
                title="Value at Risk"
                subtitle={`${num(m.varConfidencePct).toFixed(0)}% confidence · 1-day horizon · ${
                  m.varObservations
                } observations`}
              />
              <CardBody className="space-y-5">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                  <StatCard
                    label="VaR (99%, 1-day)"
                    value={fmtCurrency(num(m.var991dGhs), 'GHS')}
                    note="Diversified portfolio VaR"
                  />
                  <StatCard
                    label="Stressed VaR"
                    value={fmtCurrency(num(m.stressedVarGhs), 'GHS')}
                    note="Cedi-crisis calibration"
                    tone="amber"
                  />
                  <StatCard
                    label="Diversification benefit"
                    value={fmtCurrency(num(m.diversificationBenefitGhs), 'GHS')}
                    note="Standalone less diversified VaR"
                  />
                  <StatCard
                    label="Sum of standalone VaR"
                    value={fmtCurrency(num(m.standaloneVarTotalGhs), 'GHS')}
                    note="Undiversified currency VaR"
                  />
                </div>
                <StandaloneVarTable rows={data.standaloneVars} />
              </CardBody>
            </Card>

            {/* Hedge effectiveness */}
            <Card>
              <CardHeader
                title="Hedge effectiveness"
                subtitle="IFRS 9 prospective testing · R² ≥ 80% and dollar-offset 80–125%"
                action={
                  <StatusPill tone="slate">
                    {m.hedgeEffectiveCount} of {m.hedgeTotalCount} effective
                  </StatusPill>
                }
              />
              <CardBody className="p-0">
                <HedgeTable rows={data.hedges} />
              </CardBody>
            </Card>

            {/* Scenario comparison */}
            <Card>
              <CardHeader
                title="Depreciation scenario comparison"
                subtitle="Aggregate NOP under baseline and cedi depreciation shocks"
              />
              <CardBody className="p-0">
                <ScenarioTable
                  rows={data.scenarios}
                  aggregateLimit={aggregateLimit}
                />
              </CardBody>
            </Card>

            {/* Trend */}
            <Card>
              <CardHeader
                title="NOP / Tier 1 — 12-period trend"
                subtitle="Trailing-year path of the aggregate net open position ratio"
                action={
                  <StatusPill tone={statusTone(m.nopStatus)}>
                    {fmtPct(num(m.nopPctTier1), 2)} vs {aggregateLimit}% limit
                  </StatusPill>
                }
              />
              <CardBody>
                {trendPoints.length > 0 ? (
                  <>
                    <RatioHistoryChart
                      data={trendPoints}
                      threshold={aggregateLimit}
                      yMin={0}
                      yMax={Math.ceil(
                        Math.max(
                          ...trendPoints.map((p) => p.value),
                          aggregateLimit
                        ) + 3
                      )}
                      color="#0A2540"
                      label="NOP/Tier 1"
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
                    description="Run all scenarios to build the per-period NOP trend."
                    action={runAllButton}
                  />
                )}
              </CardBody>
            </Card>

            {/* Validations */}
            <Card>
              <CardHeader
                title="Validations"
                subtitle="NOP, VaR, and hedge-accounting rule evaluation for this period"
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

function PositionTable({
  rows,
  singleLimit,
}: {
  rows: FxCurrencyPositionRead[];
  singleLimit: number;
}) {
  const columns: Column<FxCurrencyPositionRead>[] = [
    { key: 'ccy', header: 'Currency', render: (r) => r.currency, width: '16%' },
    {
      key: 'side',
      header: 'Side',
      render: (r) => (
        <StatusPill tone={r.side === 'long' ? 'action' : 'amber'}>
          {r.side === 'long' ? 'Long' : 'Short'}
        </StatusPill>
      ),
    },
    {
      key: 'net',
      header: 'Net (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.netGhs), 'GHS'),
    },
    {
      key: 'pct',
      header: '% Tier 1',
      numeric: true,
      render: (r) => {
        const v = num(r.absPctTier1);
        return (
          <span className={!r.withinSingleLimit ? 'text-critical font-medium' : undefined}>
            {fmtPct(v, 2)}
          </span>
        );
      },
    },
    {
      key: 'limit',
      header: `Limit ${singleLimit}%`,
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.withinSingleLimit ? 'compliant' : 'breach'}>
          {r.withinSingleLimit ? 'Within' : 'Breach'}
        </StatusPill>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function StandaloneVarTable({ rows }: { rows: FxStandaloneVarRead[] }) {
  const columns: Column<FxStandaloneVarRead>[] = [
    { key: 'ccy', header: 'Currency', render: (r) => r.currency, width: '30%' },
    {
      key: 'net',
      header: 'Net position (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.netGhs), 'GHS'),
    },
    {
      key: 'var',
      header: 'Standalone VaR (99%, 1d)',
      numeric: true,
      render: (r) => fmtCurrency(num(r.standaloneVarGhs), 'GHS'),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function HedgeTable({ rows }: { rows: FxHedgeRead[] }) {
  const columns: Column<FxHedgeRead>[] = [
    { key: 'id', header: 'Hedge', render: (r) => r.hedgeId, width: '16%' },
    { key: 'pair', header: 'Pair', render: (r) => r.pair },
    { key: 'instrument', header: 'Instrument', render: (r) => r.instrument },
    {
      key: 'r2',
      header: 'Prospective R²',
      numeric: true,
      render: (r) => fmtPct(num(r.prospectiveR2Pct), 1),
    },
    {
      key: 'offset',
      header: 'Dollar offset',
      numeric: true,
      render: (r) => fmtPct(num(r.dollarOffsetPct), 1),
    },
    {
      key: 'mtm',
      header: 'MTM (GHS)',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.mtmGhs), 'GHS'),
    },
    {
      key: 'status',
      header: 'Status',
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.effective ? 'compliant' : 'breach'}>
          {r.effective ? 'Effective' : 'Ineffective'}
        </StatusPill>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function ScenarioTable({
  rows,
  aggregateLimit,
}: {
  rows: FxScenarioNopRead[];
  aggregateLimit: number;
}) {
  const columns: Column<FxScenarioNopRead>[] = [
    {
      key: 'scenario',
      header: 'Scenario',
      render: (r) => scenarioLabel(r.scenarioCode),
      width: '28%',
    },
    {
      key: 'shock',
      header: 'Depreciation shock',
      numeric: true,
      render: (r) => (num(r.shockPct) === 0 ? '—' : fmtPct(num(r.shockPct), 1)),
    },
    {
      key: 'nop',
      header: 'Aggregate NOP (GHS)',
      numeric: true,
      render: (r) => fmtCurrency(num(r.nopGhs), 'GHS'),
    },
    {
      key: 'pct',
      header: 'NOP / Tier 1',
      numeric: true,
      render: (r) => {
        const v = num(r.nopPctTier1);
        return (
          <span
            className={!r.withinAggregateLimit ? 'text-critical font-medium' : undefined}
          >
            {fmtPct(v, 2)}
          </span>
        );
      },
    },
    {
      key: 'status',
      header: `Limit ${aggregateLimit}%`,
      align: 'right',
      render: (r) => (
        <StatusPill tone={r.withinAggregateLimit ? 'compliant' : 'breach'}>
          {r.withinAggregateLimit ? 'Within' : 'Breach'}
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
