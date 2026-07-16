'use client';

import { useState } from 'react';
import { Loader2, PlayCircle } from 'lucide-react';
import type {
  ForecastRunSummaryRead,
  ForecastScenarioCode,
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
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import ForecastRunView from '@/components/forecasting/ForecastRunView';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateForecastRun,
  useForecastRun,
  useForecastRuns,
} from '@/lib/api/hooks';
import {
  fmtDateUTC,
  fmtTimestamp,
  isoDate,
  labelize,
  num,
  shortId,
} from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

const PRESET_SCENARIOS: { code: ForecastScenarioCode; label: string }[] = [
  { code: 'base', label: 'Base case' },
  { code: 'adverse', label: 'Adverse' },
  { code: 'severely_adverse', label: 'Severely adverse' },
];

const SCENARIO_LABELS: Record<string, string> = {
  base: 'Base case',
  adverse: 'Adverse',
  severely_adverse: 'Severely adverse',
  custom: 'Custom',
};

export default function ForecastDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const [scenario, setScenario] = useState<ForecastScenarioCode>('base');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [compareRunId, setCompareRunId] = useState<string | null>(null);

  const runsQuery = useForecastRuns(bankId, { limit: 25 });
  const runs = runsQuery.data?.runs ?? [];
  const latestSucceededId =
    runs.find((r) => r.status === 'succeeded')?.id ?? null;
  const activeRunId = selectedRunId ?? latestSucceededId;

  const runQuery = useForecastRun(bankId, activeRunId);
  const compareQuery = useForecastRun(bankId, compareRunId);
  const createRun = useCreateForecastRun(bankId);

  const run = runQuery.data;
  const compareRun = compareQuery.data;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting' },
          { label: 'Forecast Dashboard' },
        ]}
        title="Balance Sheet Forecast"
        subtitle="Deterministic 5-year projection from canonical financials and preset scenario assumptions"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="forecast"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            <select
              value={scenario}
              onChange={(e) =>
                setScenario(e.target.value as ForecastScenarioCode)
              }
              aria-label="Forecast scenario"
              className="px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-white hover:bg-surface"
            >
              {PRESET_SCENARIOS.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={createRun.isPending || !periodId}
              onClick={() =>
                periodId &&
                createRun.mutate(
                  {
                    reportingPeriodId: periodId,
                    scenarioCode: scenario,
                  },
                  { onSuccess: (created) => setSelectedRunId(created.id) }
                )
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
            >
              {createRun.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <PlayCircle size={13} aria-hidden />
              )}
              Run forecast
            </button>
          </div>
        }
      />

      <QueryBoundary
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {createRun.error && (
            <ErrorPanel error={createRun.error} title="Forecast run failed" />
          )}

          {!activeRunId ? (
            <EmptyState
              Icon={PlayCircle}
              title="No forecast runs yet"
              description={`Run a forecast to project ${bank?.name ?? 'the bank'}'s balance sheet, P&L, and regulatory ratios five years forward from ${period?.label ?? 'the selected period'} under a preset scenario. Every run persists an immutable, auditable record.`}
            />
          ) : runQuery.isLoading ? (
            <p className="text-body text-slate">Loading forecast run…</p>
          ) : runQuery.error ? (
            <ErrorPanel
              error={runQuery.error}
              onRetry={() => runQuery.refetch()}
            />
          ) : run && run.status !== 'succeeded' ? (
            <ErrorPanel
              error={
                new Error(
                  run.error?.message ??
                    `Run ${labelize(run.status)} — no projection output available.`
                )
              }
              title={`Run ${labelize(run.status)}`}
            />
          ) : run ? (
            <>
              <div className="flex items-center gap-3 flex-wrap">
                <StatusPill tone="action">
                  {SCENARIO_LABELS[run.scenarioCode] ?? labelize(run.scenarioCode)}{' '}
                  scenario
                </StatusPill>
                <span className="text-caption text-slate">
                  Run <span className="font-mono text-navy">{shortId(run.id)}</span>{' '}
                  · created {fmtTimestamp(run.createdAt)} · period{' '}
                  <span className="font-mono text-navy">
                    {runs.find((r) => r.id === run.id)?.periodLabel ??
                      period?.label}
                  </span>
                </span>
              </div>

              <ForecastRunView run={run} />

              {compareRun && compareRun.id !== run.id && (
                <CompareSection base={run} other={compareRun} />
              )}
            </>
          ) : null}

          {/* Runs history */}
          <Card>
            <CardHeader
              title="Forecast run history"
              subtitle="Immutable persisted runs · Select a run to view, pin a second to compare"
            />
            <CardBody className="p-0">
              <RunsHistoryTable
                runs={runs}
                activeRunId={activeRunId}
                compareRunId={compareRunId}
                onSelect={(id) => setSelectedRunId(id)}
                onToggleCompare={(id) =>
                  setCompareRunId((current) => (current === id ? null : id))
                }
              />
            </CardBody>
          </Card>
        </div>
      </QueryBoundary>
    </>
  );
}

function RunsHistoryTable({
  runs,
  activeRunId,
  compareRunId,
  onSelect,
  onToggleCompare,
}: {
  runs: ForecastRunSummaryRead[];
  activeRunId: string | null;
  compareRunId: string | null;
  onSelect: (id: string) => void;
  onToggleCompare: (id: string) => void;
}) {
  if (!runs.length) {
    return (
      <p className="px-5 py-4 text-body text-slate">
        No forecast runs yet — run a forecast to create the first auditable
        projection.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body border-collapse">
        <thead>
          <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
            <th className="text-left px-4 py-2.5">Created</th>
            <th className="text-left px-4 py-2.5">Scenario</th>
            <th className="text-left px-4 py-2.5">Period</th>
            <th className="text-right px-4 py-2.5">Avg ROE</th>
            <th className="text-right px-4 py-2.5">Y5 CAR</th>
            <th className="text-right px-4 py-2.5">Y5 LCR</th>
            <th className="text-right px-4 py-2.5">Y5 NSFR</th>
            <th className="text-left px-4 py-2.5">Status</th>
            <th className="text-center px-4 py-2.5">Compare</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const isActive = r.id === activeRunId;
            return (
              <tr
                key={r.id}
                className={`border-b border-border-light last:border-b-0 cursor-pointer ${
                  isActive ? 'bg-action-light/50' : 'hover:bg-surface-alt'
                }`}
                onClick={() => r.status === 'succeeded' && onSelect(r.id)}
              >
                <td className="px-4 py-2.5 font-mono text-caption text-slate whitespace-nowrap">
                  {fmtTimestamp(r.createdAt)}
                </td>
                <td className="px-4 py-2.5 text-navy font-medium">
                  {SCENARIO_LABELS[r.scenarioCode] ?? labelize(r.scenarioCode)}
                </td>
                <td className="px-4 py-2.5 font-mono text-caption text-slate">
                  {r.periodLabel}
                </td>
                <td className="px-4 py-2.5 num">
                  {r.avgRoePct === null ? '—' : fmtPct(num(r.avgRoePct), 2)}
                </td>
                <td className="px-4 py-2.5 num">
                  {r.year5CarPct === null ? '—' : fmtPct(num(r.year5CarPct), 2)}
                </td>
                <td className="px-4 py-2.5 num">
                  {r.year5LcrPct === null ? '—' : fmtPct(num(r.year5LcrPct), 2)}
                </td>
                <td className="px-4 py-2.5 num">
                  {r.year5NsfrPct === null ? '—' : fmtPct(num(r.year5NsfrPct), 2)}
                </td>
                <td className="px-4 py-2.5">
                  <StatusPill
                    tone={r.status === 'succeeded' ? 'success' : 'critical'}
                  >
                    {labelize(r.status)}
                  </StatusPill>
                </td>
                <td className="px-4 py-2.5 text-center">
                  <input
                    type="checkbox"
                    aria-label={`Compare run ${shortId(r.id)}`}
                    checked={r.id === compareRunId}
                    disabled={r.status !== 'succeeded'}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => onToggleCompare(r.id)}
                    className="accent-action"
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CompareSection({
  base,
  other,
}: {
  base: Parameters<typeof ForecastRunView>[0]['run'];
  other: Parameters<typeof ForecastRunView>[0]['run'];
}) {
  const baseLabel = SCENARIO_LABELS[base.scenarioCode] ?? base.scenarioCode;
  const otherLabel = SCENARIO_LABELS[other.scenarioCode] ?? other.scenarioCode;

  const years = base.path.map((p) => p.year);
  const chartData = years.map((year) => ({
    month: `Y${year}`,
    selected: num(base.path.find((p) => p.year === year)?.carPct),
    pinned: num(other.path.find((p) => p.year === year)?.carPct),
  }));

  const summaryRows: {
    label: string;
    fmt: (v: string) => string;
    pick: (run: typeof base) => string;
  }[] = [
    {
      label: 'Average ROE',
      fmt: (v) => fmtPct(num(v), 2),
      pick: (r) => r.summary.avgRoePct,
    },
    {
      label: 'Year-5 CAR',
      fmt: (v) => fmtPct(num(v), 2),
      pick: (r) => r.summary.year5CarPct,
    },
    {
      label: 'Year-5 LCR',
      fmt: (v) => fmtPct(num(v), 2),
      pick: (r) => r.summary.year5LcrPct,
    },
    {
      label: 'Year-5 NSFR',
      fmt: (v) => fmtPct(num(v), 2),
      pick: (r) => r.summary.year5NsfrPct,
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Run comparison"
        subtitle={`${baseLabel} (selected · ${shortId(base.id)}) vs ${otherLabel} (pinned · ${shortId(other.id)})`}
      />
      <CardBody className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate mb-3">
            Year-5 summary
          </p>
          <table className="w-full text-body border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-2 text-micro font-medium uppercase tracking-wider text-slate">
                  Metric
                </th>
                <th className="text-right py-2 text-micro font-medium uppercase tracking-wider text-slate">
                  {baseLabel} (selected)
                </th>
                <th className="text-right py-2 text-micro font-medium uppercase tracking-wider text-slate">
                  {otherLabel} (pinned)
                </th>
              </tr>
            </thead>
            <tbody>
              {summaryRows.map((row) => (
                <tr key={row.label} className="border-b border-border-light last:border-b-0">
                  <td className="py-2.5 text-navy/90">{row.label}</td>
                  <td className="py-2.5 num font-mono text-navy tabular-nums">
                    {row.fmt(row.pick(base))}
                  </td>
                  <td className="py-2.5 num font-mono text-navy tabular-nums">
                    {row.fmt(row.pick(other))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate mb-3">
            CAR path overlay
          </p>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart
              data={chartData}
              margin={{ top: 12, right: 24, left: 0, bottom: 8 }}
            >
              <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="month" axisLine={{ stroke: '#D0D7DE' }} tickLine={false} />
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
              <Tooltip formatter={(v: number, name) => [`${v.toFixed(2)}%`, name]} />
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
                dataKey="selected"
                stroke="#0A2540"
                strokeWidth={2}
                name={`${baseLabel} CAR`}
                dot={{ r: 3, fill: '#0A2540' }}
              />
              <Line
                type="monotone"
                dataKey="pinned"
                stroke="#2D7FF9"
                strokeWidth={2}
                name={`${otherLabel} CAR`}
                dot={{ r: 3, fill: '#2D7FF9' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardBody>
    </Card>
  );
}
