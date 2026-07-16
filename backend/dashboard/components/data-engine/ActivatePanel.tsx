'use client';

/**
 * Data flow status + advanced pipeline actions.
 *
 * The normal path is automatic: uploads are ingested, derived, and recomputed
 * in the background, so this panel leads with a live status line (last
 * recompute time + whether a job is running) rather than a run button.
 *
 * The two explicit actions are demoted to an "Advanced" area:
 *   - "Recompute now"  → POST /refresh (live tier: derive + recompute live
 *     metrics/findings, no immutable run minted).
 *   - "Mint official run for filing" → POST /official-runs (immutable tier:
 *     the auditable 22-scenario + forecast runs).
 *
 * Both enqueue a job, poll it to completion, and report the outcome inline.
 */

import { useMemo, useState } from 'react';
import { CheckCircle2, ChevronDown, Loader2, RefreshCw, Stamp } from 'lucide-react';
import type { JobRead } from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import StatusPill from '@/components/ui/StatusPill';
import { isApiError } from '@/lib/api/client';
import {
  useLiveSummary,
  useMintOfficialRun,
  useRefreshBankData,
} from '@/lib/api/hooks';
import { useIngestionBatches } from '@/lib/api/ingestion';
import { fmtRelative } from '@/lib/api/values';
import { formatDate } from './shared';

function progressCount(job: JobRead | undefined, key: string): number | null {
  const value = job?.progress?.[key];
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === 'object') return Object.keys(value).length;
  return null;
}

export default function ActivatePanel() {
  const { bank, period } = useBankContext();
  const batchesQuery = useIngestionBatches(bank?.id);
  const liveSummary = useLiveSummary(bank?.id);
  const refresh = useRefreshBankData(bank?.id);
  const mint = useMintOfficialRun(bank?.id);

  const [advancedOpen, setAdvancedOpen] = useState(false);

  const latestBatchAsOf = useMemo(() => {
    const dates = (batchesQuery.data?.batches ?? [])
      .filter((batch) => batch.status.startsWith('accepted'))
      .map((batch) => formatDate(batch.asOfDate));
    return dates.sort().at(-1);
  }, [batchesQuery.data]);

  const periodAsOf = period ? formatDate(period.periodEnd) : undefined;
  const [asOfDate, setAsOfDate] = useState<string>('');
  const effectiveAsOf = asOfDate || latestBatchAsOf || periodAsOf || '';

  const computedAt = liveSummary.data?.computedAt ?? null;
  const isStale = liveSummary.data?.isStale ?? false;
  const running = refresh.isPending || mint.isPending;

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Data flow</h2>
        <p className="mt-1 text-body text-slate leading-relaxed">
          Your data flows automatically. Uploads are ingested, derived, and
          recalculated in the background — every module dashboard reflects the
          latest data without a manual run.
        </p>
      </div>

      {/* Live status line */}
      <div className="rounded border border-border-light bg-surface/60 px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        {running ? (
          <StatusPill tone="action">
            <Loader2 size={11} className="animate-spin" aria-hidden />
            Recomputing…
          </StatusPill>
        ) : isStale ? (
          <StatusPill tone="amber">Changed since last official run</StatusPill>
        ) : (
          <StatusPill tone="compliant">Up to date</StatusPill>
        )}
        <span className="text-body text-navy">
          Last recompute{' '}
          <span className="font-medium">
            {computedAt ? fmtRelative(computedAt) : 'not yet'}
          </span>
          {liveSummary.data?.periodLabel && (
            <span className="text-slate">
              {' '}
              · period{' '}
              <span className="font-mono text-navy">
                {liveSummary.data.periodLabel}
              </span>
            </span>
          )}
        </span>
      </div>

      {/* Advanced actions — the manual pipeline controls, demoted */}
      <div className="border-t border-border-light pt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          className="inline-flex items-center gap-1.5 text-caption font-medium uppercase tracking-wider text-slate hover:text-navy"
        >
          <ChevronDown
            size={14}
            aria-hidden
            className={`transition-transform ${advancedOpen ? 'rotate-180' : ''}`}
          />
          Advanced actions
        </button>

        {advancedOpen && (
          <div className="mt-4 space-y-4">
            <div>
              <label className="block text-caption font-medium text-slate mb-1">
                As-of date
              </label>
              <input
                type="date"
                value={effectiveAsOf}
                onChange={(event) => setAsOfDate(event.target.value)}
                className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
              />
              <p className="mt-1 text-micro text-slate">
                Defaults to the latest accepted upload. Both actions run for this
                reporting date.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              {/* Recompute now → /refresh */}
              <div className="rounded border border-border-light p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <RefreshCw size={15} className="text-action" aria-hidden />
                  <h3 className="text-body font-medium text-navy">Recompute now</h3>
                </div>
                <p className="text-caption text-slate leading-relaxed">
                  Re-derive facts and refresh live metrics and alerts. Does not
                  mint an immutable regulatory run.
                </p>
                <button
                  type="button"
                  disabled={!bank || !effectiveAsOf || running}
                  onClick={() =>
                    refresh.mutate({
                      asOfDate: effectiveAsOf,
                      reason: 'Recompute now from the Data Engine console.',
                    })
                  }
                  className="inline-flex items-center gap-2 px-3 py-2 rounded text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {refresh.isPending && (
                    <Loader2 size={14} className="animate-spin" aria-hidden />
                  )}
                  {refresh.isPending ? 'Recomputing…' : 'Recompute now'}
                </button>
                <ActionResult
                  isError={refresh.isError}
                  error={refresh.error}
                  job={refresh.data}
                  successLabel={(job) => {
                    const ok = progressCount(job, 'modules_ok');
                    const failed = progressCount(job, 'modules_failed') ?? 0;
                    return `Recomputed${ok !== null ? ` ${ok} modules` : ''}${
                      failed > 0 ? ` · ${failed} failed` : ''
                    }`;
                  }}
                />
              </div>

              {/* Mint official run → /official-runs */}
              <div className="rounded border border-border-light p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Stamp size={15} className="text-navy" aria-hidden />
                  <h3 className="text-body font-medium text-navy">
                    Mint official run for filing
                  </h3>
                </div>
                <p className="text-caption text-slate leading-relaxed">
                  Create the immutable, auditable regulatory runs (22 scenarios +
                  forecast) for this reporting date.
                </p>
                <button
                  type="button"
                  disabled={!bank || !effectiveAsOf || running}
                  onClick={() =>
                    mint.mutate({
                      asOfDate: effectiveAsOf,
                      reason: 'Minted official run for filing from the Data Engine console.',
                    })
                  }
                  className="inline-flex items-center gap-2 px-3 py-2 rounded text-caption font-medium bg-navy text-white hover:bg-navy-700 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {mint.isPending && (
                    <Loader2 size={14} className="animate-spin" aria-hidden />
                  )}
                  {mint.isPending ? 'Minting…' : 'Mint official run'}
                </button>
                <ActionResult
                  isError={mint.isError}
                  error={mint.error}
                  job={mint.data}
                  successLabel={(job) => {
                    const count = progressCount(job, 'modules');
                    return `Official run minted${
                      count !== null ? ` · ${count} modules` : ''
                    }`;
                  }}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function ActionResult({
  isError,
  error,
  job,
  successLabel,
}: {
  isError: boolean;
  error: unknown;
  job: JobRead | undefined;
  successLabel: (job: JobRead) => string;
}) {
  if (isError) {
    return (
      <p className="text-caption text-critical">
        {isApiError(error) ? error.message : 'The job failed.'}
      </p>
    );
  }
  if (job && job.status === 'succeeded') {
    return (
      <p className="inline-flex items-center gap-1.5 text-caption text-success">
        <CheckCircle2 size={13} aria-hidden />
        {successLabel(job)}
      </p>
    );
  }
  return null;
}
