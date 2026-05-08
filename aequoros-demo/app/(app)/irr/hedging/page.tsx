import { Sparkles } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody } from '@/components/ui/Card';
import RecommendationCard from '@/components/ui/RecommendationCard';
import { hedgeRecommendations } from '@/lib/data/irr';
import { bank } from '@/lib/data/bank';

export default function IRRHedging() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk', href: '/irr' },
          { label: 'AI Hedging' },
        ]}
        title="AI Hedging Recommendations"
        subtitle="Deep Reinforcement Learning · Optimized for EVE buffer protection"
        asOf={bank.asOf}
      />

      <div className="px-8 py-6 space-y-6">
        <Card>
          <CardBody className="flex items-start gap-3">
            <span className="inline-flex items-center justify-center w-9 h-9 rounded bg-action-light text-action shrink-0">
              <Sparkles size={16} aria-hidden />
            </span>
            <div>
              <p className="text-body font-medium text-navy">Deep RL hedging engine — v2.4</p>
              <p className="mt-1 text-body text-navy/80 leading-relaxed max-w-3xl">
                Trained on 84 months of historical IRRBB exposures across the
                bank&apos;s peer set. Reward function: minimize EVE volatility
                subject to NII variance ≤ ±10% and hedge cost ≤ 2bps of asset
                base. Recalculated daily; recommendations require Treasurer
                approval and CRO sign-off above GHS 25M notional.
              </p>
            </div>
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {hedgeRecommendations.map((r) => (
            <RecommendationCard
              key={r.id}
              modelLabel="Deep RL · IRRBB"
              title={r.title}
              rationale={r.rationale}
              expectedImpact={r.expectedImpact}
              confidence={r.confidence}
              severity={r.confidence >= 0.75 ? 'action' : 'amber'}
            />
          ))}
        </div>
      </div>
    </>
  );
}
