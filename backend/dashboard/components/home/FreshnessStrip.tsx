'use client';

/**
 * Freshness strip — per-module live-vs-official-run hash state for the
 * effective period. A module is stale when its live input hash differs from
 * the last official (filing) run's hash, or no official run exists yet; the
 * strip then points to the Data Engine to mint one.
 */

import Link from 'next/link';
import { ArrowRight, GitCommitHorizontal } from 'lucide-react';
import type { BankReportingPeriodRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useBankFreshness } from '@/lib/api/hooks';
import { fmtRelative, shortId } from '@/lib/api/values';
import { LIVE_MODULE_LABELS } from '@/components/live/moduleDisplay';

export default function FreshnessStrip({
  bankId,
  period,
}: {
  bankId: string | undefined;
  period: BankReportingPeriodRead;
}) {
  const freshness = useBankFreshness(bankId, period.id);
  const modules = freshness.data?.modules ?? [];
  const staleCount = modules.filter((m) => m.isStale).length;

  return (
    <SectionCard
      title="Run freshness"
      subtitle="Live input hash vs last official (filing) run, per module"
      actions={
        staleCount > 0 ? (
          <Link
            href="/data-engine"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium btn-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            Mint official run
            <ArrowRight size={12} aria-hidden />
          </Link>
        ) : undefined
      }
      noPadding
      footer={
        <span>
          Period {freshness.data?.periodLabel ?? period.label} ·{' '}
          {staleCount > 0
            ? `${staleCount} module${staleCount === 1 ? '' : 's'} ahead of the last official run`
            : 'all modules match their official runs'}
        </span>
      }
    >
      {freshness.isLoading ? (
        <div
          className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-px bg-border-light"
          aria-busy="true"
        >
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="bg-surface-raised px-3 py-3 space-y-2">
              <SkeletonLine width="60%" height={9} />
              <SkeletonLine width="80%" height={9} />
            </div>
          ))}
        </div>
      ) : modules.length === 0 ? (
        <p className="px-5 py-5 text-body text-slate leading-relaxed">
          No freshness data for this period yet — the pipeline populates it on
          the first refresh.
        </p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-px bg-border-light">
          {modules.map((m) => (
            <div key={m.module} className="bg-surface-raised px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-micro uppercase tracking-wider text-slate truncate">
                  {LIVE_MODULE_LABELS[m.module]}
                </p>
                <StatusPill
                  tone={m.isStale ? 'amber' : 'compliant'}
                  className="shrink-0"
                >
                  {m.isStale ? 'Changed' : 'Live'}
                </StatusPill>
              </div>
              <p className="mt-1.5 text-micro font-mono text-slate truncate tnum">
                <GitCommitHorizontal
                  size={10}
                  className="inline-block mr-1 align-[-1px]"
                  aria-hidden
                />
                {m.liveHash ? shortId(m.liveHash, 8) : '—'}
                <span className="text-slate-light"> / </span>
                {m.officialRunHash ? shortId(m.officialRunHash, 8) : 'no run'}
              </p>
              <p className="mt-0.5 text-micro text-slate truncate">
                {m.computedAt
                  ? `live ${fmtRelative(m.computedAt)}`
                  : 'not computed'}
                {m.officialRunAt
                  ? ` · official ${fmtRelative(m.officialRunAt)}`
                  : ''}
              </p>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}
