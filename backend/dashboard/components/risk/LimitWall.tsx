'use client';

/**
 * The limit wall: payload-backed limits grouped by module, each rendered as
 * a bullet-style LimitBar with headroom, context, and a link into the owning
 * module's page.
 */

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import LimitBar from '@/components/ui/LimitBar';
import StatusPill from '@/components/ui/StatusPill';
import SectionCard from '@/components/ui/SectionCard';
import { fmtRelative } from '@/lib/api/values';
import {
  MODULE_HREFS,
  MODULE_LABELS,
  type LimitModule,
  type LimitRow,
} from './limits';

const MODULE_ORDER: LimitModule[] = ['capital', 'liquidity', 'irr', 'fx', 'ftp'];

function moduleTone(rows: LimitRow[]) {
  if (rows.some((row) => row.status === 'crit')) return 'breach' as const;
  if (rows.some((row) => row.status === 'warn')) return 'approaching' as const;
  return 'compliant' as const;
}

export default function LimitWall({
  rows,
  unavailableModules = [],
  showEmptyModules = false,
}: {
  rows: LimitRow[];
  /** Modules whose dashboard read failed — shown as unavailable, not compliant. */
  unavailableModules?: LimitModule[];
  /**
   * Render modules that contribute no rows (with an honest explanation) —
   * used on the unfiltered wall; filtered views hide empty modules.
   */
  showEmptyModules?: boolean;
}) {
  return (
    <div className="space-y-6">
      {MODULE_ORDER.map((module) => {
        const moduleRows = rows.filter((row) => row.module === module);
        const unavailable = unavailableModules.includes(module);
        if (moduleRows.length === 0 && !unavailable && !showEmptyModules) return null;
        return (
          <SectionCard
            key={module}
            title={
              <span className="inline-flex items-center gap-2.5">
                {MODULE_LABELS[module]}
                {moduleRows.length > 0 && <StatusPill tone={moduleTone(moduleRows)} />}
              </span>
            }
            actions={
              <Link
                href={MODULE_HREFS[module]}
                className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline whitespace-nowrap"
              >
                Open module
                <ArrowUpRight size={13} aria-hidden />
              </Link>
            }
            noPadding
          >
            {unavailable ? (
              <p className="px-5 py-4 text-body text-slate">
                Module dashboard unavailable — open the module page for details.
              </p>
            ) : moduleRows.length === 0 ? (
              <p className="px-5 py-4 text-body text-slate">
                {module === 'liquidity'
                  ? 'The liquidity payload exposes LCR/NSFR statuses without numeric thresholds, so no limit bars are rendered here — this page never invents a threshold. See the validation checks tab for its rule evaluations.'
                  : 'This module exposes no payload-backed limit thresholds for the period.'}
              </p>
            ) : (
              <ul className="divide-y divide-border-light">
                {moduleRows.map((row) => (
                  <li key={`${row.module}-${row.limit}`} className="px-5 py-4">
                    <LimitBar
                      label={
                        <Link
                          href={MODULE_HREFS[row.module]}
                          className="hover:text-action transition-colors"
                        >
                          {row.limit}
                        </Link>
                      }
                      value={row.value}
                      limit={row.threshold}
                      warnAt={row.warnAt}
                      direction={row.direction}
                      unit={row.unit}
                      meta={
                        <span className="whitespace-nowrap text-slate-light">
                          {row.detail}
                          {row.computedAt
                            ? ` · computed ${fmtRelative(row.computedAt)}`
                            : ''}
                        </span>
                      }
                    />
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        );
      })}
    </div>
  );
}
