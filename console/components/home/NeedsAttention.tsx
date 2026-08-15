'use client';

import Link from 'next/link';
import { ArrowUpRight, CheckCircle2 } from 'lucide-react';
import { Chip, EmptyState, SectionCard, StatusPill, type StatusTone } from '@/components/ui';
import type { OverviewNeedsAttentionItem } from '@/lib/api';

/** Severity → pill tone. Unknown severity renders neutral — never guess. */
function severityTone(severity: string): StatusTone {
  if (severity === 'crit') return 'critical';
  if (severity === 'warn') return 'amber';
  if (severity === 'ok') return 'success';
  return 'slate';
}

/** crit before warn before everything else; stable within a rank. */
const SEVERITY_RANK: Record<string, number> = { crit: 0, warn: 1, ok: 2 };

/**
 * The needs-attention queue: the `getOverview().needs_attention` feed rendered
 * as a severity-ranked list. Each entry is a BOUNDED per-category rollup (e.g.
 * "3 connections critical"), NOT a per-tenant row — items with an `href` deep
 * link to the owning surface (operations / tenants / desk).
 */
export function NeedsAttention({ items }: { items: OverviewNeedsAttentionItem[] }) {
  const sorted = [...items].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9),
  );

  return (
    <SectionCard
      title="Needs attention"
      subtitle="Bounded per-category rollups across the fleet — not per-tenant."
      actions={items.length > 0 ? <Chip tone="neutral">{items.length}</Chip> : undefined}
      noPadding
    >
      {sorted.length === 0 ? (
        <EmptyState
          Icon={CheckCircle2}
          title="Nothing needs attention"
          description="No cross-tenant alerts in the current rollup window."
        />
      ) : (
        <ul className="divide-y divide-border-light">
          {sorted.map((item, i) => {
            const row = (
              <div className="flex items-center gap-3 px-5 py-3">
                <StatusPill tone={severityTone(item.severity)} className="shrink-0">
                  {item.severity.toUpperCase()}
                </StatusPill>
                <Chip tone="neutral">{item.kind.replace(/_/g, ' ')}</Chip>
                <p
                  className="min-w-0 flex-1 truncate text-body text-navy"
                  title={item.summary}
                >
                  {item.summary}
                </p>
                {item.href && (
                  <ArrowUpRight
                    size={14}
                    className="shrink-0 text-slate-light transition-colors group-hover:text-action"
                    aria-hidden
                  />
                )}
              </div>
            );
            return (
              <li key={`${item.kind}-${i}`}>
                {item.href ? (
                  <Link href={item.href} className="group block transition-colors hover:bg-surface">
                    {row}
                  </Link>
                ) : (
                  row
                )}
              </li>
            );
          })}
        </ul>
      )}
    </SectionCard>
  );
}
