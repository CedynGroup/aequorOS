'use client';

import { useState, useMemo } from 'react';
import { Sparkles, TrendingDown } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import RecommendationCard from '@/components/ui/RecommendationCard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import CashFlowForecastChart from '@/components/charts/CashFlowForecastChart';
import { cashFlowForecast, lstmAccuracy } from '@/lib/data/liquidity';
import { bank } from '@/lib/data/bank';

export default function CashFlowForecast() {
  const [horizon, setHorizon] = useState<30 | 60 | 90>(30);
  const [showStatic, setShowStatic] = useState(true);

  const slice = useMemo(
    () => cashFlowForecast.slice(0, horizon),
    [horizon]
  );

  const cumLstm = slice.reduce((s, p) => s + p.netLstm, 0);
  const cumStatic = slice.reduce((s, p) => s + p.netStatic, 0);
  const minDay = slice.reduce((m, p) => (p.netLstm < m.netLstm ? p : m), slice[0]);

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
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        {/* Filter row */}
        <div className="card px-5 py-3 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-1.5">
            <span className="text-caption font-medium text-slate uppercase tracking-wider mr-2">
              Horizon
            </span>
            {[30, 60, 90].map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h as 30 | 60 | 90)}
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

          <div className="flex items-center gap-3">
            <label className="inline-flex items-center gap-2 text-caption text-slate cursor-pointer">
              <input
                type="checkbox"
                checked={showStatic}
                onChange={(e) => setShowStatic(e.target.checked)}
                className="accent-action"
              />
              Compare to static behavioral
            </label>
          </div>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label={`Cumulative net (${horizon}d) — LSTM`}
            value={cumLstm}
            prefix="GHS"
            suffix="M"
            decimals={1}
            status={cumLstm >= 0 ? 'compliant' : 'breach'}
          />
          <KPICard
            label={`Cumulative net (${horizon}d) — Static`}
            value={cumStatic}
            prefix="GHS"
            suffix="M"
            decimals={1}
            status={cumStatic >= 0 ? 'compliant' : 'breach'}
          />
          <KPICard
            label="LSTM accuracy (MAPE)"
            value={lstmAccuracy.mape}
            suffix="%"
            decimals={1}
            footer={`vs static MAPE ${lstmAccuracy.staticMape.toFixed(1)}%`}
          />
          <KPICard
            label="Worst day projection"
            value={minDay.netLstm}
            prefix="GHS"
            suffix="M"
            decimals={2}
            footer={`Day +${minDay.day}`}
            status={minDay.netLstm < 0 ? 'approaching' : 'compliant'}
          />
        </div>

        {/* Forecast chart */}
        <Card>
          <CardHeader
            title="Daily net cash flow"
            subtitle="LSTM forecast with 95% confidence band · static behavioral overlay"
            action={
              <span className="inline-flex items-center gap-1.5 text-caption text-action font-medium">
                <Sparkles size={13} aria-hidden /> LSTM v3.1
              </span>
            }
          />
          <CardBody>
            <CashFlowForecastChart
              data={cashFlowForecast}
              horizonDays={horizon}
              showStatic={showStatic}
            />
            <div className="mt-4 flex items-center justify-between text-caption text-slate flex-wrap gap-3">
              <div className="flex items-center gap-5">
                <span className="inline-flex items-center gap-2">
                  <span className="w-3 h-0.5 bg-action" /> LSTM forecast
                </span>
                <span className="inline-flex items-center gap-2">
                  <span className="w-3 h-0.5 bg-action/15" /> 95% CI band
                </span>
                <span className="inline-flex items-center gap-2">
                  <span
                    className="w-3 h-0.5 bg-slate"
                    style={{ borderTop: '1px dashed #5A6776' }}
                  />{' '}
                  Static behavioral
                </span>
              </div>
              <span>
                Recalculated 06:00 GMT · Trained on 36 months daily transactional
                data
              </span>
            </div>
          </CardBody>
        </Card>

        {/* Two-column: model performance + AI recommendations */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader
              title="Model performance"
              subtitle="LSTM vs static behavioral assumptions · 12-month back-test"
            />
            <CardBody className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Mean Absolute Percent Error
                  </p>
                  <div className="mt-2 flex items-baseline gap-3">
                    <span className="font-mono text-h1 text-navy tabular-nums">
                      {lstmAccuracy.mape.toFixed(1)}%
                    </span>
                    <span className="font-mono text-body text-slate line-through tabular-nums">
                      {lstmAccuracy.staticMape.toFixed(1)}%
                    </span>
                  </div>
                  <p className="mt-1 text-caption text-success font-medium inline-flex items-center gap-1">
                    <TrendingDown size={12} aria-hidden />
                    {lstmAccuracy.improvementPct.toFixed(1)}% improvement
                  </p>
                </div>
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    Root Mean Squared Error
                  </p>
                  <div className="mt-2 flex items-baseline gap-3">
                    <span className="font-mono text-h1 text-navy tabular-nums">
                      {lstmAccuracy.rmse.toFixed(2)}
                    </span>
                    <span className="font-mono text-body text-slate line-through tabular-nums">
                      {lstmAccuracy.staticRmse.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 text-caption text-success font-medium inline-flex items-center gap-1">
                    <TrendingDown size={12} aria-hidden />
                    GHS millions
                  </p>
                </div>
              </div>

              <div className="border-t border-border-light pt-4 text-body text-slate leading-relaxed">
                LSTM captures weekly seasonality (salary cycle, business-day
                outflow patterns) and month-end effects that static behavioral
                assumptions miss. Aligned with Basel Committee guidance on
                AI/ML in liquidity risk modeling and SR 11-7 model risk
                governance.
              </div>
            </CardBody>
          </Card>

          <RecommendationCard
            modelLabel="LSTM behavioral model"
            severity="success"
            title="Deploy GHS 18M surplus into 91-day BoG T-bill auction (8 Apr settle)"
            rationale="Cumulative net inflow of GHS 18.2M projected over next 30 days. Surplus exceeds operational reserve buffer by 1.8× and falls outside next stress horizon. T-bill yield 25.4% materially exceeds 7-day money market floor."
            expectedImpact="+GHS 1.14M expected NII over 91 days. LCR remains above 138% throughout. NSFR neutral (HQLA Level 1 reclassification)."
            confidence={0.86}
          />
        </div>
      </div>
    </>
  );
}
