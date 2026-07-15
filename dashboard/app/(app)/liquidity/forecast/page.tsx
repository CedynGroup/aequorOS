'use client';

import { useState } from 'react';
import { CloudOff, Loader2, RotateCw, Sparkles, TrendingDown } from 'lucide-react';
import type {
  CashflowForecastMode,
  CashflowHorizon,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { SkeletonChart } from '@/components/ui/Skeleton';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import CashFlowForecastChart from '@/components/charts/CashFlowForecastChart';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isServiceUnavailableError,
  useCashflowForecast,
  useCashflowHistory,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

const HORIZONS: CashflowHorizon[] = [30, 60, 90];

const MODES: { value: CashflowForecastMode; label: string }[] = [
  { value: 'lstm', label: 'LSTM' },
  { value: 'static', label: 'Static' },
];

export default function CashFlowForecast() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;

  const [horizon, setHorizon] = useState<CashflowHorizon>(30);
  const [mode, setMode] = useState<CashflowForecastMode>('lstm');

  const forecastQuery = useCashflowForecast(bankId, horizon, mode);
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
  }));

  const cumulativeNet = chartForecast.reduce((s, p) => s + p.netFlow, 0);
  const worstDay = chartForecast.reduce(
    (min, p) => (min === null || p.netFlow < min.netFlow ? p : min),
    null as { day: number; netFlow: number } | null
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
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
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
                    ? 'bg-navy text-white'
                    : 'text-slate hover:bg-surface'
                }`}
              >
                {h} days
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
                    ? 'bg-navy text-white'
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
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <KPICard
                label={`Cumulative net (${horizon}d) — ${mode === 'lstm' ? 'LSTM' : 'Static'}`}
                value={cumulativeNet}
                prefix="GHS"
                suffix="M"
                decimals={1}
                status={cumulativeNet >= 0 ? 'compliant' : 'breach'}
              />
              <KPICard
                label="LSTM accuracy (MAPE)"
                value={forecast.accuracy.lstmMape}
                suffix="%"
                decimals={1}
                footer={`vs static MAPE ${forecast.accuracy.staticMape.toFixed(1)}%`}
              />
              <KPICard
                label="LSTM improvement"
                value={forecast.accuracy.improvementPct}
                suffix="%"
                decimals={1}
                status="compliant"
                footer="Net-position MAPE reduction vs static"
              />
              <KPICard
                label="Worst day projection"
                value={worstDay?.netFlow ?? 0}
                prefix="GHS"
                suffix="M"
                decimals={2}
                footer={worstDay ? `Day +${worstDay.day}` : undefined}
                status={
                  (worstDay?.netFlow ?? 0) < 0 ? 'approaching' : 'compliant'
                }
              />
            </div>

            {/* Forecast chart */}
            <Card>
              <CardHeader
                title="Daily net cash flow"
                subtitle={
                  mode === 'lstm'
                    ? '90-day actuals with LSTM forecast and 95% confidence band'
                    : '90-day actuals with static behavioral forecast'
                }
                action={
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
              />
              <CardBody>
                <CashFlowForecastChart
                  history={chartHistory}
                  forecast={chartForecast}
                  showBand={mode === 'lstm'}
                  forecastLabel={mode === 'lstm' ? 'LSTM forecast' : 'Static forecast'}
                />
                <div className="mt-4 flex items-center justify-between text-caption text-slate flex-wrap gap-3">
                  <div className="flex items-center gap-5">
                    <span className="inline-flex items-center gap-2">
                      <span className="w-3 h-0.5 bg-slate" /> Actual net flow
                    </span>
                    <span className="inline-flex items-center gap-2">
                      <span className="w-3 h-0.5 bg-action" />{' '}
                      {mode === 'lstm' ? 'LSTM forecast' : 'Static forecast'}
                    </span>
                    {mode === 'lstm' && (
                      <span className="inline-flex items-center gap-2">
                        <span className="w-3 h-0.5 bg-action/15" /> 95% CI band
                      </span>
                    )}
                  </div>
                  <span>All values in GHS millions</span>
                </div>
              </CardBody>
            </Card>

            {/* Model performance / comparison panel */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card>
                <CardHeader
                  title="Model performance"
                  subtitle="LSTM vs static behavioral assumptions"
                />
                <CardBody className="space-y-4">
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
                    </div>
                  </div>

                  <div className="border-t border-border-light pt-4 text-body text-slate leading-relaxed">
                    Back-tested on a 130-day holdout; cumulative net-position
                    MAPE. The LSTM captures weekly seasonality and month-end
                    salary effects that static behavioral assumptions miss.
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="Method comparison"
                  subtitle="Switch the method toggle to view each forecast"
                />
                <CardBody className="space-y-3 text-body text-navy/85 leading-relaxed">
                  <p>
                    <span className="font-medium text-navy">LSTM behavioral model</span>{' '}
                    — recurrent network trained on the bank&apos;s daily
                    transactional net flows, forecast with a 95% confidence
                    band.
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
                </CardBody>
              </Card>
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}
