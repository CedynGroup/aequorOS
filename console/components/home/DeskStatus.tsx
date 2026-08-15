'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { Chip, SectionCard } from '@/components/ui';
import type { OverviewResponse } from '@/lib/api';

const ROWS = [
  {
    key: 'pending_determinations',
    label: 'Determinations',
    hint: 'Weekly rates COB drafts awaiting review or approval.',
    href: '/desk/determinations',
  },
  {
    key: 'pending_curve_determinations',
    label: 'Curve determinations',
    hint: 'Per-COB curve maker-checker drafts.',
    href: '/desk/curves',
  },
  {
    key: 'pending_oe_assessments',
    label: 'Operating environment',
    hint: 'BICRA assessments awaiting review or approval.',
    href: '/desk/operating-environment',
  },
] as const;

/**
 * Desk approvals rail: the three maker-checker backlogs from
 * `getOverview().desk`, each deep-linking to its desk surface.
 */
export function DeskStatus({ desk }: { desk: OverviewResponse['desk'] }) {
  return (
    <SectionCard
      title="Desk approvals"
      subtitle="Maker-checker queue across the research desk."
      noPadding
    >
      <ul className="divide-y divide-border-light">
        {ROWS.map((r) => {
          const count = desk[r.key];
          return (
            <li key={r.key}>
              <Link
                href={r.href}
                className="group flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface"
              >
                <div className="min-w-0">
                  <p className="text-body text-navy">{r.label}</p>
                  <p className="text-caption text-slate">{r.hint}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <span className="font-mono text-h3 text-navy tnum">{count}</span>
                  {count > 0 ? <Chip tone="warn">pending</Chip> : <Chip tone="ok">clear</Chip>}
                  <ChevronRight
                    size={14}
                    className="text-slate-light transition-colors group-hover:text-action"
                    aria-hidden
                  />
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}
