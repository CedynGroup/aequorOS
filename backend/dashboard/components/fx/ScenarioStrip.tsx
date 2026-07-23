import type { FxScenarioNopRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

export const FX_SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  mild_depreciation: 'Mild depreciation',
  severe_depreciation: 'Severe depreciation',
  cedi_crisis: 'Cedi crisis',
};

export function fxScenarioLabel(code: string): string {
  return FX_SCENARIO_LABELS[code] ?? code;
}

/**
 * Compact strip of the cedi depreciation scenarios: shock applied, resulting
 * aggregate NOP, and the aggregate-limit verdict for each.
 */
export default function ScenarioStrip({
  scenarios,
  aggregateLimitPct,
}: {
  scenarios: FxScenarioNopRead[];
  aggregateLimitPct: number;
}) {
  if (!scenarios.length) {
    return (
      <p className="text-body text-slate">
        No depreciation scenarios computed for this period.
      </p>
    );
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      {scenarios.map((s) => {
        const shock = num(s.shockPct);
        const pct = num(s.nopPctTier1);
        return (
          <div
            key={s.scenarioCode}
            className="rounded-md border border-border-light bg-surface px-4 py-3 flex flex-col gap-1.5 min-w-0"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-caption font-medium text-navy truncate">
                {fxScenarioLabel(s.scenarioCode)}
              </p>
              <StatusPill tone={s.withinAggregateLimit ? 'compliant' : 'breach'}>
                {s.withinAggregateLimit ? 'Within' : 'Breach'}
              </StatusPill>
            </div>
            <p className="text-micro text-slate uppercase tracking-wider">
              {shock === 0 ? 'No shock' : `Cedi depreciation ${fmtPct(shock, 1)}`}
            </p>
            <p className="font-mono text-h2 text-navy tnum">
              {fmtPct(pct, 2)}
              <span className="text-caption text-slate font-sans"> of Tier 1</span>
            </p>
            <p className="text-caption text-slate">
              NOP {fmtCurrency(num(s.nopGhs))} · limit {fmtPct(aggregateLimitPct, 0)}
            </p>
          </div>
        );
      })}
    </div>
  );
}
