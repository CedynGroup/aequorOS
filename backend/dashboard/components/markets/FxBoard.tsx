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
    <div className="overflow-x-auto border border-border rounded-lg bg-surface-raised">
      <table className="w-full min-w-[36rem] text-body">
        <thead className="bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
          <tr>
            <th className="px-4 py-2.5 text-left">Pair</th>
            <th className="px-4 py-2.5 text-right">Spot</th>
            <th className="px-4 py-2.5 text-right">1d move</th>
            <th className="px-4 py-2.5 text-right">History</th>
            <th className="px-4 py-2.5 text-right">As of</th>
            <th className="px-4 py-2.5 text-right">Source</th>
          </tr>
        </thead>
        <tbody>
          {fxRates.map((fx) => {
        const series = fx.history.map((point) => num(point.rate));
        const previous = series.length > 1 ? series[series.length - 2] : null;
        const rate = num(fx.rate);
        const movePct =
          previous !== null && previous !== 0
            ? ((rate - previous) / previous) * 100
            : null;
        return (
          <tr key={`${fx.base}${fx.quote}`} className="border-t border-border-light hover:bg-surface/60">
            <td className="px-4 py-3">
              <span className="font-mono font-medium text-navy">{fx.base}/{fx.quote}</span>
              <span className="ml-2 text-caption text-slate">{fx.rateType}</span>
            </td>
            <td className="px-4 py-3 text-right font-mono font-semibold text-navy tnum">
                {rate.toLocaleString('en-US', {
                  minimumFractionDigits: 4,
                  maximumFractionDigits: 4,
                })}
            </td>
            <td className="px-4 py-3 text-right">
              {movePct !== null ? (
                <DeltaBadge value={movePct} suffix="%" decimals={2} invert />
              ) : (
                <span className="text-caption text-slate">No prior observation</span>
              )}
            </td>
            <td className="px-4 py-3">
              {series.length > 1 && (
                <div className="ml-auto w-24">
                  <Sparkline data={series} color={sparkColor(series[0], series[series.length - 1])} />
                </div>
              )}
            </td>
            <td className="px-4 py-3 text-right text-caption font-mono text-slate">
              {fmtDateUTC(fx.asOfDate)}
            </td>
            <td className="px-4 py-3 text-right">
              <AttributionChip attribution={fx.attribution} className="justify-end" />
            </td>
          </tr>
        );
          })}
        </tbody>
      </table>
    </div>
  );
}
