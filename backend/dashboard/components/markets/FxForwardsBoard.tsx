'use client';

import type { FxRateViewRead } from '@aequoros/risk-service-api';
import { fmtDateUTC, num } from '@/lib/api/values';
import AttributionChip from './AttributionChip';

function tenorLabel(months: number | null | undefined): string {
  if (months === null || months === undefined) return 'Spot';
  if (months < 12 || months % 12 !== 0) return `${months}M`;
  return `${months / 12}Y`;
}

/** Canonical vendor/desk FX forward outrights: market-implied forecasts, not a model estimate. */
export default function FxForwardsBoard({
  forwards,
  spots,
}: {
  forwards: FxRateViewRead[];
  spots: FxRateViewRead[];
}) {
  const spotByPair = new Map(
    spots.map((spot) => [`${spot.base}/${spot.quote}`, num(spot.rate)])
  );
  const sorted = [...forwards].sort(
    (left, right) =>
      `${left.base}/${left.quote}`.localeCompare(`${right.base}/${right.quote}`) ||
      (left.tenorMonths ?? 0) - (right.tenorMonths ?? 0)
  );

  if (sorted.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface-raised px-5 py-6 text-center">
        <p className="text-body font-medium text-navy">No market-implied FX forwards are available</p>
        <p className="mt-1 text-caption text-slate">
          Forward forecasts appear here after a licensed source or canonical upload supplies an FX forward tenor.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
      <table className="w-full min-w-[42rem] text-body">
        <thead className="bg-surface/60 text-micro font-medium uppercase tracking-wider text-slate">
          <tr>
            <th className="px-4 py-2.5 text-left">Pair</th>
            <th className="px-3 py-2.5 text-right">Tenor</th>
            <th className="px-3 py-2.5 text-right">Forward outright</th>
            <th className="px-3 py-2.5 text-right">Forward points</th>
            <th className="px-3 py-2.5 text-right">As of</th>
            <th className="px-4 py-2.5 text-right">Source</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((forward) => {
            const pair = `${forward.base}/${forward.quote}`;
            const outright = num(forward.rate);
            const spot = spotByPair.get(pair);
            const points = spot === undefined ? null : outright - spot;
            return (
              <tr
                key={`${pair}-${forward.tenorMonths}-${forward.asOfDate.toISOString()}`}
                className="border-t border-border-light hover:bg-surface/60"
              >
                <td className="px-4 py-3 font-mono font-medium text-navy">{pair}</td>
                <td className="px-3 py-3 text-right font-mono text-caption text-navy">
                  {tenorLabel(forward.tenorMonths)}
                </td>
                <td className="px-3 py-3 text-right font-mono font-semibold text-navy tnum">
                  {outright.toLocaleString('en-US', {
                    minimumFractionDigits: 4,
                    maximumFractionDigits: 4,
                  })}
                </td>
                <td className="px-3 py-3 text-right font-mono text-caption tnum text-slate">
                  {points === null ? '—' : `${points >= 0 ? '+' : ''}${points.toFixed(4)}`}
                </td>
                <td className="px-3 py-3 text-right font-mono text-caption text-slate">
                  {fmtDateUTC(forward.asOfDate)}
                </td>
                <td className="px-4 py-3 text-right">
                  <AttributionChip attribution={forward.attribution} className="justify-end" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
