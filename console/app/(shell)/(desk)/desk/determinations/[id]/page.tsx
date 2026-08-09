'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  AlertTriangle,
  ArrowLeft,
  Calculator,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Send,
  Upload,
  Users,
  XCircle,
} from 'lucide-react';
import {
  approveDeskDetermination,
  computeDeskDetermination,
  getDeskDetermination,
  listDeskPublications,
  publishDeskDetermination,
  rejectDeskDetermination,
  submitDeskDetermination,
  supersedeDeskDetermination,
  toApiError,
  type ApiError,
  type DeskCurveBlock,
  type DeskDetermination,
  type DeskPublication,
  type DeskRateEntry,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import {
  DeterminationStatusPill,
  MethodologyChip,
  PublicationResults,
  QaBadge,
} from '@/components/desk';
import {
  Chip,
  CopyButton,
  EmptyState,
  ErrorPanel,
  FieldRow,
  Skeleton,
  SkeletonRows,
  StatusChip,
} from '@/components/ui';

/**
 * /desk/determinations/[id] — the weekly determination screen (spec §11a):
 * lifecycle rail with the CURRENT allowed actions, and — after compute — the
 * derived curves, QA-gate results, and rates exactly as the pipeline stored
 * them. Sources: GET /desk/determinations/{id} and GET /desk/publications.
 *
 * Maker-checker honesty: the two refusals the approve path can return —
 * reviewer == preparer, and qa_passed=false — are rendered as explicit,
 * explained states, never generic errors.
 */

// ---------------------------------------------------------------------------
// Small tolerant helpers: derived_values/qa_results are pipeline-owned JSON.
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Primitive → text, structure → compact JSON. Never fabricates. */
function show(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function PassFailChip({ pass, label }: { pass: boolean | undefined; label: string }) {
  if (pass === undefined) return <Chip>{label}</Chip>;
  return <Chip tone={pass ? 'ok' : 'crit'}>{`${label} · ${pass ? 'pass' : 'fail'}`}</Chip>;
}

// ---------------------------------------------------------------------------
// Lifecycle rail: draft -> pending review -> approved -> published, with the
// rejected branch off pending review and the supersede branch off published.
// ---------------------------------------------------------------------------

const MAIN_PATH = ['draft', 'pending_review', 'approved', 'published'] as const;

function LifecycleRail({ status }: { status: string }) {
  const idx = (MAIN_PATH as readonly string[]).indexOf(status);
  const rejected = status === 'rejected';
  // A rejected determination made it as far as pending review.
  const reachedIdx = rejected ? 1 : idx;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {MAIN_PATH.map((state, i) => {
        const current = !rejected && i === idx;
        const done = reachedIdx >= 0 && i < reachedIdx;
        const cls = current
          ? 'bg-action-light text-action border border-action/40'
          : done
            ? 'bg-success-light text-success border border-transparent'
            : 'bg-surface text-slate-light border border-border-light';
        return (
          <span key={state} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={13} className="text-slate-light" />}
            <span
              className={`rounded px-2 py-0.5 text-micro font-medium uppercase tracking-wide ${cls}`}
            >
              {state.replace(/_/g, ' ')}
            </span>
          </span>
        );
      })}
      <span className="ml-2 flex items-center gap-1.5 text-micro text-slate-light">
        <span>branches:</span>
        <span
          className={`rounded px-2 py-0.5 font-medium uppercase tracking-wide ${
            rejected
              ? 'bg-critical-light text-critical'
              : 'bg-surface text-slate-light border border-border-light'
          }`}
        >
          rejected
        </span>
        <span className="rounded border border-border-light bg-surface px-2 py-0.5 font-medium uppercase tracking-wide">
          superseded → new draft
        </span>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Curves + rates rendering (honest tables straight from derived_values).
// ---------------------------------------------------------------------------

// Spec §8 codes first; anything else after, in payload order.
const CURVE_ORDER = ['AEQ.GHS.SOV.ZERO', 'AEQ.GHS.SOV.FWD', 'AEQ.GHS.OIS'];

function CurveCard({ code, block }: { code: string; block: DeskCurveBlock }) {
  const interpolation = asRecord(block.definition)?.interpolation;
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Chip mono>{code}</Chip>
        {block.curve_type && (
          <span className="text-micro uppercase tracking-wide text-slate">{block.curve_type}</span>
        )}
        {typeof interpolation === 'string' && (
          <span className="font-mono text-micro text-slate-light">{interpolation}</span>
        )}
        {block.digest && (
          <span
            className="ml-auto font-mono text-micro text-slate-light"
            title={`curve build digest ${block.digest}`}
          >
            {block.digest.slice(0, 12)}…
          </span>
        )}
      </div>

      {block.build_error ? (
        <div className="mt-3 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
          <XCircle size={14} className="mt-0.5 shrink-0 text-critical" />
          <p className="min-w-0 break-words text-caption text-critical">{block.build_error}</p>
        </div>
      ) : (
        <>
          {(block.points?.length ?? 0) > 0 ? (
            <table className="mt-3 w-full text-body">
              <thead>
                <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
                  <th className="py-1.5 pr-4 font-medium">Tenor (months)</th>
                  <th className="py-1.5 font-medium text-right">Zero rate (%)</th>
                </tr>
              </thead>
              <tbody>
                {(block.points ?? []).map((p, i) => (
                  <tr key={i} className="border-b border-border-light last:border-b-0">
                    <td className="num py-1.5 pr-4 text-left text-ink">{show(p.tenor_months)}</td>
                    <td className="num py-1.5 text-ink">{show(p.rate_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="mt-3 text-caption text-slate">The pipeline emitted no curve points.</p>
          )}
          {block.overnight_anchor_pct && (
            <p className="mt-2 text-caption text-slate">
              Overnight anchor:{' '}
              <span className="font-mono text-ink">{block.overnight_anchor_pct}%</span>
            </p>
          )}
          {block.disclosure && (
            <p className="mt-2 border-t border-border-light pt-2 text-caption text-slate">
              {block.disclosure}
            </p>
          )}
        </>
      )}
    </div>
  );
}

const TREATMENT_ORDER = ['pass_through', 'windowed', 'derived'];

function RatesSection({ rates }: { rates: Record<string, DeskRateEntry> }) {
  const entries = Object.entries(rates);
  const groups = new Map<string, [string, DeskRateEntry][]>();
  for (const [code, entry] of entries) {
    const key = entry.treatment ?? 'unspecified';
    const bucket = groups.get(key) ?? [];
    bucket.push([code, entry]);
    groups.set(key, bucket);
  }
  const orderedKeys = [
    ...TREATMENT_ORDER.filter((k) => groups.has(k)),
    ...[...groups.keys()].filter((k) => !TREATMENT_ORDER.includes(k)),
  ];

  return (
    <div className="card">
      <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">Rates</h2>
      {entries.length === 0 && (
        <p className="px-5 py-4 text-caption text-slate">The pipeline emitted no rates.</p>
      )}
      {orderedKeys.map((treatment) => (
        <div key={treatment} className="border-b border-border-light last:border-b-0">
          <div className="bg-surface px-5 py-1.5 text-micro uppercase tracking-wide text-slate">
            {treatment.replace(/_/g, ' ')}
          </div>
          <table className="w-full text-body">
            <tbody>
              {(groups.get(treatment) ?? []).map(([code, entry]) => (
                <tr key={code} className="border-b border-border-light last:border-b-0">
                  <td className="px-5 py-2">
                    <span className="font-mono text-caption text-ink">{code}</span>
                    {entry.source_series && entry.source_series.length > 0 && (
                      <div
                        className="max-w-md truncate font-mono text-micro text-slate-light"
                        title={entry.source_series.join(', ')}
                      >
                        ← {entry.source_series.join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="num px-3 py-2 text-ink">{show(entry.value)}</td>
                  <td className="px-3 py-2 text-caption text-slate">{entry.unit ?? DASH}</td>
                  <td className="px-3 py-2 text-caption text-slate">
                    as of {fmtDate(entry.as_of)}
                  </td>
                  <td className="px-5 py-2 text-right">
                    {entry.staleness_flag ? (
                      <Chip tone="warn">stale carry-forward</Chip>
                    ) : (
                      <span className="text-micro text-slate-light">fresh</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The page.
// ---------------------------------------------------------------------------

type Action =
  | 'compute'
  | 'submit'
  | 'approve'
  | 'reject'
  | 'publish'
  | 'supersede';

export default function DeterminationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();

  const det = useApi(() => getDeskDetermination(id), [id]);
  const pubs = useApi(() => listDeskPublications(id), [id]);

  const [busy, setBusy] = useState<Action | null>(null);
  const [actionError, setActionError] = useState<{ action: Action; error: ApiError } | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [publishArmed, setPublishArmed] = useState(false);
  const [lastPublication, setLastPublication] = useState<DeskPublication | null>(null);

  async function run(action: Action, call: () => Promise<unknown>) {
    setBusy(action);
    setActionError(null);
    try {
      const result = await call();
      if (action === 'supersede') {
        // The correction is a NEW draft — continue the maker-checker walk there.
        const draft = result as DeskDetermination;
        router.push(`/desk/determinations/${draft.id}`);
        return;
      }
      if (action === 'publish') {
        setLastPublication(result as DeskPublication);
        setPublishArmed(false);
        pubs.reload();
      }
      if (action === 'reject') {
        setRejectOpen(false);
        setRejectReason('');
      }
      det.reload();
    } catch (err) {
      setActionError({ action, error: toApiError(err) });
    }
    setBusy(null);
  }

  const d = det.data;
  const status = d?.status ?? '';

  // The two approve refusals the backend can return, told apart by message —
  // both must be explicit UI states, not generic errors.
  const approveError = actionError?.action === 'approve' ? actionError.error : null;
  const fourEyes =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /preparer/i.test(approveError.message);
  const qaGateRefused =
    approveError !== null &&
    approveError.status === 409 &&
    /qa gate|qa_passed/i.test(approveError.message);

  const computed = Boolean(d && d.derived_values && Object.keys(d.derived_values).length > 0);
  const curves = (d?.derived_values.curves ?? {}) as Record<string, DeskCurveBlock>;
  const curveCodes = [
    ...CURVE_ORDER.filter((c) => c in curves),
    ...Object.keys(curves).filter((c) => !CURVE_ORDER.includes(c)),
  ];
  const qa = d?.qa_results ?? {};
  const snapshotEntries = (d?.input_snapshot ?? [])
    .map(asRecord)
    .filter((e): e is Record<string, unknown> => e !== null);

  const actionBtn =
    'inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-body font-medium text-ink hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <div>
      <Link
        href="/desk/determinations"
        className="mb-4 inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
      >
        <ArrowLeft size={13} /> All determinations
      </Link>

      {det.loading && (
        <div className="card space-y-3 p-5">
          <Skeleton className="h-7 w-72" />
          <Skeleton className="h-4 w-96" />
          <Skeleton className="h-4 w-80" />
        </div>
      )}
      {det.error && (
        <ErrorPanel error={det.error} onRetry={det.reload} context="Loading determination" />
      )}

      {d && (
        <div className="space-y-6">
          {/* ------------------------------------------------ header card */}
          <div className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-h1 text-navy">Determination · {fmtDate(d.cob_date)}</h1>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <DeterminationStatusPill status={d.status} />
                  <MethodologyChip code={d.methodology_code} version={d.methodology_version} />
                  <QaBadge determination={d} />
                  {d.supersedes_id && (
                    <Link href={`/desk/determinations/${d.supersedes_id}`}>
                      <Chip tone="warn" title="This is a correction draft — it supersedes a published determination">
                        supersedes {d.supersedes_id.slice(0, 8)}…
                      </Chip>
                    </Link>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 text-caption text-slate">
                <Users size={14} />
                <span>
                  prepared by <span className="font-mono text-ink">{d.prepared_by}</span>
                  {' · '}
                  reviewed by{' '}
                  {d.reviewed_by ? (
                    <span className="font-mono text-ink">{d.reviewed_by}</span>
                  ) : (
                    <span className="text-slate-light">{DASH}</span>
                  )}
                </span>
              </div>
            </div>

            <div className="mt-4 border-t border-border-light pt-4">
              <LifecycleRail status={d.status} />
            </div>

            {d.status === 'rejected' && d.review_note && (
              <div className="mt-3 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <XCircle size={14} className="mt-0.5 shrink-0 text-critical" />
                <p className="min-w-0 break-words text-caption text-critical">
                  Rejected by <span className="font-mono">{d.reviewed_by}</span>: {d.review_note}
                </p>
              </div>
            )}

            <div className="mt-4 grid gap-x-10 border-t border-border-light pt-3 sm:grid-cols-2">
              <FieldRow label="Input digest">
                <span className="inline-flex items-center gap-0.5">
                  <span className="break-all font-mono text-caption">{d.input_digest}</span>
                  <CopyButton value={d.input_digest} label="Copy input digest" />
                </span>
              </FieldRow>
              <FieldRow label="Created">
                <span title={fmtTs(d.created_at)}>{fmtDate(d.created_at)}</span>
              </FieldRow>
              <FieldRow label="Published at">
                <span title={fmtTs(d.published_at)}>
                  {d.published_at ? fmtTs(d.published_at) : DASH}
                </span>
              </FieldRow>
              <FieldRow label="Snapshot entries">
                <span className="font-mono">{snapshotEntries.length}</span>
              </FieldRow>
            </div>

            {/* ------------------------------------------ current actions */}
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
              {status === 'draft' && (
                <>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void run('compute', () => computeDeskDetermination(id))}
                    className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {busy === 'compute' ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Calculator size={14} />
                    )}
                    Compute
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void run('submit', () => submitDeskDetermination(id))}
                    className={actionBtn}
                  >
                    {busy === 'submit' ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Send size={14} />
                    )}
                    Submit for review
                  </button>
                  {!computed && (
                    <span className="text-caption text-slate">
                      Compute first — what the checker reviews is what was computed.
                    </span>
                  )}
                </>
              )}

              {status === 'pending_review' && (
                <>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void run('approve', () => approveDeskDetermination(id))}
                    className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {busy === 'approve' ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <CheckCircle2 size={14} />
                    )}
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => setRejectOpen((v) => !v)}
                    className={actionBtn}
                  >
                    <XCircle size={14} /> Reject…
                  </button>
                  <span className="text-caption text-slate">
                    Checker step: confirms correct application of{' '}
                    <span className="font-mono">
                      {d.methodology_code} v{d.methodology_version}
                    </span>
                    , not choice of assumptions.
                  </span>
                </>
              )}

              {status === 'approved' && (
                <>
                  {publishArmed ? (
                    <>
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void run('publish', () => publishDeskDetermination(id))}
                        className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy === 'publish' ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Upload size={14} />
                        )}
                        Confirm — publish to every bank
                      </button>
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => setPublishArmed(false)}
                        className={actionBtn}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => setPublishArmed(true)}
                      className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium"
                    >
                      <Upload size={14} /> Publish…
                    </button>
                  )}
                  <span className="text-caption text-slate">
                    Publish is a deliberate, logged action — it fans out to every tenant.
                  </span>
                </>
              )}

              {status === 'published' && (
                <>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void run('publish', () => publishDeskDetermination(id))}
                    className={actionBtn}
                  >
                    {busy === 'publish' ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Upload size={14} />
                    )}
                    Re-publish
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void run('supersede', () => supersedeDeskDetermination(id))}
                    className={actionBtn}
                  >
                    {busy === 'supersede' ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <ChevronRight size={14} />
                    )}
                    Supersede (open correction draft)
                  </button>
                  <span className="text-caption text-slate">
                    Published determinations are immutable — re-publish heals partial fan-outs;
                    corrections walk the full maker-checker path as a new draft.
                  </span>
                </>
              )}

              {status === 'rejected' && (
                <span className="text-caption text-slate">
                  Rejected determinations are terminal — open a new determination for this COB
                  date from the list once the inputs are corrected.
                </span>
              )}
            </div>

            {/* Reject reason (required by the API) */}
            {rejectOpen && status === 'pending_review' && (
              <div className="mt-3 rounded border border-border-light bg-surface p-3">
                <label className="block">
                  <span className="mb-1 block text-caption font-medium text-slate">
                    Rejection reason (recorded on the determination)
                  </span>
                  <textarea
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    rows={2}
                    className="w-full rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink placeholder:text-slate-light focus:border-focus focus:outline-none"
                    placeholder="What is wrong with this determination?"
                  />
                </label>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    disabled={busy !== null || rejectReason.trim() === ''}
                    onClick={() =>
                      void run('reject', () => rejectDeskDetermination(id, rejectReason.trim()))
                    }
                    className="btn-primary px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {busy === 'reject' ? 'Rejecting…' : 'Reject determination'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setRejectOpen(false)}
                    className={actionBtn}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* ------------------------------- action errors, told honestly */}
            {actionError && fourEyes && (
              <div className="mt-3 flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                <Users size={14} className="mt-0.5 shrink-0 text-warning" />
                <div className="min-w-0">
                  <p className="text-body font-medium text-navy">
                    Four-eyes: the preparer cannot approve their own determination — sign in as a
                    second operator.
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    Prepared by <span className="font-mono">{d.prepared_by}</span>, and that is
                    who you are signed in as. API said: “{actionError.error.message}”
                  </p>
                </div>
              </div>
            )}
            {actionError && qaGateRefused && (
              <div className="mt-3 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-critical" />
                <div className="min-w-0">
                  <p className="text-body font-medium text-navy">
                    A hard QA gate failed (qa_passed = false) — this determination cannot be
                    approved.
                  </p>
                  <p className="mt-1 text-caption text-slate">
                    Correct the inputs and recompute (a rejected review sends it back to the
                    preparer). The failed gates are itemized in the QA panel below. API said:
                    “{actionError.error.message}”
                  </p>
                </div>
              </div>
            )}
            {actionError && !fourEyes && !qaGateRefused && (
              <div className="mt-3">
                <ErrorPanel
                  error={actionError.error}
                  context={`Action “${actionError.action}”`}
                />
              </div>
            )}
          </div>

          {/* ------------------------------------ publish fan-out results */}
          {lastPublication && (
            <div className="card p-5">
              <h2 className="text-h3 text-navy">Publication fan-out</h2>
              <p className="mt-1 text-caption text-slate">
                Per-bank delivery results from the publish you just ran. Partial failure is
                recorded, never rolled back — re-publish to heal failed banks.
              </p>
              <div className="mt-3">
                <PublicationResults publication={lastPublication} />
              </div>
            </div>
          )}

          {/* ------------------------------------------------ derived data */}
          {!computed ? (
            <div className="card">
              <EmptyState
                title="Not computed yet"
                hint="Run Compute to finalize the input snapshot and derive curves, rates, and QA results from the approved methodology parameters."
              />
            </div>
          ) : (
            <>
              {/* QA gate results */}
              <div className="card p-5">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-h3 text-navy">QA gate results</h2>
                  {qa.qa_passed === true && <Chip tone="ok">qa passed</Chip>}
                  {qa.qa_passed === false && <Chip tone="crit">qa failed</Chip>}
                  {qa.nss_fallback_used === true && (
                    <Chip tone="warn" title="Too few liquid points — parametric NSS fallback used">
                      NSS fallback
                    </Chip>
                  )}
                </div>

                {qa.gates && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {Object.entries(qa.gates).map(([gate, verdict]) => (
                      <Chip key={gate} tone={verdict === 'pass' ? 'ok' : 'crit'}>
                        {gate.replace(/_/g, ' ')} · {verdict}
                      </Chip>
                    ))}
                  </div>
                )}

                <div className="mt-4 grid items-start gap-4 lg:grid-cols-3">
                  {/* Forward QA (positivity + oscillation) */}
                  <div className="rounded border border-border-light bg-surface p-3">
                    <h3 className="text-body font-medium text-navy">Forward curve QA</h3>
                    {qa.forward_qa ? (
                      <div className="mt-1 divide-y divide-border-light">
                        <FieldRow label="Min forward">
                          <span className="font-mono">{show(qa.forward_qa.min_forward)}</span>
                        </FieldRow>
                        <FieldRow label="Positivity">
                          <PassFailChip
                            pass={qa.forward_qa.positivity_pass}
                            label={
                              qa.forward_qa.positivity_required === false
                                ? 'not required'
                                : 'required'
                            }
                          />
                        </FieldRow>
                        <FieldRow label="Slope sign changes">
                          <span className="font-mono">{show(qa.forward_qa.slope_sign_changes)}</span>
                        </FieldRow>
                        <FieldRow label="Oscillation ratio">
                          <span className="font-mono">
                            {show(qa.forward_qa.total_variation_ratio)} /{' '}
                            {show(qa.forward_qa.oscillation_tolerance)}
                          </span>
                        </FieldRow>
                        <FieldRow label="Oscillation">
                          <PassFailChip pass={qa.forward_qa.oscillation_pass} label="gate" />
                        </FieldRow>
                        <FieldRow label="Overall">
                          <PassFailChip pass={qa.forward_qa.passed} label="forward QA" />
                        </FieldRow>
                      </div>
                    ) : (
                      <p className="mt-2 text-caption text-slate">
                        No forward QA recorded — the sovereign curve did not build.
                      </p>
                    )}
                  </div>

                  {/* GRR cross-check */}
                  <div className="rounded border border-border-light bg-surface p-3">
                    <h3 className="text-body font-medium text-navy">GRR cross-check</h3>
                    {qa.grr_check ? (
                      <div className="mt-1 divide-y divide-border-light">
                        <FieldRow label="Status">
                          <StatusChip value={qa.grr_check.status} />
                        </FieldRow>
                        <FieldRow label="Published">
                          <span className="font-mono">{show(qa.grr_check.published_pct)}</span>
                        </FieldRow>
                        <FieldRow label="Reconstructed">
                          <span className="font-mono">{show(qa.grr_check.reconstructed_pct)}</span>
                        </FieldRow>
                        <FieldRow label="Delta (pp)">
                          <span className="font-mono">
                            {show(qa.grr_check.gap_pp)} (tol {show(qa.grr_check.tolerance_pp)})
                          </span>
                        </FieldRow>
                        <FieldRow label="Reference month">
                          {show(qa.grr_check.reference_month)}
                        </FieldRow>
                      </div>
                    ) : (
                      <p className="mt-2 text-caption text-slate">No GRR check recorded.</p>
                    )}
                  </div>

                  {/* Diagnostics: overnight spread + cointegration */}
                  <div className="rounded border border-border-light bg-surface p-3">
                    <h3 className="text-body font-medium text-navy">Diagnostics</h3>
                    {qa.overnight_spread && (
                      <div className="mt-1 divide-y divide-border-light">
                        {Object.entries(qa.overnight_spread).map(([k, v]) => (
                          <FieldRow key={k} label={k.replace(/_/g, ' ')}>
                            <span className="font-mono text-caption">{show(v)}</span>
                          </FieldRow>
                        ))}
                      </div>
                    )}
                    {qa.cointegration_diagnostic && (
                      <div className="mt-3 border-t border-border-light pt-2">
                        <div className="text-micro uppercase tracking-wide text-slate">
                          Cointegration (diagnostic only)
                        </div>
                        <div className="divide-y divide-border-light">
                          {Object.entries(qa.cointegration_diagnostic)
                            .filter(([k]) => k !== 'note')
                            .map(([k, v]) => (
                              <FieldRow key={k} label={k.replace(/_/g, ' ')}>
                                <span className="break-all font-mono text-caption">{show(v)}</span>
                              </FieldRow>
                            ))}
                        </div>
                        {typeof qa.cointegration_diagnostic.note === 'string' && (
                          <p className="mt-1 text-micro text-slate-light">
                            {qa.cointegration_diagnostic.note}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                {/* Steward flags */}
                {(qa.flags?.length ?? 0) > 0 && (
                  <div className="mt-4 border-t border-border-light pt-3">
                    <h3 className="text-body font-medium text-navy">Steward flags</h3>
                    <ul className="mt-2 space-y-1">
                      {(qa.flags ?? []).map((f, i) => (
                        <li key={i} className="flex flex-wrap items-center gap-2">
                          <Chip tone="warn">{f.flag?.replace(/_/g, ' ') ?? 'flag'}</Chip>
                          <span className="font-mono text-caption text-ink">{f.series ?? DASH}</span>
                          {f.detail && <span className="text-caption text-slate">{f.detail}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Curves */}
              {curveCodes.length > 0 && (
                <div className="grid items-start gap-4 lg:grid-cols-3">
                  {curveCodes.map((code) => (
                    <CurveCard key={code} code={code} block={curves[code]} />
                  ))}
                </div>
              )}

              {/* Rates grouped by treatment */}
              {d.derived_values.rates && <RatesSection rates={d.derived_values.rates} />}
            </>
          )}

          {/* ------------------------------------------------ input snapshot */}
          <div className="card">
            <details>
              <summary className="cursor-pointer px-5 py-3 text-h3 text-navy">
                Input snapshot · {snapshotEntries.length} entries
              </summary>
              {snapshotEntries.length === 0 ? (
                <p className="px-5 pb-4 text-caption text-slate">The snapshot is empty.</p>
              ) : (
                <div className="max-h-96 overflow-y-auto border-t border-border-light">
                  <table className="w-full text-body">
                    <thead>
                      <tr className="sticky top-0 bg-surface text-left text-micro uppercase tracking-wide text-slate">
                        <th className="px-5 py-1.5 font-medium">Series</th>
                        <th className="px-3 py-1.5 font-medium">As of</th>
                        <th className="px-5 py-1.5 font-medium text-right">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshotEntries.map((e, i) => (
                        <tr key={i} className="border-b border-border-light last:border-b-0">
                          <td className="px-5 py-1.5 font-mono text-caption text-ink">
                            {show(e.series_code)}
                          </td>
                          <td className="px-3 py-1.5 text-caption text-slate">
                            {show(e.as_of_date)}
                          </td>
                          <td className="num px-5 py-1.5 text-ink">{show(e.value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </details>
          </div>

          {/* ------------------------------------------- publication history */}
          <div className="card">
            <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">
              Publications of this determination
            </h2>
            {pubs.loading && <SkeletonRows rows={2} />}
            {pubs.error && (
              <div className="p-4">
                <ErrorPanel error={pubs.error} onRetry={pubs.reload} context="Loading publications" />
              </div>
            )}
            {pubs.data && pubs.data.publications.length === 0 && (
              <EmptyState
                title="Not published yet"
                hint="Publishing fans this determination's curves and rates out to every bank through the desk adapter."
              />
            )}
            {pubs.data && pubs.data.publications.length > 0 && (
              <ul className="divide-y divide-border-light">
                {pubs.data.publications.map((p) => (
                  <li key={p.id} className="px-5 py-3">
                    <PublicationResults publication={p} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
