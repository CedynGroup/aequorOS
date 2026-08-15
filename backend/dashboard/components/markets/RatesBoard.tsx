'use client';

/**
 * Reference-rates board: the desk-published policy, money-market, and
 * lending reference rates from the views indices, grouped by family. Every
 * card carries value, as-of date, and source attribution + freshness.
 *
 * Grouping matches on jurisdiction-neutral code SUFFIXES (".MPR",
 * ".TBILL.", ...) — the currency prefix comes from the data, never from
 * display code. Lending APRs are official regulator-published data, shown
 * distinctly from the bank's private overlays (spec §11b, §12).
 */

import type { IndexViewRead } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import AttributionChip from './AttributionChip';
import { MonoChip } from './chips';

export type RateGroupKey = 'policy' | 'money-market' | 'lending';

type RateGroup = {
  key: RateGroupKey;
  title: string;
  subtitle: string;
  matches: (code: string) => boolean;
};

// Order matters: the first matching group claims the code.
const RATE_GROUPS: RateGroup[] = [
  {
    key: 'policy',
    title: 'Policy & reference',
    subtitle: 'Central-bank policy anchor and the published reference rate',
    matches: (code) => code.endsWith('.MPR') || code.endsWith('.GRR'),
  },
  {
    key: 'money-market',
    title: 'Money market',
    subtitle: 'Interbank and bill auction rates',
    matches: (code) => code.includes('.INTERBANK') || code.includes('.TBILL.'),
  },
  {
    key: 'lending',
    title: 'Lending',
    subtitle:
      'Official regulator-published bank APRs and the derived lending base — not your private overlays',
    matches: (code) => code.includes('.APR') || code.includes('.BASE.'),
  },
];

/** Codes this board claims; everything else stays in the indices strip. */
export function isReferenceRateCode(code: string): boolean {
  return RATE_GROUPS.some((group) => group.matches(code));
}

/**
 * Reference rates publish as percent-valued index numbers (GHS.MPR = 15.0,
 * not 0.15) — unlike curve points, which are decimal fractions. Render the
 * stored value as-is; do NOT ×100 (that produced the 1500%/1023% defect).
 */
function fmtRateValue(value: string): string {
  return fmtPct(num(value), 2);
}

/** "GHS.TBILL.91.DISCOUNT" → "T-bill 91d discount"-style readable label. */
function rateLabel(code: string): string {
  const parts = code.split('.');
  // Drop the currency prefix; the code chip keeps the full identity.
  const body = parts.length > 1 ? parts.slice(1) : parts;
  return body
    .map((part) => (/^\d+$/.test(part) ? `${part}d` : labelize(part.toLowerCase())))
    .join(' · ');
}

function RateRow({ index }: { index: IndexViewRead }) {
  return (
    <tr className="border-t border-border-light hover:bg-surface/60">
      <td className="px-4 py-3">
        <span className="block font-medium text-navy">{rateLabel(index.indexCode)}</span>
        <span className="mt-1 inline-flex items-center gap-2"><MonoChip>{index.indexCode}</MonoChip>
          {index.scenario !== 'base' && <StatusPill tone="amber">{labelize(index.scenario)}</StatusPill>}
        </span>
      </td>
      <td className="px-4 py-3 text-right font-mono font-semibold text-navy tnum">{fmtRateValue(index.value)}</td>
      <td className="px-4 py-3 text-right text-caption font-mono text-slate">{fmtDateUTC(index.asOfDate)}</td>
      <td className="px-4 py-3 text-right"><AttributionChip attribution={index.attribution} className="justify-end" /></td>
    </tr>
  );
}

export default function RatesBoard({
  indices,
  groups: selectedGroups,
}: {
  indices: IndexViewRead[];
  groups?: RateGroupKey[];
}) {
  const claimed = new Set<string>();
  const groups = RATE_GROUPS.filter(
    (group) => selectedGroups === undefined || selectedGroups.includes(group.key)
  ).map((group) => {
    const members = indices.filter(
      (index) =>
        !claimed.has(`${index.indexCode}-${index.scenario}`) &&
        group.matches(index.indexCode)
    );
    members.forEach((index) => claimed.add(`${index.indexCode}-${index.scenario}`));
    return { group, members };
  }).filter(({ members }) => members.length > 0);

  if (groups.length === 0) return null;

  return (
    <div className="space-y-4">
      {groups.map(({ group, members }) => (
        <section key={group.key} className="overflow-x-auto border border-border rounded-lg bg-surface-raised">
          <div className="px-4 py-3 border-b border-border bg-surface/45">
            <h3 className="text-body font-semibold text-navy">{group.title}</h3>
            <p className="text-caption text-slate mt-0.5">{group.subtitle}</p>
          </div>
          <table className="w-full min-w-[37rem] text-body">
            <thead className="text-micro font-medium uppercase tracking-wider text-slate">
              <tr>
                <th className="px-4 py-2.5 text-left">Instrument</th>
                <th className="px-4 py-2.5 text-right">Rate</th>
                <th className="px-4 py-2.5 text-right">As of</th>
                <th className="px-4 py-2.5 text-right">Source</th>
              </tr>
            </thead>
            <tbody>
              {members.map((index) => (
                <RateRow key={`${index.indexCode}-${index.scenario}`} index={index} />
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
