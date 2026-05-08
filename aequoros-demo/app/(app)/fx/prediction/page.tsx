import { Sparkles, TrendingDown } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KPICard from '@/components/ui/KPICard';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import RecommendationCard from '@/components/ui/RecommendationCard';
import FxRateChart from '@/components/charts/FxRateChart';
import { fxRatePrediction, fxModelAccuracy } from '@/lib/data/fx';
import { bank } from '@/lib/data/bank';

export default function RatePrediction() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk', href: '/fx' },
          { label: 'Rate Prediction' },
        ]}
        title="Rate Prediction"
        subtitle="ML-based 30/60/90-day forecast · Compared to forward market implieds"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <KPICard
            label="Spot GHS/USD"
            value={12.5}
            decimals={2}
            footer="Δ +0.04 (+0.32% 1d)"
            sparkline={[12.42, 12.44, 12.46, 12.45, 12.48, 12.49, 12.5, 12.5]}
          />
          <KPICard
            label="ML 90-day forecast"
            value={12.94}
            decimals={2}
            footer="vs forward implied 12.71"
            status="amber"
          />
          <KPICard
            label="Model MAPE"
            value={fxModelAccuracy.mape}
            suffix="%"
            decimals={1}
            footer={`vs forward MAPE ${fxModelAccuracy.forwardImpliedMape.toFixed(1)}%`}
            status="compliant"
          />
          <KPICard
            label="Direction hit rate"
            value={fxModelAccuracy.hitRate * 100}
            suffix="%"
            decimals={0}
            footer="12-month back-test"
            status="compliant"
          />
        </div>

        <Card>
          <CardHeader
            title="GHS / USD — 90 days back, 90 days forward"
            subtitle={`${fxModelAccuracy.modelType} · 95% confidence band shown`}
            action={
              <span className="inline-flex items-center gap-1.5 text-caption text-action font-medium">
                <Sparkles size={13} aria-hidden /> Ensemble v3.2
              </span>
            }
          />
          <CardBody>
            <FxRateChart data={fxRatePrediction} />
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card>
            <CardHeader title="Model performance" subtitle="ML ensemble vs forward implied" />
            <CardBody className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    MAPE
                  </p>
                  <div className="mt-2 flex items-baseline gap-3">
                    <span className="font-mono text-h1 text-navy tabular-nums">
                      {fxModelAccuracy.mape.toFixed(1)}%
                    </span>
                    <span className="font-mono text-body text-slate line-through tabular-nums">
                      {fxModelAccuracy.forwardImpliedMape.toFixed(1)}%
                    </span>
                  </div>
                  <p className="mt-1 text-caption text-success font-medium inline-flex items-center gap-1">
                    <TrendingDown size={12} aria-hidden />
                    46.2% improvement
                  </p>
                </div>
                <div>
                  <p className="text-micro font-medium uppercase tracking-wider text-slate">
                    RMSE
                  </p>
                  <p className="mt-2 font-mono text-h1 text-navy tabular-nums">
                    {fxModelAccuracy.rmse.toFixed(2)}
                  </p>
                  <p className="mt-1 text-caption text-slate">GHS per USD</p>
                </div>
              </div>
              <div className="border-t border-border-light pt-4 text-body text-slate leading-relaxed">
                Ensemble model combines XGBoost on macro factors (BoG MPR,
                inflation, T-bill spread, sovereign CDS) with LSTM on price
                action and volatility surface. Refit weekly; live inference
                runs hourly.
              </div>
            </CardBody>
          </Card>

          <RecommendationCard
            modelLabel="Ensemble · GHS/USD"
            severity="amber"
            title="Restructure 11-day expiring forward — extend tenor or lift hedge ratio"
            rationale="Active forward (FX-2025-091, USD 4M at 12.62) expires in 11 days. ML 90d forecast (12.94) exceeds forward implied (12.71) by 1.8%. Net long USD position would benefit, but unhedged exposure adds 0.4% to NOP/capital. Recommend rolling at next BoG auction or extending to 6M tenor."
            expectedImpact="Maintains 74% hedge ratio. NOP/capital remains within BoG limit (4.92% vs 5.0%). Rolling forward locks in current curve before BoG mid-April policy meeting."
            confidence={0.74}
          />
        </div>
      </div>
    </>
  );
}
