'use client';

/**
 * The Notice-mandated monthly state-migration matrix (3×3 + entry/exit legs)
 * as a CSS table — cell fills need per-cell styling recharts cannot express.
 * Deterioration (below the diagonal) tints critical, cures tint success.
 */

import type { MigrationCellRead } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtInt } from '@/lib/format';

const STATES = ['performing', 'performing_restructured', 'npl'];
const LABELS: Record<string, string> = {
  performing: 'Performing',
  performing_restructured: 'Performing restructured',
  npl: 'Non-performing',
};

function cellTone(fromIndex: number, toIndex: number): string {
  if (fromIndex === toIndex) return 'bg-surface-alt';
  return toIndex > fromIndex ? 'bg-critical-light/50' : 'bg-success-light/50';
}

export default function MigrationMatrix({
  matrix,
  entries,
  exits,
}: {
  matrix: MigrationCellRead[];
  entries: MigrationCellRead[];
  exits: MigrationCellRead[];
}) {
  const cell = (from: string, to: string) =>
    matrix.find((c) => c.fromState === from && c.toState === to);
  const exit = (from: string) => exits.find((c) => c.fromState === from);
  const entry = (to: string) => entries.find((c) => c.toState === to);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-caption">
        <thead>
          <tr>
            <th className="px-4 py-2 text-left text-micro uppercase tracking-wider text-slate">
              From ↓ / To →
            </th>
            {STATES.map((state) => (
              <th
                key={state}
                className="px-4 py-2 text-right text-micro uppercase tracking-wider text-slate"
              >
                {LABELS[state]}
              </th>
            ))}
            <th className="px-4 py-2 text-right text-micro uppercase tracking-wider text-slate">
              Departed
            </th>
          </tr>
        </thead>
        <tbody>
          {STATES.map((from, fromIndex) => (
            <tr key={from} className="border-t border-border-light">
              <td className="px-4 py-2 text-navy">{LABELS[from]}</td>
              {STATES.map((to, toIndex) => {
                const value = cell(from, to);
                return (
                  <td
                    key={to}
                    className={`px-4 py-2 text-right font-mono tnum ${cellTone(fromIndex, toIndex)}`}
                  >
                    {value ? (
                      <>
                        <span className="text-navy">{fmtCurrency(num(value.exposureGhs))}</span>
                        <span className="block text-micro text-slate">
                          {fmtInt(value.loanCount)} loans
                        </span>
                      </>
                    ) : (
                      <span className="text-slate-light">—</span>
                    )}
                  </td>
                );
              })}
              <td className="px-4 py-2 text-right font-mono tnum">
                {exit(from) ? (
                  <>
                    <span className="text-navy">{fmtCurrency(num(exit(from)!.exposureGhs))}</span>
                    <span className="block text-micro text-slate">
                      {fmtInt(exit(from)!.loanCount)} loans
                    </span>
                  </>
                ) : (
                  <span className="text-slate-light">—</span>
                )}
              </td>
            </tr>
          ))}
          <tr className="border-t border-border">
            <td className="px-4 py-2 text-navy">New in month</td>
            {STATES.map((to) => (
              <td key={to} className="px-4 py-2 text-right font-mono tnum">
                {entry(to) ? (
                  <>
                    <span className="text-navy">{fmtCurrency(num(entry(to)!.exposureGhs))}</span>
                    <span className="block text-micro text-slate">
                      {fmtInt(entry(to)!.loanCount)} loans
                    </span>
                  </>
                ) : (
                  <span className="text-slate-light">—</span>
                )}
              </td>
            ))}
            <td className="px-4 py-2" />
          </tr>
        </tbody>
      </table>
    </div>
  );
}
