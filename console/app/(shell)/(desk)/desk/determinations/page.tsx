'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Loader2, Plus } from 'lucide-react';
import {
  createDeskDetermination,
  listDeskDeterminations,
  toApiError,
  type ApiError,
  type DeskDetermination,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  CurvesQaBadge,
  DeterminationStatusPill,
  MethodologyChip,
  QaBadge,
} from '@/components/desk';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows } from '@/components/ui';

/**
 * /desk/determinations — Research Desk work queue (Track 1 weekly rates).
 * Capture job stages drafts for the Analyst; never auto-submits.
 * Source: GET /operator/v1/desk/determinations.
 */

const CAPTURE_IDENTITY = 'desk-capture-job@aequoros.system';

function queueOf(d: DeskDetermination): 'analyst' | 'supervisor' | 'published' | 'other' {
  if (d.status === 'draft') return 'analyst';
  if (d.status === 'pending_review') return 'supervisor';
  if (d.status === 'published') return 'published';
  return 'other';
}

export default function DeterminationsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(() => listDeskDeterminations());

  const [creating, setCreating] = useState(false);
  const [cobDate, setCobDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<ApiError | null>(null);

  const queues = useMemo(() => {
    const rows = data?.determinations ?? [];
    return {
      analyst: rows.filter((d) => queueOf(d) === 'analyst'),
      supervisor: rows.filter((d) => queueOf(d) === 'supervisor'),
      published: rows.filter((d) => queueOf(d) === 'published'),
      newCapture: rows.filter(
        (d) => d.status === 'draft' && d.prepared_by === CAPTURE_IDENTITY,
      ),
    };
  }, [data]);

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
          title="Research Desk"
          sub="Weekly rates workflow: capture stages a draft for the Analyst → review & adjustments → submit → Supervisor approve → publish. Track 2 methodology changes live under Methodology."
        />
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="btn-primary inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-body font-medium"
        >
          <Plus size={15} /> New determination
        </button>
      </div>

      {queues.newCapture.length > 0 && (
        <div className="card mb-5 border-action/40 bg-action-light/40 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone="accent">New weekly capture</Chip>
            <span className="text-body font-medium text-navy">
              {queues.newCapture.length} package
              {queues.newCapture.length === 1 ? '' : 's'} waiting for Analyst review
            </span>
          </div>
          <ul className="mt-2 space-y-1">
            {queues.newCapture.slice(0, 5).map((d) => (
              <li key={d.id}>
                <Link
                  href={`/desk/determinations/${d.id}`}
                  className="text-body text-action hover:underline"
                >
                  COB {fmtDate(d.cob_date)} — open rates package
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">Awaiting analyst</div>
          <div className="mt-1 font-mono text-h2 text-navy">{queues.analyst.length}</div>
          <p className="text-caption text-slate">Draft packages to review & adjust</p>
        </div>
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">Awaiting supervisor</div>
          <div className="mt-1 font-mono text-h2 text-navy">{queues.supervisor.length}</div>
          <p className="text-caption text-slate">Submitted for four-eyes approval</p>
        </div>
        <div className="card p-3">
          <div className="text-micro uppercase tracking-wide text-slate">Published</div>
          <div className="mt-1 font-mono text-h2 text-navy">{queues.published.length}</div>
          <p className="text-caption text-slate">Live on client Markets tabs</p>
        </div>
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
                <th className="px-4 py-2.5 font-medium">Rates / curves</th>
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
                    <div className="flex flex-wrap gap-1">
                      <QaBadge determination={d} />
                      <CurvesQaBadge determination={d} />
                      {d.prepared_by === CAPTURE_IDENTITY && d.status === 'draft' && (
                        <Chip tone="accent">new capture</Chip>
                      )}
                      {(d.research_adjustments?.length ?? 0) > 0 && (
                        <Chip tone="warn">{d.research_adjustments.length} adj</Chip>
                      )}
                    </div>
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
