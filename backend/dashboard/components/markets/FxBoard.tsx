'use client';

/**
 * FX board: one spot card per servable pair — latest arbitrated rate, a
 * trailing-history sparkline, day-over-day move, and source attribution.
 */

import type { FxRateViewRead } from '@aequoros/risk-service-api';
import Sparkline from '@/components/ui/Sparkline';
import DeltaBadge from '@/components/ui/DeltaBadge';
import { fmtDateUTC, num } from '@/lib/api/values';
import AttributionChip from './AttributionChip';

function sparkColor(first: number, last: number): string {
  if (last > first) return 'rgb(var(--warn))'; // quote weakening vs base
  if (last < first) return 'rgb(var(--ok))';
  return 'rgb(var(--accent))';
}

export default function FxBoard({ fxRates }: { fxRates: FxRateViewRead[] }) {
  return (
    <div
      className={`grid grid-cols-1 gap-4 ${fxRates.length > 1 ? 'sm:grid-cols-2' : ''}`}
    >
      {fxRates.map((fx) => {
        const series = fx.history.map((point) => num(point.rate));
        const previous = series.length > 1 ? series[series.length - 2] : null;
        const rate = num(fx.rate);
        const movePct =
          previous !== null && previous !== 0
            ? ((rate - previous) / previous) * 100
            : null;
        return (
          <div key={`${fx.base}${fx.quote}`} className="card px-4 py-3.5 flex flex-col gap-2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <p className="text-micro font-medium text-slate uppercase tracking-wider">
                {fx.base}/{fx.quote}{' '}
                <span className="normal-case tracking-normal">· {fx.rateType}</span>
              </p>
              <span className="text-caption text-slate font-mono">
                {fmtDateUTC(fx.asOfDate)}
              </span>
            </div>
            <div className="flex items-end justify-between gap-3">
              <span className="font-mono text-kpi text-navy tnum">
                {rate.toLocaleString('en-US', {
                  minimumFractionDigits: 4,
                  maximumFractionDigits: 4,
                })}
              </span>
              {series.length > 1 && (
                <Sparkline
                  data={series}
                  color={sparkColor(series[0], series[series.length - 1])}
                />
              )}
            </div>
            <div className="flex items-center justify-between gap-2">
              {movePct !== null ? (
                <DeltaBadge value={movePct} suffix="%" decimals={2} invert />
              ) : (
                <span className="text-caption text-slate">No prior observation</span>
              )}
              <AttributionChip attribution={fx.attribution} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
