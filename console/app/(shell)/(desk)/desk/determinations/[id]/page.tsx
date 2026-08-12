'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
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
  getDeskDeterminationPackage,
  listDeskPublications,
  publishDeskDetermination,
  putDeskResearchAdjustments,
  rejectDeskDetermination,
  submitDeskDetermination,
  supersedeDeskDetermination,
  toApiError,
  type ApiError,
  type DeskCurveBlock,
  type DeskDetermination,
  type DeskPackageView,
  type DeskPublication,
  type DeskRateEntry,
  type DeskResearchAdjustment,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  CurvesQaBadge,
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
} from '@/components/ui';

/**
 * Research Desk guided weekly rates workflow:
 * 1 Capture & inputs → 2 Rates review (+ WoW) → 3 Adjustments →
 * 4 Review & Confirm (submit) → 5 Supervisor (approve / publish).
 */

const STEPS = [
  { id: 1, key: 'inputs', label: 'Capture & inputs' },
  { id: 2, key: 'rates', label: 'Rates review' },
  { id: 3, key: 'adjust', label: 'Research adjustments' },
  { id: 4, key: 'confirm', label: 'Review & Confirm' },
  { id: 5, key: 'supervisor', label: 'Supervisor' },
] as const;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function show(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

function defaultStep(status: string): number {
  if (status === 'draft') return 1;
  if (status === 'pending_review' || status === 'approved' || status === 'published') return 5;
  if (status === 'rejected') return 4;
  return 1;
}

function StepRail({
  step,
  status,
  onSelect,
}: {
  step: number;
  status: string;
  onSelect: (n: number) => void;
}) {
  const supervisorOnly = status !== 'draft' && status !== 'rejected';
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {STEPS.map((s, i) => {
        const locked = supervisorOnly && s.id < 5 && status !== 'rejected';
        // After submit, Analyst steps are still browsable read-only.
        const current = s.id === step;
        const done = s.id < step || (supervisorOnly && s.id < 5);
        const cls = current
          ? 'bg-action-light text-action border border-action/40'
          : done
            ? 'bg-success-light text-success border border-transparent'
            : 'bg-surface text-slate-light border border-border-light';
        return (
          <span key={s.id} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={13} className="text-slate-light" />}
            <button
              type="button"
              disabled={false}
              onClick={() => onSelect(s.id)}
              className={`rounded px-2.5 py-1 text-micro font-medium uppercase tracking-wide ${cls} ${
                locked ? 'opacity-90' : 'hover:opacity-90'
              }`}
              title={locked ? 'Read-only after submit' : s.label}
            >
              {s.id}. {s.label}
            </button>
          </span>
        );
      })}
    </div>
  );
}

const CURVE_ORDER = [
  'AEQ.GHS.SOV.ZERO',
  'AEQ.GHS.SOV.FWD',
  'AEQ.GHS.OIS',
  'AEQ.GHS.CORP',
];
const TREATMENT_ORDER = [
  'pass_through',
  'windowed',
  'derived',
  'research_override',
  'research_spread',
  'research_adjustment',
];

function RatesTable({
  rates,
  deltas,
}: {
  rates: Record<string, DeskRateEntry>;
  deltas?: DeskPackageView['week_over_week']['deltas'];
}) {
  const deltaMap = new Map((deltas ?? []).map((d) => [d.series_code, d]));
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
      <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">Rates package</h2>
      {entries.length === 0 && (
        <p className="px-5 py-4 text-caption text-slate">No rates computed yet.</p>
      )}
      {orderedKeys.map((treatment) => (
        <div key={treatment} className="border-b border-border-light last:border-b-0">
          <div className="bg-surface px-5 py-1.5 text-micro uppercase tracking-wide text-slate">
            {treatment.replace(/_/g, ' ')}
          </div>
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
                <th className="px-5 py-1.5 font-medium">Series</th>
                <th className="px-3 py-1.5 font-medium text-right">Value</th>
                <th className="px-3 py-1.5 font-medium text-right">Δ vs prior</th>
                <th className="px-3 py-1.5 font-medium">As of</th>
                <th className="px-5 py-1.5 font-medium">Freshness</th>
              </tr>
            </thead>
            <tbody>
              {(groups.get(treatment) ?? []).map(([code, entry]) => {
                const d = deltaMap.get(code);
                const delta = d?.delta_pp != null ? Number(d.delta_pp) : null;
                return (
                  <tr key={code} className="border-b border-border-light last:border-b-0">
                    <td className="px-5 py-2">
                      <span className="font-mono text-caption text-ink">{code}</span>
                      {entry.source_series && entry.source_series.length > 0 && (
                        <div className="max-w-md truncate font-mono text-micro text-slate-light">
                          ← {entry.source_series.join(', ')}
                        </div>
                      )}
                    </td>
                    <td className="num px-3 py-2 text-ink">{show(entry.value)}</td>
                    <td className="num px-3 py-2 text-ink">
                      {delta == null || Number.isNaN(delta) ? (
                        <span className="text-slate-light">{DASH}</span>
                      ) : (
                        <span
                          className={
                            delta > 0 ? 'text-critical' : delta < 0 ? 'text-success' : 'text-slate'
                          }
                        >
                          {delta > 0 ? '+' : ''}
                          {delta.toFixed(2)} pp
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-caption text-slate">
                      {entry.as_of ? fmtDate(entry.as_of) : DASH}
                    </td>
                    <td className="px-5 py-2">
                      {entry.staleness_flag ? (
                        <Chip tone="warn">stale</Chip>
                      ) : (
                        <span className="text-micro text-slate-light">fresh</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

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
      </div>
      {block.build_error ? (
        <p className="mt-3 text-caption text-critical">{block.build_error}</p>
      ) : (block.points?.length ?? 0) > 0 ? (
        <table className="mt-3 w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
              <th className="py-1.5 pr-4 font-medium">Tenor (m)</th>
              <th className="py-1.5 font-medium text-right">Zero %</th>
            </tr>
          </thead>
          <tbody>
            {(block.points ?? []).map((p, i) => (
              <tr key={i} className="border-b border-border-light last:border-b-0">
                <td className="num py-1.5 pr-4">{show(p.tenor_months)}</td>
                <td className="num py-1.5 text-right">{show(p.rate_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="mt-3 text-caption text-slate">No curve points.</p>
      )}
      {block.disclosure && (
        <p className="mt-2 border-t border-border-light pt-2 text-caption text-slate">
          {block.disclosure}
        </p>
      )}
    </div>
  );
}

function AdjustmentsPanel({
  determination,
  busy,
  onSaved,
}: {
  determination: DeskDetermination;
  busy: boolean;
  onSaved: () => void;
}) {
  const existing = determination.research_adjustments ?? [];
  const [seriesCode, setSeriesCode] = useState('GHS.LENDING.INDICATOR');
  const [kind, setKind] = useState<DeskResearchAdjustment['kind']>('override');
  const [value, setValue] = useState('');
  const [rationale, setRationale] = useState('');
  const [rows, setRows] = useState<DeskResearchAdjustment[]>(existing);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setRows(determination.research_adjustments ?? []);
  }, [determination.research_adjustments]);

  const inputClass =
    'rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink focus:border-focus focus:outline-none';

  function addRow() {
    if (!seriesCode.trim()) return;
    if ((kind === 'override' || kind === 'additive_bps') && (!rationale.trim() || !value.trim()))
      return;
    const next: DeskResearchAdjustment = {
      series_code: seriesCode.trim(),
      kind,
      value: kind === 'assumption_note' ? null : value.trim(),
      rationale: rationale.trim(),
    };
    setRows((prev) => {
      const without = prev.filter((r) => r.series_code !== next.series_code);
      return [...without, next].sort((a, b) => a.series_code.localeCompare(b.series_code));
    });
    setValue('');
    setRationale('');
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await putDeskResearchAdjustments(
        determination.id,
        rows.map((r) => ({
          series_code: r.series_code,
          kind: r.kind,
          value: r.value,
          rationale: r.rationale,
        })),
      );
      onSaved();
    } catch (err) {
      setError(toApiError(err));
    }
    setSaving(false);
  }

  if (determination.status !== 'draft') {
    return (
      <div className="card">
        <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">
          Research adjustments
        </h2>
        {(existing.length ?? 0) === 0 ? (
          <p className="px-5 py-4 text-caption text-slate">No research adjustments on this package.</p>
        ) : (
          <table className="w-full text-body">
            <tbody>
              {existing.map((a, i) => (
                <tr key={i} className="border-b border-border-light">
                  <td className="px-5 py-2 font-mono text-caption">{a.series_code}</td>
                  <td className="px-3 py-2 text-caption">{a.kind.replace(/_/g, ' ')}</td>
                  <td className="num px-3 py-2">{a.value ?? DASH}</td>
                  <td className="px-5 py-2 text-caption text-slate">{a.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  }

  return (
    <div className="card p-5">
      <h2 className="text-h3 text-navy">Research adjustments</h2>
      <p className="mt-1 text-caption text-slate">
        Track-1 weekly judgment only — does not rewrite the methodology register. Override and
        additive bps require a numeric value and rationale. Saving recomputes the package digest.
      </p>
      {rows.length > 0 && (
        <ul className="mt-3 divide-y divide-border-light rounded border border-border-light">
          {rows.map((r) => (
            <li key={r.series_code} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span className="font-mono text-caption text-ink">{r.series_code}</span>
              <Chip>{r.kind.replace(/_/g, ' ')}</Chip>
              {r.value != null && r.value !== '' && (
                <span className="font-mono text-caption">{r.value}</span>
              )}
              <span className="min-w-0 flex-1 truncate text-caption text-slate">{r.rationale}</span>
              <button
                type="button"
                className="text-caption text-critical hover:underline"
                onClick={() => setRows((p) => p.filter((x) => x.series_code !== r.series_code))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="mb-1 block text-caption font-medium text-slate">Series</span>
          <input
            className={`${inputClass} w-56 font-mono`}
            value={seriesCode}
            onChange={(e) => setSeriesCode(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-caption font-medium text-slate">Kind</span>
          <select
            className={inputClass}
            value={kind}
            onChange={(e) => setKind(e.target.value as DeskResearchAdjustment['kind'])}
          >
            <option value="override">override</option>
            <option value="additive_bps">additive bps</option>
            <option value="assumption_note">assumption note</option>
          </select>
        </label>
        {kind !== 'assumption_note' && (
          <label className="block">
            <span className="mb-1 block text-caption font-medium text-slate">
              {kind === 'additive_bps' ? 'Bps' : 'Value (%)'}
            </span>
            <input
              className={`${inputClass} w-28 font-mono`}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
        )}
        <label className="block min-w-[14rem] flex-1">
          <span className="mb-1 block text-caption font-medium text-slate">Rationale</span>
          <input
            className={`${inputClass} w-full`}
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
          />
        </label>
        <button type="button" onClick={addRow} className="btn-primary px-3 py-2 text-body">
          Add
        </button>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy || saving}
          onClick={() => void save()}
          className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body disabled:opacity-50"
        >
          {saving && <Loader2 size={14} className="animate-spin" />}
          Save adjustments &amp; recompute
        </button>
      </div>
      {error && (
        <div className="mt-3">
          <ErrorPanel error={error} context="Saving adjustments" />
        </div>
      )}
    </div>
  );
}

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
  const pkg = useApi(() => getDeskDeterminationPackage(id), [id]);
  const pubs = useApi(() => listDeskPublications(id), [id]);

  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState<Action | null>(null);
  const [actionError, setActionError] = useState<{ action: Action; error: ApiError } | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [publishArmed, setPublishArmed] = useState(false);
  const [lastPublication, setLastPublication] = useState<DeskPublication | null>(null);
  const [stepSynced, setStepSynced] = useState(false);

  const d = det.data;
  const status = d?.status ?? '';

  useEffect(() => {
    if (d && !stepSynced) {
      setStep(defaultStep(d.status));
      setStepSynced(true);
    }
  }, [d, stepSynced]);

  async function run(action: Action, call: () => Promise<unknown>) {
    setBusy(action);
    setActionError(null);
    try {
      const result = await call();
      if (action === 'supersede') {
        router.push(`/desk/determinations/${(result as DeskDetermination).id}`);
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
      if (action === 'submit') setStep(5);
      det.reload();
      pkg.reload();
    } catch (err) {
      setActionError({ action, error: toApiError(err) });
    }
    setBusy(null);
  }

  const approveError = actionError?.action === 'approve' ? actionError.error : null;
  const fourEyes =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /preparer/i.test(approveError.message);
  const qaGateRefused =
    approveError !== null &&
    approveError.status === 409 &&
    /rates_qa|qa gate|qa_passed/i.test(approveError.message);

  const computed = Boolean(d && d.derived_values && Object.keys(d.derived_values).length > 0);
  const curves = (d?.derived_values.curves ?? {}) as Record<string, DeskCurveBlock>;
  const curveCodes = [
    ...CURVE_ORDER.filter((c) => c in curves),
    ...Object.keys(curves).filter((c) => !CURVE_ORDER.includes(c)),
  ];
  const rates = d?.derived_values.rates ?? {};
  const packageView = pkg.data;
  const completeness = packageView?.completeness;
  const wow = packageView?.week_over_week;

  const actionBtn =
    'inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-body font-medium text-ink hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50';

  function navButtons() {
    return (
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border-light pt-4">
        <button
          type="button"
          disabled={step <= 1}
          onClick={() => setStep((s) => Math.max(1, s - 1))}
          className={actionBtn}
        >
          <ArrowLeft size={14} /> Back
        </button>
        {step < 5 && (
          <button
            type="button"
            onClick={() => setStep((s) => Math.min(5, s + 1))}
            className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium"
          >
            Next <ArrowRight size={14} />
          </button>
        )}
      </div>
    );
  }

  return (
    <div>
      <Link
        href="/desk/determinations"
        className="mb-4 inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
      >
        <ArrowLeft size={13} /> Research Desk queue
      </Link>

      {det.loading && (
        <div className="card space-y-3 p-5">
          <Skeleton className="h-7 w-72" />
          <Skeleton className="h-4 w-96" />
        </div>
      )}
      {det.error && (
        <ErrorPanel error={det.error} onRetry={det.reload} context="Loading determination" />
      )}

      {d && (
        <div className="space-y-5">
          {/* Header */}
          <div className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="text-h1 text-navy">Weekly rates package · {fmtDate(d.cob_date)}</h1>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <DeterminationStatusPill status={d.status} />
                  <MethodologyChip code={d.methodology_code} version={d.methodology_version} />
                  <QaBadge determination={d} />
                  <CurvesQaBadge determination={d} />
                  {(d.research_adjustments?.length ?? 0) > 0 && (
                    <Chip tone="warn">{d.research_adjustments.length} research adj</Chip>
                  )}
                  {completeness && (
                    <Chip tone={completeness.ready ? 'ok' : 'crit'}>
                      {completeness.required_present}/{completeness.required_total} required series
                    </Chip>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 text-caption text-slate">
                <Users size={14} />
                <span>
                  Analyst <span className="font-mono text-ink">{d.prepared_by}</span>
                  {' · '}
                  Supervisor{' '}
                  {d.reviewed_by ? (
                    <span className="font-mono text-ink">{d.reviewed_by}</span>
                  ) : (
                    <span className="text-slate-light">{DASH}</span>
                  )}
                </span>
              </div>
            </div>

            <div className="mt-4 border-t border-border-light pt-4">
              <StepRail step={step} status={d.status} onSelect={setStep} />
            </div>

            {d.status === 'rejected' && d.review_note && (
              <div className="mt-3 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <XCircle size={14} className="mt-0.5 shrink-0 text-critical" />
                <p className="text-caption text-critical">
                  Rejected by <span className="font-mono">{d.reviewed_by}</span>: {d.review_note}
                </p>
              </div>
            )}

            {status === 'draft' && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void run('compute', () => computeDeskDetermination(id))}
                  className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                >
                  {busy === 'compute' ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Calculator size={14} />
                  )}
                  {computed ? 'Recompute' : 'Compute rates package'}
                </button>
                <span className="text-caption text-slate">
                  Capture stages a draft; Analyst computes, adjusts, then submits for Supervisor.
                </span>
              </div>
            )}

            {actionError && fourEyes && (
              <div className="mt-3 flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                <Users size={14} className="mt-0.5 shrink-0 text-warning" />
                <p className="text-body font-medium text-navy">
                  Four-eyes: preparer cannot approve — sign in as Supervisor (second operator).
                </p>
              </div>
            )}
            {actionError && qaGateRefused && (
              <div className="mt-3 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-critical" />
                <p className="text-body font-medium text-navy">
                  Rates package QA not ready — recompute after fixing inputs.
                </p>
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

          {/* -------- Step 1: Capture & inputs -------- */}
          {step === 1 && (
            <div className="space-y-4">
              <div className="card p-5">
                <h2 className="text-h3 text-navy">Capture completeness</h2>
                <p className="mt-1 text-caption text-slate">
                  Required weekly rates series as of this COB. Missing series need manual entry
                  under Observations, then Recompute.
                </p>
                {pkg.loading && <SkeletonRows rows={4} />}
                {pkg.error && (
                  <div className="mt-3">
                    <ErrorPanel error={pkg.error} onRetry={pkg.reload} context="Package checklist" />
                  </div>
                )}
                {completeness && (
                  <>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Chip tone={completeness.ready ? 'ok' : 'crit'}>
                        {completeness.ready ? 'required series complete' : 'required series missing'}
                      </Chip>
                      {completeness.required_stale.length > 0 && (
                        <Chip tone="warn">{completeness.required_stale.length} stale</Chip>
                      )}
                    </div>
                    <table className="mt-3 w-full text-body">
                      <thead>
                        <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
                          <th className="py-1.5 pr-3 font-medium">Series</th>
                          <th className="py-1.5 pr-3 font-medium">Req</th>
                          <th className="py-1.5 pr-3 font-medium">Status</th>
                          <th className="py-1.5 pr-3 font-medium text-right">Value</th>
                          <th className="py-1.5 pr-3 font-medium">As of</th>
                          <th className="py-1.5 font-medium">Provenance</th>
                        </tr>
                      </thead>
                      <tbody>
                        {completeness.items.map((item) => (
                          <tr
                            key={item.series_code}
                            className="border-b border-border-light last:border-b-0"
                          >
                            <td className="py-1.5 pr-3 font-mono text-caption text-ink">
                              {item.series_code}
                            </td>
                            <td className="py-1.5 pr-3 text-caption text-slate">
                              {item.required ? 'yes' : 'opt'}
                            </td>
                            <td className="py-1.5 pr-3">
                              <Chip
                                tone={
                                  item.status === 'present'
                                    ? 'ok'
                                    : item.status === 'stale'
                                      ? 'warn'
                                      : 'crit'
                                }
                              >
                                {item.status}
                              </Chip>
                            </td>
                            <td className="num py-1.5 pr-3 text-ink">{item.value ?? DASH}</td>
                            <td className="py-1.5 pr-3 text-caption text-slate">
                              {item.as_of_date ? fmtDate(item.as_of_date) : DASH}
                            </td>
                            <td className="py-1.5 text-caption text-slate">
                              {item.provenance.source === 'manual' && (
                                <span>
                                  manual · <span className="font-mono">{item.provenance.entered_by}</span>
                                </span>
                              )}
                              {item.provenance.source === 'capture' && (
                                <span>
                                  capture ·{' '}
                                  <span className="font-mono">{item.provenance.source_key}</span>
                                  {item.provenance.source_url && (
                                    <a
                                      href={item.provenance.source_url}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="ml-1 text-action hover:underline"
                                    >
                                      source
                                    </a>
                                  )}
                                </span>
                              )}
                              {item.provenance.source === 'missing' && DASH}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {completeness.failed_captures.length > 0 && (
                      <div className="mt-4 border-t border-border-light pt-3">
                        <h3 className="text-body font-medium text-navy">Recent failed captures</h3>
                        <ul className="mt-2 space-y-1">
                          {completeness.failed_captures.map((c) => (
                            <li key={c.id} className="text-caption text-critical">
                              <span className="font-mono">{c.source_key}</span> · {c.as_of_date}:{' '}
                              {c.parse_error ?? 'failed'}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </div>

              <div className="card">
                <details open>
                  <summary className="cursor-pointer px-5 py-3 text-h3 text-navy">
                    Input snapshot &amp; field provenance · {packageView?.input_provenance.length ?? 0}{' '}
                    entries
                  </summary>
                  {packageView && packageView.input_provenance.length > 0 ? (
                    <div className="max-h-96 overflow-y-auto border-t border-border-light">
                      <table className="w-full text-body">
                        <thead>
                          <tr className="sticky top-0 bg-surface text-left text-micro uppercase tracking-wide text-slate">
                            <th className="px-5 py-1.5 font-medium">Series</th>
                            <th className="px-3 py-1.5 font-medium">As of</th>
                            <th className="px-3 py-1.5 font-medium text-right">Value</th>
                            <th className="px-5 py-1.5 font-medium">Provenance</th>
                          </tr>
                        </thead>
                        <tbody>
                          {packageView.input_provenance.map((e, i) => (
                            <tr key={i} className="border-b border-border-light last:border-b-0">
                              <td className="px-5 py-1.5 font-mono text-caption">{e.series_code}</td>
                              <td className="px-3 py-1.5 text-caption text-slate">
                                {e.as_of_date ? fmtDate(String(e.as_of_date)) : DASH}
                              </td>
                              <td className="num px-3 py-1.5">{show(e.value)}</td>
                              <td className="px-5 py-1.5 text-caption text-slate">
                                {e.provenance?.source ?? DASH}
                                {e.provenance?.source_key && (
                                  <span className="ml-1 font-mono">{e.provenance.source_key}</span>
                                )}
                                {e.provenance?.entered_by && (
                                  <span className="ml-1 font-mono">{e.provenance.entered_by}</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="px-5 pb-4 text-caption text-slate">
                      Snapshot is empty until Compute finalizes windowed inputs.
                    </p>
                  )}
                </details>
              </div>
              {navButtons()}
            </div>
          )}

          {/* -------- Step 2: Rates -------- */}
          {step === 2 && (
            <div className="space-y-4">
              {!computed ? (
                <div className="card">
                  <EmptyState
                    title="Not computed yet"
                    hint="Run Compute rates package in the header, then review levels and week-over-week deltas."
                  />
                </div>
              ) : (
                <>
                  {wow && (
                    <div className="card p-4">
                      <h2 className="text-h3 text-navy">Week-over-week context</h2>
                      <p className="mt-1 text-caption text-slate">
                        {wow.prior_cob_date
                          ? `Compared to last published package COB ${fmtDate(wow.prior_cob_date)}.`
                          : 'No prior published package — deltas will appear after the first publish.'}
                      </p>
                    </div>
                  )}
                  <RatesTable rates={rates} deltas={wow?.deltas} />
                </>
              )}
              {navButtons()}
            </div>
          )}

          {/* -------- Step 3: Adjustments -------- */}
          {step === 3 && (
            <div className="space-y-4">
              <AdjustmentsPanel
                determination={d}
                busy={busy !== null}
                onSaved={() => {
                  det.reload();
                  pkg.reload();
                }}
              />
              {navButtons()}
            </div>
          )}

          {/* -------- Step 4: Review & Confirm -------- */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="card p-5">
                <h2 className="text-h3 text-navy">Review &amp; Confirm</h2>
                <p className="mt-1 text-caption text-slate">
                  Confirm methodology applied correctly and research adjustments are intentional.
                  Submit pushes the package to the Supervisor for four-eyes approval.
                </p>
                <div className="mt-3 grid gap-x-10 sm:grid-cols-2">
                  <FieldRow label="COB">{fmtDate(d.cob_date)}</FieldRow>
                  <FieldRow label="Methodology">
                    {d.methodology_code} v{d.methodology_version}
                  </FieldRow>
                  <FieldRow label="Input digest">
                    <span className="inline-flex items-center gap-0.5">
                      <span className="break-all font-mono text-caption">
                        {d.input_digest.slice(0, 16)}…
                      </span>
                      <CopyButton value={d.input_digest} label="Copy input digest" />
                    </span>
                  </FieldRow>
                  <FieldRow label="Package digest">
                    <span className="font-mono text-caption">
                      {d.derived_values.package_digest
                        ? `${d.derived_values.package_digest.slice(0, 16)}…`
                        : DASH}
                    </span>
                  </FieldRow>
                  <FieldRow label="Rates QA">
                    <QaBadge determination={d} />
                  </FieldRow>
                  <FieldRow label="Curves QA">
                    <CurvesQaBadge determination={d} />
                    <span className="ml-2 text-caption text-slate">
                      (does not block rates publish)
                    </span>
                  </FieldRow>
                  <FieldRow label="Research adjustments">
                    {d.research_adjustments?.length ?? 0}
                  </FieldRow>
                  <FieldRow label="Completeness">
                    {completeness
                      ? `${completeness.required_present}/${completeness.required_total} required`
                      : DASH}
                  </FieldRow>
                </div>

                {(d.research_adjustments?.length ?? 0) > 0 && (
                  <div className="mt-4 border-t border-border-light pt-3">
                    <h3 className="text-body font-medium text-navy">Adjustments to confirm</h3>
                    <ul className="mt-2 space-y-1">
                      {d.research_adjustments.map((a, i) => (
                        <li key={i} className="text-caption text-slate">
                          <span className="font-mono text-ink">{a.series_code}</span> · {a.kind} ·{' '}
                          {a.value ?? 'note'} — {a.rationale}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {status === 'draft' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    <button
                      type="button"
                      disabled={busy !== null || !computed}
                      onClick={() => void run('submit', () => submitDeskDetermination(id))}
                      className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                    >
                      {busy === 'submit' ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Send size={14} />
                      )}
                      Submit for Supervisor review
                    </button>
                    {!computed && (
                      <span className="text-caption text-slate">
                        Compute the rates package before submit.
                      </span>
                    )}
                  </div>
                )}
                {status === 'pending_review' && (
                  <p className="mt-4 text-caption text-slate">
                    Submitted — awaiting Supervisor on step 5.
                  </p>
                )}
              </div>
              {computed && <RatesTable rates={rates} deltas={wow?.deltas} />}
              {navButtons()}
            </div>
          )}

          {/* -------- Step 5: Supervisor -------- */}
          {step === 5 && (
            <div className="space-y-4">
              <div
                className={`card p-5 ${
                  status === 'pending_review' || status === 'approved'
                    ? 'border-action/30 ring-1 ring-action/20'
                    : ''
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-h3 text-navy">Supervisor review</h2>
                  {(status === 'pending_review' || status === 'approved') && (
                    <Chip tone="accent">checker mode</Chip>
                  )}
                </div>
                <p className="mt-1 text-caption text-slate">
                  Four-eyes: confirm correct application of{' '}
                  <span className="font-mono">
                    {d.methodology_code} v{d.methodology_version}
                  </span>{' '}
                  and that research adjustments are defensible. Approver must not be the preparer.
                </p>

                <div className="mt-3 grid gap-x-10 sm:grid-cols-2">
                  <FieldRow label="Prepared by">
                    <span className="font-mono">{d.prepared_by}</span>
                  </FieldRow>
                  <FieldRow label="Package digest">
                    <span className="font-mono text-caption">
                      {d.derived_values.package_digest?.slice(0, 20) ?? DASH}…
                    </span>
                  </FieldRow>
                  <FieldRow label="Rates QA">
                    <QaBadge determination={d} />
                  </FieldRow>
                  <FieldRow label="Curves QA">
                    <CurvesQaBadge determination={d} />
                  </FieldRow>
                </div>

                {(d.research_adjustments?.length ?? 0) > 0 && (
                  <div className="mt-4 rounded border border-warning/40 bg-warning-light/40 p-3">
                    <h3 className="text-body font-medium text-navy">
                      Research adjustments requiring sign-off
                    </h3>
                    <ul className="mt-2 space-y-1">
                      {d.research_adjustments.map((a, i) => (
                        <li key={i} className="text-caption text-ink">
                          <span className="font-mono">{a.series_code}</span> ·{' '}
                          <strong>{a.kind}</strong> {a.value ?? ''} — {a.rationale}
                          {a.applied_by && (
                            <span className="text-slate"> · by {a.applied_by}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {status === 'pending_review' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void run('approve', () => approveDeskDetermination(id))}
                      className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                    >
                      {busy === 'approve' ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <CheckCircle2 size={14} />
                      )}
                      Approve rates package
                    </button>
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => setRejectOpen((v) => !v)}
                      className={actionBtn}
                    >
                      <XCircle size={14} /> Reject…
                    </button>
                  </div>
                )}

                {rejectOpen && status === 'pending_review' && (
                  <div className="mt-3 rounded border border-border-light bg-surface p-3">
                    <label className="block">
                      <span className="mb-1 block text-caption font-medium text-slate">
                        Rejection reason
                      </span>
                      <textarea
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        rows={2}
                        className="w-full rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink focus:border-focus focus:outline-none"
                      />
                    </label>
                    <div className="mt-2 flex gap-2">
                      <button
                        type="button"
                        disabled={busy !== null || !rejectReason.trim()}
                        onClick={() =>
                          void run('reject', () =>
                            rejectDeskDetermination(id, rejectReason.trim()),
                          )
                        }
                        className="btn-primary px-3 py-1.5 text-body disabled:opacity-50"
                      >
                        Reject determination
                      </button>
                      <button type="button" onClick={() => setRejectOpen(false)} className={actionBtn}>
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {status === 'approved' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    {publishArmed ? (
                      <>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() => void run('publish', () => publishDeskDetermination(id))}
                          className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
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
                          onClick={() => setPublishArmed(false)}
                          className={actionBtn}
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setPublishArmed(true)}
                        className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium"
                      >
                        <Upload size={14} /> Publish…
                      </button>
                    )}
                    <span className="text-caption text-slate">
                      Deliberate action — fans rates (and curves if QA passed) to every tenant.
                    </span>
                  </div>
                )}

                {status === 'published' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
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
                      Supersede (correction draft)
                    </button>
                  </div>
                )}

                {status === 'draft' && (
                  <p className="mt-4 text-caption text-slate">
                    Package is still with the Analyst — complete steps 1–4 and submit first.
                  </p>
                )}
              </div>

              {computed && <RatesTable rates={rates} deltas={wow?.deltas} />}

              {curveCodes.length > 0 && (
                <div>
                  <h2 className="mb-2 text-h3 text-navy">Curves &amp; diagnostics (secondary)</h2>
                  <div className="grid items-start gap-4 lg:grid-cols-3">
                    {curveCodes.map((code) => (
                      <CurveCard key={code} code={code} block={curves[code]} />
                    ))}
                  </div>
                </div>
              )}

              {lastPublication && (
                <div className="card p-5">
                  <h2 className="text-h3 text-navy">Publication fan-out</h2>
                  <div className="mt-3">
                    <PublicationResults publication={lastPublication} />
                  </div>
                </div>
              )}

              <div className="card">
                <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">
                  Publications of this determination
                </h2>
                {pubs.loading && <SkeletonRows rows={2} />}
                {pubs.data && pubs.data.publications.length === 0 && (
                  <EmptyState title="Not published yet" hint="Publish after Supervisor approval." />
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
              {navButtons()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
