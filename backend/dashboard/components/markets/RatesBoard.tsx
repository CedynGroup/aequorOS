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

type RateGroup = {
  key: string;
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

/** Reference rates are decimal fractions; render as percentages. */
function fmtRateValue(value: string): string {
  return fmtPct(num(value) * 100, 2);
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

function RateCard({ index }: { index: IndexViewRead }) {
  return (
    <div className="card px-4 py-3.5 flex flex-col gap-2 min-w-0">
      <div className="flex items-center justify-between gap-2">
        <p className="text-micro font-medium text-slate uppercase tracking-wider truncate">
          {rateLabel(index.indexCode)}
        </p>
        {index.scenario !== 'base' && (
          <StatusPill tone="amber">{labelize(index.scenario)}</StatusPill>
        )}
      </div>
      <div className="flex items-end justify-between gap-3">
        <span className="font-mono text-kpi text-navy tnum">
          {fmtRateValue(index.value)}
        </span>
        <MonoChip>{index.indexCode}</MonoChip>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-slate font-mono">
          {fmtDateUTC(index.asOfDate)}
        </span>
        <AttributionChip attribution={index.attribution} />
      </div>
    </div>
  );
}

export default function RatesBoard({ indices }: { indices: IndexViewRead[] }) {
  const claimed = new Set<string>();
  const groups = RATE_GROUPS.map((group) => {
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
    <div className="space-y-5">
      {groups.map(({ group, members }) => (
        <div key={group.key} className="space-y-2">
          <div>
            <h3 className="text-body font-semibold text-navy">{group.title}</h3>
            <p className="text-caption text-slate">{group.subtitle}</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {members.map((index) => (
              <RateCard key={`${index.indexCode}-${index.scenario}`} index={index} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
