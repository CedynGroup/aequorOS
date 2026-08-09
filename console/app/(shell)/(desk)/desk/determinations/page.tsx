'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Loader2, Plus } from 'lucide-react';
import {
  createDeskDetermination,
  listDeskDeterminations,
  toApiError,
  type ApiError,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import { DeterminationStatusPill, MethodologyChip, QaBadge } from '@/components/desk';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

/**
 * /desk/determinations — the weekly determination screen's inventory
 * (spec §11a Track 1). Source: GET /operator/v1/desk/determinations.
 * Every column comes from that payload; the QA badge is the stored
 * derived_values.qa_passed, never recomputed here.
 */
export default function DeterminationsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(() => listDeskDeterminations());

  const [creating, setCreating] = useState(false);
  const [cobDate, setCobDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<ApiError | null>(null);

  async function create() {
    setSubmitting(true);
    setCreateError(null);
    try {
      const row = await createDeskDetermination({ cob_date: cobDate });
      router.push(`/desk/determinations/${row.id}`);
    } catch (err) {
      setCreateError(toApiError(err));
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <PageHeader
          title="Determinations"
          sub="Track 1: one application of the approved methodology per COB date — draft, compute, submit, approve, publish."
        />
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="btn-primary inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-body font-medium"
        >
          <Plus size={15} /> New determination
        </button>
      </div>

      {creating && (
        <div className="card mb-5 p-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="mb-1 block text-caption font-medium text-slate">COB date</span>
              <input
                type="date"
                value={cobDate}
                onChange={(e) => setCobDate(e.target.value)}
                className="rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink focus:border-focus focus:outline-none"
              />
            </label>
            <button
              type="button"
              disabled={submitting || !cobDate}
              onClick={() => void create()}
              className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              Open draft
            </button>
            <p className="text-caption text-slate">
              Opens a draft bound to the methodology version effective on this date and snapshots
              the current observations. The API refuses when no approved methodology is effective
              or no observations exist on or before the date.
            </p>
          </div>
          {createError && (
            <div className="mt-3">
              <ErrorPanel error={createError} context="Opening a draft determination" />
              {createError.status === 409 && (
                <p className="mt-2 text-caption text-slate">
                  A determination is by definition an application of an approved methodology to
                  captured observations — approve one under{' '}
                  <Link href="/desk/methodology" className="text-action hover:underline">
                    Methodology
                  </Link>{' '}
                  or enter observations under{' '}
                  <Link href="/desk/observations" className="text-action hover:underline">
                    Observations
                  </Link>{' '}
                  first.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      <div className="card overflow-x-auto">
        {loading && <SkeletonRows rows={6} />}

        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading determinations" />
          </div>
        )}

        {data && data.determinations.length === 0 && (
          <EmptyState
            title="No determinations yet"
            hint="Open a draft for a COB date to start the weekly Track-1 run. It needs an approved methodology and captured observations."
          />
        )}

        {data && data.determinations.length > 0 && (
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                <th className="px-4 py-2.5 font-medium">COB date</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Methodology</th>
                <th className="px-4 py-2.5 font-medium">QA</th>
                <th className="px-4 py-2.5 font-medium">Prepared by</th>
                <th className="px-4 py-2.5 font-medium">Reviewed by</th>
                <th className="px-4 py-2.5 font-medium">Published</th>
                <th className="px-4 py-2.5 font-medium">Correction</th>
              </tr>
            </thead>
            <tbody>
              {data.determinations.map((d) => (
                <tr
                  key={d.id}
                  onClick={() => router.push(`/desk/determinations/${d.id}`)}
                  className="cursor-pointer border-b border-border-light last:border-b-0 hover:bg-surface"
                >
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/desk/determinations/${d.id}`}
                      className="font-medium text-navy hover:text-action"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {fmtDate(d.cob_date)}
                    </Link>
                    <div className="font-mono text-micro text-slate" title={d.input_digest}>
                      {d.input_digest.slice(0, 12)}…
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <DeterminationStatusPill status={d.status} />
                  </td>
                  <td className="px-4 py-2.5">
                    <MethodologyChip code={d.methodology_code} version={d.methodology_version} />
                  </td>
                  <td className="px-4 py-2.5">
                    <QaBadge determination={d} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-caption text-ink">{d.prepared_by}</td>
                  <td className="px-4 py-2.5 font-mono text-caption text-ink">
                    {d.reviewed_by ?? DASH}
                  </td>
                  <td className="px-4 py-2.5 text-caption text-ink">
                    {fmtDate(d.published_at)}
                  </td>
                  <td className="px-4 py-2.5">
                    {d.supersedes_id ? (
                      <Chip tone="warn" title={`supersedes ${d.supersedes_id}`}>
                        supersedes
                      </Chip>
                    ) : (
                      <span className="text-slate-light">{DASH}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
