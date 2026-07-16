'use client';

/**
 * Per-module freshness pill for a dashboard header. Reads the polled
 * bank-freshness view and shows, for one module:
 *   - green "Live · updated {relative}" when the live figure matches the last
 *     official run, or
 *   - amber "Data changed since last official run" plus a "Mint official run"
 *     action when the underlying data has moved since the last immutable run.
 *
 * Minting enqueues an official run and polls it to completion; on success the
 * freshness view re-fetches and the badge flips back to green.
 */

import { AlertTriangle, Loader2, RadioTower } from 'lucide-react';
import type { LiveModule } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { isApiError } from '@/lib/api/client';
import { useBankFreshness, useMintOfficialRun } from '@/lib/api/hooks';
import { fmtRelative } from '@/lib/api/values';

export default function FreshnessBadge({
  bankId,
  periodId,
  module,
  asOfDate,
}: {
  bankId: string | undefined;
  periodId: string | undefined;
  module: LiveModule;
  /** Period-end as-of date (YYYY-MM-DD) used when minting an official run. */
  asOfDate: string | undefined;
}) {
  const freshness = useBankFreshness(bankId, periodId);
  const mint = useMintOfficialRun(bankId);

  const row = freshness.data?.modules.find((m) => m.module === module);
  if (!row) return null;

  if (!row.isStale) {
    return (
      <div className="inline-flex items-center gap-2">
        <StatusPill tone="compliant">
          <RadioTower size={11} aria-hidden />
          Live
        </StatusPill>
        {row.computedAt && (
          <span className="text-caption text-slate whitespace-nowrap">
            updated {fmtRelative(row.computedAt)}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      className="inline-flex items-center gap-2 flex-wrap"
      title="Data changed since last official run"
    >
      <StatusPill tone="amber">
        <AlertTriangle size={11} aria-hidden />
        Changed
      </StatusPill>
      <span className="text-caption text-slate whitespace-nowrap hidden lg:inline">
        since last official run
      </span>
      <button
        type="button"
        disabled={!asOfDate || mint.isPending}
        onClick={() =>
          asOfDate &&
          mint.mutate({
            asOfDate,
            reason: `Minted official ${module} run from the ${module} dashboard freshness badge.`,
          })
        }
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-caption font-medium btn-primary disabled:opacity-60"
      >
        {mint.isPending ? (
          <Loader2 size={12} className="animate-spin" aria-hidden />
        ) : null}
        {mint.isPending ? 'Minting…' : 'Mint official run'}
      </button>
      {mint.isError && (
        <span className="text-caption text-critical">
          {isApiError(mint.error) ? mint.error.message : 'Mint failed.'}
        </span>
      )}
    </div>
  );
}
