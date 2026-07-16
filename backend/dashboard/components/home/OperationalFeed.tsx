'use client';

/**
 * Operational feed — recent ingestion batches and data activations merged
 * into one compact timeline with status dots, newest first. Reads the Data
 * Engine hooks only; every row links back to the Data Engine console.
 */

import Link from 'next/link';
import { ArrowRight, DatabaseZap } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useDataActivations, useIngestionBatches } from '@/lib/api/ingestion';
import { fmtRelative, labelize } from '@/lib/api/values';

type FeedEvent = {
  key: string;
  at: Date;
  dotClass: string;
  title: string;
  meta: string;
};

const FEED_LIMIT = 8;

function batchDotClass(status: string): string {
  switch (status) {
    case 'accepted':
      return 'bg-success';
    case 'accepted_with_warnings':
      return 'bg-warning';
    case 'rejected':
    case 'failed':
      return 'bg-critical';
    default:
      return 'bg-slate-light';
  }
}

export default function OperationalFeed({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const batches = useIngestionBatches(bankId);
  const activations = useDataActivations(bankId);

  const isLoading = batches.isLoading || activations.isLoading;

  const events: FeedEvent[] = [
    ...(batches.data?.batches ?? []).map((b): FeedEvent => {
      const counts = [
        `${b.recordsAccepted} accepted`,
        b.recordsWarning > 0 ? `${b.recordsWarning} warnings` : null,
        b.recordsError > 0 ? `${b.recordsError} errors` : null,
      ]
        .filter(Boolean)
        .join(' · ');
      return {
        key: `batch-${b.id}`,
        at: b.completedAt ?? b.startedAt ?? b.createdAt,
        dotClass: batchDotClass(b.status),
        title: `${labelize(b.sourceSystem.toLowerCase())} ingestion · ${labelize(b.status)}`,
        meta: counts,
      };
    }),
    ...(activations.data?.activations ?? []).map((a, idx): FeedEvent => {
      const parts = [
        a.periodLabel ? `period ${a.periodLabel}` : null,
        typeof a.factsCreated === 'number' ? `${a.factsCreated} facts` : null,
        typeof a.modulesSucceeded === 'number'
          ? `${a.modulesSucceeded} modules ok`
          : null,
        typeof a.modulesFailed === 'number' && a.modulesFailed > 0
          ? `${a.modulesFailed} failed`
          : null,
      ]
        .filter(Boolean)
        .join(' · ');
      const failed = typeof a.modulesFailed === 'number' && a.modulesFailed > 0;
      return {
        key: `activation-${a.activatedAt.getTime()}-${idx}`,
        at: a.activatedAt,
        dotClass: failed ? 'bg-warning' : 'bg-action',
        title: 'Data activation',
        meta: parts,
      };
    }),
  ]
    .sort((a, b) => b.at.getTime() - a.at.getTime())
    .slice(0, FEED_LIMIT);

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          <DatabaseZap size={15} className="text-action" aria-hidden />
          Operational feed
        </span>
      }
      subtitle="Recent ingestion batches and data activations"
      actions={
        <Link
          href="/data-engine"
          className="text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus rounded"
        >
          Data Engine <ArrowRight size={12} aria-hidden />
        </Link>
      }
      noPadding
      footer={
        <span>
          {batches.data?.batches.length ?? 0} batches ·{' '}
          {activations.data?.activations.length ?? 0} activations on record
        </span>
      }
    >
      {isLoading ? (
        <div className="px-5 py-4 space-y-3" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <SkeletonLine key={i} width={`${85 - i * 10}%`} height={12} />
          ))}
        </div>
      ) : events.length === 0 ? (
        <p className="px-5 py-5 text-body text-slate leading-relaxed">
          No ingestion activity yet — upload source files or push records via
          the API to start the pipeline.
        </p>
      ) : (
        <ul className="divide-y divide-border-light">
          {events.map((event) => (
            <li key={event.key} className="px-5 py-2.5 flex items-center gap-3">
              <span
                aria-hidden
                className={`w-2 h-2 rounded-full shrink-0 ${event.dotClass}`}
              />
              <div className="min-w-0 flex-1">
                <p className="text-body text-navy truncate">{event.title}</p>
                {event.meta && (
                  <p className="text-caption text-slate truncate">
                    {event.meta}
                  </p>
                )}
              </div>
              <span className="text-caption text-slate whitespace-nowrap shrink-0 font-mono tnum">
                {fmtRelative(event.at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
