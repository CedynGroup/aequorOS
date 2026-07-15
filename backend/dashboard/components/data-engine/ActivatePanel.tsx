'use client';

/**
 * Activate: derive the six modules' financial facts from the ingested
 * canonical data for one as-of date, then recompute every module for the new
 * reporting period. Per-group derivation results and per-module run outcomes
 * are shown honestly — including the plug warnings and any module that could
 * not run on the uploaded book.
 */

import { useMemo, useState } from 'react';
import { Loader2, Zap } from 'lucide-react';
import type {
  ActivationGroupRead,
  ActivationRunRead,
} from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import StatusPill from '@/components/ui/StatusPill';
import { isApiError } from '@/lib/api/client';
import { useActivateBankData, useIngestionBatches } from '@/lib/api/ingestion';
import { formatDate } from './shared';

const MODULE_LABELS: Record<ActivationRunRead['module'], string> = {
  liquidity: 'Liquidity (LCR/NSFR)',
  capital: 'Capital (Basel CAR)',
  irr: 'Interest Rate Risk',
  fx: 'FX Risk',
  ftp: 'Transfer Pricing',
  forecast: 'Balance Sheet Forecast',
};

function runTone(status: ActivationRunRead['status']) {
  if (status === 'succeeded') return 'success' as const;
  if (status === 'partial') return 'amber' as const;
  return 'critical' as const;
}

function GroupChip({ group }: { group: ActivationGroupRead }) {
  const derived = group.status === 'derived';
  const hasWarnings = group.warnings.length > 0;
  return (
    <div className="bg-white px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption font-mono text-navy truncate" title={group.group}>
          {group.group}
        </p>
        <StatusPill tone={derived ? (hasWarnings ? 'amber' : 'success') : 'slate'}>
          {derived ? `${group.rows} rows` : 'skipped'}
        </StatusPill>
      </div>
      {group.note && <p className="mt-1 text-micro text-slate">{group.note}</p>}
    </div>
  );
}

export default function ActivatePanel() {
  const { bank } = useBankContext();
  const batchesQuery = useIngestionBatches(bank?.id);
  const activate = useActivateBankData(bank?.id);

  const latestBatchAsOf = useMemo(() => {
    const dates = (batchesQuery.data?.batches ?? [])
      .filter((batch) => batch.status.startsWith('accepted'))
      .map((batch) => formatDate(batch.asOfDate));
    return dates.sort().at(-1);
  }, [batchesQuery.data]);

  const [asOfDate, setAsOfDate] = useState<string>('');
  const effectiveAsOf = asOfDate || latestBatchAsOf || '';

  const result = activate.data;
  const warnings = result?.warnings ?? [];

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Activate</h2>
        <p className="mt-1 text-body text-slate">
          Derive the regulatory fact set from the ingested canonical data and run
          all six calculation modules for that reporting period. Re-activating
          rebuilds the facts and adds new immutable runs.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
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
        </div>
        <button
          type="button"
          disabled={!bank || !effectiveAsOf || activate.isPending}
          onClick={() =>
            activate.mutate({ asOfDate: effectiveAsOf, runCalculations: true })
          }
          className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {activate.isPending ? (
            <Loader2 size={15} className="animate-spin" aria-hidden />
          ) : (
            <Zap size={15} aria-hidden />
          )}
          {activate.isPending
            ? 'Deriving facts & running modules…'
            : 'Derive module facts & run calculations'}
        </button>
      </div>

      {activate.isError && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">
            {isApiError(activate.error)
              ? activate.error.message
              : 'Activation failed.'}
          </p>
        </div>
      )}

      {result && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <StatusPill tone="action">{result.periodLabel}</StatusPill>
            <span className="text-body text-navy">
              {result.factsCreated} facts derived
              {result.factsDeleted > 0
                ? ` (replaced ${result.factsDeleted} previous)`
                : ''}
              {result.periodCreated ? ' · new reporting period created' : ''}
            </span>
          </div>

          <div>
            <h3 className="text-caption font-medium uppercase tracking-wider text-slate mb-2">
              Derivation by fact group
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-px bg-border-light rounded overflow-hidden border border-border-light">
              {result.groups.map((group) => (
                <GroupChip key={group.group} group={group} />
              ))}
            </div>
          </div>

          {result.runs.length > 0 && (
            <div>
              <h3 className="text-caption font-medium uppercase tracking-wider text-slate mb-2">
                Module runs
              </h3>
              <div className="rounded border border-border divide-y divide-border-light">
                {result.runs.map((run) => (
                  <div
                    key={run.module}
                    className="flex flex-wrap items-center gap-3 px-4 py-2.5"
                  >
                    <StatusPill tone={runTone(run.status)}>{run.status}</StatusPill>
                    <span className="text-body font-medium text-navy">
                      {MODULE_LABELS[run.module]}
                    </span>
                    {run.headline && (
                      <span className="text-caption font-mono text-slate">
                        {run.headline}
                      </span>
                    )}
                    <span className="ml-auto text-micro font-mono text-slate">
                      {run.scenariosSucceeded}/{run.scenariosSucceeded + run.scenariosFailed}{' '}
                      scenarios
                    </span>
                    {run.error && (
                      <p className="w-full text-caption text-critical">{run.error}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {warnings.length > 0 && (
            <div className="rounded border border-warning/30 bg-warning-light/40 px-4 py-3 space-y-1">
              {warnings.map((warning, index) => (
                <p key={index} className="text-caption text-warning">
                  {warning}
                </p>
              ))}
            </div>
          )}

          <div className="rounded border border-action/30 bg-action-light/40 px-4 py-3">
            <p className="text-body text-navy">
              Switch the reporting period selector (top right) to{' '}
              <span className="font-mono font-medium">{result.periodLabel}</span> to
              view all dashboards on your data.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
