'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  ArrowLeft,
  Calculator,
  CheckCircle2,
  FileText,
  Loader2,
  Plus,
  Table2,
  Trash2,
  TrendingUp,
  Upload,
  Waypoints,
  XCircle,
} from 'lucide-react';
import {
  constructCurve,
  listCurveDefinitions,
  publishCurve,
  toApiError,
  type ApiError,
  type DeskCurveConstructResponse,
  type DeskCurveDefinition,
  type DeskCurveLeg,
  type DeskCurveQuote,
  type DeskPublication,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  CeremonyBanner,
  CurveDefinitionStatusPill,
  LineChart,
  activeDefinitionFor,
} from '@/components/curves';
import { PublicationResults } from '@/components/desk';
import {
  Chip,
  CopyButton,
  EmptyState,
  ErrorPanel,
  SkeletonRows,
} from '@/components/ui';

/**
 * /desk/curves/[curveCode] — the Curve Construction workspace (FC-4, spec §4.1).
 *
 * The Refinitiv-familiar surface: an Assumptions-analogue parameter panel
 * (fields DERIVED from the approved definition, read-only because Track-2 governs
 * them; As-of Date + the quote grid are the Track-1 run inputs), a live
 * instrument grid, and the three linked results views — the tenor-adjusted
 * forward grid (Start/End/DF/Yield), charts, and pillar nodes — with the hard QA
 * gates gating publish to golden copy.
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

function fmtMonths(months: number): string {
  if (months <= 0) return 'spot';
  if (months % 12 === 0) return `${months / 12}y`;
  return `${months}m`;
}

const inputClass =
  'rounded-md border border-border bg-surface-base px-2.5 py-1.5 text-body text-ink focus:border-focus focus:outline-none';

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

function RailStep({
  n,
  label,
  state,
}: {
  n: number;
  label: string;
  state: 'done' | 'current' | 'todo' | 'fail';
}) {
  const cls =
    state === 'done'
      ? 'bg-success-light text-success border-transparent'
      : state === 'current'
        ? 'bg-action-light text-action border-action/40'
        : state === 'fail'
          ? 'bg-critical-light text-critical border-transparent'
          : 'bg-surface text-slate-light border-border-light';
  return (
    <span
      className={`rounded border px-2.5 py-1 text-micro font-medium uppercase tracking-wide ${cls}`}
    >
      {n}. {label}
    </span>
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

  const [publishArmed, setPublishArmed] = useState(false);
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishError, setPublishError] = useState<ApiError | null>(null);
  const [publication, setPublication] = useState<DeskPublication | null>(null);

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
    setPublication(null);
    setPublishArmed(false);
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

  async function runPublish() {
    if (quotes.length === 0) return;
    setPublishBusy(true);
    setPublishError(null);
    try {
      const pub = await publishCurve({ curve_code: curveCode, as_of: asOf, quotes });
      setPublication(pub);
      setPublishArmed(false);
    } catch (err) {
      setPublishError(toApiError(err));
    }
    setPublishBusy(false);
  }

  const canPublish = active !== null && result !== null && result.qa.passed && !stale;

  // lifecycle rail states
  const stepDef: 'done' | 'fail' = active ? 'done' : 'fail';
  const stepQuotes = quotes.length > 0;
  const stepConstruct = result !== null && !stale;
  const stepQaFail = result !== null && !result.qa.passed;
  const stepPublished = publication !== null;

  return (
    <div>
      <Link
        href="/desk/curves"
        className="mb-4 inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
      >
        <ArrowLeft size={13} /> Curve definitions
      </Link>

      {defs.loading && (
        <div className="card space-y-3 p-5">
          <SkeletonRows rows={5} />
        </div>
      )}
      {defs.error && (
        <ErrorPanel error={defs.error} onRetry={defs.reload} context="Loading curve definitions" />
      )}

      {defs.data && definitions.length === 0 && (
        <div className="card p-5">
          <EmptyState
            title={`No definition named ${curveCode}`}
            hint={
              <>
                This curve code is not in the register.{' '}
                <Link href="/desk/curves" className="text-action hover:underline">
                  Back to definitions
                </Link>{' '}
                to create one.
              </>
            }
          />
        </div>
      )}

      {defs.data && definitions.length > 0 && (
        <div className="space-y-5">
          {/* Header */}
          <div className="card p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Waypoints size={18} className="text-slate" />
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
                    <Chip tone="warn">no approved definition effective on {asOf}</Chip>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <RailStep n={1} label="Definition" state={stepDef} />
                <RailStep n={2} label="Quotes" state={stepQuotes ? 'done' : 'current'} />
                <RailStep
                  n={3}
                  label="Construct"
                  state={stepConstruct ? 'done' : stepQuotes ? 'current' : 'todo'}
                />
                <RailStep
                  n={4}
                  label="QA"
                  state={
                    stepQaFail ? 'fail' : result && result.qa.passed && !stale ? 'done' : 'todo'
                  }
                />
                <RailStep n={5} label="Publish" state={stepPublished ? 'done' : 'todo'} />
              </div>
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
          <div className="card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
              <h2 className="text-h3 text-navy">Assumptions</h2>
              <Chip tone="warn">Track-2 governed · read-only</Chip>
            </div>
            <div className="grid gap-4 p-5 sm:grid-cols-2">
              <div>
                <div className="text-micro uppercase tracking-wide text-slate-light">
                  As-of date (cob)
                </div>
                <input
                  type="date"
                  value={asOf}
                  onChange={(e) => setAsOf(e.target.value)}
                  className={`${inputClass} mt-1`}
                />
                <p className="mt-1 text-micro text-slate-light">
                  Track-1 run input — valuation date; resolves the effective definition.
                </p>
              </div>
              <div className="flex items-end">
                <p className="text-caption text-slate">
                  Every field below is derived from the approved definition. Changing one is a
                  Track-2 event under{' '}
                  <Link href="/desk/curves" className="text-action hover:underline">
                    Curve definitions
                  </Link>
                  .
                </p>
              </div>
            </div>
            {active && (
              <div className="grid gap-4 border-t border-border-light px-5 py-4 sm:grid-cols-3 lg:grid-cols-4">
                <ParamCell label="Currency" mono>
                  {active.currency}
                </ParamCell>
                <ParamCell label="Calendar">{active.calendar_name}</ParamCell>
                <ParamCell label="Curve definition" mono>
                  {active.curve_code} v{active.version}
                </ParamCell>
                <ParamCell label="Curve kind">{active.curve_kind}</ParamCell>
                <ParamCell label="Projection index" mono>
                  {active.projection_index ?? DASH}
                </ParamCell>
                <ParamCell label="Discount curve" mono>
                  {active.discount_curve_code ?? DASH}
                </ParamCell>
                <ParamCell label="Payment frequency">
                  {active.payment_frequency ?? DASH}
                </ParamCell>
                <ParamCell label="Payment interval">{active.payment_interval_months}m</ParamCell>
                <ParamCell label="Curve frequency" mono>
                  {active.curve_frequency}
                </ParamCell>
                <ParamCell label="Interpolation">{active.interpolation_method}</ParamCell>
                <ParamCell label="Output basis (Convert to)" mono>
                  {active.output_daycount}
                </ParamCell>
                <ParamCell label="Spot lag">{active.spot_lag_days}d</ParamCell>
                <ParamCell label="Roll convention">{active.roll_convention}</ParamCell>
                <ParamCell label="Extrapolation">{active.extrapolation_rule}</ParamCell>
                <ParamCell label="Instrument set" mono>
                  {active.instrument_set_ref}
                </ParamCell>
              </div>
            )}
          </div>

          {/* Instrument grid */}
          <div className="card">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
              <div>
                <h2 className="text-h3 text-navy">Instrument grid</h2>
                <p className="mt-0.5 text-caption text-slate">
                  Enter this cob&apos;s quotes. Rate is a percentage; the OIS/discount leg defines
                  the discounting curve, the projection leg the forward index.
                </p>
              </div>
              <button
                type="button"
                onClick={addRow}
                className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-caption font-medium text-ink hover:bg-surface"
              >
                <Plus size={13} /> Add instrument
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-body">
                <thead>
                  <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                    <th className="px-3 py-2 font-medium">Include</th>
                    <th className="px-3 py-2 font-medium">Instrument</th>
                    <th className="px-3 py-2 font-medium">Tenor</th>
                    <th className="px-3 py-2 font-medium text-right">Quote %</th>
                    <th className="px-3 py-2 font-medium">Leg</th>
                    <th className="px-3 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
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
                        <select
                          value={r.instrument}
                          onChange={(e) => updateRow(r.key, { instrument: e.target.value })}
                          className={inputClass}
                        >
                          {INSTRUMENT_KINDS.map((k) => (
                            <option key={k.value} value={k.value}>
                              {k.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-1.5">
                        <input
                          value={r.tenor}
                          onChange={(e) => updateRow(r.key, { tenor: e.target.value })}
                          placeholder={r.instrument === 'fra' ? '3x6' : '3M'}
                          className={`${inputClass} w-24 font-mono`}
                        />
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <input
                          value={r.quotePct}
                          onChange={(e) => updateRow(r.key, { quotePct: e.target.value })}
                          inputMode="decimal"
                          placeholder="0.000"
                          className={`${inputClass} w-24 text-right font-mono`}
                        />
                      </td>
                      <td className="px-3 py-1.5">
                        <select
                          value={r.leg}
                          onChange={(e) =>
                            updateRow(r.key, { leg: e.target.value as DeskCurveLeg })
                          }
                          className={inputClass}
                        >
                          <option value="discount">discount</option>
                          <option value="projection">projection</option>
                        </select>
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
                  ))}
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-3 py-4 text-center text-caption text-slate">
                        No instruments — add at least one discount-leg quote.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center gap-3 border-t border-border-light px-5 py-3">
              <button
                type="button"
                disabled={constructBusy || quotes.length === 0 || !active}
                onClick={() => void runConstruction()}
                className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
              >
                {constructBusy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Calculator size={14} />
                )}
                Run construction
              </button>
              <span className="text-caption text-slate">
                {quotes.length} quote{quotes.length === 1 ? '' : 's'} ready
                {hasProjectionLeg ? ' · multi-curve (projection on discount)' : ' · self-discounting'}
              </span>
              {stale && (
                <Chip tone="warn">inputs changed — re-run construction</Chip>
              )}
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
          </div>

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
              <div className="card">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
                  <h2 className="text-h3 text-navy">QA gates</h2>
                  {result.qa.passed ? (
                    <Chip tone="ok">all hard gates pass</Chip>
                  ) : (
                    <Chip tone="crit">hard gate failed — publish blocked</Chip>
                  )}
                </div>
                <div className="px-5 py-2">
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
                </div>
              </div>

              {/* Results tabs */}
              <div className="card">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
                  <div className="flex items-end gap-1">
                    {(
                      [
                        { id: 'grid', label: 'Forward grid', icon: Table2 },
                        { id: 'charts', label: 'Charts', icon: TrendingUp },
                        { id: 'pillars', label: 'Pillar nodes', icon: Waypoints },
                      ] as const
                    ).map(({ id, label, icon: Icon }) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setTab(id)}
                        className={`-mb-px inline-flex items-center gap-1.5 rounded-t border-b-2 px-3 py-1.5 text-caption font-medium ${
                          tab === id
                            ? 'border-action text-action'
                            : 'border-transparent text-slate hover:text-ink'
                        }`}
                      >
                        <Icon size={13} /> {label}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 text-micro text-slate">
                    <span>
                      yields in <span className="font-mono text-ink">{result.output_basis}</span>
                    </span>
                    <span>· digest</span>
                    <span className="inline-flex items-center gap-0.5">
                      <span className="font-mono text-caption text-ink">
                        {result.input_digest.slice(0, 12)}…
                      </span>
                      <CopyButton value={result.input_digest} label="Copy input digest" />
                    </span>
                  </div>
                </div>

                {/* Forward grid table */}
                {tab === 'grid' && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-body">
                      <thead>
                        <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                          <th className="px-4 py-2 font-medium">#</th>
                          <th className="px-4 py-2 font-medium">Start date</th>
                          <th className="px-4 py-2 font-medium">End date</th>
                          <th className="px-4 py-2 font-medium text-right">Discount factor</th>
                          <th className="px-4 py-2 font-medium text-right">Yield</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.map((row, i) => (
                          <tr
                            key={i}
                            className={`border-b border-border-light last:border-b-0 ${
                              i === 0 ? 'bg-surface/60' : ''
                            }`}
                          >
                            <td className="px-4 py-1.5 font-mono text-micro text-slate">
                              {i === 0 ? 'spot' : i}
                            </td>
                            <td className="px-4 py-1.5 font-mono text-caption text-ink">
                              {fmtDate(row.start)}
                            </td>
                            <td className="px-4 py-1.5 font-mono text-caption text-ink">
                              {fmtDate(row.end)}
                            </td>
                            <td className="num px-4 py-1.5 text-ink">
                              {row.discount_factor.toFixed(7)}
                            </td>
                            <td className="num px-4 py-1.5 text-ink">
                              {i === 0 ? DASH : fmtPct(row.forward_yield)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Charts */}
                {tab === 'charts' && (
                  <div className="grid gap-6 p-5 lg:grid-cols-2">
                    <div>
                      <h3 className="mb-2 text-body font-medium text-navy">
                        Forward yield vs tenor
                      </h3>
                      <LineChart
                        ariaLabel="Forward yield versus tenor"
                        color="var(--chart-1)"
                        points={result.rows
                          .slice(1)
                          .map((row, i) => ({
                            x: (i + 1) * result.curve_frequency_months,
                            y: row.forward_yield * 100,
                          }))}
                        yFormat={(v) => `${v.toFixed(2)}%`}
                        xFormat={(v) => fmtMonths(v)}
                      />
                    </div>
                    <div>
                      <h3 className="mb-2 text-body font-medium text-navy">Discount factor curve</h3>
                      <LineChart
                        ariaLabel="Discount factor curve"
                        color="var(--chart-3)"
                        points={result.rows.map((row, i) => ({
                          x: i * result.curve_frequency_months,
                          y: row.discount_factor,
                        }))}
                        yFormat={(v) => v.toFixed(4)}
                        xFormat={(v) => fmtMonths(v)}
                      />
                    </div>
                  </div>
                )}

                {/* Pillar nodes */}
                {tab === 'pillars' && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-body">
                      <thead>
                        <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                          <th className="px-4 py-2 font-medium">Instrument</th>
                          <th className="px-4 py-2 font-medium">Tenor</th>
                          <th className="px-4 py-2 font-medium">Leg</th>
                          <th className="px-4 py-2 font-medium">Adjusted maturity</th>
                          <th className="px-4 py-2 font-medium text-right">Quote</th>
                          <th className="px-4 py-2 font-medium text-right">Discount factor</th>
                          <th className="px-4 py-2 font-medium text-right">Reprice residual</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.pillars.map((p, i) => (
                          <tr key={i} className="border-b border-border-light last:border-b-0">
                            <td className="px-4 py-1.5 text-caption text-ink">{p.instrument}</td>
                            <td className="px-4 py-1.5 font-mono text-caption text-ink">{p.tenor}</td>
                            <td className="px-4 py-1.5">
                              <Chip tone={p.leg === 'projection' ? 'accent' : 'neutral'}>{p.leg}</Chip>
                            </td>
                            <td className="px-4 py-1.5 font-mono text-caption text-ink">
                              {fmtDate(p.pillar_date)}
                            </td>
                            <td className="num px-4 py-1.5 text-ink">{fmtPct(p.quote)}</td>
                            <td className="num px-4 py-1.5 text-ink">{p.discount_factor.toFixed(7)}</td>
                            <td className="num px-4 py-1.5 text-slate">
                              {p.reprice_residual.toExponential(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Publish */}
              <div className="card p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-h3 text-navy">Publish to golden copy</h2>
                  {result.qa.passed ? (
                    <Chip tone="ok">QA green</Chip>
                  ) : (
                    <Chip tone="crit">QA blocks publish</Chip>
                  )}
                </div>
                <p className="mt-1 text-caption text-slate">
                  Publishing constructs the curve again server-side and fans it out to every bank
                  under <span className="font-mono text-ink">{curveCode}</span> through the desk
                  determination seam (bitemporal + lineage + audit). Dual control already happened on
                  the definition.
                </p>

                {canPublish ? (
                  <div className="mt-4 border-t border-border-light pt-4">
                    {publishArmed ? (
                      <div className="space-y-3">
                        <CeremonyBanner>
                          <p className="font-medium text-navy">
                            Confirm publish — this writes golden copy for every tenant
                          </p>
                          <p className="mt-1">
                            Curve <span className="font-mono">{curveCode}</span> v{active?.version} as
                            of {asOf}. Per-bank failures are recorded, not rolled back; re-publishing
                            heals a partial fan-out.
                          </p>
                        </CeremonyBanner>
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            disabled={publishBusy}
                            onClick={() => void runPublish()}
                            className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                          >
                            {publishBusy ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Upload size={14} />
                            )}
                            Confirm — publish to every bank
                          </button>
                          <button
                            type="button"
                            onClick={() => setPublishArmed(false)}
                            className="rounded border border-border px-3 py-1.5 text-body font-medium text-ink hover:bg-surface"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setPublishArmed(true);
                          setPublishError(null);
                        }}
                        className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium"
                      >
                        <Upload size={14} /> Publish…
                      </button>
                    )}
                  </div>
                ) : (
                  <p className="mt-4 border-t border-border-light pt-4 text-caption text-slate">
                    {stale
                      ? 'Re-run construction on the current inputs to enable publish.'
                      : result.qa.passed
                        ? 'Publish is available once an approved definition is effective on the as-of date.'
                        : 'Publish is disabled until every hard QA gate passes.'}
                  </p>
                )}

                {publishError && (
                  <div className="mt-3">
                    <ErrorPanel error={publishError} context="Publishing the curve" />
                  </div>
                )}

                {publication && (
                  <div className="mt-4 border-t border-border-light pt-4">
                    <h3 className="mb-2 text-body font-medium text-navy">Publication fan-out</h3>
                    <PublicationResults publication={publication} />
                  </div>
                )}
              </div>
            </>
          )}

          {!result && active && (
            <div className="card">
              <EmptyState
                title="No construction yet"
                hint="Enter this cob's quotes in the instrument grid above and Run construction to preview the forward grid, charts, pillar nodes and QA."
              />
            </div>
          )}

          {/* Methodology transparency drawer */}
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
                    <ParamCell label="Instrument set" mono>
                      {active.instrument_set_ref}
                    </ParamCell>
                    <ParamCell label="Interpolation">{active.interpolation_method}</ParamCell>
                    <ParamCell label="Output basis" mono>
                      {active.output_daycount}
                    </ParamCell>
                    <ParamCell label="Calendar">{active.calendar_name}</ParamCell>
                    <ParamCell label="Curve frequency" mono>
                      {active.curve_frequency}
                    </ParamCell>
                    <ParamCell label="Payment interval">
                      {active.payment_interval_months}m
                    </ParamCell>
                    <ParamCell label="Spot lag">{active.spot_lag_days}d</ParamCell>
                    <ParamCell label="Roll convention">{active.roll_convention}</ParamCell>
                    <ParamCell label="Extrapolation">{active.extrapolation_rule}</ParamCell>
                    <ParamCell label="Projection index" mono>
                      {active.projection_index ?? DASH}
                    </ParamCell>
                    <ParamCell label="Discount curve" mono>
                      {active.discount_curve_code ?? DASH}
                    </ParamCell>
                    <ParamCell label="Proposed by" mono>
                      {active.proposed_by}
                    </ParamCell>
                    <ParamCell label="Approved by" mono>
                      {active.approved_by ?? DASH}
                    </ParamCell>
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
    </div>
  );
}
