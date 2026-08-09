'use client';

import { useMemo, useState } from 'react';
import { AlertTriangle, Loader2, ScrollText, ShieldCheck } from 'lucide-react';
import {
  approveDeskMethodologyVersion,
  ensureDefaultDeskMethodology,
  listDeskMethodologies,
  proposeDeskMethodologyVersion,
  toApiError,
  type ApiError,
  type DeskMethodology,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, DASH } from '@/lib/format';
import {
  Chip,
  EmptyState,
  ErrorPanel,
  FieldRow,
  PageHeader,
  SkeletonRows,
  StatusChip,
} from '@/components/ui';

/**
 * /desk/methodology — the methodology register (spec §5): for each code,
 * every version with its full parameter set, rationale, proposer, approver,
 * and effective date. This is the artifact a bank's due-diligence team or a
 * regulator inspects.
 *
 * Track 2 is DELIBERATELY heavier than the weekly screen (spec §11a: "the
 * two must be visibly different actions"): proposing or approving a version
 * goes through an amber ceremony panel, never a one-click affordance.
 *
 * Source: GET/POST /operator/v1/desk/methodologies…
 */

// ---------------------------------------------------------------------------
// Parameter rendering: a readable grouped tree, never a raw JSON dump.
// Groups mirror the §5 step structure; unknown keys land in "Other
// parameters" so nothing the register holds is ever hidden.
// ---------------------------------------------------------------------------

const PARAM_GROUPS: { title: string; keys: string[] }[] = [
  {
    title: 'Data quality (steps 1–2)',
    keys: [
      'parameter_status',
      'min_trade_count',
      'max_staleness_days',
      'outlier_zscore_bound',
      'weighting_scheme',
      'aggregation_window_days',
    ],
  },
  {
    title: 'Curve construction (steps 3–6)',
    keys: [
      'target_day_count',
      'target_compounding',
      'interpolation_method',
      'nss_fallback_min_liquid_points',
      'nss_bounds',
      'extrapolation_rule',
      'extrapolation_anchor_tenor_y',
      'oscillation_tolerance',
      'enforce_positive_forwards',
      'forward_qa_grid',
    ],
  },
  {
    title: 'Discounting — AGD (step 7)',
    keys: [
      'mpc_meeting_dates',
      'overnight_spread_window_bdays',
      'overnight_spread_bps',
      'expected_policy_moves_bps',
      'discount_basis',
      'agd_node_grid_months',
      'fwd_node_grid_months',
    ],
  },
  { title: 'Cointegration diagnostic', keys: ['cointegration'] },
  {
    title: 'GRR & derived values (step 8)',
    keys: ['grr_formula', 'grr_check_tolerance_pp', 'liquidity_premium_bps_by_tenor'],
  },
];

const GROUPED_KEYS = new Set([...PARAM_GROUPS.flatMap((g) => g.keys), 'series_methodologies']);

function isPrimitive(v: unknown): v is string | number | boolean | null {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v);
}

/** Recursive readable value: primitives inline, arrays listed, objects nested. */
function ParamValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-light">not set</span>;
  }
  if (isPrimitive(value)) {
    return <span className="break-words font-mono text-caption text-ink">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.every(isPrimitive)) {
      return (
        <span className="break-words font-mono text-caption text-ink">
          {value.map(String).join(', ')}
        </span>
      );
    }
    // Array of objects (e.g. mpc_meeting_dates) — compact rows.
    return (
      <div className="space-y-0.5">
        {value.map((item, i) => (
          <div key={i} className="font-mono text-caption text-ink">
            {isPrimitive(item) ? (
              String(item)
            ) : (
              Object.entries(item as Record<string, unknown>)
                .map(([k, v]) => `${k}=${isPrimitive(v) ? String(v) : JSON.stringify(v)}`)
                .join(' · ')
            )}
          </div>
        ))}
      </div>
    );
  }
  // Object — nested key/value rows; 'verdict' keys get a chip for visibility
  // (the cointegration local_evidence verdict is a governance datum).
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    <div className={depth > 0 ? 'border-l border-border-light pl-3' : ''}>
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-3 py-0.5">
          <span className="shrink-0 text-caption text-slate">{k.replace(/_/g, ' ')}</span>
          <span className="min-w-0 text-right">
            {k === 'verdict' && typeof v === 'string' ? (
              <Chip tone={/reject|not_/.test(v) ? 'warn' : 'ok'}>{v.replace(/_/g, ' ')}</Chip>
            ) : (
              <ParamValue value={v} depth={depth + 1} />
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

function SeriesTreatmentsTable({ table }: { table: Record<string, unknown> }) {
  const rows = Object.entries(table);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body">
        <thead>
          <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
            <th className="py-1.5 pr-3 font-medium">Series / pattern</th>
            <th className="py-1.5 pr-3 font-medium">Treatment</th>
            <th className="py-1.5 pr-3 font-medium">Unit</th>
            <th className="py-1.5 pr-3 font-medium">Max staleness</th>
            <th className="py-1.5 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([code, spec]) => {
            const s = (spec ?? {}) as Record<string, unknown>;
            const rest = Object.entries(s).filter(
              ([k]) => !['treatment', 'unit', 'max_staleness_days'].includes(k),
            );
            return (
              <tr key={code} className="border-b border-border-light last:border-b-0">
                <td className="py-1.5 pr-3 font-mono text-caption text-ink">{code}</td>
                <td className="py-1.5 pr-3">
                  {typeof s.treatment === 'string' ? (
                    <Chip mono>{s.treatment}</Chip>
                  ) : (
                    <span className="text-slate-light">{DASH}</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-caption text-slate">
                  {typeof s.unit === 'string' ? s.unit : DASH}
                </td>
                <td className="num py-1.5 pr-3 text-left text-caption text-slate">
                  {s.max_staleness_days !== undefined ? `${String(s.max_staleness_days)}d` : DASH}
                </td>
                <td className="py-1.5 text-micro text-slate">
                  {rest.length === 0
                    ? DASH
                    : rest
                        .map(
                          ([k, v]) =>
                            `${k}=${isPrimitive(v) ? String(v) : JSON.stringify(v)}`,
                        )
                        .join(' · ')}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ParameterTree({ parameters }: { parameters: Record<string, unknown> }) {
  const otherKeys = Object.keys(parameters).filter((k) => !GROUPED_KEYS.has(k));
  const rawTreatments = parameters.series_methodologies;
  const treatments =
    rawTreatments && typeof rawTreatments === 'object' && !Array.isArray(rawTreatments)
      ? (rawTreatments as Record<string, unknown>)
      : null;
  return (
    <div className="space-y-4">
      {PARAM_GROUPS.map((group) => {
        const present = group.keys.filter((k) => k in parameters);
        if (present.length === 0) return null;
        return (
          <div key={group.title} className="rounded border border-border-light bg-surface p-3">
            <h4 className="mb-1 text-micro font-medium uppercase tracking-wide text-slate">
              {group.title}
            </h4>
            <div className="divide-y divide-border-light">
              {present.map((key) => (
                <div key={key} className="flex items-baseline justify-between gap-4 py-1.5">
                  <span className="shrink-0 text-caption text-slate">
                    {key.replace(/_/g, ' ')}
                  </span>
                  <span className="min-w-0 text-right">
                    <ParamValue value={parameters[key]} />
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {treatments && (
        <div className="rounded border border-border-light bg-surface p-3">
          <h4 className="mb-1 text-micro font-medium uppercase tracking-wide text-slate">
            Series treatments (declared per series — undeclared series are refused)
          </h4>
          <SeriesTreatmentsTable table={treatments} />
        </div>
      )}

      {otherKeys.length > 0 && (
        <div className="rounded border border-border-light bg-surface p-3">
          <h4 className="mb-1 text-micro font-medium uppercase tracking-wide text-slate">
            Other parameters
          </h4>
          <div className="divide-y divide-border-light">
            {otherKeys.map((key) => (
              <div key={key} className="flex items-baseline justify-between gap-4 py-1.5">
                <span className="shrink-0 text-caption text-slate">{key.replace(/_/g, ' ')}</span>
                <span className="min-w-0 text-right">
                  <ParamValue value={parameters[key]} />
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The Track-2 ceremony banner — the visual weight that separates a
// methodology change from daily work.
// ---------------------------------------------------------------------------

function CeremonyBanner({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" />
      <div className="min-w-0 text-caption text-slate">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The page.
// ---------------------------------------------------------------------------

export default function MethodologyPage() {
  const { data, error, loading, reload } = useApi(() => listDeskMethodologies());

  // versions grouped by code, ascending version (API sorts that way already).
  const byCode = useMemo(() => {
    const map = new Map<string, DeskMethodology[]>();
    for (const row of data?.methodologies ?? []) {
      const bucket = map.get(row.methodology_code) ?? [];
      bucket.push(row);
      map.set(row.methodology_code, bucket);
    }
    return map;
  }, [data]);

  // Selected version per code; default = current approved, else latest.
  const [selected, setSelected] = useState<Record<string, number>>({});
  function currentApproved(versions: DeskMethodology[]): DeskMethodology | null {
    const approved = versions.filter((v) => v.status === 'approved');
    return approved.length > 0 ? approved[approved.length - 1] : null;
  }
  function selectedVersion(code: string, versions: DeskMethodology[]): DeskMethodology {
    const pick = selected[code];
    const found = versions.find((v) => v.version === pick);
    return found ?? currentApproved(versions) ?? versions[versions.length - 1];
  }

  // ---- bootstrap (empty register) ----------------------------------------
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<ApiError | null>(null);
  async function seedDefault() {
    setSeeding(true);
    setSeedError(null);
    try {
      await ensureDefaultDeskMethodology();
      reload();
    } catch (err) {
      setSeedError(toApiError(err));
    }
    setSeeding(false);
  }

  // ---- Track 2: propose ----------------------------------------------------
  const [proposeFor, setProposeFor] = useState<string | null>(null);
  const [rationale, setRationale] = useState('');
  const [paramsText, setParamsText] = useState('');
  const [proposeError, setProposeError] = useState<ApiError | null>(null);
  const [proposing, setProposing] = useState(false);

  function openPropose(code: string, versions: DeskMethodology[]) {
    const base = selectedVersion(code, versions);
    setProposeFor(code);
    setRationale('');
    setParamsText(JSON.stringify(base.parameters, null, 2));
    setProposeError(null);
  }

  const paramsParse = useMemo((): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } => {
    if (proposeFor === null) return { ok: false, error: '' };
    try {
      const parsed: unknown = JSON.parse(paramsText);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Parameters must be a JSON object.' };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : 'Invalid JSON.' };
    }
  }, [proposeFor, paramsText]);

  async function submitPropose() {
    if (proposeFor === null || !paramsParse.ok) return;
    setProposing(true);
    setProposeError(null);
    try {
      await proposeDeskMethodologyVersion(proposeFor, {
        parameters: paramsParse.value,
        change_rationale: rationale.trim(),
      });
      setProposeFor(null);
      reload();
    } catch (err) {
      setProposeError(toApiError(err));
    }
    setProposing(false);
  }

  // ---- Track 2: approve ----------------------------------------------------
  const [approveFor, setApproveFor] = useState<{ code: string; version: number } | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState(() =>
    new Date().toISOString().slice(0, 10),
  );
  const [approveError, setApproveError] = useState<ApiError | null>(null);
  const [approving, setApproving] = useState(false);

  const approveDualControl =
    approveError !== null &&
    (approveError.status === 409 || approveError.status === 422) &&
    /proposer/i.test(approveError.message);

  async function submitApprove() {
    if (approveFor === null) return;
    setApproving(true);
    setApproveError(null);
    try {
      await approveDeskMethodologyVersion(approveFor.code, approveFor.version, {
        effective_from: effectiveFrom,
      });
      setApproveFor(null);
      reload();
    } catch (err) {
      setApproveError(toApiError(err));
    }
    setApproving(false);
  }

  const inputClass =
    'w-full rounded-md border border-border bg-surface-base px-3 py-2 text-body text-ink placeholder:text-slate-light focus:border-focus focus:outline-none';

  return (
    <div>
      <PageHeader
        title="Methodology register"
        sub="Track 2: versioned parameters under dual control. The weekly run READS from this register; changing it is a rare, documented, effective-dated event — never something done in passing during a publish."
      />

      {loading && (
        <div className="card">
          <SkeletonRows rows={6} />
        </div>
      )}
      {error && <ErrorPanel error={error} onRetry={reload} context="Loading the register" />}

      {data && data.methodologies.length === 0 && (
        <div className="card p-5">
          <EmptyState
            title="No methodologies registered"
            hint="Seed the default AEQ-GHS-CURVES v1 draft below. Seeding only creates the DRAFT — a second operator must approve it (Track 2) before any determination can run."
          />
          <div className="flex justify-center pb-6">
            <button
              type="button"
              disabled={seeding}
              onClick={() => void seedDefault()}
              className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:opacity-50"
            >
              {seeding && <Loader2 size={14} className="animate-spin" />}
              Seed default methodology (v1 draft)
            </button>
          </div>
          {seedError && (
            <div className="px-5 pb-5">
              <ErrorPanel error={seedError} context="Seeding the default methodology" />
            </div>
          )}
        </div>
      )}

      {data &&
        [...byCode.entries()].map(([code, versions]) => {
          const active = currentApproved(versions);
          const shown = selectedVersion(code, versions);
          return (
            <section key={code} className="card mb-6">
              {/* -------------------------------------------- code header */}
              <div className="flex flex-wrap items-center gap-3 border-b border-border-light px-5 py-3">
                <ScrollText size={16} className="text-slate" />
                <h2 className="text-h3 text-navy">
                  <span className="font-mono">{code}</span>
                </h2>
                {active ? (
                  <Chip tone="ok">
                    current approved · v{active.version}
                    {active.effective_from ? ` · effective ${fmtDate(active.effective_from)}` : ''}
                  </Chip>
                ) : (
                  <Chip tone="warn">no approved version — determinations will be refused</Chip>
                )}
                <button
                  type="button"
                  onClick={() =>
                    proposeFor === code ? setProposeFor(null) : openPropose(code, versions)
                  }
                  className="ml-auto inline-flex items-center gap-1.5 rounded border border-warning/60 bg-warning-light px-3 py-1.5 text-caption font-medium text-warning hover:opacity-90"
                >
                  <AlertTriangle size={13} /> Propose new version (Track 2)
                </button>
              </div>

              {/* ------------------------------------------- version list */}
              <div className="overflow-x-auto border-b border-border-light">
                <table className="w-full text-body">
                  <thead>
                    <tr className="bg-surface text-left text-micro uppercase tracking-wide text-slate">
                      <th className="px-5 py-2 font-medium">Version</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Effective from</th>
                      <th className="px-3 py-2 font-medium">Proposed by</th>
                      <th className="px-3 py-2 font-medium">Approved by</th>
                      <th className="px-5 py-2 font-medium">Rationale</th>
                      <th className="px-5 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => {
                      const isActive = active?.version === v.version;
                      const isShown = shown.version === v.version;
                      return (
                        <tr
                          key={v.id}
                          onClick={() => setSelected((s) => ({ ...s, [code]: v.version }))}
                          className={`cursor-pointer border-b border-border-light last:border-b-0 ${
                            isActive ? 'bg-success-light/40' : ''
                          } ${isShown ? 'outline outline-1 -outline-offset-1 outline-action/40' : ''} hover:bg-surface`}
                        >
                          <td className="px-5 py-2 font-mono text-ink">v{v.version}</td>
                          <td className="px-3 py-2">
                            <StatusChip value={v.status} />
                          </td>
                          <td className="px-3 py-2 text-caption text-ink">
                            {fmtDate(v.effective_from)}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.proposed_by}
                          </td>
                          <td className="px-3 py-2 font-mono text-micro text-slate">
                            {v.approved_by ? (
                              <span title={fmtTs(v.approved_at)}>{v.approved_by}</span>
                            ) : (
                              DASH
                            )}
                          </td>
                          <td
                            className="max-w-sm truncate px-5 py-2 text-caption text-slate"
                            title={v.change_rationale}
                          >
                            {v.change_rationale}
                          </td>
                          <td className="px-5 py-2 text-right">
                            {v.status === 'draft' && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setApproveFor(
                                    approveFor?.code === code && approveFor.version === v.version
                                      ? null
                                      : { code, version: v.version },
                                  );
                                  setApproveError(null);
                                }}
                                className="inline-flex items-center gap-1 rounded border border-warning/60 bg-warning-light px-2.5 py-1 text-micro font-medium uppercase tracking-wide text-warning hover:opacity-90"
                              >
                                <ShieldCheck size={12} /> Approve…
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* ----------------------------------------- approve ceremony */}
              {approveFor?.code === code && (
                <div className="space-y-3 border-b border-border-light px-5 py-4">
                  <CeremonyBanner>
                    <p className="font-medium text-navy">
                      Track-2 approval — v{approveFor.version} of{' '}
                      <span className="font-mono">{code}</span>
                    </p>
                    <p className="mt-1">
                      Approving makes this parameter set the governing methodology from its
                      effective date. Dual control applies: the proposer cannot approve their own
                      version. This is an audited, effective-dated event; history is never
                      silently altered.
                    </p>
                  </CeremonyBanner>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="block">
                      <span className="mb-1 block text-caption font-medium text-slate">
                        Effective from
                      </span>
                      <input
                        type="date"
                        className={inputClass}
                        value={effectiveFrom}
                        onChange={(e) => setEffectiveFrom(e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={approving || !effectiveFrom}
                      onClick={() => void submitApprove()}
                      className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:opacity-50"
                    >
                      {approving && <Loader2 size={14} className="animate-spin" />}
                      Approve v{approveFor.version}
                    </button>
                    <button
                      type="button"
                      onClick={() => setApproveFor(null)}
                      className="rounded border border-border px-4 py-2 text-body font-medium text-ink hover:bg-surface"
                    >
                      Cancel
                    </button>
                  </div>
                  {approveError && approveDualControl && (
                    <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                      <ShieldCheck size={14} className="mt-0.5 shrink-0 text-warning" />
                      <div className="min-w-0">
                        <p className="text-body font-medium text-navy">
                          Dual control: the proposer cannot approve their own methodology version
                          — sign in as a second operator.
                        </p>
                        <p className="mt-1 text-caption text-slate">
                          API said: “{approveError.message}”
                        </p>
                      </div>
                    </div>
                  )}
                  {approveError && !approveDualControl && (
                    <ErrorPanel error={approveError} context="Approving the version" />
                  )}
                </div>
              )}

              {/* ----------------------------------------- propose ceremony */}
              {proposeFor === code && (
                <div className="space-y-3 border-b border-border-light px-5 py-4">
                  <CeremonyBanner>
                    <p className="font-medium text-navy">
                      Track-2 methodology change — this is NOT part of a weekly publish
                    </p>
                    <p className="mt-1">
                      Changing any versioned parameter or formula is a controlled event: it
                      drafts v{versions[versions.length - 1].version + 1} with a documented
                      rationale, requires approval by a second operator at a higher bar, and is
                      effective-dated. Running determinations keep their bound version.
                    </p>
                  </CeremonyBanner>
                  <label className="block">
                    <span className="mb-1 block text-caption font-medium text-slate">
                      Change rationale (required — recorded in the register)
                    </span>
                    <textarea
                      rows={3}
                      className={inputClass}
                      value={rationale}
                      onChange={(e) => setRationale(e.target.value)}
                      placeholder="Why is this parameter set changing? Cite re-estimation evidence."
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-caption font-medium text-slate">
                      Parameters (JSON — prefilled from v{shown.version})
                    </span>
                    <textarea
                      rows={16}
                      spellCheck={false}
                      className={`${inputClass} font-mono text-caption`}
                      value={paramsText}
                      onChange={(e) => setParamsText(e.target.value)}
                    />
                  </label>
                  {!paramsParse.ok && paramsParse.error && (
                    <p className="text-caption text-critical">JSON error: {paramsParse.error}</p>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={proposing || rationale.trim() === '' || !paramsParse.ok}
                      onClick={() => void submitPropose()}
                      className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {proposing && <Loader2 size={14} className="animate-spin" />}
                      Propose v{versions[versions.length - 1].version + 1}
                    </button>
                    <button
                      type="button"
                      onClick={() => setProposeFor(null)}
                      className="rounded border border-border px-4 py-2 text-body font-medium text-ink hover:bg-surface"
                    >
                      Cancel
                    </button>
                  </div>
                  {proposeError && (
                    <ErrorPanel error={proposeError} context="Proposing the version" />
                  )}
                </div>
              )}

              {/* --------------------------------------- parameter set view */}
              <div className="px-5 py-4">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <h3 className="text-body font-medium text-navy">
                    Parameter set — v{shown.version}
                  </h3>
                  <StatusChip value={shown.status} />
                  {active?.version === shown.version && <Chip tone="ok">governing version</Chip>}
                </div>
                <div className="mb-3 grid gap-x-10 sm:grid-cols-2">
                  <FieldRow label="Proposed by">
                    <span className="font-mono text-caption">{shown.proposed_by}</span>
                  </FieldRow>
                  <FieldRow label="Approved by">
                    {shown.approved_by ? (
                      <span className="font-mono text-caption" title={fmtTs(shown.approved_at)}>
                        {shown.approved_by}
                      </span>
                    ) : (
                      DASH
                    )}
                  </FieldRow>
                  <FieldRow label="Effective from">{fmtDate(shown.effective_from)}</FieldRow>
                  <FieldRow label="Created">{fmtDate(shown.created_at)}</FieldRow>
                </div>
                <p className="mb-4 rounded border border-border-light bg-surface p-3 text-caption text-slate">
                  <span className="font-medium text-ink">Rationale:</span> {shown.change_rationale}
                </p>
                <ParameterTree parameters={shown.parameters} />
              </div>
            </section>
          );
        })}
    </div>
  );
}
