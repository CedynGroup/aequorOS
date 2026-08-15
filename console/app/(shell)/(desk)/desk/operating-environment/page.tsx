'use client';

import { useMemo, useState, type ReactNode } from 'react';
import {
  Building2,
  Calculator,
  CheckCircle2,
  FileText,
  Gauge,
  Info,
  Landmark,
  Loader2,
  ShieldCheck,
  Upload,
  XCircle,
} from 'lucide-react';
import {
  approveOperatingEnvironmentAssessment,
  computeOperatingEnvironmentPreview,
  getWorkforceSession,
  listOperatingEnvironmentAssessments,
  publishOperatingEnvironmentAssessment,
  stageOperatingEnvironmentAssessment,
  submitOperatingEnvironmentAssessment,
  toApiError,
  type ApiError,
  type OperatingEnvironmentAssessment,
  type OperatingEnvironmentBreakdown,
  type OperatingEnvironmentComputeRequest,
  type OperatingEnvironmentInputsBody,
  type OperatingEnvironmentPreview,
  type OperatingEnvironmentProvenance,
  type OperatingEnvironmentPublishResult,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import { DeterminationStatusPill } from '@/components/desk';
import {
  Chip,
  CopyButton,
  EmptyState,
  ErrorPanel,
  PageHeader,
  SkeletonRows,
  StatusChip,
  type Tone,
} from '@/components/ui';

/**
 * /desk/operating-environment — the Operating-Environment workbench
 * (spec docs/internal/operating_environment_score.md).
 *
 * The desk's surface for the governed, data-derived jurisdiction
 * operating-environment score (GHANA_OPERATING_ENVIRONMENT_SCORE ∈ [0,1], one
 * value for the whole banking system). Deliberately mirrors the Curve
 * Construction workspace: an inputs panel (the observable BICRA sub-factor
 * inputs, grouped by the two pillars; sovereign rating + policy rate shown
 * auto-pulled; the one analyst judgment marked), a live compute that renders
 * the full breakdown (input → sub-score → sub-factor → pillar → composite →
 * [0,1] strength, capped by the sovereign governor), a read-only assumptions
 * panel (the versioned methodology parameters, Track-2 to change), and the
 * maker-checker lifecycle rail draft → submit → approve → publish with
 * four-eyes enforced visually.
 */

// ---------------------------------------------------------------------------
// Input metadata — mirrors app/domain/rating/operating_environment.py
// (DEFAULT_PARAMETERS): the two pillars, their sub-factors, and the observable
// inputs. policy_rate_pct and sovereign_rating are auto-pulled overrides and
// are handled apart from the desk-entered fields.
// ---------------------------------------------------------------------------

interface FieldMeta {
  code: string;
  label: string;
  unit: string;
  placeholder: string;
}

interface SubFactorMeta {
  code: string;
  label: string;
  fields: FieldMeta[];
}

interface PillarMeta {
  code: string;
  label: string;
  blurb: string;
  icon: typeof Building2;
  subFactors: SubFactorMeta[];
}

const PILLARS: PillarMeta[] = [
  {
    code: 'economic',
    label: 'Economic risk',
    blurb: 'Risk of the economy the banking system operates in.',
    icon: Building2,
    subFactors: [
      {
        code: 'economic_resilience',
        label: 'Economic resilience',
        fields: [
          { code: 'real_gdp_growth_pct', label: 'Real GDP growth', unit: '%', placeholder: '4.0' },
          { code: 'gdp_per_capita_usd', label: 'GDP per capita', unit: 'USD', placeholder: '2200' },
        ],
      },
      {
        code: 'economic_imbalances',
        label: 'Economic imbalances',
        fields: [
          { code: 'cpi_inflation_pct', label: 'CPI inflation', unit: '%', placeholder: '23' },
          {
            code: 'private_credit_to_gdp_growth_pct',
            label: 'Private-credit / GDP growth',
            unit: '%',
            placeholder: '11',
          },
        ],
      },
      {
        code: 'credit_risk_economy',
        label: 'Credit risk in economy',
        fields: [
          { code: 'system_npl_pct', label: 'System NPL ratio', unit: '%', placeholder: '21' },
          {
            code: 'private_debt_to_gdp_pct',
            label: 'Private debt / GDP',
            unit: '%',
            placeholder: '32',
          },
        ],
      },
    ],
  },
  {
    code: 'industry',
    label: 'Industry risk',
    blurb: 'Risk of the banking system itself.',
    icon: Landmark,
    subFactors: [
      // institutional_framework is the judgment + sovereign sub-factor — rendered specially.
      {
        code: 'competitive_dynamics',
        label: 'Competitive dynamics',
        fields: [
          { code: 'system_roa_pct', label: 'System ROA', unit: '%', placeholder: '1.2' },
          {
            code: 'system_credit_growth_pct',
            label: 'System credit growth',
            unit: '%',
            placeholder: '18',
          },
        ],
      },
      {
        code: 'systemwide_funding',
        label: 'System-wide funding',
        fields: [
          {
            code: 'system_loan_to_deposit_pct',
            label: 'System loan-to-deposit',
            unit: '%',
            placeholder: '70',
          },
          { code: 'system_car_pct', label: 'System CAR', unit: '%', placeholder: '14' },
          {
            code: 'external_funding_pct',
            label: 'External-funding reliance',
            unit: '%',
            placeholder: '25',
          },
        ],
      },
    ],
  },
];

/** Every desk-entered numeric (threshold) input code — all required for compute. */
const THRESHOLD_FIELD_CODES: string[] = PILLARS.flatMap((p) =>
  p.subFactors.flatMap((sf) => sf.fields.map((f) => f.code)),
);

/** Pretty labels for the breakdown view (includes the auto-pulled codes). */
const INPUT_LABELS: Record<string, string> = {
  real_gdp_growth_pct: 'Real GDP growth',
  gdp_per_capita_usd: 'GDP per capita',
  cpi_inflation_pct: 'CPI inflation',
  private_credit_to_gdp_growth_pct: 'Private-credit / GDP growth',
  policy_rate_pct: 'Policy rate (MPR)',
  system_npl_pct: 'System NPL ratio',
  private_debt_to_gdp_pct: 'Private debt / GDP',
  regulatory_quality_score: 'Regulatory quality',
  sovereign_rating: 'Sovereign rating',
  system_roa_pct: 'System ROA',
  system_credit_growth_pct: 'System credit growth',
  system_loan_to_deposit_pct: 'System loan-to-deposit',
  system_car_pct: 'System CAR',
  external_funding_pct: 'External-funding reliance',
};

const SUBFACTOR_LABELS: Record<string, string> = {
  economic_resilience: 'Economic resilience',
  economic_imbalances: 'Economic imbalances',
  credit_risk_economy: 'Credit risk in economy',
  institutional_framework: 'Institutional framework',
  competitive_dynamics: 'Competitive dynamics',
  systemwide_funding: 'System-wide funding',
};

const PILLAR_LABELS: Record<string, string> = {
  economic: 'Economic risk',
  industry: 'Industry risk',
};

const INPUT_UNITS: Record<string, string> = {
  real_gdp_growth_pct: '%',
  gdp_per_capita_usd: 'USD',
  cpi_inflation_pct: '%',
  private_credit_to_gdp_growth_pct: '%',
  policy_rate_pct: '%',
  system_npl_pct: '%',
  private_debt_to_gdp_pct: '%',
  system_roa_pct: '%',
  system_credit_growth_pct: '%',
  system_loan_to_deposit_pct: '%',
  system_car_pct: '%',
  external_funding_pct: '%',
};

const inputClass =
  'rounded-md border border-border bg-surface-base px-2.5 py-1.5 text-body text-ink focus:border-focus focus:outline-none';

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

function labelFor(map: Record<string, string>, code: string): string {
  return map[code] ?? code.replace(/_/g, ' ');
}

function toNum(value: string | number): number {
  return typeof value === 'number' ? value : Number(value);
}

function fmtScore(value: string | number): string {
  const n = toNum(value);
  return Number.isFinite(n) ? n.toFixed(3) : String(value);
}

function fmtRisk(value: string | number): string {
  const n = toNum(value);
  return Number.isFinite(n) ? n.toFixed(2) : String(value);
}

function fmtWeight(value: string | number): string {
  const n = toNum(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(0)}%` : String(value);
}

/** Sub-scores and pillar/sub-factor risk are 1..6 (1 = lowest risk). */
function riskTone(value: number): Tone {
  if (!Number.isFinite(value)) return 'neutral';
  if (value <= 2) return 'ok';
  if (value <= 4) return 'warn';
  return 'crit';
}

// ---------------------------------------------------------------------------
// Small presentational helpers (local, matching the curve workspace)
// ---------------------------------------------------------------------------

function RailStep({
  n,
  label,
  state,
}: {
  n: number;
  label: string;
  state: 'done' | 'current' | 'todo';
}) {
  const cls =
    state === 'done'
      ? 'bg-success-light text-success border-transparent'
      : state === 'current'
        ? 'bg-action-light text-action border-action/40'
        : 'bg-surface text-slate-light border-border-light';
  return (
    <span className={`rounded border px-2.5 py-1 text-micro font-medium uppercase tracking-wide ${cls}`}>
      {n}. {label}
    </span>
  );
}

function StatCell({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-micro uppercase tracking-wide text-slate-light">{label}</div>
      <div className="num mt-0.5 text-h3 text-ink">{children}</div>
      {hint && <div className="mt-0.5 text-micro text-slate-light">{hint}</div>}
    </div>
  );
}

function RiskChip({ value }: { value: number }) {
  return (
    <Chip tone={riskTone(value)}>
      {Number.isInteger(value) ? value : value.toFixed(2)} / 6
    </Chip>
  );
}

/** Human display of one input's observed value inside the breakdown table. */
function inputValueDisplay(
  code: string,
  kind: string,
  value: string,
  sovereignCategory: string,
): string {
  if (kind === 'sovereign') return sovereignCategory.toUpperCase();
  if (kind === 'judgment') return `${value} (judgment)`;
  const unit = INPUT_UNITS[code];
  return unit && unit !== 'USD' ? `${value}${unit}` : unit === 'USD' ? `$${value}` : value;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OperatingEnvironmentPage() {
  const [jurisdiction, setJurisdiction] = useState('GH');
  const [cobDate, setCobDate] = useState(() => new Date().toISOString().slice(0, 10));

  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(THRESHOLD_FIELD_CODES.map((c) => [c, ''])),
  );
  const [regQuality, setRegQuality] = useState('4');
  const [sovereignRating, setSovereignRating] = useState('');
  const [policyRate, setPolicyRate] = useState('');

  const [preview, setPreview] = useState<OperatingEnvironmentPreview | null>(null);
  const [previewSig, setPreviewSig] = useState<string | null>(null);
  const [computeBusy, setComputeBusy] = useState(false);
  const [computeError, setComputeError] = useState<ApiError | null>(null);

  const [assessment, setAssessment] = useState<OperatingEnvironmentAssessment | null>(null);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<ApiError | null>(null);
  const [publication, setPublication] = useState<OperatingEnvironmentPublishResult | null>(null);

  const session = useApi(() => getWorkforceSession(), []);
  const operatorEmail = session.data?.email ?? null;

  const recent = useApi(
    () => listOperatingEnvironmentAssessments({ jurisdictionCode: jurisdiction }),
    [jurisdiction],
  );

  // A single value-based signature over every editable input — the staleness
  // heuristic (a computed preview is stale once any input drifts from it).
  const currentSig = useMemo(
    () =>
      JSON.stringify({ jurisdiction, cobDate, values, regQuality, sovereignRating, policyRate }),
    [jurisdiction, cobDate, values, regQuality, sovereignRating, policyRate],
  );
  const stale = preview !== null && previewSig !== currentSig;

  const missingFields = THRESHOLD_FIELD_CODES.filter(
    (c) => values[c].trim() === '' || Number.isNaN(Number(values[c])),
  );
  const inputsReady = missingFields.length === 0;

  function updateValue(code: string, next: string) {
    setValues((prev) => ({ ...prev, [code]: next }));
  }

  function buildRequest(): OperatingEnvironmentComputeRequest | null {
    if (!inputsReady) return null;
    const inputs: OperatingEnvironmentInputsBody = {
      real_gdp_growth_pct: values.real_gdp_growth_pct.trim(),
      gdp_per_capita_usd: values.gdp_per_capita_usd.trim(),
      cpi_inflation_pct: values.cpi_inflation_pct.trim(),
      private_credit_to_gdp_growth_pct: values.private_credit_to_gdp_growth_pct.trim(),
      system_npl_pct: values.system_npl_pct.trim(),
      private_debt_to_gdp_pct: values.private_debt_to_gdp_pct.trim(),
      regulatory_quality_score: Number(regQuality),
      system_roa_pct: values.system_roa_pct.trim(),
      system_credit_growth_pct: values.system_credit_growth_pct.trim(),
      system_loan_to_deposit_pct: values.system_loan_to_deposit_pct.trim(),
      system_car_pct: values.system_car_pct.trim(),
      external_funding_pct: values.external_funding_pct.trim(),
    };
    // Optional overrides — omit when blank so the backend auto-pulls (the model
    // is closed, so never send an unset key).
    if (sovereignRating.trim() !== '') inputs.sovereign_rating = sovereignRating.trim();
    if (policyRate.trim() !== '') inputs.policy_rate_pct = policyRate.trim();
    return { jurisdiction_code: jurisdiction.trim().toUpperCase(), cob_date: cobDate, inputs };
  }

  async function runCompute() {
    const body = buildRequest();
    if (body === null) return;
    setComputeBusy(true);
    setComputeError(null);
    // A fresh compute discards any staged draft — the governed artifact must
    // reflect the inputs that were reviewed.
    setAssessment(null);
    setPublication(null);
    setLifecycleError(null);
    try {
      const res = await computeOperatingEnvironmentPreview(body);
      setPreview(res);
      setPreviewSig(currentSig);
    } catch (err) {
      setComputeError(toApiError(err));
    }
    setComputeBusy(false);
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
      const body = buildRequest();
      if (body === null) return;
      const row = await stageOperatingEnvironmentAssessment(body);
      setAssessment(row);
      recent.reload();
    });
  const submitDraft = () =>
    runLifecycle(async () => {
      if (!assessment) return;
      setAssessment(await submitOperatingEnvironmentAssessment(assessment.id));
      recent.reload();
    });
  const approveDraft = () =>
    runLifecycle(async () => {
      if (!assessment) return;
      setAssessment(await approveOperatingEnvironmentAssessment(assessment.id));
      recent.reload();
    });
  const publishDraft = () =>
    runLifecycle(async () => {
      if (!assessment) return;
      const result = await publishOperatingEnvironmentAssessment(assessment.id);
      setPublication(result);
      setAssessment((prev) =>
        prev ? { ...prev, status: result.status === 'failed' ? prev.status : 'published' } : prev,
      );
      recent.reload();
    });

  /** Rehydrate the workbench from a stored assessment (e.g. a second operator
   * resuming a pending_review draft to approve it). */
  function loadAssessment(row: OperatingEnvironmentAssessment) {
    const snap = row.inputs;
    const nextValues = Object.fromEntries(
      THRESHOLD_FIELD_CODES.map((c) => [c, snap.observations[c] ?? '']),
    ) as Record<string, string>;
    const nextReg = String(snap.judgments.regulatory_quality_score ?? 4);
    const sovProv = snap.provenance?.sovereign;
    const rateProv = snap.provenance?.policy_rate;
    const nextSov =
      sovProv?.source === 'desk_entered' ? (sovProv.rating ?? '') : '';
    const nextRate =
      rateProv?.source === 'desk_entered' ? (snap.observations.policy_rate_pct ?? '') : '';

    setJurisdiction(row.jurisdiction_code);
    setCobDate(row.cob_date);
    setValues(nextValues);
    setRegQuality(nextReg);
    setSovereignRating(nextSov);
    setPolicyRate(nextRate);

    // Present the stored breakdown as the live preview (identical inputs).
    setPreview({
      jurisdiction_code: row.jurisdiction_code,
      cob_date: row.cob_date,
      methodology_version: row.methodology_version,
      score: row.score,
      input_digest: row.input_digest,
      inputs: row.inputs,
      breakdown: row.breakdown,
    });
    setPreviewSig(
      JSON.stringify({
        jurisdiction: row.jurisdiction_code,
        cobDate: row.cob_date,
        values: nextValues,
        regQuality: nextReg,
        sovereignRating: nextSov,
        policyRate: nextRate,
      }),
    );
    setAssessment(row);
    setPublication(null);
    setComputeError(null);
    setLifecycleError(null);
  }

  const isProposer =
    operatorEmail !== null &&
    assessment !== null &&
    operatorEmail.toLowerCase() === assessment.proposed_by.toLowerCase();
  const canStage = preview !== null && !stale && assessment === null;

  const status = assessment?.status ?? null;
  const breakdown = preview?.breakdown ?? null;

  // lifecycle rail states
  const stepCompute: 'done' | 'current' | 'todo' = preview !== null && !stale ? 'done' : inputsReady ? 'current' : 'todo';
  const stepStage = assessment !== null;
  const stepSubmitted = status === 'pending_review' || status === 'approved' || status === 'published';
  const stepApproved = status === 'approved' || status === 'published';
  const stepPublished = publication !== null || status === 'published';

  return (
    <div className="space-y-5">
      <PageHeader
        title="Operating environment"
        sub="A governed, data-derived jurisdiction operating-environment score (0–1, higher = stronger banking system). Computed BICRA-style over two pillars, capped near the sovereign, and published as one systematic input to every tenant's implied-rating model under maker-checker."
      />

      {/* Lifecycle rail */}
      <div className="card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Gauge size={18} className="text-slate" />
              <h2 className="text-h3 text-navy">Determination lifecycle</h2>
              {assessment ? (
                <DeterminationStatusPill status={assessment.status} />
              ) : preview ? (
                <Chip tone="accent">computed — not yet staged</Chip>
              ) : (
                <Chip>no computation yet</Chip>
              )}
            </div>
            <p className="mt-1 text-caption text-slate">
              Maker-checker, four-eyes: an analyst computes and stages a draft, submits it, a{' '}
              <span className="font-medium">distinct</span> supervisor approves, and only then does it
              publish to every tenant. History is never rewritten.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <RailStep n={1} label="Compute" state={stepCompute} />
            <RailStep n={2} label="Stage draft" state={stepStage ? 'done' : stepCompute === 'done' ? 'current' : 'todo'} />
            <RailStep
              n={3}
              label="Submit"
              state={stepSubmitted ? 'done' : status === 'draft' ? 'current' : 'todo'}
            />
            <RailStep
              n={4}
              label="Approve"
              state={stepApproved ? 'done' : status === 'pending_review' ? 'current' : 'todo'}
            />
            <RailStep
              n={5}
              label="Publish"
              state={stepPublished ? 'done' : status === 'approved' ? 'current' : 'todo'}
            />
          </div>
        </div>
      </div>

      {/* Inputs panel */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
          <h2 className="text-h3 text-navy">Observable inputs</h2>
          <Chip tone="warn">desk-entered · v1 (BoG feed ingestion later)</Chip>
        </div>

        <div className="grid gap-4 border-b border-border-light px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <div className="text-micro uppercase tracking-wide text-slate-light">Jurisdiction</div>
            <input
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value.toUpperCase())}
              maxLength={8}
              className={`${inputClass} mt-1 w-full font-mono uppercase`}
              placeholder="GH"
            />
          </div>
          <div>
            <div className="text-micro uppercase tracking-wide text-slate-light">COB date</div>
            <input
              type="date"
              value={cobDate}
              onChange={(e) => setCobDate(e.target.value)}
              className={`${inputClass} mt-1 w-full`}
            />
          </div>
          <div className="sm:col-span-2 lg:col-span-2 flex items-end">
            <p className="text-caption text-slate">
              One value for the whole system. Enter the economic and banking-system aggregates below;
              the sovereign rating and policy rate are auto-pulled from published data unless you
              override them.
            </p>
          </div>
        </div>

        {/* Two pillars */}
        <div className="grid gap-0 lg:grid-cols-2">
          {PILLARS.map((pillar, pi) => {
            const Icon = pillar.icon;
            return (
              <div
                key={pillar.code}
                className={`px-5 py-4 ${pi === 0 ? 'lg:border-r border-border-light' : ''}`}
              >
                <div className="mb-3 flex items-center gap-2">
                  <Icon size={15} className="text-slate" />
                  <h3 className="text-body font-medium text-navy">{pillar.label}</h3>
                  <span className="text-micro text-slate-light">{pillar.blurb}</span>
                </div>

                {/* The judgment + sovereign sub-factor leads the industry pillar. */}
                {pillar.code === 'industry' && (
                  <div className="mb-4 rounded border border-border-light bg-surface/50 p-3">
                    <div className="mb-2 text-micro uppercase tracking-wide text-slate-light">
                      Institutional framework
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="block">
                        <span className="mb-1 flex items-center gap-1.5 text-caption font-medium text-slate">
                          Regulatory quality
                          <Chip tone="accent">analyst judgment</Chip>
                        </span>
                        <select
                          value={regQuality}
                          onChange={(e) => setRegQuality(e.target.value)}
                          className={`${inputClass} w-full`}
                        >
                          {[1, 2, 3, 4, 5, 6].map((n) => (
                            <option key={n} value={n}>
                              {n} — {n <= 2 ? 'strong' : n <= 4 ? 'adequate' : 'weak'}
                            </option>
                          ))}
                        </select>
                        <span className="mt-1 block text-micro text-slate-light">
                          1 = lowest risk … 6 = highest risk (documented judgment sub-score).
                        </span>
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-caption font-medium text-slate">
                          Sovereign rating
                        </span>
                        <input
                          value={sovereignRating}
                          onChange={(e) => setSovereignRating(e.target.value)}
                          className={`${inputClass} w-full font-mono`}
                          placeholder="auto-pull (e.g. BBB-)"
                          spellCheck={false}
                        />
                        <span className="mt-1 block text-micro text-slate-light">
                          Blank → pulled from the published sovereign rating.
                        </span>
                      </label>
                    </div>
                  </div>
                )}

                <div className="space-y-4">
                  {pillar.subFactors.map((sf) => (
                    <div key={sf.code}>
                      <div className="mb-2 text-micro uppercase tracking-wide text-slate-light">
                        {sf.label}
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {sf.fields.map((f) => (
                          <label key={f.code} className="block">
                            <span className="mb-1 block text-caption font-medium text-slate">
                              {f.label} <span className="text-slate-light">({f.unit})</span>
                            </span>
                            <input
                              value={values[f.code]}
                              onChange={(e) => updateValue(f.code, e.target.value)}
                              inputMode="decimal"
                              placeholder={f.placeholder}
                              className={`${inputClass} w-full text-right font-mono`}
                            />
                          </label>
                        ))}
                        {/* policy rate rides economic_imbalances but is auto-pulled */}
                        {sf.code === 'economic_imbalances' && (
                          <label className="block">
                            <span className="mb-1 block text-caption font-medium text-slate">
                              Policy rate / MPR <span className="text-slate-light">(%)</span>
                            </span>
                            <input
                              value={policyRate}
                              onChange={(e) => setPolicyRate(e.target.value)}
                              inputMode="decimal"
                              placeholder="auto-pull"
                              className={`${inputClass} w-full text-right font-mono`}
                            />
                            <span className="mt-1 block text-micro text-slate-light">
                              Blank → pulled from the published MPR.
                            </span>
                          </label>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Compute action */}
        <div className="flex flex-wrap items-center gap-3 border-t border-border-light px-5 py-3">
          <button
            type="button"
            disabled={computeBusy || !inputsReady}
            onClick={() => void runCompute()}
            className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            {computeBusy ? <Loader2 size={14} className="animate-spin" /> : <Calculator size={14} />}
            Compute
          </button>
          {inputsReady ? (
            <span className="text-caption text-slate">
              12 observable inputs + 1 judgment ready · sovereign &amp; policy rate{' '}
              {sovereignRating.trim() || policyRate.trim() ? 'partly overridden' : 'auto-pulled'}
            </span>
          ) : (
            <span className="text-caption text-slate">
              {missingFields.length} input{missingFields.length === 1 ? '' : 's'} still needed
            </span>
          )}
          {stale && <Chip tone="warn">inputs changed — re-compute</Chip>}
        </div>
        {computeError && (
          <div className="px-5 pb-5">
            <ErrorPanel error={computeError} context="Computing the operating-environment score" />
            {computeError.status === 409 && (
              <p className="mt-2 text-caption text-slate">
                An auto-pulled input (published sovereign rating or MPR) is missing for this COB.
                Enter it explicitly above, or publish it first.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Result breakdown */}
      {preview && breakdown && (
        <>
          {stale && (
            <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
              <Info size={15} className="mt-0.5 shrink-0 text-warning" />
              <p className="text-caption text-slate">
                Inputs changed since this score was computed. Re-compute before staging — the figures
                below are from the previous inputs.
              </p>
            </div>
          )}

          <ScoreCard breakdown={breakdown} inputDigest={preview.input_digest} />

          <PillarBreakdown breakdown={breakdown} />

          <ProvenancePanel provenance={breakdown.provenance} />

          {/* Governance & publication */}
          <div className="card p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-h3 text-navy">Governance &amp; publication</h2>
              {assessment ? (
                <DeterminationStatusPill status={assessment.status} />
              ) : (
                <Chip tone="ok">ready to stage</Chip>
              )}
            </div>
            <p className="mt-1 text-caption text-slate">
              Staging opens a governed determination. It moves draft → submit → approve → publish;
              publishing fans{' '}
              <span className="font-mono text-ink">GHANA_OPERATING_ENVIRONMENT_SCORE</span> out to
              every tenant so the implied-rating model picks it up unchanged.
            </p>

            {/* Stage */}
            {!assessment && (
              <div className="mt-4 border-t border-border-light pt-4">
                {canStage ? (
                  <button
                    type="button"
                    disabled={lifecycleBusy}
                    onClick={() => void stageDraft()}
                    className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                  >
                    {lifecycleBusy ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <FileText size={14} />
                    )}
                    Stage draft assessment
                  </button>
                ) : (
                  <p className="text-caption text-slate">
                    Re-compute on the current inputs to stage a draft.
                  </p>
                )}
              </div>
            )}

            {/* Lifecycle actions once a draft exists */}
            {assessment && (
              <div className="mt-4 space-y-3 border-t border-border-light pt-4">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-slate">
                  <span>
                    assessment <span className="font-mono text-ink">{assessment.id.slice(0, 8)}…</span>
                  </span>
                  <span>
                    proposed by <span className="font-mono text-ink">{assessment.proposed_by}</span>
                  </span>
                  {assessment.approved_by && (
                    <span>
                      approved by{' '}
                      <span className="font-mono text-ink">{assessment.approved_by}</span>
                      {assessment.approved_at ? ` · ${relTime(assessment.approved_at)}` : ''}
                    </span>
                  )}
                </div>

                {assessment.status === 'draft' && (
                  <button
                    type="button"
                    disabled={lifecycleBusy}
                    onClick={() => void submitDraft()}
                    className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                  >
                    {lifecycleBusy ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Upload size={14} />
                    )}
                    Submit for review
                  </button>
                )}

                {assessment.status === 'pending_review' && (
                  <div className="space-y-2">
                    <button
                      type="button"
                      disabled={lifecycleBusy || isProposer}
                      onClick={() => void approveDraft()}
                      className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {lifecycleBusy ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <ShieldCheck size={14} />
                      )}
                      Approve (four-eyes)
                    </button>
                    {isProposer && (
                      <p className="flex items-center gap-1.5 text-caption text-warning">
                        <XCircle size={13} className="shrink-0" />
                        You proposed this assessment — a different supervisor must approve it.
                      </p>
                    )}
                  </div>
                )}

                {assessment.status === 'approved' && (
                  <div className="space-y-3">
                    <div className="flex items-start gap-2 rounded border border-warning/50 bg-warning-light p-3">
                      <ShieldCheck size={15} className="mt-0.5 shrink-0 text-warning" />
                      <div className="min-w-0 text-caption text-slate">
                        <p className="font-medium text-navy">
                          Approved — publishing writes the jurisdiction index for every tenant
                        </p>
                        <p className="mt-1">
                          Score {fmtScore(assessment.score)} for {assessment.jurisdiction_code} as of{' '}
                          {fmtDate(assessment.cob_date)}. Per-tenant failures are recorded, not rolled
                          back; re-publishing heals a partial fan-out.
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={lifecycleBusy}
                      onClick={() => void publishDraft()}
                      className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-body font-medium disabled:opacity-50"
                    >
                      {lifecycleBusy ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Upload size={14} />
                      )}
                      Publish to every tenant
                    </button>
                  </div>
                )}

                {assessment.status === 'published' && !publication && (
                  <p className="flex items-center gap-1.5 text-caption text-success">
                    <CheckCircle2 size={13} className="shrink-0" /> Published as the jurisdiction
                    index.
                  </p>
                )}
              </div>
            )}

            {lifecycleError && (
              <div className="mt-3">
                <ErrorPanel error={lifecycleError} context="Operating-environment governance action" />
              </div>
            )}

            {publication && (
              <div className="mt-4 border-t border-border-light pt-4">
                <PublishFanout publication={publication} />
              </div>
            )}
          </div>
        </>
      )}

      {/* Assumptions panel */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
          <h2 className="text-h3 text-navy">Assumptions</h2>
          <Chip tone="warn">Track-2 governed · read-only</Chip>
        </div>
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCell label="Methodology version">
            <span className="font-mono text-body">{breakdown?.methodology_version ?? DASH}</span>
          </StatCell>
          <StatCell label="Risk scale" hint="composite risk domain">
            {breakdown ? `${fmtRisk(breakdown.risk_min)}–${fmtRisk(breakdown.risk_max)}` : '1–6'}
          </StatCell>
          <StatCell label="Pillars" hint="economic + industry, equal-weighted">
            2
          </StatCell>
          <StatCell label="Strength range" hint="published score domain">
            0–1
          </StatCell>
        </div>
        <div className="border-t border-border-light px-5 py-4 text-caption text-slate">
          Every threshold table, sub-factor and pillar weight, the risk min/max, the sovereign-risk
          table and the sovereign-governor map are{' '}
          <span className="font-medium text-ink">versioned methodology parameters</span> (documented
          calibration placeholders pending independent validation). Changing any of them is a Track-2
          event under the methodology register — never a per-run edit.
        </div>
      </div>

      {/* Recent assessments */}
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
          <h2 className="text-h3 text-navy">Recent assessments</h2>
          <span className="text-caption text-slate">
            {jurisdiction} · {recent.data?.total ?? 0} total
          </span>
        </div>
        {recent.loading && <SkeletonRows rows={4} />}
        {recent.error && (
          <div className="p-5">
            <ErrorPanel error={recent.error} onRetry={recent.reload} context="Loading assessments" />
          </div>
        )}
        {recent.data && recent.data.assessments.length === 0 && (
          <EmptyState
            title="No assessments yet"
            hint="Compute a score above and stage the first draft for this jurisdiction."
          />
        )}
        {recent.data && recent.data.assessments.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-body">
              <thead>
                <tr className="border-b border-border-light bg-surface text-left text-micro uppercase tracking-wide text-slate">
                  <th className="px-5 py-2 font-medium">COB</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium text-right">Score</th>
                  <th className="px-3 py-2 font-medium">Methodology</th>
                  <th className="px-3 py-2 font-medium">Proposed by</th>
                  <th className="px-3 py-2 font-medium">Approved by</th>
                  <th className="px-3 py-2 font-medium">Created</th>
                  <th className="px-5 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {recent.data.assessments.map((a) => {
                  const isOpen = assessment?.id === a.id;
                  return (
                    <tr
                      key={a.id}
                      className={`border-b border-border-light last:border-b-0 ${
                        isOpen ? 'bg-action-light/30' : ''
                      }`}
                    >
                      <td className="px-5 py-2 font-mono text-caption text-ink">
                        {fmtDate(a.cob_date)}
                      </td>
                      <td className="px-3 py-2">
                        <DeterminationStatusPill status={a.status} />
                      </td>
                      <td className="num px-3 py-2 text-right text-ink">{fmtScore(a.score)}</td>
                      <td className="px-3 py-2 font-mono text-micro text-slate">
                        {a.methodology_version}
                      </td>
                      <td className="px-3 py-2 font-mono text-micro text-slate">{a.proposed_by}</td>
                      <td className="px-3 py-2 font-mono text-micro text-slate">
                        {a.approved_by ? (
                          <span title={fmtTs(a.approved_at)}>{a.approved_by}</span>
                        ) : (
                          DASH
                        )}
                      </td>
                      <td className="px-3 py-2 text-micro text-slate" title={fmtTs(a.created_at)}>
                        {relTime(a.created_at)}
                      </td>
                      <td className="px-5 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => loadAssessment(a)}
                          className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-micro font-medium text-ink hover:bg-surface"
                        >
                          {isOpen ? 'Loaded' : 'Load'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-views
// ---------------------------------------------------------------------------

function ScoreCard({
  breakdown,
  inputDigest,
}: {
  breakdown: OperatingEnvironmentBreakdown;
  inputDigest: string;
}) {
  const score = toNum(breakdown.score);
  const pct = Number.isFinite(score) ? Math.max(0, Math.min(1, score)) * 100 : 0;
  const governor = breakdown.governor_applied;
  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="text-micro uppercase tracking-wide text-slate-light">
            Operating-environment strength
          </div>
          <div className="num mt-1 text-[2.75rem] leading-none text-navy">{fmtScore(breakdown.score)}</div>
          <p className="mt-1 text-caption text-slate">
            0–1, higher = stronger banking system. Feeds every tenant&apos;s implied-rating model.
          </p>
          {/* [0,1] meter */}
          <div className="mt-3 h-2 w-64 max-w-full overflow-hidden rounded-full bg-surface">
            <div
              className="h-full rounded-full bg-[color:rgb(var(--accent))]"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-3">
          <StatCell label="Composite risk" hint="1 (low) – 6 (high)">
            {fmtRisk(breakdown.composite_risk)}
          </StatCell>
          <StatCell label="Raw strength" hint="before sovereign cap">
            {fmtScore(breakdown.strength_raw)}
          </StatCell>
          <StatCell label="Sovereign" hint="rating category">
            <span className="uppercase">{breakdown.sovereign_category}</span>
          </StatCell>
          <StatCell label="Governor cap" hint="max plausible strength">
            {fmtScore(breakdown.governor_cap)}
          </StatCell>
          <div className="min-w-0">
            <div className="text-micro uppercase tracking-wide text-slate-light">Governor</div>
            <div className="mt-1">
              {governor ? (
                <Chip tone="warn">binding — capped</Chip>
              ) : (
                <Chip tone="ok">not binding</Chip>
              )}
            </div>
          </div>
          <div className="min-w-0">
            <div className="text-micro uppercase tracking-wide text-slate-light">Input digest</div>
            <div className="mt-1 inline-flex items-center gap-0.5">
              <span className="font-mono text-caption text-ink">{inputDigest.slice(0, 12)}…</span>
              <CopyButton value={inputDigest} label="Copy input digest" />
            </div>
          </div>
        </div>
      </div>
      {governor && (
        <p className="mt-4 flex items-start gap-1.5 border-t border-border-light pt-3 text-caption text-slate">
          <Info size={13} className="mt-0.5 shrink-0 text-warning" />
          The sovereign governor is binding: raw strength {fmtScore(breakdown.strength_raw)} exceeds
          the {breakdown.sovereign_category.toUpperCase()} cap of {fmtScore(breakdown.governor_cap)},
          so the published score is held at the cap — a banking system can&apos;t be much stronger
          than its sovereign.
        </p>
      )}
    </div>
  );
}

function PillarBreakdown({ breakdown }: { breakdown: OperatingEnvironmentBreakdown }) {
  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
        <h2 className="text-h3 text-navy">Breakdown</h2>
        <span className="text-caption text-slate">input → sub-score → sub-factor → pillar</span>
      </div>
      <div className="space-y-0">
        {breakdown.pillars.map((pillar, pi) => (
          <div key={pillar.code} className={pi > 0 ? 'border-t border-border-light' : ''}>
            <div className="flex flex-wrap items-center justify-between gap-2 bg-surface/60 px-5 py-2.5">
              <div className="flex items-center gap-2">
                <h3 className="text-body font-medium text-navy">
                  {labelFor(PILLAR_LABELS, pillar.code)}
                </h3>
                <span className="text-micro text-slate-light">
                  weight {fmtWeight(pillar.weight)}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-micro uppercase tracking-wide text-slate-light">
                  pillar risk
                </span>
                <RiskChip value={toNum(pillar.score)} />
              </div>
            </div>

            {pillar.sub_factors.map((sf) => (
              <div key={sf.code} className="border-t border-border-light px-5 py-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-caption font-medium text-ink">
                      {labelFor(SUBFACTOR_LABELS, sf.code)}
                    </span>
                    <span className="text-micro text-slate-light">
                      weight {fmtWeight(sf.weight)} of pillar
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-micro uppercase tracking-wide text-slate-light">
                      sub-factor risk
                    </span>
                    <RiskChip value={toNum(sf.score)} />
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-body">
                    <thead>
                      <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
                        <th className="py-1.5 pr-4 font-medium">Input</th>
                        <th className="py-1.5 pr-4 font-medium">Kind</th>
                        <th className="py-1.5 pr-4 font-medium text-right">Observed</th>
                        <th className="py-1.5 pr-4 font-medium text-right">Sub-score</th>
                        <th className="py-1.5 font-medium text-right">Weight</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sf.inputs.map((item) => (
                        <tr key={item.code} className="border-b border-border-light last:border-b-0">
                          <td className="py-1.5 pr-4 text-caption text-ink">
                            {labelFor(INPUT_LABELS, item.code)}
                          </td>
                          <td className="py-1.5 pr-4">
                            <Chip tone={item.kind === 'judgment' ? 'accent' : 'neutral'}>
                              {item.kind}
                            </Chip>
                          </td>
                          <td className="num py-1.5 pr-4 text-right text-ink">
                            {inputValueDisplay(
                              item.code,
                              item.kind,
                              item.value,
                              breakdown.sovereign_category,
                            )}
                          </td>
                          <td className="py-1.5 pr-4 text-right">
                            <RiskChip value={item.sub_score} />
                          </td>
                          <td className="num py-1.5 text-right text-slate">
                            {fmtWeight(item.weight)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function ProvenancePanel({ provenance }: { provenance: OperatingEnvironmentProvenance }) {
  const rows: { label: string; data: Record<string, string> | undefined }[] = [
    { label: 'Sovereign rating', data: provenance.sovereign },
    { label: 'Policy rate (MPR)', data: provenance.policy_rate },
  ];
  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-light px-5 py-3">
        <h2 className="text-h3 text-navy">Input provenance</h2>
        <span className="text-caption text-slate">auto-pulled unless desk-entered</span>
      </div>
      <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
        {rows.map((row) => (
          <div key={row.label} className="rounded border border-border-light bg-surface/40 p-3">
            <div className="mb-1 text-caption font-medium text-navy">{row.label}</div>
            {row.data ? (
              <dl className="space-y-0.5">
                {Object.entries(row.data).map(([k, v]) => (
                  <div key={k} className="flex items-baseline justify-between gap-3">
                    <dt className="text-micro uppercase tracking-wide text-slate-light">
                      {k.replace(/_/g, ' ')}
                    </dt>
                    <dd className="font-mono text-caption text-ink">{v}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-caption text-slate">{DASH}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function PublishFanout({ publication }: { publication: OperatingEnvironmentPublishResult }) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
        <h3 className="text-body font-medium text-navy">Publication fan-out</h3>
        <StatusChip value={publication.status} />
        <span>
          fanned out to <span className="font-mono text-ink">{publication.banks}</span> tenant
          {publication.banks === 1 ? '' : 's'}
        </span>
      </div>
      {publication.results.length === 0 ? (
        <p className="mt-2 text-caption text-slate">
          No per-tenant results recorded — there were no banks to publish to.
        </p>
      ) : (
        <table className="mt-2 w-full text-body">
          <thead>
            <tr className="border-b border-border-light text-left text-micro uppercase tracking-wide text-slate">
              <th className="py-1.5 pr-4 font-medium">Bank</th>
              <th className="py-1.5 pr-4 font-medium">Ingestion batch</th>
              <th className="py-1.5 pr-4 font-medium">Status</th>
              <th className="py-1.5 font-medium">Error</th>
            </tr>
          </thead>
          <tbody>
            {publication.results.map((r, i) => (
              <tr key={r.bank_id ?? i} className="border-b border-border-light last:border-b-0">
                <td className="py-1.5 pr-4 font-mono text-caption text-ink">{r.bank_id ?? DASH}</td>
                <td className="py-1.5 pr-4 font-mono text-caption text-slate">
                  {r.ingestion_batch_id ?? DASH}
                </td>
                <td className="py-1.5 pr-4">
                  <StatusChip value={r.status} />
                </td>
                <td className="py-1.5 text-caption text-critical">{r.error ?? DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
