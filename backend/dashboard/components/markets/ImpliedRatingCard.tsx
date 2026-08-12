import type { LiveModuleView } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { fmtTimestamp, num } from '@/lib/api/values';

function formatPercent(value: string): string {
  return `${num(value).toFixed(2)}%`;
}

function PdBandRow({
  label,
  lower,
  point,
  upper,
}: {
  label: string;
  lower: string;
  point: string;
  upper: string;
}) {
  return (
    <div className="grid grid-cols-[42px_1fr] gap-3 text-caption">
      <span className="font-medium text-slate">{label}</span>
      <div className="grid grid-cols-3 gap-2 text-right font-mono tnum text-navy">
        <span>{formatPercent(lower)}</span>
        <span>{formatPercent(point)}</span>
        <span className="font-semibold">{formatPercent(upper)}</span>
      </div>
    </div>
  );
}

/** Bank's current live Treasury/ALM credit assessment, distinct from agency observations. */
export default function ImpliedRatingCard({ rating }: { rating: LiveModuleView }) {
  const metrics = rating.metrics as Record<string, string>;
  if (metrics.availability === 'unavailable') return null;

  return (
    <div className="border border-border bg-surface-raised rounded-lg overflow-hidden">
      <div className="grid grid-cols-1 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <div className="px-5 py-4 border-b lg:border-b-0 lg:border-r border-border bg-surface/55">
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Indicative internal assessment
          </p>
          <div className="mt-2 flex items-end gap-3">
            <span className="font-mono text-display tnum text-navy">
              {metrics.pit_rating_grade}
            </span>
            <span className="pb-1 text-caption text-slate">PIT grade</span>
          </div>
          <p className="mt-2 text-caption text-slate">
            Sovereign {metrics.sovereign_ceiling.toUpperCase()}
            {metrics.ceiling_applied === 'true' ? ' ceiling applied' : ' ceiling not binding'}
          </p>
        </div>

        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-border">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">One-year probability of default</p>
            <div className="flex items-center gap-2">
              <StatusPill tone="slate">Methodology v{metrics.methodology_version}</StatusPill>
              <StatusPill tone={metrics.ddep_eligible === 'true' ? 'success' : 'critical'}>
                DDEP {metrics.ddep_eligible === 'true' ? 'eligible' : 'ineligible'}
              </StatusPill>
            </div>
          </div>
          <div className="px-5 py-3">
            <div className="grid grid-cols-[42px_1fr] gap-3 pb-2 text-micro uppercase tracking-wider text-slate">
              <span>Basis</span>
              <div className="grid grid-cols-3 gap-2 text-right">
                <span>Lower</span>
                <span>Point</span>
                <span>Upper</span>
              </div>
            </div>
            <div className="space-y-2">
              <PdBandRow
                label="PIT"
                lower={metrics.pit_pd_lower_pct}
                point={metrics.pit_pd_point_pct}
                upper={metrics.pit_pd_upper_pct}
              />
              <PdBandRow
                label="TTC"
                lower={metrics.ttc_pd_lower_pct}
                point={metrics.ttc_pd_point_pct}
                upper={metrics.ttc_pd_upper_pct}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-5 py-2.5 border-t border-border bg-surface/45 text-caption text-slate">
        <span>Live risk calculation from current Capital, Liquidity, IRRBB, and FX metrics</span>
        <span>
          Live as of {fmtTimestamp(rating.computedAt)} at {num(metrics.confidence_level) * 100}% confidence
        </span>
      </div>
    </div>
  );
}