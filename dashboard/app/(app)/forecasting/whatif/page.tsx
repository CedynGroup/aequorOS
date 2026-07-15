'use client';

import { useState } from 'react';
import { Loader2, PlayCircle } from 'lucide-react';
import type {
  RegulatoryRunRead,
  WhatIfResultRead,
  WhatIfShockCode,
} from '@aequoros/risk-service-api';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
} from 'recharts';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunWhatIf,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

const SHOCKS: {
  code: WhatIfShockCode;
  label: string;
  description: string;
}[] = [
  {
    code: 'rate_shock_up_400',
    label: 'Interest rate shock +400bps',
    description:
      'Sustained policy tightening — funding costs reprice faster than the loan book.',
  },
  {
    code: 'cedi_depreciation_20',
    label: 'Cedi depreciation 20%',
    description:
      'GHS depreciation inflates FX-linked risk-weighted assets across the horizon.',
  },
  {
    code: 'default_spike',
    label: 'Loan default spike (2.5× credit losses)',
    description:
      'Sectoral concentration risk materializes — annual credit losses multiply 2.5×.',
  },
  {
    code: 'mpr_cut_200',
    label: 'BoG policy rate cut −200bps',
    description:
      'Easing cycle compresses the net interest margin as assets reprice downward.',
  },
];

type PathPoint = {
  year: number;
  carPct: number;
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
  createdAt: Date | null;
};

function fromResult(result: WhatIfResultRead): WhatIfView {
  const comparison = (c: { base: string; shocked: string; delta: string }) => ({
    base: num(c.base),
    shocked: num(c.shocked),
    delta: num(c.delta),
  });
  return {
    basePath: result.basePath.map((p) => ({ year: p.year, carPct: num(p.carPct) })),
    shockedPath: result.shockedPath.map((p) => ({
      year: p.year,
      carPct: num(p.carPct),
    })),
    deltas: result.deltas.map((d) => ({
      year: d.year,
      carDeltaPp: num(d.carDeltaPp),
      lcrDeltaPp: num(d.lcrDeltaPp),
      nsfrDeltaPp: num(d.nsfrDeltaPp),
      netIncomeDelta: num(d.netIncomeDelta),
    })),
    year5: {
      carPct: comparison(result.year5.carPct),
      lcrPct: comparison(result.year5.lcrPct),
      nsfrPct: comparison(result.year5.nsfrPct),
      netIncome: comparison(result.year5.netIncome),
    },
    createdAt: result.createdAt,
  };
}

/** Stored what-if regulatory runs carry the payload as raw snake_case metrics. */
function fromStoredRun(run: RegulatoryRunRead): WhatIfView | null {
  const metrics = run.metrics as {
    base_path?: { year: number; car_pct: string }[];
    shocked_path?: { year: number; car_pct: string }[];
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
  const comparison = (key: string): Comparison => {
    const c = metrics.year5?.[key];
    return c
      ? { base: num(c.base), shocked: num(c.shocked), delta: num(c.delta) }
      : { base: 0, shocked: 0, delta: 0 };
  };
  return {
    basePath: metrics.base_path.map((p) => ({
      year: p.year,
      carPct: num(p.car_pct),
    })),
    shockedPath: (metrics.shocked_path ?? []).map((p) => ({
      year: p.year,
      carPct: num(p.car_pct),
    })),
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
    createdAt: run.createdAt,
  };
}

export default function WhatIfAnalysis() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  // Latest stored run per shock, for reload on mount.
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

  // Fresh results from this session, keyed by shock.
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

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting', href: '/forecasting' },
          { label: 'What-if Analysis' },
        ]}
        title="What-if Analysis"
        subtitle="Pre-defined macro shocks applied to the base 5-year projection · Deterministic base-vs-shocked comparison"
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

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {SHOCKS.map((shock) => {
              const fresh = freshResults[shock.code];
              const stored = storedByShock[shock.code];
              const view = fresh
                ? fromResult(fresh)
                : stored
                ? fromStoredRun(stored)
                : null;
              return (
                <ShockCard
                  key={shock.code}
                  label={shock.label}
                  description={shock.description}
                  view={view}
                  isRunning={pendingShock === shock.code}
                  disabled={runWhatIf.isPending || !periodId}
                  onRun={() => runShock(shock.code)}
                />
              );
            })}
          </div>

          <p className="text-caption text-slate max-w-3xl leading-relaxed">
            Each shock re-runs the deterministic 5-year projection with the
            shocked assumption set and compares it to the unshocked base run
            on identical canonical inputs. {bank?.name ?? 'The bank'}&apos;s
            results persist as auditable what-if regulatory runs.
          </p>
        </div>
      </QueryBoundary>
    </>
  );
}

function ShockCard({
  label,
  description,
  view,
  isRunning,
  disabled,
  onRun,
}: {
  label: string;
  description: string;
  view: WhatIfView | null;
  isRunning: boolean;
  disabled: boolean;
  onRun: () => void;
}) {
  const breach =
    view !== null && view.shockedPath.some((p) => p.carPct < 10);

  const chartData = (view?.basePath ?? []).map((point) => ({
    month: `Y${point.year}`,
    base: point.carPct,
    shocked:
      view?.shockedPath.find((p) => p.year === point.year)?.carPct ?? null,
  }));

  return (
    <Card className={breach ? 'border-l-4 border-l-critical' : ''}>
      <CardHeader
        title={label}
        subtitle={description}
        action={
          <button
            type="button"
            disabled={disabled}
            onClick={onRun}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
          >
            {isRunning ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <PlayCircle size={13} aria-hidden />
            )}
            Run
          </button>
        }
      />
      <CardBody className="space-y-5">
        {!view ? (
          <p className="text-body text-slate">
            {isRunning
              ? 'Running shock projection…'
              : 'Not yet run for this period — run the shock to compare against the base projection.'}
          </p>
        ) : (
          <>
            {/* Year-5 comparison strip */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <ComparisonCell
                label="Y5 CAR"
                comparison={view.year5.carPct}
                fmt={(v) => fmtPct(v, 2)}
                deltaFmt={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)} pp`}
              />
              <ComparisonCell
                label="Y5 LCR"
                comparison={view.year5.lcrPct}
                fmt={(v) => fmtPct(v, 1)}
                deltaFmt={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)} pp`}
              />
              <ComparisonCell
                label="Y5 NSFR"
                comparison={view.year5.nsfrPct}
                fmt={(v) => fmtPct(v, 1)}
                deltaFmt={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)} pp`}
              />
              <ComparisonCell
                label="Y5 net income"
                comparison={view.year5.netIncome}
                fmt={(v) => fmtCurrency(v, 'GHS')}
                deltaFmt={(v) => fmtCurrencySigned(v, 'GHS')}
              />
            </div>

            {/* Base vs shocked CAR path */}
            <ResponsiveContainer width="100%" height={240}>
              <LineChart
                data={chartData}
                margin={{ top: 12, right: 24, left: 0, bottom: 8 }}
              >
                <CartesianGrid
                  stroke="#E4E8EC"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="month"
                  axisLine={{ stroke: '#D0D7DE' }}
                  tickLine={false}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                  width={48}
                  domain={[
                    (dataMin: number) => Math.floor(Math.min(dataMin, 10) - 1),
                    (dataMax: number) => Math.ceil(dataMax + 1),
                  ]}
                />
                <Tooltip
                  formatter={(v: number, name) => [`${v.toFixed(2)}%`, name]}
                />
                <Legend
                  verticalAlign="top"
                  align="right"
                  height={28}
                  iconType="line"
                  wrapperStyle={{ fontSize: '11px' }}
                />
                <ReferenceLine
                  y={10}
                  stroke="#B3261E"
                  strokeDasharray="4 4"
                  label={{
                    value: 'BoG min 10%',
                    position: 'insideBottomRight',
                    fill: '#B3261E',
                    fontSize: 11,
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="base"
                  stroke="#0A2540"
                  strokeWidth={2}
                  name="Base CAR"
                  dot={{ r: 3, fill: '#0A2540' }}
                />
                <Line
                  type="monotone"
                  dataKey="shocked"
                  stroke="#B3261E"
                  strokeWidth={2}
                  name="Shocked CAR"
                  dot={{ r: 3, fill: '#B3261E' }}
                />
              </LineChart>
            </ResponsiveContainer>

            {/* Per-year deltas */}
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2">
                Impact vs base by year
              </p>
              <table className="w-full text-caption border-collapse">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                      Year
                    </th>
                    <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                      ΔCAR (pp)
                    </th>
                    <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                      ΔLCR (pp)
                    </th>
                    <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                      ΔNSFR (pp)
                    </th>
                    <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                      Δ net income
                    </th>
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
                        <td className="py-1.5 font-medium text-navy">Y{d.year}</td>
                        <DeltaCell value={d.carDeltaPp} fmt={(v) => v.toFixed(2)} />
                        <DeltaCell value={d.lcrDeltaPp} fmt={(v) => v.toFixed(2)} />
                        <DeltaCell value={d.nsfrDeltaPp} fmt={(v) => v.toFixed(2)} />
                        <DeltaCell
                          value={d.netIncomeDelta}
                          fmt={(v) => fmtCurrency(Math.abs(v), 'GHS')}
                          signed
                        />
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            {view.createdAt && (
              <p className="text-caption text-slate">
                Last run {fmtTimestamp(view.createdAt)}
              </p>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function ComparisonCell({
  label,
  comparison,
  fmt,
  deltaFmt,
}: {
  label: string;
  comparison: Comparison;
  fmt: (v: number) => string;
  deltaFmt: (v: number) => string;
}) {
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
        {fmt(comparison.shocked)}
      </p>
      <p className="text-caption text-slate">
        base <span className="font-mono">{fmt(comparison.base)}</span>{' '}
        <span
          className={`font-mono font-medium ${
            comparison.delta < 0 ? 'text-critical' : 'text-success'
          }`}
        >
          {deltaFmt(comparison.delta)}
        </span>
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
      className={`py-1.5 text-right font-mono tabular-nums ${
        value < 0 ? 'text-critical' : value > 0 ? 'text-success' : 'text-slate'
      }`}
    >
      {display}
    </td>
  );
}
