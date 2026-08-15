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
  rejectDeskDetermination,
  submitDeskDetermination,
  supersedeDeskDetermination,
  toApiError,
  type ApiError,
  type DeskCurveBlock,
  type DeskDetermination,
  type DeskPublication,
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
import { AdjustmentsPanel } from '@/components/desk/AdjustmentsPanel';
import { CompletenessPanel, InputProvenancePanel } from '@/components/desk/CompletenessPanel';
import { CurveCard } from '@/components/desk/CurveCard';
import { DeterminationTimeline } from '@/components/desk/DeterminationTimeline';
import { PublishConfirmModal } from '@/components/desk/PublishConfirmModal';
import { RatesTable } from '@/components/desk/RatesTable';
import { CURVE_ORDER } from '@/components/desk/util';
import {
  Button,
  Chip,
  CopyButton,
  EmptyState,
  ErrorPanel,
  Field,
  FieldRow,
  SectionCard,
  Skeleton,
  SkeletonRows,
  Stepper,
  Textarea,
  type Step,
} from '@/components/ui';

/**
 * Research Desk guided weekly rates workflow:
 * 1 Capture & inputs → 2 Rates review (+ WoW) → 3 Adjustments →
 * 4 Review & Confirm (submit) → 5 Supervisor (approve / publish).
 */

const STEPS = [
  { id: 1, key: 'inputs', label: 'Capture & inputs', description: 'Required series' },
  { id: 2, key: 'rates', label: 'Rates review', description: 'Levels & WoW' },
  { id: 3, key: 'adjust', label: 'Adjustments', description: 'Track-1 judgment' },
  { id: 4, key: 'confirm', label: 'Review & Confirm', description: 'Submit for review' },
  { id: 5, key: 'supervisor', label: 'Supervisor', description: 'Approve / publish' },
] as const;

function defaultStep(status: string): number {
  if (status === 'draft') return 1;
  if (status === 'pending_review' || status === 'approved' || status === 'published') return 5;
  if (status === 'rejected') return 4;
  return 1;
}

type Action = 'compute' | 'submit' | 'approve' | 'reject' | 'publish' | 'supersede';

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
  const [publishOpen, setPublishOpen] = useState(false);
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
        setPublishOpen(false);
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

  // Refusal classification (kept from the maker-checker gates, made robust to
  // both 409 and 422 envelopes and the qa_passed alias).
  const approveError = actionError?.action === 'approve' ? actionError.error : null;
  const fourEyes =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /preparer|four[- ]?eyes|same (user|operator)/i.test(approveError.message);
  const qaGateRefused =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /rates_qa|qa[ _-]?gate|qa[ _-]?passed|not ready/i.test(approveError.message);

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

  const wizardSteps: Step[] = STEPS.map((s) => ({
    key: s.key,
    label: `${s.id}. ${s.label}`,
    description: s.description,
    status: status === 'rejected' && s.key === 'supervisor' ? ('error' as const) : undefined,
  }));

  function navButtons() {
    return (
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border-light pt-4">
        <Button
          variant="secondary"
          icon={<ArrowLeft size={14} />}
          disabled={step <= 1}
          onClick={() => setStep((s) => Math.max(1, s - 1))}
        >
          Back
        </Button>
        {step < 5 && (
          <Button onClick={() => setStep((s) => Math.min(5, s + 1))}>
            Next <ArrowRight size={14} />
          </Button>
        )}
      </div>
    );
  }

  return (
    <div>
      {det.loading && (
        <div className="space-y-4">
          <Link
            href="/desk/determinations"
            className="inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
          >
            <ArrowLeft size={13} /> Research Desk queue
          </Link>
          <div className="card space-y-3 p-5">
            <Skeleton className="h-7 w-72" />
            <Skeleton className="h-4 w-96" />
          </div>
        </div>
      )}
      {det.error && (
        <ErrorPanel error={det.error} onRetry={det.reload} context="Loading determination" />
      )}

      {d && (
        <div className="space-y-5">
          {/* Header / control card */}
          <div className="card p-5">
            <nav
              aria-label="Breadcrumb"
              className="mb-2 flex items-center gap-1 text-caption text-slate"
            >
              <Link href="/desk/determinations" className="hover:text-action">
                Research Desk
              </Link>
              <span className="text-slate-light">/</span>
              <span className="text-slate-light">{fmtDate(d.cob_date)}</span>
            </nav>

            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
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

            <div className="mt-5 border-t border-border-light pt-5">
              <Stepper steps={wizardSteps} current={step - 1} />
            </div>

            {d.status === 'rejected' && d.review_note && (
              <div className="mt-4 flex items-start gap-2 rounded border border-critical/40 bg-critical-light p-3">
                <XCircle size={14} className="mt-0.5 shrink-0 text-critical" />
                <p className="text-caption text-critical">
                  Rejected by <span className="font-mono">{d.reviewed_by}</span>: {d.review_note}
                </p>
              </div>
            )}

            {status === 'draft' && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                <Button
                  icon={<Calculator size={14} />}
                  loading={busy === 'compute'}
                  disabled={busy !== null}
                  onClick={() => void run('compute', () => computeDeskDetermination(id))}
                >
                  {computed ? 'Recompute' : 'Compute rates package'}
                </Button>
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
                <ErrorPanel error={actionError.error} context={`Action “${actionError.action}”`} />
              </div>
            )}
          </div>

          {/* Lifecycle — visible on every step */}
          <DeterminationTimeline determination={d} />

          {/* -------- Step 1: Capture & inputs -------- */}
          {step === 1 && (
            <div className="space-y-4">
              {pkg.loading && (
                <div className="card">
                  <SkeletonRows rows={4} />
                </div>
              )}
              {pkg.error && (
                <ErrorPanel error={pkg.error} onRetry={pkg.reload} context="Package checklist" />
              )}
              {completeness && <CompletenessPanel completeness={completeness} />}
              {packageView && <InputProvenancePanel pkg={packageView} />}
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
                    <SectionCard title="Week-over-week context">
                      <p className="text-caption text-slate">
                        {wow.prior_cob_date
                          ? `Compared to last published package COB ${fmtDate(wow.prior_cob_date)}.`
                          : 'No prior published package — deltas will appear after the first publish.'}
                      </p>
                    </SectionCard>
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
              <SectionCard
                title="Review & Confirm"
                subtitle="Confirm methodology applied correctly and research adjustments are intentional. Submit pushes the package to the Supervisor for four-eyes approval."
              >
                <div className="grid gap-x-10 sm:grid-cols-2">
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
                    <span className="inline-flex items-center gap-2">
                      <CurvesQaBadge determination={d} />
                      <span className="text-caption text-slate">(does not block rates publish)</span>
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
                    <Button
                      icon={<Send size={14} />}
                      loading={busy === 'submit'}
                      disabled={busy !== null || !computed}
                      onClick={() => void run('submit', () => submitDeskDetermination(id))}
                    >
                      Submit for Supervisor review
                    </Button>
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
              </SectionCard>
              {computed && <RatesTable rates={rates} deltas={wow?.deltas} />}
              {navButtons()}
            </div>
          )}

          {/* -------- Step 5: Supervisor -------- */}
          {step === 5 && (
            <div className="space-y-4">
              <SectionCard
                title="Supervisor review"
                subtitle={
                  <>
                    Four-eyes: confirm correct application of{' '}
                    <span className="font-mono">
                      {d.methodology_code} v{d.methodology_version}
                    </span>{' '}
                    and that research adjustments are defensible. Approver must not be the preparer.
                  </>
                }
                actions={
                  (status === 'pending_review' || status === 'approved') && (
                    <Chip tone="accent">checker mode</Chip>
                  )
                }
                className={
                  status === 'pending_review' || status === 'approved'
                    ? 'border-action/30 ring-1 ring-action/20'
                    : ''
                }
              >
                <div className="grid gap-x-10 sm:grid-cols-2">
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
                          {a.applied_by && <span className="text-slate"> · by {a.applied_by}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {status === 'pending_review' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    <Button
                      icon={<CheckCircle2 size={14} />}
                      loading={busy === 'approve'}
                      disabled={busy !== null}
                      onClick={() => void run('approve', () => approveDeskDetermination(id))}
                    >
                      Approve rates package
                    </Button>
                    <Button
                      variant="secondary"
                      icon={<XCircle size={14} />}
                      disabled={busy !== null}
                      onClick={() => setRejectOpen((v) => !v)}
                    >
                      Reject…
                    </Button>
                  </div>
                )}

                {rejectOpen && status === 'pending_review' && (
                  <div className="mt-3 rounded border border-border-light bg-surface p-3">
                    <Field label="Rejection reason">
                      <Textarea
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                        rows={2}
                      />
                    </Field>
                    <div className="mt-2 flex gap-2">
                      <Button
                        variant="danger"
                        loading={busy === 'reject'}
                        disabled={busy !== null || !rejectReason.trim()}
                        onClick={() =>
                          void run('reject', () => rejectDeskDetermination(id, rejectReason.trim()))
                        }
                      >
                        Reject determination
                      </Button>
                      <Button variant="secondary" onClick={() => setRejectOpen(false)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}

                {status === 'approved' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    <Button icon={<Upload size={14} />} onClick={() => setPublishOpen(true)}>
                      Publish…
                    </Button>
                    <span className="text-caption text-slate">
                      Deliberate action — fans rates (and curves if QA passed) to every tenant.
                    </span>
                  </div>
                )}

                {status === 'published' && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-4">
                    <Button
                      variant="secondary"
                      icon={<Upload size={14} />}
                      disabled={busy !== null}
                      onClick={() => setPublishOpen(true)}
                    >
                      Re-publish
                    </Button>
                    <Button
                      variant="secondary"
                      loading={busy === 'supersede'}
                      disabled={busy !== null}
                      onClick={() => void run('supersede', () => supersedeDeskDetermination(id))}
                    >
                      Supersede (correction draft)
                    </Button>
                  </div>
                )}

                {status === 'draft' && (
                  <p className="mt-4 text-caption text-slate">
                    Package is still with the Analyst — complete steps 1–4 and submit first.
                  </p>
                )}
              </SectionCard>

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
                <SectionCard title="Publication fan-out">
                  <PublicationResults publication={lastPublication} />
                </SectionCard>
              )}

              <SectionCard title="Publications of this determination" noPadding>
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
              </SectionCard>
              {navButtons()}
            </div>
          )}

          <PublishConfirmModal
            open={publishOpen}
            onClose={() => setPublishOpen(false)}
            onConfirm={() => void run('publish', () => publishDeskDetermination(id))}
            loading={busy === 'publish'}
            determination={d}
            title={status === 'published' ? 'Re-publish to every bank' : 'Publish to every bank'}
          />
        </div>
      )}
    </div>
  );
}
