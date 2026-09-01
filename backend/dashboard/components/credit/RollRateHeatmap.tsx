'use client';

/**
 * DPD bucket roll rates as a CSS-table heatmap (recharts has no heatmap
 * primitive; faking one with scatter would be worse). Forward rolls tint
 * critical scaled by rate; cures tint success; empty cells stay plain —
 * a bucket with no opening exposure has NO rate, never 0.0%.
 */

import type { RollRateCellRead } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';

const BUCKETS = ['current', '1_29', '30_59', '60_89', '90_179', '180_359', '360_plus'];
const LABELS: Record<string, string> = {
  current: 'Current',
  '1_29': '1–29',
  '30_59': '30–59',
  '60_89': '60–89',
  '90_179': '90–179',
  '180_359': '180–359',
  '360_plus': '360+',
};

function tone(fromIndex: number, toIndex: number, rate: number): string {
  if (fromIndex === toIndex) return 'bg-surface-alt';
  const alpha = Math.min(0.15 + (rate / 100) * 0.5, 0.65);
  return toIndex > fromIndex
    ? `rgba(var(--crit) / ${alpha})`
    : `rgba(var(--ok) / ${alpha})`;
}

export default function RollRateHeatmap({ rollRates }: { rollRates: RollRateCellRead[] }) {
  const cell = (from: string, to: string) =>
    rollRates.find((c) => c.fromBucket === from && c.toBucket === to);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-caption">
        <thead>
          <tr>
            <th className="px-3 py-2 text-left text-micro uppercase tracking-wider text-slate">
              DPD ↓ / → days
            </th>
            {BUCKETS.map((bucket) => (
              <th
                key={bucket}
                className="px-3 py-2 text-right text-micro uppercase tracking-wider text-slate"
              >
                {LABELS[bucket]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {BUCKETS.map((from, fromIndex) => (
            <tr key={from} className="border-t border-border-light">
              <td className="px-3 py-2 text-navy">{LABELS[from]}</td>
              {BUCKETS.map((to, toIndex) => {
                const value = cell(from, to);
                if (!value) {
                  return (
                    <td key={to} className="px-3 py-2 text-right text-slate-light">
                      —
                    </td>
                  );
                }
                const rate = num(value.ratePct);
                const isDiagonal = fromIndex === toIndex;
                return (
                  <td
                    key={to}
                    className={`px-3 py-2 text-right font-mono tnum text-navy ${
                      isDiagonal ? 'bg-surface-alt' : ''
                    }`}
                    style={
                      isDiagonal
                        ? undefined
                        : { backgroundColor: tone(fromIndex, toIndex, rate) }
                    }
                    title={`${value.loanCount} loans`}
                  >
                    {rate.toFixed(1)}%
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
