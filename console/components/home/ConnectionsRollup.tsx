'use client';

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import { SectionCard } from '@/components/ui';
import type { OverviewResponse } from '@/lib/api';

const CELLS = [
  { key: 'ok', label: 'Healthy', text: 'text-success', bg: 'bg-success-light' },
  { key: 'warn', label: 'Warning', text: 'text-warning', bg: 'bg-warning-light' },
  { key: 'crit', label: 'Critical', text: 'text-critical', bg: 'bg-critical-light' },
] as const;

/**
 * Connection-health rollup: the fleet-wide ok/warn/crit tallies from
 * `getOverview().connections`, linking through to the full data-engine list on
 * Operations.
 */
export function ConnectionsRollup({
  connections,
}: {
  connections: OverviewResponse['connections'];
}) {
  const total = connections.ok + connections.warn + connections.crit;

  return (
    <SectionCard
      title="Connections"
      subtitle="Data-engine connection health, fleet-wide."
      actions={
        <Link
          href="/operations"
          className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
        >
          Operations
          <ArrowUpRight size={13} aria-hidden />
        </Link>
      }
    >
      {total === 0 ? (
        <p className="text-caption text-slate">No data-engine connections configured yet.</p>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {CELLS.map((c) => (
            <div key={c.key} className={`rounded-md px-3 py-2.5 ${c.bg}`}>
              <p className={`font-mono text-h2 tnum ${c.text}`}>{connections[c.key]}</p>
              <p className="mt-0.5 text-micro font-medium uppercase tracking-wider text-slate">
                {c.label}
              </p>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}
