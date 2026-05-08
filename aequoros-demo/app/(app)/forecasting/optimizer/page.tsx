import { Sparkles } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import RecommendationCard from '@/components/ui/RecommendationCard';
import { rlRecommendations } from '@/lib/data/forecasting';
import { bank } from '@/lib/data/bank';

export default function RLOptimizer() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'RL Optimizer' },
        ]}
        title="Strategic RL Optimizer"
        subtitle="Reinforcement Learning agent · Optimal balance sheet allocation"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardBody className="flex items-start gap-3">
            <span className="inline-flex items-center justify-center w-9 h-9 rounded bg-action-light text-action shrink-0">
              <Sparkles size={16} aria-hidden />
            </span>
            <div>
              <p className="text-body font-medium text-navy">
                Reinforcement Learning agent — v1.4
              </p>
              <p className="mt-1 text-body text-navy/80 leading-relaxed max-w-3xl">
                The RL agent explores balance sheet allocation policies under a
                36-month horizon, optimizing for risk-adjusted return on capital
                subject to regulatory constraints (CAR ≥ 13%, LCR ≥ 110%, NSFR ≥
                105%, NOP ≤ 4.5%). Reward function jointly captures NII, capital
                efficiency, and stress-tested resilience. Policy retrained
                monthly; recommendations require Treasurer + CRO sign-off above
                GHS 50M asset reallocation.
              </p>
            </div>
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {rlRecommendations.map((r) => (
            <RecommendationCard
              key={r.id}
              modelLabel={`Strategic RL · ${r.id.toUpperCase()}`}
              title={r.title}
              rationale={r.rationale}
              expectedImpact={r.expectedImpact}
              confidence={r.confidence}
              severity={r.confidence >= 0.78 ? 'action' : 'amber'}
            />
          ))}
        </div>

        <Card>
          <CardHeader
            title="Model governance"
            subtitle="SR 11-7 alignment · Backtest and validation"
          />
          <CardBody className="text-body text-navy/85 leading-relaxed space-y-3">
            <p>
              All RL recommendations are tracked through a 3-layer governance
              process: agent output → Treasurer review (within scope) → CRO
              approval (escalation above threshold) → ALCO ratification (capital
              plan implications). Independent model risk validation performed
              annually by Internal Audit per SR 11-7 framework.
            </p>
            <p>
              Backtest performance over Q3 2024 — Q1 2026 (synthetic
              counterfactual): risk-adjusted return on capital +180bps vs
              static heuristic baseline; no observed CAR or LCR breaches under
              recommended allocations.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
