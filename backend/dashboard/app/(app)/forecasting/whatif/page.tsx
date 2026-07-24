'use client';

/**
 * What-if Lab — two-panel shock laboratory over the deterministic 5-year
 * projection engine. The left panel lists the shock library (the exact
 * shock codes the API accepts) with the shocked-vs-base assumption diff
 * from the persisted result; the right panel compares the base and shocked
 * paths on any persisted metric, with per-year deltas and run provenance.
 * Mutation flow (useRunWhatIf + stored-run hydration) is unchanged.
 */

import { useState } from 'react';
import { FlaskConical, Loader2, PlayCircle } from 'lucide-react';
import type {
  ProjectionYearRead,
  RegulatoryRunRead,
  WhatIfResultRead,
  WhatIfShockCode,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DeltaBadge from '@/components/ui/DeltaBadge';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import RunProvenance from '@/components/forecasting/RunProvenance';
import ScenarioLinesChart, {
  type ScenarioPoint,
} from '@/components/forecasting/charts/ScenarioLinesChart';
import { ASSUMPTION_FIELDS } from '@/components/forecasting/lib';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunWhatIf,
} from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

// ---------------------------------------------------------------------------
// Shock library — the four shock codes the what-if endpoint accepts.
// ---------------------------------------------------------------------------

const SHOCKS: { code: WhatIfShockCode; label: string; description: string }[] = [
  {
    code: 'rate_shock_up_400',
    label: 'Interest rate shock +400bps',
    description:
      'Sustained policy tightening — funding costs reprice faster than the loan book.',
  },
  {
    code: 'cedi_depreciation_20',
    label: 'Local-currency depreciation 20%',
    description:
      'Local-currency depreciation inflates FX-linked risk-weighted assets across the horizon.',
  },
  {
    code: 'default_spike',
    label: 'Loan default spike (2.5× credit losses)',
    description:
      'Sectoral concentration risk materializes — annual credit losses multiply 2.5×.',
  },
  {
    code: 'mpr_cut_200',
    label: 'Policy rate cut −200bps',
    description:
      'Easing cycle compresses the net interest margin as assets reprice downward.',
  },
];

// ---------------------------------------------------------------------------
// Normalized view — fresh results and stored regulatory runs carry the same
// payload (full projection paths, assumptions, deltas, year-5 comparison).
// ---------------------------------------------------------------------------

type PathPoint = {
  year: number;
  carPct: number;
  lcrPct: number;
  nsfrPct: number;
  netIncome: number | null;
  totalAssets: number;
};

type DeltaRow = {
  year: number;
  carDeltaPp: number;
  lcrDeltaPp: number;
  nsfrDeltaPp: number;
  netIncomeDelta: number;
};

type Comparison = { base: number; shocked: number; delta: number };

type WhatIfView = {
  basePath: PathPoint[];
  shockedPath: PathPoint[];
  deltas: DeltaRow[];
  year5: {
    carPct: Comparison;
    lcrPct: Comparison;
    nsfrPct: Comparison;
    netIncome: Comparison;
  };
  baseAssumptions: Record<string, number>;
  shockedAssumptions: Record<string, number>;
  provenance: { runId: string; inputHash: string; createdAt: Date | null };
};

function pointFromRead(p: ProjectionYearRead): PathPoint {
  return {
    year: p.year,
    carPct: num(p.carPct),
    lcrPct: num(p.lcrPct),
    nsfrPct: num(p.nsfrPct),
    netIncome: p.year === 0 ? null : num(p.netIncome),
    totalAssets: num(p.totalAssets),
  };
}

function fromResult(result: WhatIfResultRead): WhatIfView | null {
  // Failed runs come back as data (status: failed, year5: null) — the caller
  // surfaces result.error; there is nothing to project here.
  if (!result.year5) return null;
  const year5 = result.year5;
  const comparison = (c: { base: string; shocked: string; delta: string }) => ({
    base: num(c.base),
    shocked: num(c.shocked),
    delta: num(c.delta),
  });
  const assumptionMap = (a: Record<string, unknown>) => {
    const out: Record<string, number> = {};
    for (const field of ASSUMPTION_FIELDS) {
      const raw = (a as Record<string, string | undefined>)[field.key];
      if (raw !== undefined) out[field.apiKey] = num(raw);
    }
    return out;
  };
  return {
    basePath: result.basePath.map(pointFromRead),
    shockedPath: result.shockedPath.map(pointFromRead),
    deltas: result.deltas.map((d) => ({
      year: d.year,
      carDeltaPp: num(d.carDeltaPp),
      lcrDeltaPp: num(d.lcrDeltaPp),
      nsfrDeltaPp: num(d.nsfrDeltaPp),
      netIncomeDelta: num(d.netIncomeDelta),
    })),
    year5: {
      carPct: comparison(year5.carPct),
      lcrPct: comparison(year5.lcrPct),
      nsfrPct: comparison(year5.nsfrPct),
      netIncome: comparison(year5.netIncome),
    },
    baseAssumptions: assumptionMap(
      result.baseAssumptions as unknown as Record<string, unknown>
    ),
    shockedAssumptions: assumptionMap(
      result.shockedAssumptions as unknown as Record<string, unknown>
    ),
    provenance: {
      runId: result.runId,
      inputHash: result.inputHash,
      createdAt: result.createdAt,
    },
  };
}

/** Stored what-if regulatory runs carry the payload as raw snake_case metrics. */
function fromStoredRun(run: RegulatoryRunRead): WhatIfView | null {
  type RawYear = Record<string, string | number | null> & { year: number };
  const metrics = run.metrics as {
    base_path?: RawYear[];
    shocked_path?: RawYear[];
    base_assumptions?: Record<string, string>;
    shocked_assumptions?: Record<string, string>;
    deltas?: {
      year: number;
      car_delta_pp: string;
      lcr_delta_pp: string;
      nsfr_delta_pp: string;
      net_income_delta: string;
    }[];
    year5?: Record<string, { base: string; shocked: string; delta: string }>;
  };
  if (!Array.isArray(metrics.base_path) || !metrics.year5) return null;

  const point = (p: RawYear): PathPoint => ({
    year: p.year,
    carPct: num(p.car_pct as string),
    lcrPct: num(p.lcr_pct as string),
    nsfrPct: num(p.nsfr_pct as string),
    netIncome: p.year === 0 ? null : num(p.net_income as string),
    totalAssets: num(p.total_assets as string),
  });
  const comparison = (key: string): Comparison => {
    const c = metrics.year5?.[key];
    return c
      ? { base: num(c.base), shocked: num(c.shocked), delta: num(c.delta) }
      : { base: 0, shocked: 0, delta: 0 };
  };
  const assumptionMap = (a: Record<string, string> | undefined) => {
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(a ?? {})) out[key] = num(value);
    return out;
  };

  return {
    basePath: metrics.base_path.map(point),
    shockedPath: (metrics.shocked_path ?? []).map(point),
    deltas: (metrics.deltas ?? []).map((d) => ({
      year: d.year,
      carDeltaPp: num(d.car_delta_pp),
      lcrDeltaPp: num(d.lcr_delta_pp),
      nsfrDeltaPp: num(d.nsfr_delta_pp),
      netIncomeDelta: num(d.net_income_delta),
    })),
    year5: {
      carPct: comparison('car_pct'),
      lcrPct: comparison('lcr_pct'),
      nsfrPct: comparison('nsfr_pct'),
      netIncome: comparison('net_income'),
    },
    baseAssumptions: assumptionMap(metrics.base_assumptions),
    shockedAssumptions: assumptionMap(metrics.shocked_assumptions),
    provenance: {
      runId: run.id,
      inputHash: run.inputHash,
      createdAt: run.createdAt,
    },
  };
}

// ---------------------------------------------------------------------------
// Metric registry for the comparison chart.
// ---------------------------------------------------------------------------

const WHATIF_METRICS = [
  {
    code: 'carPct',
    label: 'CAR',
    fmt: (v: number) => fmtPct(v, 2),
    tick: (v: number) => `${Math.round(v)}%`,
    threshold: 10,
    thresholdLabel: 'Regulatory min 10%',
  },
  {
    code: 'lcrPct',
    label: 'LCR',
    fmt: (v: number) => fmtPct(v, 1),
    tick: (v: number) => `${Math.round(v)}%`,
    threshold: 100,
    thresholdLabel: 'Regulatory min 100%',
  },
  {
    code: 'nsfrPct',
    label: 'NSFR',
    fmt: (v: number) => fmtPct(v, 1),
    tick: (v: number) => `${Math.round(v)}%`,
    threshold: 100,
    thresholdLabel: 'Regulatory min 100%',
  },
  {
    code: 'netIncome',
    label: 'Net income',
    fmt: (v: number) => fmtCurrency(v),
    tick: (v: number) => fmtCurrency(v, undefined, { decimals: 1 }),
    threshold: undefined,
    thresholdLabel: undefined,
  },
  {
    code: 'totalAssets',
    label: 'Total assets',
    fmt: (v: number) => fmtCurrency(v),
    tick: (v: number) => fmtCurrency(v, undefined, { decimals: 1 }),
    threshold: undefined,
    thresholdLabel: undefined,
  },
] as const;

type WhatIfMetricCode = (typeof WHATIF_METRICS)[number]['code'];

export default function WhatIfLab() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const [activeShock, setActiveShock] = useState<WhatIfShockCode>(
    'rate_shock_up_400'
  );

  // Latest stored run per shock, for reload on mount (unchanged wiring).
  const runsQuery = useRegulatoryRuns(bankId, { module: 'whatif', limit: 50 });
  const latestIds = new Map<string, string>();
  for (const run of runsQuery.data?.runs ?? []) {
    if (!latestIds.has(run.scenarioCode)) {
      latestIds.set(run.scenarioCode, run.id);
    }
  }
  const storedRate = useRegulatoryRun(bankId, latestIds.get('rate_shock_up_400'));
  const storedCedi = useRegulatoryRun(bankId, latestIds.get('cedi_depreciation_20'));
  const storedDefault = useRegulatoryRun(bankId, latestIds.get('default_spike'));
  const storedMpr = useRegulatoryRun(bankId, latestIds.get('mpr_cut_200'));
  const storedByShock: Record<string, RegulatoryRunRead | undefined> = {
    rate_shock_up_400: storedRate.data,
    cedi_depreciation_20: storedCedi.data,
    default_spike: storedDefault.data,
    mpr_cut_200: storedMpr.data,
  };

  // Fresh results from this session, keyed by shock (unchanged wiring).
  const [freshResults, setFreshResults] = useState<
    Partial<Record<WhatIfShockCode, WhatIfResultRead>>
  >({});
  const runWhatIf = useRunWhatIf(bankId);
  const pendingShock = runWhatIf.isPending
    ? runWhatIf.variables?.shockCode ?? null
    : null;

  const runShock = (shockCode: WhatIfShockCode) => {
    if (!periodId) return;
    runWhatIf.mutate(
      { reportingPeriodId: periodId, shockCode },
      {
        onSuccess: (result) =>
          setFreshResults((prev) => ({ ...prev, [result.shockCode]: result })),
      }
    );
  };

  const viewFor = (code: WhatIfShockCode): WhatIfView | null => {
    const fresh = freshResults[code];
    if (fresh) {
      const view = fromResult(fresh);
      if (view) return view;
    }
    const stored = storedByShock[code];
    return stored ? fromStoredRun(stored) : null;
  };

  /** The engine's diagnostic when the latest attempt for this shock FAILED —
   * failures are data (e.g. balance_sheet_infeasible), never a blank screen. */
  const failureFor = (code: WhatIfShockCode) => {
    const fresh = freshResults[code];
    if (fresh && fresh.status === 'failed') {
      return {
        code: fresh.error?.code ?? 'run_failed',
        message:
          fresh.error?.message ??
          'The what-if projection failed. Review the run inputs and retry.',
        runId: fresh.runId,
      };
    }
    const stored = storedByShock[code];
    if (stored && stored.status === 'failed') {
      return {
        code: stored.error?.code ?? 'run_failed',
        message:
          stored.error?.message ??
          'The what-if projection failed. Review the run inputs and retry.',
        runId: stored.id,
      };
    }
    return null;
  };

  const activeView = viewFor(activeShock);
  const activeFailure = failureFor(activeShock);
  const activeMeta = SHOCKS.find((s) => s.code === activeShock)!;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'What-if Lab' },
        ]}
        title="What-if Lab"
        subtitle="Deterministic macro shocks re-projected against the unshocked base run on identical canonical inputs"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <QueryBoundary
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {runWhatIf.error && (
            <ErrorPanel error={runWhatIf.error} title="What-if run failed" />
          )}

          {activeFailure && (
            <div
              role="alert"
              className="card border-l-4 border-l-critical bg-critical-light/40 px-5 py-4"
            >
              <p className="text-body font-medium text-navy">
                This shock could not be projected
              </p>
              <p className="mt-1 text-body text-slate leading-relaxed">
                {activeFailure.message}
              </p>
              <p className="mt-2 text-caption text-slate">
                Engine diagnostic <code className="font-mono">{activeFailure.code}</code>
                {' · '}run <code className="font-mono">{activeFailure.runId.slice(0, 8)}</code>
                {' — '}adjust the scenario assumptions in the Assumption Registry or
                choose a milder shock, then run again.
                {activeView ? ' The last successful projection is shown below.' : ''}
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
            {/* Left panel — shock library */}
            <SectionCard
              title="Shock library"
              subtitle="The four macro shocks the projection engine accepts"
              className="xl:sticky xl:top-4"
            >
              <div className="space-y-3">
                {SHOCKS.map((shock) => {
                  const view = viewFor(shock.code);
                  const isActive = shock.code === activeShock;
                  const isRunning = pendingShock === shock.code;
                  const breach =
                    view !== null &&
                    view.shockedPath.some((p) => p.carPct < 10);
                  return (
                    <button
                      key={shock.code}
                      type="button"
                      onClick={() => setActiveShock(shock.code)}
                      aria-pressed={isActive}
                      className={`w-full text-left rounded-md border p-3.5 transition-colors ${
                        isActive
                          ? 'border-action bg-action-light/40'
                          : 'border-border-light bg-surface-raised hover:border-border'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-body font-medium text-navy">
                          {shock.label}
                        </p>
                        {isRunning ? (
                          <StatusPill tone="pending">Running</StatusPill>
                        ) : view ? (
                          breach ? (
                            <StatusPill tone="critical">CAR breach</StatusPill>
                          ) : (
                            <StatusPill tone="success">Run</StatusPill>
                          )
                        ) : (
                          <StatusPill tone="slate">Not run</StatusPill>
                        )}
                      </div>
                      <p className="mt-1 text-caption text-slate leading-relaxed">
                        {shock.description}
                      </p>
                      {view && (
                        <p className="mt-2 text-caption text-slate">
                          Y5 CAR{' '}
                          <span className="font-mono tnum text-navy">
                            {fmtPct(view.year5.carPct.shocked, 2)}
                          </span>{' '}
                          <DeltaBadge
                            value={view.year5.carPct.delta}
                            suffix=" pp"
                            decimals={2}
                          />
                        </p>
                      )}
                    </button>
                  );
                })}

                <button
                  type="button"
                  disabled={runWhatIf.isPending || !periodId}
                  onClick={() => runShock(activeShock)}
                  className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2.5 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  {pendingShock === activeShock ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <PlayCircle size={13} aria-hidden />
                  )}
                  {activeView ? 'Re-run' : 'Run'} {activeMeta.label}
                </button>

                <p className="text-caption text-slate leading-relaxed">
                  Each run re-projects the full 5-year path with the shocked
                  assumption set and persists an auditable what-if regulatory
                  run alongside the unshocked base.
                </p>
              </div>
            </SectionCard>

            {/* Right panel — results */}
            <div className="xl:col-span-2 space-y-6">
              {!activeView ? (
                <EmptyState
                  Icon={FlaskConical}
                  title={
                    pendingShock === activeShock
                      ? 'Running shock projection…'
                      : 'Shock not yet run for this period'
                  }
                  description={`Run “${activeMeta.label}” to compare the shocked 5-year path against the deterministic base projection on identical canonical inputs.`}
                />
              ) : (
                <ShockResult view={activeView} shockLabel={activeMeta.label} />
              )}
            </div>
          </div>
        </div>
      </QueryBoundary>
    </>
  );
}

// ---------------------------------------------------------------------------
// Right panel — result view
// ---------------------------------------------------------------------------

function ShockResult({
  view,
  shockLabel,
}: {
  view: WhatIfView;
  shockLabel: string;
}) {
  const [metricCode, setMetricCode] = useState<WhatIfMetricCode>('carPct');
  const metric = WHATIF_METRICS.find((m) => m.code === metricCode)!;

  const chartData: ScenarioPoint[] = view.basePath.map((p) => {
    const shocked = view.shockedPath.find((s) => s.year === p.year);
    return {
      label: `Y${p.year}`,
      base: p[metricCode],
      shocked: shocked ? shocked[metricCode] : null,
    };
  });

  // Assumption diff — which resolved assumptions the shock actually moved.
  const movedAssumptions = ASSUMPTION_FIELDS.map((field) => ({
    field,
    base: view.baseAssumptions[field.apiKey],
    shocked: view.shockedAssumptions[field.apiKey],
  })).filter(
    (d) =>
      d.base !== undefined && d.shocked !== undefined && d.base !== d.shocked
  );

  return (
    <>
      {/* Year-5 delta chips */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <ComparisonCell
          label="Y5 CAR"
          comparison={view.year5.carPct}
          fmt={(v) => fmtPct(v, 2)}
          deltaSuffix=" pp"
        />
        <ComparisonCell
          label="Y5 LCR"
          comparison={view.year5.lcrPct}
          fmt={(v) => fmtPct(v, 1)}
          deltaSuffix=" pp"
        />
        <ComparisonCell
          label="Y5 NSFR"
          comparison={view.year5.nsfrPct}
          fmt={(v) => fmtPct(v, 1)}
          deltaSuffix=" pp"
        />
        <ComparisonCell
          label="Y5 net income"
          comparison={view.year5.netIncome}
          fmt={(v) => fmtCurrency(v)}
          currencyDelta
        />
      </div>

      {/* Base vs shocked path */}
      <ChartFrame
        title="Base vs shocked path"
        subtitle={`${shockLabel} · both paths persisted on the what-if run`}
        height={280}
        actions={
          <select
            value={metricCode}
            onChange={(e) => setMetricCode(e.target.value as WhatIfMetricCode)}
            aria-label="Comparison metric"
            className="px-2.5 py-1.5 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
          >
            {WHATIF_METRICS.map((m) => (
              <option key={m.code} value={m.code}>
                {m.label}
              </option>
            ))}
          </select>
        }
        footer={
          <RunProvenance
            runId={view.provenance.runId}
            inputHash={view.provenance.inputHash}
            createdAt={view.provenance.createdAt}
            note="Shocked and base paths computed by the same engine on identical canonical inputs."
          />
        }
      >
        <ScenarioLinesChart
          data={chartData}
          series={[
            { key: 'base', name: 'Base', colorIndex: 0 },
            { key: 'shocked', name: 'Shocked', colorIndex: 3, dashed: true },
          ]}
          valueFormatter={metric.fmt}
          tickFormatter={metric.tick}
          threshold={metric.threshold}
          thresholdLabel={metric.thresholdLabel}
        />
      </ChartFrame>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Per-year deltas */}
        <SectionCard
          title="Impact vs base by year"
          subtitle="Persisted per-year deltas on the what-if run"
          noPadding
        >
          <div className="overflow-x-auto">
            <table className="w-full text-caption border-collapse tnum">
              <thead>
                <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                  <th className="text-left px-4 py-2">Year</th>
                  <th className="text-right px-4 py-2">ΔCAR (pp)</th>
                  <th className="text-right px-4 py-2">ΔLCR (pp)</th>
                  <th className="text-right px-4 py-2">ΔNSFR (pp)</th>
                  <th className="text-right px-4 py-2">Δ net income</th>
                </tr>
              </thead>
              <tbody>
                {view.deltas
                  .filter((d) => d.year > 0)
                  .map((d) => (
                    <tr
                      key={d.year}
                      className="border-b border-border-light last:border-b-0"
                    >
                      <td className="px-4 py-2 font-medium text-navy">Y{d.year}</td>
                      <DeltaCell value={d.carDeltaPp} fmt={(v) => v.toFixed(2)} />
                      <DeltaCell value={d.lcrDeltaPp} fmt={(v) => v.toFixed(2)} />
                      <DeltaCell value={d.nsfrDeltaPp} fmt={(v) => v.toFixed(2)} />
                      <DeltaCell
                        value={d.netIncomeDelta}
                        fmt={(v) => fmtCurrency(Math.abs(v))}
                        signed
                      />
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </SectionCard>

        {/* Assumption diff */}
        <SectionCard
          title="What the shock moved"
          subtitle="Shocked vs base resolved assumptions, from the persisted run payload"
          noPadding
        >
          {movedAssumptions.length === 0 ? (
            <p className="px-5 py-4 text-body text-slate">
              The stored payload for this run does not include an assumption
              diff — re-run the shock to persist one.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-caption border-collapse tnum">
                <thead>
                  <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                    <th className="text-left px-4 py-2">Assumption</th>
                    <th className="text-right px-4 py-2">Base</th>
                    <th className="text-right px-4 py-2">Shocked</th>
                    <th className="text-right px-4 py-2">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {movedAssumptions.map(({ field, base, shocked }) => (
                    <tr
                      key={field.key}
                      className="border-b border-border-light last:border-b-0"
                    >
                      <td className="px-4 py-2 text-navy/90">{field.label}</td>
                      <td className="px-4 py-2 text-right font-mono tnum">
                        {base.toFixed(2)}
                        {field.unit}
                      </td>
                      <td className="px-4 py-2 text-right font-mono tnum text-navy font-medium">
                        {shocked.toFixed(2)}
                        {field.unit}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <DeltaBadge
                          value={shocked - base}
                          suffix={field.unit.trim() === 'pp' ? ' pp' : field.unit}
                          decimals={2}
                          invert={
                            field.key === 'creditLossRatePct' ||
                            field.key === 'costToIncomePct' ||
                            field.key === 'fxDepreciationPct'
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      </div>
    </>
  );
}

function ComparisonCell({
  label,
  comparison,
  fmt,
  deltaSuffix = '',
  currencyDelta = false,
}: {
  label: string;
  comparison: Comparison;
  fmt: (v: number) => string;
  deltaSuffix?: string;
  currencyDelta?: boolean;
}) {
  return (
    <div className="card px-4 py-3.5">
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-kpi text-navy tnum">
        {fmt(comparison.shocked)}
      </p>
      <p className="text-caption text-slate flex items-center gap-1.5 flex-wrap">
        base <span className="font-mono tnum">{fmt(comparison.base)}</span>
        {currencyDelta ? (
          <span
            className={`font-mono font-medium tnum ${
              comparison.delta < 0 ? 'text-critical' : 'text-success'
            }`}
          >
            {fmtCurrencySigned(comparison.delta)}
          </span>
        ) : (
          <DeltaBadge value={comparison.delta} suffix={deltaSuffix} decimals={2} />
        )}
      </p>
    </div>
  );
}

function DeltaCell({
  value,
  fmt,
  signed = false,
}: {
  value: number;
  fmt: (v: number) => string;
  signed?: boolean;
}) {
  const display = signed
    ? `${value >= 0 ? '+' : '-'}${fmt(value)}`
    : `${value >= 0 ? '+' : ''}${fmt(value)}`;
  return (
    <td
      className={`px-4 py-2 text-right font-mono tnum ${
        value < 0 ? 'text-critical' : value > 0 ? 'text-success' : 'text-slate'
      }`}
    >
      {display}
    </td>
  );
}
