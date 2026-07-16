'use client';

/**
 * Scenarios — the scenario manager for the forecasting workspace:
 *  1. Scenario designer: preset assumptions + slider overrides → persisted
 *     forecast run (existing createForecastRun mutation, presentation only).
 *  2. Run registry: every immutable forecast run with input hash + status.
 *  3. Side-by-side comparison of any two succeeded runs — metric path
 *     overlay, per-year deltas, and resolved-assumption diff.
 */

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, GitCompareArrows, Loader2, PlayCircle, RotateCcw } from 'lucide-react';
import type {
  ForecastPresetCode,
  ForecastRunRead,
  ForecastRunSummaryRead,
  ForecastScenarioListRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DeltaBadge from '@/components/ui/DeltaBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import ScenarioLinesChart, {
  type ScenarioPoint,
} from '@/components/forecasting/charts/ScenarioLinesChart';
import {
  ASSUMPTION_FIELDS,
  type AssumptionField,
  type AssumptionKey,
  scenarioLabel,
} from '@/components/forecasting/lib';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateForecastRun,
  useForecastRun,
  useForecastRuns,
  useForecastScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, num, shortId } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

type FormValues = Record<AssumptionKey, number>;

function presetValues(
  scenarios: ForecastScenarioListRead,
  preset: ForecastPresetCode
): FormValues | null {
  const found = scenarios.scenarios.find((s) => s.code === preset);
  if (!found) return null;
  const defaults: Record<string, string> = {
    fee_income_pct_assets: scenarios.defaults.feeIncomePctAssets,
    tax_rate_pct: scenarios.defaults.taxRatePct,
    securities_shift_pp: scenarios.defaults.securitiesShiftPp,
  };
  const values = {} as FormValues;
  for (const field of ASSUMPTION_FIELDS) {
    const raw = found.assumptions[field.apiKey] ?? defaults[field.apiKey];
    values[field.key] = raw === undefined ? 0 : num(raw);
  }
  return values;
}

// ---------------------------------------------------------------------------
// Comparison metric registry — every option maps to a persisted path field.
// ---------------------------------------------------------------------------

const COMPARE_METRICS = [
  {
    code: 'carPct',
    label: 'CAR',
    fmt: (v: number) => fmtPct(v, 2),
    isCurrency: false,
  },
  {
    code: 'lcrPct',
    label: 'LCR',
    fmt: (v: number) => fmtPct(v, 1),
    isCurrency: false,
  },
  {
    code: 'nsfrPct',
    label: 'NSFR',
    fmt: (v: number) => fmtPct(v, 1),
    isCurrency: false,
  },
  {
    code: 'totalAssets',
    label: 'Total assets',
    fmt: (v: number) => fmtCurrency(v, 'GHS'),
    isCurrency: true,
  },
  {
    code: 'nii',
    label: 'NII',
    fmt: (v: number) => fmtCurrency(v, 'GHS'),
    isCurrency: true,
  },
  {
    code: 'netIncome',
    label: 'Net income',
    fmt: (v: number) => fmtCurrency(v, 'GHS'),
    isCurrency: true,
  },
] as const;

type CompareMetricCode = (typeof COMPARE_METRICS)[number]['code'];

export default function ScenariosPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const scenariosQuery = useForecastScenarios(bankId);
  const runsQuery = useForecastRuns(bankId, { limit: 50 });
  const createRun = useCreateForecastRun(bankId);

  const runs = runsQuery.data?.runs ?? [];

  // A/B selection for the comparison section.
  const [runAId, setRunAId] = useState<string | null>(null);
  const [runBId, setRunBId] = useState<string | null>(null);
  const runA = useForecastRun(bankId, runAId);
  const runB = useForecastRun(bankId, runBId);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'Scenarios' },
        ]}
        title="Scenario Manager"
        subtitle="Design scenario assumptions, run persisted projections, and compare immutable runs side-by-side"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <QueryBoundary
        isLoading={scenariosQuery.isLoading || runsQuery.isLoading}
        error={scenariosQuery.error ?? runsQuery.error}
        onRetry={() => {
          void scenariosQuery.refetch();
          void runsQuery.refetch();
        }}
      >
        <div className="px-8 py-6 space-y-6">
          {scenariosQuery.data && (
            <ScenarioDesigner
              scenarios={scenariosQuery.data}
              periodId={periodId}
              createRun={createRun}
            />
          )}

          <SectionCard
            title="Forecast run registry"
            subtitle="Immutable persisted runs — every row carries its input hash · pick A and B to compare"
            noPadding
          >
            <RunRegistryTable
              runs={runs}
              runAId={runAId}
              runBId={runBId}
              onPickA={(id) => setRunAId((cur) => (cur === id ? null : id))}
              onPickB={(id) => setRunBId((cur) => (cur === id ? null : id))}
            />
          </SectionCard>

          {runAId && runBId && runAId !== runBId ? (
            runA.data && runB.data ? (
              <CompareSection a={runA.data} b={runB.data} />
            ) : runA.isLoading || runB.isLoading ? (
              <p className="text-body text-slate">Loading comparison…</p>
            ) : null
          ) : (
            <EmptyState
              Icon={GitCompareArrows}
              title="Pick two runs to compare"
              description="Select a run in column A and another in column B of the registry to overlay their projection paths, per-year deltas, and resolved assumptions."
            />
          )}
        </div>
      </QueryBoundary>
    </>
  );
}

// ---------------------------------------------------------------------------
// Scenario designer (preserved createForecastRun mutation)
// ---------------------------------------------------------------------------

function ScenarioDesigner({
  scenarios,
  periodId,
  createRun,
}: {
  scenarios: ForecastScenarioListRead;
  periodId: string | undefined;
  createRun: ReturnType<typeof useCreateForecastRun>;
}) {
  const [preset, setPreset] = useState<ForecastPresetCode>('base');
  const [overrides, setOverrides] = useState<Partial<FormValues>>({});

  const baseline = useMemo(
    () => presetValues(scenarios, preset),
    [scenarios, preset]
  );
  const values: FormValues | null = baseline
    ? { ...baseline, ...overrides }
    : null;
  const touched = ASSUMPTION_FIELDS.filter(
    (f) => baseline && values && values[f.key] !== baseline[f.key]
  );
  const isCustom = touched.length > 0;

  const submit = () => {
    if (!periodId || !values) return;
    if (isCustom) {
      createRun.mutate({
        reportingPeriodId: periodId,
        scenarioCode: 'custom',
        assumptions: {
          loanGrowthPct: values.loanGrowthPct,
          depositGrowthPct: values.depositGrowthPct,
          nimPct: values.nimPct,
          costToIncomePct: values.costToIncomePct,
          creditLossRatePct: values.creditLossRatePct,
          fxDepreciationPct: values.fxDepreciationPct,
          dividendPayoutPct: values.dividendPayoutPct,
          feeIncomePctAssets: values.feeIncomePctAssets,
          taxRatePct: values.taxRatePct,
          securitiesShiftPp: values.securitiesShiftPp,
        },
      });
    } else {
      createRun.mutate({ reportingPeriodId: periodId, scenarioCode: preset });
    }
  };

  const result = createRun.data;

  if (!values || !baseline) return null;

  return (
    <SectionCard
      title="Scenario designer"
      subtitle="Start from a preset, adjust any assumption, and persist a new immutable projection run"
      actions={
        <button
          type="button"
          disabled={createRun.isPending || !periodId}
          onClick={submit}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
        >
          {createRun.isPending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <PlayCircle size={13} aria-hidden />
          )}
          Run {isCustom ? 'custom scenario' : scenarioLabel(preset)}
        </button>
      }
    >
      <div className="space-y-5">
        {/* Preset selector */}
        <div className="flex items-center gap-4 flex-wrap">
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Start from preset
          </p>
          <div className="inline-flex gap-1 bg-surface p-1 rounded">
            {scenarios.scenarios.map((s) => (
              <button
                key={s.code}
                type="button"
                onClick={() => {
                  setPreset(s.code);
                  setOverrides({});
                }}
                className={`px-3 py-1.5 rounded text-caption font-medium ${
                  preset === s.code && !isCustom
                    ? 'bg-surface-raised text-navy shadow-sm'
                    : 'text-slate hover:text-navy'
                }`}
              >
                {scenarioLabel(s.code)}
              </button>
            ))}
          </div>
          {isCustom && (
            <span className="inline-flex items-center gap-2">
              <StatusPill tone="action">
                Custom — {touched.length} of {ASSUMPTION_FIELDS.length} changed
              </StatusPill>
              <button
                type="button"
                onClick={() => setOverrides({})}
                className="inline-flex items-center gap-1 text-caption font-medium text-slate hover:text-navy"
              >
                <RotateCcw size={12} aria-hidden />
                Reset to {scenarioLabel(preset)}
              </button>
            </span>
          )}
        </div>

        {/* Sliders */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-5">
          {ASSUMPTION_FIELDS.map((field) => (
            <AssumptionSlider
              key={field.key}
              field={field}
              value={values[field.key]}
              presetValue={baseline[field.key]}
              onChange={(v) =>
                setOverrides((prev) => ({ ...prev, [field.key]: v }))
              }
            />
          ))}
        </div>

        {/* Variance chips */}
        {isCustom && (
          <div className="flex items-center gap-x-6 gap-y-2 flex-wrap border-t border-border-light pt-4">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Variance vs {scenarioLabel(preset)}
            </p>
            {touched.map((field) => {
              const delta = values[field.key] - baseline[field.key];
              return (
                <span key={field.key} className="inline-flex items-center gap-1.5">
                  <span className="text-caption text-slate">{field.label}</span>
                  <DeltaBadge
                    value={delta}
                    suffix={field.unit.trim() === 'pp' ? ' pp' : field.unit}
                    decimals={1}
                  />
                </span>
              );
            })}
          </div>
        )}

        {createRun.error && (
          <ErrorPanel error={createRun.error} title="Forecast run failed" />
        )}

        {/* Fresh-run result strip */}
        {result && result.status === 'succeeded' ? (
          <div className="border-t border-border-light pt-4 space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <StatusPill tone="success">Run succeeded</StatusPill>
              <StatusPill tone="action">
                {scenarioLabel(result.scenarioCode)}
              </StatusPill>
              <RunBadge run={result} />
              <Link
                href={`/forecasting?run=${result.id}`}
                className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
              >
                Open on Balance Sheet <ArrowUpRight size={12} aria-hidden />
              </Link>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiStat
                label="Average ROE"
                value={fmtPct(num(result.summary.avgRoePct), 2)}
              />
              <KpiStat
                label="Year-5 CAR"
                value={fmtPct(num(result.summary.year5CarPct), 2)}
              />
              <KpiStat
                label="Year-5 LCR"
                value={fmtPct(num(result.summary.year5LcrPct), 1)}
              />
              <KpiStat
                label="Cumulative net income"
                value={fmtCurrency(num(result.summary.cumulativeNetIncome), 'GHS')}
              />
            </div>
          </div>
        ) : result ? (
          <ErrorPanel
            error={
              new Error(
                result.error?.message ??
                  'The forecast run did not complete successfully.'
              )
            }
            title="Run failed"
          />
        ) : null}
      </div>
    </SectionCard>
  );
}

function AssumptionSlider({
  field,
  value,
  presetValue,
  onChange,
}: {
  field: AssumptionField;
  value: number;
  presetValue: number;
  onChange: (value: number) => void;
}) {
  const changed = value !== presetValue;
  return (
    <div>
      <label className="block text-micro font-medium uppercase tracking-wider text-slate mb-1.5">
        {field.label}{' '}
        <span className={`font-mono tnum ${changed ? 'text-action' : 'text-navy'}`}>
          {value}
          {field.unit}
        </span>
      </label>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="w-full accent-action"
          aria-label={field.label}
        />
        <input
          type="number"
          min={field.min}
          max={field.max}
          step={field.step}
          value={value}
          onChange={(e) => {
            const parsed = parseFloat(e.target.value);
            if (Number.isFinite(parsed)) {
              onChange(Math.min(field.max, Math.max(field.min, parsed)));
            }
          }}
          className="w-20 shrink-0 px-2 py-1 text-caption font-mono text-navy border border-border rounded bg-surface-raised tnum"
          aria-label={`${field.label} value`}
        />
      </div>
      <p className="mt-1 text-caption text-slate truncate" title={field.definition}>
        {field.definition}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run registry
// ---------------------------------------------------------------------------

function RunRegistryTable({
  runs,
  runAId,
  runBId,
  onPickA,
  onPickB,
}: {
  runs: ForecastRunSummaryRead[];
  runAId: string | null;
  runBId: string | null;
  onPickA: (id: string) => void;
  onPickB: (id: string) => void;
}) {
  if (!runs.length) {
    return (
      <p className="px-5 py-4 text-body text-slate">
        No forecast runs yet — run a scenario above to create the first
        auditable projection.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse tnum">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Created</th>
            <th className="text-left px-4 py-2.5">Scenario</th>
            <th className="text-left px-4 py-2.5">Period</th>
            <th className="text-left px-4 py-2.5">Input hash</th>
            <th className="text-right px-4 py-2.5">Avg ROE</th>
            <th className="text-right px-4 py-2.5">Y5 CAR</th>
            <th className="text-right px-4 py-2.5">Y5 LCR</th>
            <th className="text-left px-4 py-2.5">Status</th>
            <th className="text-center px-4 py-2.5">A</th>
            <th className="text-center px-4 py-2.5">B</th>
            <th className="text-center px-4 py-2.5" aria-label="Open" />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const selected = r.id === runAId || r.id === runBId;
            return (
              <tr
                key={r.id}
                className={`border-b border-border-light last:border-b-0 ${
                  selected ? 'bg-action-light/50' : 'hover:bg-surface-alt'
                }`}
              >
                <td className="px-4 py-2.5 font-mono text-caption text-slate whitespace-nowrap">
                  {fmtTimestamp(r.createdAt)}
                </td>
                <td className="px-4 py-2.5 text-navy font-medium">
                  {scenarioLabel(r.scenarioCode)}
                </td>
                <td className="px-4 py-2.5 font-mono text-caption text-slate">
                  {r.periodLabel}
                </td>
                <td className="px-4 py-2.5 font-mono text-caption text-slate">
                  {shortId(r.inputHash)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tnum">
                  {r.avgRoePct === null ? '—' : fmtPct(num(r.avgRoePct), 2)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tnum">
                  {r.year5CarPct === null ? '—' : fmtPct(num(r.year5CarPct), 2)}
                </td>
                <td className="px-4 py-2.5 text-right font-mono tnum">
                  {r.year5LcrPct === null ? '—' : fmtPct(num(r.year5LcrPct), 1)}
                </td>
                <td className="px-4 py-2.5">
                  <StatusPill
                    tone={r.status === 'succeeded' ? 'success' : 'critical'}
                  >
                    {r.status}
                  </StatusPill>
                </td>
                <td className="px-4 py-2.5 text-center">
                  <input
                    type="radio"
                    name="compare-a"
                    aria-label={`Compare run ${shortId(r.id)} as A`}
                    checked={r.id === runAId}
                    disabled={r.status !== 'succeeded'}
                    onChange={() => onPickA(r.id)}
                    onClick={() => r.id === runAId && onPickA(r.id)}
                    className="accent-action"
                  />
                </td>
                <td className="px-4 py-2.5 text-center">
                  <input
                    type="radio"
                    name="compare-b"
                    aria-label={`Compare run ${shortId(r.id)} as B`}
                    checked={r.id === runBId}
                    disabled={r.status !== 'succeeded'}
                    onChange={() => onPickB(r.id)}
                    onClick={() => r.id === runBId && onPickB(r.id)}
                    className="accent-action"
                  />
                </td>
                <td className="px-2 py-2.5 text-center">
                  {r.status === 'succeeded' && (
                    <Link
                      href={`/forecasting?run=${r.id}`}
                      className="inline-flex text-slate hover:text-action"
                      aria-label={`Open run ${shortId(r.id)} on the Balance Sheet tab`}
                    >
                      <ArrowUpRight size={14} aria-hidden />
                    </Link>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Side-by-side comparison
// ---------------------------------------------------------------------------

function CompareSection({ a, b }: { a: ForecastRunRead; b: ForecastRunRead }) {
  const [metricCode, setMetricCode] = useState<CompareMetricCode>('carPct');
  const metric = COMPARE_METRICS.find((m) => m.code === metricCode)!;

  const labelA = `A · ${scenarioLabel(a.scenarioCode)}`;
  const labelB = `B · ${scenarioLabel(b.scenarioCode)}`;

  const years = a.path.map((p) => p.year);
  const valueAt = (run: ForecastRunRead, year: number): number | null => {
    const p = run.path.find((x) => x.year === year);
    if (!p) return null;
    const raw = p[metricCode as keyof typeof p];
    return raw === null || raw === undefined ? null : num(raw as string);
  };

  const chartData: ScenarioPoint[] = years.map((year) => ({
    label: year === 0 ? 'Y0' : `Y${year}`,
    a: valueAt(a, year),
    b: valueAt(b, year),
  }));

  const summaryRows: {
    label: string;
    a: number;
    b: number;
    fmt: (v: number) => string;
    isCurrency?: boolean;
  }[] = [
    {
      label: 'Average ROE',
      a: num(a.summary.avgRoePct),
      b: num(b.summary.avgRoePct),
      fmt: (v: number) => fmtPct(v, 2),
    },
    {
      label: 'Year-5 CAR',
      a: num(a.summary.year5CarPct),
      b: num(b.summary.year5CarPct),
      fmt: (v: number) => fmtPct(v, 2),
    },
    {
      label: 'Year-5 LCR',
      a: num(a.summary.year5LcrPct),
      b: num(b.summary.year5LcrPct),
      fmt: (v: number) => fmtPct(v, 1),
    },
    {
      label: 'Year-5 NSFR',
      a: num(a.summary.year5NsfrPct),
      b: num(b.summary.year5NsfrPct),
      fmt: (v: number) => fmtPct(v, 1),
    },
    {
      label: 'Cumulative net income',
      a: num(a.summary.cumulativeNetIncome),
      b: num(b.summary.cumulativeNetIncome),
      fmt: (v: number) => fmtCurrency(v, 'GHS'),
      isCurrency: true,
    },
  ];

  const assumptionDiffs = ASSUMPTION_FIELDS.map((field) => ({
    field,
    a: num(a.assumptions[field.key]),
    b: num(b.assumptions[field.key]),
  }));
  const changedAssumptions = assumptionDiffs.filter((d) => d.a !== d.b);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-h2 text-navy">Run comparison</h2>
        <StatusPill tone="action">{labelA}</StatusPill>
        <RunBadge run={a} />
        <span className="text-slate text-caption">vs</span>
        <StatusPill tone="amber">{labelB}</StatusPill>
        <RunBadge run={b} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="Path overlay"
          subtitle="Both persisted paths on the selected metric"
          height={280}
          actions={
            <select
              value={metricCode}
              onChange={(e) => setMetricCode(e.target.value as CompareMetricCode)}
              aria-label="Comparison metric"
              className="px-2.5 py-1.5 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
            >
              {COMPARE_METRICS.map((m) => (
                <option key={m.code} value={m.code}>
                  {m.label}
                </option>
              ))}
            </select>
          }
        >
          <ScenarioLinesChart
            data={chartData}
            series={[
              { key: 'a', name: labelA, colorIndex: 0 },
              { key: 'b', name: labelB, colorIndex: 3, dashed: true },
            ]}
            valueFormatter={metric.fmt}
            tickFormatter={
              metric.isCurrency
                ? (v) => fmtCurrency(v, 'GHS', { decimals: 1 })
                : (v) => `${Math.round(v)}%`
            }
          />
        </ChartFrame>

        <SectionCard
          title="Summary deltas"
          subtitle="Persisted run summaries · Δ is B − A"
          noPadding
        >
          <div className="overflow-x-auto">
            <table className="w-full text-body border-collapse tnum">
              <thead>
                <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                  <th className="text-left px-4 py-2.5">Metric</th>
                  <th className="text-right px-4 py-2.5">{labelA}</th>
                  <th className="text-right px-4 py-2.5">{labelB}</th>
                  <th className="text-right px-4 py-2.5">Δ (B − A)</th>
                </tr>
              </thead>
              <tbody>
                {summaryRows.map((row) => (
                  <tr key={row.label} className="border-b border-border-light last:border-b-0">
                    <td className="px-4 py-2.5 text-navy/90">{row.label}</td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {row.fmt(row.a)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {row.fmt(row.b)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {row.isCurrency ? (
                        <DeltaBadge
                          value={(row.b - row.a) / 1_000_000}
                          suffix="M GHS"
                          decimals={1}
                        />
                      ) : (
                        <DeltaBadge value={row.b - row.a} suffix=" pp" decimals={2} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      </div>

      {/* Per-year deltas for the selected metric */}
      <SectionCard
        title={`Per-year ${metric.label} deltas`}
        subtitle="Difference between the two persisted paths, year by year (B − A)"
        noPadding
      >
        <div className="overflow-x-auto">
          <table className="w-full text-body border-collapse tnum">
            <thead>
              <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                <th className="text-left px-4 py-2.5">Year</th>
                <th className="text-right px-4 py-2.5">{labelA}</th>
                <th className="text-right px-4 py-2.5">{labelB}</th>
                <th className="text-right px-4 py-2.5">Δ (B − A)</th>
              </tr>
            </thead>
            <tbody>
              {years.map((year) => {
                const va = valueAt(a, year);
                const vb = valueAt(b, year);
                return (
                  <tr key={year} className="border-b border-border-light last:border-b-0">
                    <td className="px-4 py-2.5 font-medium text-navy">Y{year}</td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {va === null ? '—' : metric.fmt(va)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {vb === null ? '—' : metric.fmt(vb)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {va === null || vb === null ? (
                        '—'
                      ) : metric.isCurrency ? (
                        <DeltaBadge
                          value={(vb - va) / 1_000_000}
                          suffix="M GHS"
                          decimals={1}
                        />
                      ) : (
                        <DeltaBadge value={vb - va} suffix=" pp" decimals={2} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* Assumption diff */}
      <SectionCard
        title="Resolved assumption diff"
        subtitle="Assumptions persisted on each run — differences highlighted"
        noPadding
        footer={
          changedAssumptions.length === 0 ? (
            <span>
              Both runs resolved identical assumptions — any output difference
              comes from the underlying canonical inputs.
            </span>
          ) : (
            <span>
              {changedAssumptions.length} of {ASSUMPTION_FIELDS.length}{' '}
              assumptions differ between the two runs.
            </span>
          )
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full text-body border-collapse tnum">
            <thead>
              <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                <th className="text-left px-4 py-2.5">Assumption</th>
                <th className="text-right px-4 py-2.5">{labelA}</th>
                <th className="text-right px-4 py-2.5">{labelB}</th>
                <th className="text-right px-4 py-2.5">Δ (B − A)</th>
              </tr>
            </thead>
            <tbody>
              {assumptionDiffs.map(({ field, a: va, b: vb }) => {
                const changed = va !== vb;
                return (
                  <tr
                    key={field.key}
                    className={`border-b border-border-light last:border-b-0 ${
                      changed ? 'bg-warning-light/30' : ''
                    }`}
                  >
                    <td className="px-4 py-2.5 text-navy/90">{field.label}</td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {va.toFixed(1)}
                      {field.unit}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono tnum">
                      {vb.toFixed(1)}
                      {field.unit}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {changed ? (
                        <DeltaBadge
                          value={vb - va}
                          suffix={field.unit.trim() === 'pp' ? ' pp' : field.unit}
                          decimals={1}
                        />
                      ) : (
                        <span className="text-slate">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
