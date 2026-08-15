'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  ArrowLeft,
  Calculator,
  CheckCircle2,
  FileText,
  Plus,
  Trash2,
  Waypoints,
  XCircle,
} from 'lucide-react';
import {
  approveCurveDetermination,
  constructCurve,
  getWorkforceSession,
  listCurveDefinitions,
  publishCurveDetermination,
  stageCurveDetermination,
  submitCurveDetermination,
  toApiError,
  type ApiError,
  type DeskCurveConstructResponse,
  type DeskCurveDefinition,
  type DeskCurveGridRow,
  type DeskCurveLeg,
  type DeskCurvePillarView,
  type DeskCurveQuote,
  type DeskDetermination,
  type DeskPublication,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  CeremonyBanner,
  CurveDefinitionStatusPill,
  activeDefinitionFor,
} from '@/components/curves';
import { CurveResultCharts } from '@/components/curves/CurveResultCharts';
import { DeterminationStatusPill, PublicationResults } from '@/components/desk';
import {
  Button,
  Chip,
  type Column,
  CopyButton,
  DataTable,
  EmptyState,
  ErrorPanel,
  Field,
  InfoTip,
  Input,
  QueryBoundary,
  SectionCard,
  Select,
  StatusPill,
  Stepper,
  SubTabs,
  type Step,
  type Tone,
} from '@/components/ui';

/**
 * /desk/curves/[curveCode] — the Curve Construction workspace (FC-4, spec §4.1).
 *
 * The Eikon-familiar surface: an Assumptions-analogue parameter panel (fields
 * DERIVED from the approved definition, read-only because Track-2 governs them;
 * As-of Date + the quote grid are the Track-1 run inputs), a live instrument
 * grid, and the three linked results views — the tenor-adjusted forward grid
 * (Start/End/DF/Yield), interactive charts, and pillar nodes — with the hard QA
 * gates gating publish to golden copy through the FC-G2 per-cob maker-checker.
 */

const INSTRUMENT_KINDS: { value: string; label: string }[] = [
  { value: 'deposit', label: 'Deposit / MM' },
  { value: 'bill', label: 'T-bill' },
  { value: 'fra', label: 'FRA' },
  { value: 'swap', label: 'Par swap' },
  { value: 'ois', label: 'OIS swap' },
];

interface GridRow {
  key: string;
  instrument: string;
  tenor: string;
  quotePct: string;
  leg: DeskCurveLeg;
  include: boolean;
}

let rowSeq = 0;
function blankRow(instrument = 'deposit', leg: DeskCurveLeg = 'discount'): GridRow {
  rowSeq += 1;
  return { key: `r${rowSeq}`, instrument, tenor: '', quotePct: '', leg, include: true };
}

function fmtPct(dec: number, digits = 3): string {
  return `${(dec * 100).toFixed(digits)}%`;
}

// Per-row readiness of an instrument entry, surfaced as a state chip.
function rowState(r: GridRow): { tone: Tone; label: string } {
  if (!r.include) return { tone: 'neutral', label: 'excluded' };
  const q = Number(r.quotePct);
  if (r.quotePct.trim() !== '' && Number.isNaN(q)) return { tone: 'crit', label: 'bad quote' };
  if (r.tenor.trim() === '' || r.quotePct.trim() === '') return { tone: 'warn', label: 'incomplete' };
  return { tone: 'ok', label: 'ready' };
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function ParamCell({ label, mono, children }: { label: string; mono?: boolean; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-micro uppercase tracking-wide text-slate-light">{label}</div>
      <div className={`truncate text-body text-ink ${mono ? 'font-mono' : ''}`}>{children}</div>
    </div>
  );
}

function QaGate({ ok, label, measure }: { ok: boolean; label: string; measure: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-light py-2 last:border-b-0">
      <span className="flex items-center gap-2 text-body text-ink">
        {ok ? (
          <CheckCircle2 size={15} className="shrink-0 text-success" />
        ) : (
          <XCircle size={15} className="shrink-0 text-critical" />
        )}
        {label}
      </span>
      <span className="num text-caption text-slate">{measure}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CurveWorkspacePage() {
  const routeParams = useParams<{ curveCode: string }>();
  const raw = routeParams.curveCode;
  const curveCode = decodeURIComponent(Array.isArray(raw) ? raw[0] : raw);

  const defs = useApi(() => listCurveDefinitions(curveCode), [curveCode]);

  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10));
  const [rows, setRows] = useState<GridRow[]>(() => [
    blankRow('deposit'),
    blankRow('deposit'),
    blankRow('swap'),
    blankRow('ois'),
  ]);
  const [tab, setTab] = useState<'grid' | 'charts' | 'pillars'>('grid');

  const [result, setResult] = useState<DeskCurveConstructResponse | null>(null);
  const [resultSig, setResultSig] = useState<string | null>(null);
  const [constructBusy, setConstructBusy] = useState(false);
  const [constructError, setConstructError] = useState<ApiError | null>(null);

  // FC-G2 per-cob maker-checker lifecycle: a staged DRAFT determination that
  // walks stage -> submit -> approve -> publish, four-eyes enforced server-side.
  const [determination, setDetermination] = useState<DeskDetermination | null>(null);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<ApiError | null>(null);
  const [publication, setPublication] = useState<DeskPublication | null>(null);

  // The signed-in operator, for the four-eyes visual (a proposer may not approve).
  const session = useApi(() => getWorkforceSession(), []);
  const operatorEmail = session.data?.email ?? null;

  const definitions = defs.data?.definitions ?? [];
  const active = useMemo(() => activeDefinitionFor(definitions, asOf), [definitions, asOf]);
  const latest = definitions[definitions.length - 1] as DeskCurveDefinition | undefined;

  const quotes: DeskCurveQuote[] = useMemo(
    () =>
      rows
        .filter(
          (r) =>
            r.include &&
            r.instrument.trim() !== '' &&
            r.tenor.trim() !== '' &&
            r.quotePct.trim() !== '' &&
            !Number.isNaN(Number(r.quotePct)),
        )
        .map((r) => ({
          instrument: r.instrument,
          tenor: r.tenor.trim(),
          quote: Number(r.quotePct) / 100,
          leg: r.leg,
        })),
    [rows],
  );

  const currentSig = useMemo(() => JSON.stringify({ asOf, quotes }), [asOf, quotes]);
  const stale = result !== null && resultSig !== currentSig;
  const hasProjectionLeg = quotes.some((q) => q.leg === 'projection');
  const incompleteCount = rows.filter((r) => r.include && rowState(r).label !== 'ready').length;

  function updateRow(key: string, patch: Partial<GridRow>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }
  function addRow() {
    setRows((prev) => [...prev, blankRow()]);
  }
  function removeRow(key: string) {
    setRows((prev) => prev.filter((r) => r.key !== key));
  }

  async function runConstruction() {
    if (quotes.length === 0) return;
    setConstructBusy(true);
    setConstructError(null);
    // A fresh construction discards any staged determination — the governed
    // artifact must always reflect the inputs that were reviewed.
    setPublication(null);
    setDetermination(null);
    setLifecycleError(null);
    try {
      const res = await constructCurve({ curve_code: curveCode, as_of: asOf, quotes });
      setResult(res);
      setResultSig(currentSig);
      setTab('grid');
    } catch (err) {
      setConstructError(toApiError(err));
    }
    setConstructBusy(false);
  }

  async function runLifecycle(step: () => Promise<void>) {
    setLifecycleBusy(true);
    setLifecycleError(null);
    try {
      await step();
    } catch (err) {
      setLifecycleError(toApiError(err));
    }
    setLifecycleBusy(false);
  }

  const stageDraft = () =>
    runLifecycle(async () => {
      const det = await stageCurveDetermination({ curve_code: curveCode, as_of: asOf, quotes });
      setDetermination(det);
    });
  const submitDraft = () =>
    runLifecycle(async () => {
      if (!determination) return;
      setDetermination(await submitCurveDetermination(determination.id));
    });
  const approveDraft = () =>
    runLifecycle(async () => {
      if (!determination) return;
      setDetermination(await approveCurveDetermination(determination.id));
    });
  const publishDraft = () =>
    runLifecycle(async () => {
      if (!determination) return;
      setPublication(await publishCurveDetermination(determination.id));
      setDetermination((prev) => (prev ? { ...prev, status: 'published' } : prev));
    });

  const isProposer =
    operatorEmail !== null &&
    determination !== null &&
    operatorEmail.toLowerCase() === determination.prepared_by.toLowerCase();
  const canStage =
    active !== null && result !== null && result.qa.passed && !stale && determination === null;

  // Lifecycle rail states (determination-status driven).
  const detStatus = determination?.status ?? null;
  const stepConstruct = result !== null && !stale;
  const stepQaFail = result !== null && !result.qa.passed;
  const stepStaged = determination !== null;
  const stepSubmitted =
    detStatus === 'pending_review' || detStatus === 'approved' || detStatus === 'published';
  const stepApproved = detStatus === 'approved' || detStatus === 'published';
  const stepPublished = publication !== null || detStatus === 'published';

  const steps: Step[] = [
    { key: 'def', label: 'Definition', status: active ? 'complete' : 'error' },
    {
      key: 'construct',
      label: 'Construct',
      status: stepQaFail
        ? 'error'
        : stepConstruct
          ? 'complete'
          : quotes.length
            ? 'current'
            : 'upcoming',
    },
    {
      key: 'stage',
      label: 'Stage draft',
      status: stepStaged ? 'complete' : stepConstruct && result?.qa.passed ? 'current' : 'upcoming',
    },
    {
      key: 'submit',
      label: 'Submit',
      status:
        detStatus === 'rejected'
          ? 'error'
          : stepSubmitted
            ? 'complete'
            : detStatus === 'draft'
              ? 'current'
              : 'upcoming',
    },
    {
      key: 'approve',
      label: 'Approve',
      status: stepApproved ? 'complete' : detStatus === 'pending_review' ? 'current' : 'upcoming',
    },
    {
      key: 'publish',
      label: 'Publish',
      status: stepPublished ? 'complete' : detStatus === 'approved' ? 'current' : 'upcoming',
    },
  ];

  const gridColumns: Column<DeskCurveGridRow & { idx: number }>[] = [
    {
      key: 'idx',
      header: '#',
      render: (r) => (
        <span className="font-mono text-micro text-slate">{r.idx === 0 ? 'spot' : r.idx}</span>
      ),
    },
    { key: 'start', header: 'Start date', render: (r) => <span className="font-mono text-caption text-ink">{fmtDate(r.start)}</span> },
    { key: 'end', header: 'End date', render: (r) => <span className="font-mono text-caption text-ink">{fmtDate(r.end)}</span> },
    {
      key: 'df',
      header: 'Discount factor',
      numeric: true,
      render: (r) => r.discount_factor.toFixed(7),
    },
    {
      key: 'yield',
      header: 'Yield',
      numeric: true,
      render: (r) => (r.idx === 0 ? DASH : fmtPct(r.forward_yield)),
    },
  ];

  const pillarColumns: Column<DeskCurvePillarView>[] = [
    { key: 'instrument', header: 'Instrument', render: (p) => <span className="text-caption text-ink">{p.instrument}</span> },
    { key: 'tenor', header: 'Tenor', render: (p) => <span className="font-mono text-caption text-ink">{p.tenor}</span> },
    {
      key: 'leg',
      header: 'Leg',
      render: (p) => <Chip tone={p.leg === 'projection' ? 'accent' : 'neutral'}>{p.leg}</Chip>,
    },
    {
      key: 'maturity',
      header: 'Adjusted maturity',
      render: (p) => <span className="font-mono text-caption text-ink">{fmtDate(p.pillar_date)}</span>,
    },
    { key: 'quote', header: 'Quote', numeric: true, render: (p) => fmtPct(p.quote) },
    { key: 'dfp', header: 'Discount factor', numeric: true, render: (p) => p.discount_factor.toFixed(7) },
    {
      key: 'residual',
      header: 'Reprice residual',
      numeric: true,
      render: (p) => <span className="text-slate">{p.reprice_residual.toExponential(2)}</span>,
    },
  ];

  return (
    <div>
      <Link
        href="/desk/curves"
        className="mb-4 inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
      >
        <ArrowLeft size={13} /> Curve definitions
      </Link>

      <QueryBoundary
        loading={defs.loading}
        error={defs.error}
        onRetry={defs.reload}
        context="Loading curve definitions"
      >
        {definitions.length === 0 ? (
          <SectionCard title="Not found" noPadding>
            <EmptyState
              title={`No definition named ${curveCode}`}
              description={
                <>
                  This curve code is not in the register.{' '}
                  <Link href="/desk/curves" className="text-action hover:underline">
                    Back to definitions
                  </Link>{' '}
                  to create one.
                </>
              }
            />
          </SectionCard>
        ) : (
          <div className="space-y-5">
            {/* Header + lifecycle stepper */}
            <div className="card p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Waypoints size={18} className="text-slate" aria-hidden />
                    <h1 className="text-h1 text-navy">Curve construction</h1>
                    <Chip mono>{curveCode}</Chip>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {active ? (
                      <>
                        <CurveDefinitionStatusPill status="approved" />
                        <Chip tone="accent">applying v{active.version}</Chip>
                        {active.effective_from && (
                          <span className="text-caption text-slate">
                            effective {fmtDate(active.effective_from)}
                          </span>
                        )}
                      </>
                    ) : (
                      <StatusPill tone="amber">no approved definition effective on {asOf}</StatusPill>
                    )}
                  </div>
                </div>
              </div>
              <div className="mt-5 overflow-x-auto border-t border-border-light pt-5">
                <Stepper steps={steps} className="min-w-[680px]" />
              </div>
            </div>

            {!active && (
              <div className="card p-5">
                <CeremonyBanner>
                  <p className="font-medium text-navy">No approved definition for this as-of date</p>
                  <p className="mt-1">
                    Construction applies the latest APPROVED version whose effective date is on or
                    before the cob. Approve a version under{' '}
                    <Link href="/desk/curves" className="text-action hover:underline">
                      Curve definitions
                    </Link>{' '}
                    or pick an as-of date on or after an approved version&apos;s effective date.
                    {latest && (
                      <>
                        {' '}
                        Latest version is v{latest.version} ({latest.status}).
                      </>
                    )}
                  </p>
                </CeremonyBanner>
              </div>
            )}

            {/* Parameter panel (Assumptions analogue) */}
            <SectionCard
              title={
                <span className="inline-flex items-center gap-1.5">
                  Assumptions
                  <InfoTip label="About the assumptions panel">
                    Every field except the as-of date is derived from the approved definition.
                    Changing one is a Track-2 governance event on the definitions page — never a
                    run-time input here.
                  </InfoTip>
                </span>
              }
              actions={<Chip tone="warn">Track-2 governed · read-only</Chip>}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <Field
                  label="As-of date (cob)"
                  hint="Track-1 run input — valuation date; resolves the effective definition."
                  className="max-w-xs"
                >
                  <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                </Field>
                <p className="flex items-end text-caption text-slate">
                  Every field below is derived from the approved definition. Changing one is a
                  Track-2 event under{' '}
                  <Link href="/desk/curves" className="ml-1 text-action hover:underline">
                    Curve definitions
                  </Link>
                  .
                </p>
              </div>
              {active && (
                <div className="mt-4 grid gap-4 border-t border-border-light pt-4 sm:grid-cols-3 lg:grid-cols-4">
                  <ParamCell label="Currency" mono>{active.currency}</ParamCell>
                  <ParamCell label="Calendar">{active.calendar_name}</ParamCell>
                  <ParamCell label="Curve definition" mono>{active.curve_code} v{active.version}</ParamCell>
                  <ParamCell label="Curve kind">{active.curve_kind}</ParamCell>
                  <ParamCell label="Projection index" mono>{active.projection_index ?? DASH}</ParamCell>
                  <ParamCell label="Discount curve" mono>{active.discount_curve_code ?? DASH}</ParamCell>
                  <ParamCell label="Payment frequency">{active.payment_frequency ?? DASH}</ParamCell>
                  <ParamCell label="Payment interval">{active.payment_interval_months}m</ParamCell>
                  <ParamCell label="Curve frequency" mono>{active.curve_frequency}</ParamCell>
                  <ParamCell label="Interpolation">{active.interpolation_method}</ParamCell>
                  <ParamCell label="Output basis (Convert to)" mono>{active.output_daycount}</ParamCell>
                  <ParamCell label="Spot lag">{active.spot_lag_days}d</ParamCell>
                  <ParamCell label="Roll convention">{active.roll_convention}</ParamCell>
                  <ParamCell label="Extrapolation">{active.extrapolation_rule}</ParamCell>
                  <ParamCell label="Instrument set" mono>{active.instrument_set_ref}</ParamCell>
                </div>
              )}
            </SectionCard>

            {/* Instrument grid */}
            <SectionCard
              title="Instrument grid"
              subtitle="Enter this cob's quotes. Rate is a percentage; the discount leg defines the discounting curve, the projection leg the forward index."
              actions={
                <Button variant="secondary" size="sm" icon={<Plus size={13} />} onClick={addRow}>
                  Add instrument
                </Button>
              }
              noPadding
            >
              <div className="overflow-x-auto">
                <table className="w-full text-body">
                  <thead>
                    <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                      <th className="px-3 py-2 font-medium">Include</th>
                      <th className="px-3 py-2 font-medium">Instrument</th>
                      <th className="px-3 py-2 font-medium">Tenor</th>
                      <th className="px-3 py-2 text-right font-medium">Quote %</th>
                      <th className="px-3 py-2 font-medium">Leg</th>
                      <th className="px-3 py-2 font-medium">State</th>
                      <th className="px-3 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const st = rowState(r);
                      const quoteInvalid = r.quotePct.trim() !== '' && Number.isNaN(Number(r.quotePct));
                      return (
                        <tr key={r.key} className="border-b border-border-light last:border-b-0">
                          <td className="px-3 py-1.5">
                            <input
                              type="checkbox"
                              checked={r.include}
                              onChange={(e) => updateRow(r.key, { include: e.target.checked })}
                              className="h-4 w-4 accent-[color:rgb(var(--accent))]"
                              aria-label="Include in construction"
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <Select
                              value={r.instrument}
                              onChange={(e) => updateRow(r.key, { instrument: e.target.value })}
                              className="py-1.5"
                            >
                              {INSTRUMENT_KINDS.map((k) => (
                                <option key={k.value} value={k.value}>
                                  {k.label}
                                </option>
                              ))}
                            </Select>
                          </td>
                          <td className="px-3 py-1.5">
                            <Input
                              value={r.tenor}
                              onChange={(e) => updateRow(r.key, { tenor: e.target.value })}
                              placeholder={r.instrument === 'fra' ? '3x6' : '3M'}
                              className="w-24 py-1.5 font-mono"
                            />
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            <Input
                              value={r.quotePct}
                              onChange={(e) => updateRow(r.key, { quotePct: e.target.value })}
                              inputMode="decimal"
                              placeholder="0.000"
                              invalid={quoteInvalid}
                              className="w-24 py-1.5 text-right font-mono"
                            />
                          </td>
                          <td className="px-3 py-1.5">
                            <Select
                              value={r.leg}
                              onChange={(e) => updateRow(r.key, { leg: e.target.value as DeskCurveLeg })}
                              className="py-1.5"
                            >
                              <option value="discount">discount</option>
                              <option value="projection">projection</option>
                            </Select>
                          </td>
                          <td className="px-3 py-1.5">
                            <Chip tone={st.tone}>{st.label}</Chip>
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            <button
                              type="button"
                              onClick={() => removeRow(r.key)}
                              className="rounded p-1 text-slate hover:bg-surface hover:text-critical"
                              aria-label="Remove instrument"
                            >
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {rows.length === 0 && (
                      <tr>
                        <td colSpan={7} className="px-3 py-4 text-center text-caption text-slate">
                          No instruments — add at least one discount-leg quote.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="flex flex-wrap items-center gap-3 border-t border-border-light px-5 py-3">
                <Button
                  icon={<Calculator size={14} />}
                  loading={constructBusy}
                  disabled={quotes.length === 0 || !active}
                  onClick={() => void runConstruction()}
                >
                  Run construction
                </Button>
                <span className="text-caption text-slate">
                  {quotes.length} quote{quotes.length === 1 ? '' : 's'} ready
                  {hasProjectionLeg
                    ? ' · multi-curve (projection on discount)'
                    : ' · self-discounting'}
                </span>
                {incompleteCount > 0 && (
                  <Chip tone="warn">
                    {incompleteCount} included row{incompleteCount === 1 ? '' : 's'} incomplete — excluded
                  </Chip>
                )}
                {stale && <Chip tone="warn">inputs changed — re-run construction</Chip>}
              </div>
              {constructError && (
                <div className="px-5 pb-5">
                  <ErrorPanel error={constructError} context="Running construction" />
                  {constructError.status === 409 && (
                    <p className="mt-2 text-caption text-slate">
                      No approved definition is effective on {asOf} — approve one under{' '}
                      <Link href="/desk/curves" className="text-action hover:underline">
                        Curve definitions
                      </Link>
                      .
                    </p>
                  )}
                </div>
              )}
            </SectionCard>

            {/* Results */}
            {result && (
              <>
                {stale && (
                  <CeremonyBanner>
                    <p className="text-navy">
                      The grid or as-of date changed since this result was computed. Re-run
                      construction before publishing — the figures below are from the previous inputs.
                    </p>
                  </CeremonyBanner>
                )}

                {/* QA panel */}
                <SectionCard
                  title="QA gates"
                  actions={
                    result.qa.passed ? (
                      <StatusPill tone="success">all hard gates pass</StatusPill>
                    ) : (
                      <StatusPill tone="critical">hard gate failed — publish blocked</StatusPill>
                    )
                  }
                >
                  <QaGate
                    ok={result.qa.reprice_pass}
                    label="Instrument reprice residuals"
                    measure={`max ${result.qa.reprice_max_residual.toExponential(2)} ≤ ${result.qa.reprice_tolerance.toExponential(0)}`}
                  />
                  <QaGate
                    ok={result.qa.forward_positivity_pass}
                    label="Forward positivity"
                    measure={`min fwd ${fmtPct(result.qa.forward_min)}`}
                  />
                  <QaGate
                    ok={result.qa.forward_oscillation_pass}
                    label="Forward oscillation"
                    measure={`TV ratio ${result.qa.forward_total_variation_ratio.toFixed(2)}`}
                  />
                  <QaGate ok={result.qa.monotone_df_pass} label="Monotone discount factors" measure="0 < DF ≤ 1" />
                  <QaGate
                    ok={result.qa.pillar_coverage_pass}
                    label="Pillar coverage"
                    measure={`${result.qa.pillar_count} ≥ ${result.qa.pillar_min_count}`}
                  />
                </SectionCard>

                {/* Results tabs */}
                <SectionCard
                  title="Construction results"
                  subtitle={
                    <>
                      Applying v{result.definition_version} · as of {fmtDate(result.as_of)} · yields
                      in <span className="font-mono text-ink">{result.output_basis}</span>
                    </>
                  }
                  actions={
                    <span className="inline-flex items-center gap-1 text-micro text-slate">
                      digest
                      <span className="font-mono text-caption text-ink">
                        {result.input_digest.slice(0, 12)}…
                      </span>
                      <CopyButton value={result.input_digest} label="Copy input digest" />
                    </span>
                  }
                  noPadding
                >
                  <SubTabs
                    items={[
                      { key: 'grid', label: 'Forward grid' },
                      { key: 'charts', label: 'Charts' },
                      { key: 'pillars', label: 'Pillar nodes' },
                    ]}
                    active={tab}
                    onChange={(k) => setTab(k as 'grid' | 'charts' | 'pillars')}
                  />
                  {tab === 'grid' && (
                    <DataTable
                      columns={gridColumns}
                      rows={result.rows.map((row, i) => ({ ...row, idx: i }))}
                      density="compact"
                      rowClassName={(r) => (r.idx === 0 ? 'bg-surface/60' : '')}
                    />
                  )}
                  {tab === 'charts' && <CurveResultCharts result={result} />}
                  {tab === 'pillars' && (
                    <DataTable columns={pillarColumns} rows={result.pillars} density="compact" />
                  )}
                </SectionCard>

                {/* Governance & publication — the per-cob maker-checker (FC-G2) */}
                <SectionCard
                  title="Governance & publication"
                  actions={
                    determination ? (
                      <DeterminationStatusPill status={determination.status} />
                    ) : result.qa.passed ? (
                      <StatusPill tone="success">QA green — ready to stage</StatusPill>
                    ) : (
                      <StatusPill tone="critical">QA blocks the draft</StatusPill>
                    )
                  }
                >
                  <p className="text-caption text-slate">
                    A weekly build is a per-cob determination under maker-checker: stage a draft, an
                    analyst submits it, a <span className="font-medium">distinct</span> supervisor
                    approves it, and only then does it fan out to every bank under{' '}
                    <span className="font-mono text-ink">{curveCode}</span> (bitemporal + lineage +
                    audit). Definition-level dual control still applies underneath.
                  </p>

                  {/* Stage: available only after a QA-green construction */}
                  {!determination && (
                    <div className="mt-4 border-t border-border-light pt-4">
                      {canStage ? (
                        <Button
                          icon={<FileText size={14} />}
                          loading={lifecycleBusy}
                          onClick={() => void stageDraft()}
                        >
                          Stage draft determination
                        </Button>
                      ) : (
                        <p className="text-caption text-slate">
                          {stale
                            ? 'Re-run construction on the current inputs to stage a draft.'
                            : result.qa.passed
                              ? 'Staging needs an approved definition effective on the as-of date.'
                              : 'Staging is disabled until every hard QA gate passes.'}
                        </p>
                      )}
                    </div>
                  )}

                  {/* Lifecycle actions once a draft exists */}
                  {determination && (
                    <div className="mt-4 space-y-3 border-t border-border-light pt-4">
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-slate">
                        <span>
                          determination{' '}
                          <span className="font-mono text-ink">{determination.id.slice(0, 8)}…</span>
                        </span>
                        <span>
                          prepared by{' '}
                          <span className="font-mono text-ink">{determination.prepared_by}</span>
                        </span>
                        {determination.reviewed_by && (
                          <span>
                            approved by{' '}
                            <span className="font-mono text-ink">{determination.reviewed_by}</span>
                          </span>
                        )}
                      </div>

                      {determination.status === 'draft' && (
                        <Button
                          icon={<FileText size={14} />}
                          loading={lifecycleBusy}
                          onClick={() => void submitDraft()}
                        >
                          Submit for review
                        </Button>
                      )}

                      {determination.status === 'pending_review' && (
                        <div className="space-y-2">
                          <Button
                            icon={<CheckCircle2 size={14} />}
                            loading={lifecycleBusy}
                            disabled={isProposer}
                            onClick={() => void approveDraft()}
                          >
                            Approve (four-eyes)
                          </Button>
                          {isProposer && (
                            <p className="flex items-center gap-1.5 text-caption text-warning">
                              <XCircle size={13} className="shrink-0" />
                              You staged this determination — a different supervisor must approve it.
                            </p>
                          )}
                        </div>
                      )}

                      {determination.status === 'approved' && (
                        <div className="space-y-3">
                          <CeremonyBanner>
                            <p className="font-medium text-navy">
                              Approved — publishing writes golden copy for every tenant
                            </p>
                            <p className="mt-1">
                              Curve <span className="font-mono">{curveCode}</span> v{active?.version}{' '}
                              as of {asOf}. Per-bank failures are recorded, not rolled back;
                              re-publishing heals a partial fan-out.
                            </p>
                          </CeremonyBanner>
                          <Button
                            icon={<CheckCircle2 size={14} />}
                            loading={lifecycleBusy}
                            onClick={() => void publishDraft()}
                          >
                            Publish to every bank
                          </Button>
                        </div>
                      )}

                      {determination.status === 'rejected' && (
                        <p className="text-caption text-critical">
                          This determination was rejected
                          {determination.review_note ? `: ${determination.review_note}` : ''}. Re-run
                          construction to stage a fresh draft.
                        </p>
                      )}

                      {determination.status === 'published' && !publication && (
                        <p className="flex items-center gap-1.5 text-caption text-success">
                          <CheckCircle2 size={13} className="shrink-0" /> Published to golden copy.
                        </p>
                      )}
                    </div>
                  )}

                  {lifecycleError && (
                    <div className="mt-3">
                      <ErrorPanel error={lifecycleError} context="Curve governance action" />
                    </div>
                  )}

                  {publication && (
                    <div className="mt-4 border-t border-border-light pt-4">
                      <h3 className="mb-2 text-body font-medium text-navy">Publication fan-out</h3>
                      <PublicationResults publication={publication} />
                    </div>
                  )}
                </SectionCard>
              </>
            )}

            {!result && active && (
              <SectionCard title="Results" noPadding>
                <EmptyState
                  title="No construction yet"
                  description="Enter this cob's quotes in the instrument grid above and Run construction to preview the forward grid, charts, pillar nodes and QA."
                />
              </SectionCard>
            )}

            {/* Methodology transparency */}
            {active && (
              <div className="card">
                <details>
                  <summary className="flex cursor-pointer items-center gap-2 px-5 py-3 text-h3 text-navy">
                    <FileText size={15} className="text-slate" />
                    Methodology — the exact recipe (v{active.version})
                  </summary>
                  <div className="space-y-4 border-t border-border-light px-5 py-4">
                    <p className="rounded border border-border-light bg-surface p-3 text-caption text-slate">
                      <span className="font-medium text-ink">Rationale:</span>{' '}
                      {active.change_rationale}
                    </p>
                    <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
                      <ParamCell label="Instrument set" mono>{active.instrument_set_ref}</ParamCell>
                      <ParamCell label="Interpolation">{active.interpolation_method}</ParamCell>
                      <ParamCell label="Output basis" mono>{active.output_daycount}</ParamCell>
                      <ParamCell label="Calendar">{active.calendar_name}</ParamCell>
                      <ParamCell label="Curve frequency" mono>{active.curve_frequency}</ParamCell>
                      <ParamCell label="Payment interval">{active.payment_interval_months}m</ParamCell>
                      <ParamCell label="Spot lag">{active.spot_lag_days}d</ParamCell>
                      <ParamCell label="Roll convention">{active.roll_convention}</ParamCell>
                      <ParamCell label="Extrapolation">{active.extrapolation_rule}</ParamCell>
                      <ParamCell label="Projection index" mono>{active.projection_index ?? DASH}</ParamCell>
                      <ParamCell label="Discount curve" mono>{active.discount_curve_code ?? DASH}</ParamCell>
                      <ParamCell label="Proposed by" mono>{active.proposed_by}</ParamCell>
                      <ParamCell label="Approved by" mono>{active.approved_by ?? DASH}</ParamCell>
                      <ParamCell label="Effective from">{fmtDate(active.effective_from)}</ParamCell>
                    </div>
                    {Object.keys(active.params).length > 0 && (
                      <div>
                        <div className="mb-1 text-micro uppercase tracking-wide text-slate-light">
                          Advanced params
                        </div>
                        <pre className="overflow-x-auto rounded border border-border-light bg-surface p-3 font-mono text-caption text-ink">
                          {JSON.stringify(active.params, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </details>
              </div>
            )}
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}
