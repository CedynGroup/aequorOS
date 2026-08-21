'use client';

import { useState } from 'react';
import { CloudOff, Loader2, RotateCw, Sparkles, TrendingDown } from 'lucide-react';
import type {
  CashflowForecastMode,
  CashflowForecastScenario,
  CashflowHorizon,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import Sparkline from '@/components/ui/Sparkline';
import { SkeletonChart } from '@/components/ui/Skeleton';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import CashFlowForecastChart, { CumulativeCashFlowChart } from '@/components/charts/CashFlowForecastChart';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isServiceUnavailableError,
  useCashflowForecast,
  useCashflowHistory,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';
import { currencyCode, fmtCurrency } from '@/lib/format';

const HORIZONS: CashflowHorizon[] = [30, 60, 90];

const MODES: { value: CashflowForecastMode; label: string }[] = [
  { value: 'lstm', label: 'LSTM' },
  { value: 'static', label: 'Static' },
];

const SCENARIOS: { value: CashflowForecastScenario; label: string }[] = [
  { value: 'baseline', label: 'Baseline' },
  { value: 'adverse', label: 'Adverse' },
  { value: 'severe', label: 'Severe' },
];

export default function CashFlowForecast() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const [horizon, setHorizon] = useState<CashflowHorizon>(30);
  const [mode, setMode] = useState<CashflowForecastMode>('lstm');
  const [scenario, setScenario] = useState<CashflowForecastScenario>('baseline');

  const forecastQuery = useCashflowForecast(bankId, horizon, mode, scenario);
  const historyQuery = useCashflowHistory(bankId, 90);

  const forecast = forecastQuery.data;
  const historyPoints = historyQuery.data?.points ?? [];

  const chartHistory = historyPoints.map((p, i) => ({
    day: i - (historyPoints.length - 1),
    netFlow: p.netFlow,
  }));
  const chartForecast = (forecast?.points ?? []).map((p) => ({
    day: p.day,
    netFlow: p.netFlow,
    lower: p.lower,
    upper: p.upper,
    p5: p.p5,
    p50: p.p50,
    p95: p.p95,
    behavioral: p.behavioralNetFlow,
    contractual: p.contractualNetFlow,
    scenarioAdjustment: p.scenarioAdjustment,
  }));

  const cumulativeNet = chartForecast.reduce((sum, point) => sum + point.p50, 0);
  const cumulativeForecast = chartForecast.reduce<
    { day: number; central: number; lower: number; upper: number }[]
  >((points, point) => {
    const prior = points.at(-1) ?? { central: 0, lower: 0, upper: 0 };
    points.push({
      day: point.day,
      central: prior.central + point.p50,
      lower: prior.lower + point.p5,
      upper: prior.upper + point.p95,
    });
    return points;
  }, []);
  const worstCumulative = cumulativeForecast.reduce(
    (worst, point) => (worst === null || point.central < worst.central ? point : worst),
    null as { day: number; central: number; lower: number; upper: number } | null
  );
  const firstCumulativeDeficit = cumulativeForecast.find((point) => point.central < 0);
  const lowerCumulativeNet = cumulativeForecast.at(-1)?.lower ?? 0;
  const worstDay = chartForecast.reduce(
    (worst, point) => (worst === null || point.p50 < worst.p50 ? point : worst),
    null as (typeof chartForecast)[number] | null
  );

  const offline =
    isServiceUnavailableError(forecastQuery.error) ||
    isServiceUnavailableError(historyQuery.error);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Cash Flow Forecast' },
        ]}
        title="Cash Flow Forecast"
        subtitle="Daily net cash flow projection · LSTM behavioral model vs static assumptions"
      />

      <div className="px-8 py-6 space-y-6">
        {/* Filter row */}
        <div className="card px-5 py-3 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="text-caption font-medium text-slate uppercase tracking-wider mr-2">
              Horizon
            </span>
            {HORIZONS.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h)}
                className={`px-3 py-1.5 rounded text-caption font-medium transition-colors ${
                  horizon === h
                    ? 'bg-nav text-white'
                    : 'text-slate hover:bg-surface'
                }`}
              >
                {h} days
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-caption font-medium text-slate uppercase tracking-wider mr-2">
              Scenario
            </span>
            {SCENARIOS.map((entry) => (
              <button
                key={entry.value}
                type="button"
                onClick={() => setScenario(entry.value)}
                className={`px-3 py-1.5 rounded text-caption font-medium transition-colors ${
                  scenario === entry.value ? 'bg-nav text-white' : 'text-slate hover:bg-surface'
                }`}
              >
                {entry.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-caption font-medium text-slate uppercase tracking-wider mr-2">
              Method
            </span>
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setMode(m.value)}
                className={`px-3 py-1.5 rounded text-caption font-medium transition-colors ${
                  mode === m.value
                    ? 'bg-nav text-white'
                    : 'text-slate hover:bg-surface'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {offline ? (
          <div className="card border-l-4 border-l-critical bg-critical-light/40 p-5 flex items-start gap-3">
            <CloudOff size={18} className="text-critical shrink-0 mt-0.5" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-body font-medium text-navy">
                Cash flow forecasting is unavailable
              </p>
              <p className="mt-1 text-body text-navy/80 leading-relaxed">
                The backend could not load the forecasting model (
                <span className="font-mono">app/ml</span>). Check the backend logs,
                then retry.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                void forecastQuery.refetch();
                void historyQuery.refetch();
              }}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
            >
              <RotateCw size={13} aria-hidden />
              Retry
            </button>
          </div>
        ) : forecastQuery.error || historyQuery.error ? (
          <ErrorPanel
            error={forecastQuery.error ?? historyQuery.error}
            onRetry={() => {
              void forecastQuery.refetch();
              void historyQuery.refetch();
            }}
          />
        ) : forecastQuery.isLoading || historyQuery.isLoading ? (
          <div className="space-y-3">
            <SkeletonChart height={340} />
            <p className="text-caption text-slate flex items-center gap-2">
              <Loader2 size={13} className="animate-spin" aria-hidden />
              Training model on first request — the initial LSTM call fits the
              network before responding and can take a minute.
            </p>
          </div>
        ) : forecast ? (
          <>
            {/* KPIs */}
            <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-4">
              <KpiStat
                label={`Cumulative net (${horizon}d) — ${mode === 'lstm' ? 'LSTM' : 'Static'}`}
                value={fmtCurrency(cumulativeNet)}
                status={cumulativeNet >= 0 ? 'ok' : 'crit'}
                hint="Sum of projected daily net cash flows"
              />
              <KpiStat
                label="LSTM accuracy (MAPE)"
                value={forecast.accuracy.lstmMape.toFixed(1)}
                unit="%"
                hint={`vs static MAPE ${forecast.accuracy.staticMape.toFixed(1)}%`}
              />
              <KpiStat
                label="LSTM improvement"
                value={forecast.accuracy.improvementPct.toFixed(1)}
                unit="%"
                status="ok"
                hint="Net-position MAPE reduction vs static"
              />
              <KpiStat
                label="Worst day projection"
                value={fmtCurrency(worstDay?.p50 ?? 0)}
                status={(worstDay?.p50 ?? 0) < 0 ? 'warn' : 'ok'}
                hint={worstDay ? `Day +${worstDay.day}` : undefined}
              />
              <KpiStat
                label="Worst cumulative position"
                value={fmtCurrency(worstCumulative?.central ?? 0)}
                status={(worstCumulative?.central ?? 0) < 0 ? 'crit' : 'ok'}
                hint={worstCumulative ? `P50 path at day +${worstCumulative.day}` : undefined}
              />
              <KpiStat
                label="First cumulative deficit"
                value={firstCumulativeDeficit ? `Day +${firstCumulativeDeficit.day}` : 'None'}
                status={firstCumulativeDeficit ? 'crit' : 'ok'}
                hint={firstCumulativeDeficit ? `P50 path ${fmtCurrency(firstCumulativeDeficit.central)}` : 'P50 path stays non-negative'}
              />
            </div>

            {/* Forecast chart */}
            <ChartFrame
              title="Daily net cash flow"
              subtitle={
                mode === 'lstm'
                  ? 'Hybrid behavioural and contractual cash flow, with simulated P5/P50/P95 paths'
                  : '90-day actuals with static behavioral forecast'
              }
              height={340}
              actions={
                <span className="inline-flex items-center gap-2 text-caption text-action font-medium">
                  <Sparkles size={13} aria-hidden />
                  {forecast.modelVersion}
                  <span className="text-slate font-normal">
                    · as of{' '}
                    <span className="font-mono text-navy">
                      {fmtDateUTC(forecast.asOfDate)}
                    </span>
                  </span>
                </span>
              }
              footer={
                <>
                  <span className="inline-flex items-center gap-2">
                    <span className="w-3 h-0.5 bg-slate" /> Actual net flow
                  </span>
                  <span className="inline-flex items-center gap-2">
                    <span className="w-3 h-0.5 bg-action" />{' '}
                    {mode === 'lstm' ? 'LSTM forecast' : 'Static forecast'}
                  </span>
                  {mode === 'lstm' && (
                    <span className="inline-flex items-center gap-2">
                      <span className="w-3 h-0.5 bg-action/15" /> Simulated P5/P95 band
                    </span>
                  )}
                  <span className="ml-auto">All values in {currencyCode()}</span>
                </>
              }
            >
              <CashFlowForecastChart
                history={chartHistory}
                forecast={chartForecast.map((point) => ({
                  day: point.day,
                  netFlow: point.p50,
                  lower: point.p5,
                  upper: point.p95,
                }))}
                showBand={mode === 'lstm'}
                forecastLabel={mode === 'lstm' ? 'LSTM forecast' : 'Static forecast'}
              />
            </ChartFrame>

            <ChartFrame
              title="Cumulative cash position"
              subtitle="Running P5/P50/P95 net-cash position over the selected horizon from simulated residual paths."
              height={280}
              footer={<><span>P50 cumulative: {fmtCurrency(cumulativeNet)}</span><span className="ml-auto">P5 cumulative: {fmtCurrency(lowerCumulativeNet)}</span></>}
            >
              <CumulativeCashFlowChart data={cumulativeForecast} showBand={mode === 'lstm'} />
            </ChartFrame>

            {/* Model performance / comparison panel */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title="Model performance"
                subtitle="LSTM vs static behavioral assumptions"
              >
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        Mean Absolute Percent Error
                      </p>
                      <div className="mt-2 flex items-baseline gap-3">
                        <span className="font-mono text-h1 text-navy tabular-nums">
                          {forecast.accuracy.lstmMape.toFixed(1)}%
                        </span>
                        <span className="font-mono text-body text-slate line-through tabular-nums">
                          {forecast.accuracy.staticMape.toFixed(1)}%
                        </span>
                      </div>
                      <p className="mt-1 text-caption text-success font-medium inline-flex items-center gap-1">
                        <TrendingDown size={12} aria-hidden />
                        {forecast.accuracy.improvementPct.toFixed(1)}% improvement
                      </p>
                    </div>
                    <div>
                      <p className="text-micro font-medium uppercase tracking-wider text-slate">
                        Model version
                      </p>
                      <div className="mt-2">
                        <span className="font-mono text-h2 text-navy">
                          {forecast.modelVersion}
                        </span>
                      </div>
                      <p className="mt-1 text-caption text-slate">
                        Forecast as of{' '}
                        <span className="font-mono text-navy">
                          {fmtDateUTC(forecast.asOfDate)}
                        </span>
                      </p>
                      <p className="mt-1 text-caption text-slate">
                        Model scope: <span className="font-medium text-navy">{forecast.modelScope === 'bank_specific' ? 'Bank-specific history' : 'Generic bootstrap'}</span>
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-4 border-t border-border-light pt-4">
                    <div><p className="text-micro font-medium uppercase tracking-wider text-slate">Bias</p><p className="mt-1 font-mono text-body text-navy">{forecast.accuracy.biasPct.toFixed(2)}%</p></div>
                    <div><p className="text-micro font-medium uppercase tracking-wider text-slate">P5–P95 coverage</p><p className="mt-1 font-mono text-body text-navy">{forecast.accuracy.intervalCoveragePct.toFixed(1)}%</p></div>
                    <div><p className="text-micro font-medium uppercase tracking-wider text-slate">Residual drift</p><p className="mt-1 font-mono text-body text-navy">{forecast.accuracy.residualDriftPct.toFixed(1)}%</p></div>
                  </div>

                  <div className="border-t border-border-light pt-4 text-body text-slate leading-relaxed">
                    Back-tested on a 130-day holdout; cumulative net-position
                    MAPE. The LSTM captures weekly seasonality and month-end
                    salary effects that static behavioral assumptions miss.
                  </div>
                </div>
              </SectionCard>

              <SectionCard
                title="Method comparison"
                subtitle="Switch the method toggle to view each forecast"
              >
                <div className="space-y-3 text-body text-navy/85 leading-relaxed">
                  <p>
                    <span className="font-medium text-navy">LSTM behavioral model</span>{' '}
                    — recurrent network trained on the bank&apos;s daily
                    transactional net flows, served with simulated P5/P50/P95
                    paths.
                  </p>
                  <p>
                    <span className="font-medium text-navy">Static behavioral</span>{' '}
                    — fixed run-off and inflow assumptions applied uniformly
                    across the horizon; the benchmark the LSTM is measured
                    against.
                  </p>
                  <p className="text-caption text-slate border-t border-border-light pt-3">
                    Model governance per SR 11-7: accuracy is re-benchmarked on
                    each retrain; forecasts are decision support, not an
                    autonomous control.
                  </p>
                </div>
              </SectionCard>
            </div>

            <SectionCard title="Hybrid forecast composition" subtitle="Every projected day separates contractual maturities, behavioural flow, and the active scenario overlay.">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-caption">
                <div><p className="text-slate">Behavioural component</p><p className="mt-1 text-body font-medium text-navy">{fmtCurrency(chartForecast.reduce((sum, point) => sum + point.behavioral, 0))}</p></div>
                <div><p className="text-slate">Contractual maturities</p><p className="mt-1 text-body font-medium text-navy">{fmtCurrency(chartForecast.reduce((sum, point) => sum + point.contractual, 0))}</p></div>
                <div><p className="text-slate">Scenario overlay</p><p className="mt-1 text-body font-medium text-navy">{fmtCurrency(chartForecast.reduce((sum, point) => sum + point.scenarioAdjustment, 0))}</p></div>
              </div>
              <ul className="mt-5 list-disc space-y-1 pl-5 text-caption text-slate">
                {forecast.scenarioAssumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}
              </ul>
            </SectionCard>
          </>
        ) : null}
      </div>
    </>
  );
}
