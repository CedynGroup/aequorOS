'use client';

/**
 * Forward tab (spec §5, §4 FC-5/G1) — the desk's PUBLISHED forward grid for a
 * curve, read straight from `GET /curves/{name}/forward-grid`. Unlike the
 * derived grid inside CurvesExplorer (which rolls dates and computes DFs from
 * the bank-facing points), this reads the authoritative Start/End/DF/forward-
 * yield rows the desk built in `curve_construction`. Rates/yields on the payload
 * are DECIMAL FRACTIONS — rendered ×100.
 *
 * The BasisSwitcher and the day-count helpers are reused from the existing
 * forward-grid module: the published forward yield is the anchor column, and
 * "Convert to" re-expresses it on another day-count while holding the published
 * discount factor invariant (identical semantics to the derived grid).
 */

import { useEffect, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  BookOpen,
  ArrowRight,
  CalendarDays,
  Download,
  GitBranch,
  Info,
  LineChart as LineChartIcon,
  LockKeyhole,
} from 'lucide-react';
import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import ChartFrame from '@/components/ui/ChartFrame';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import SubTabs from '@/components/ui/SubTabs';
import { isApiError } from '@/lib/api/client';
import { useForwardGrid } from '@/lib/api/hooks';
import { fmtDateUTC, num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import {
  axisProps,
  chartMargins,
  chartTooltipProps,
  CHART_GRID,
  seriesColor,
} from '@/lib/chartTheme';
import {
  actualDays,
  BASIS_LABELS,
  type DayCountBasis,
  yearFraction,
  yieldFromDf,
} from '@/lib/markets/curveGrid';
import { BasisSwitcher } from './ForwardGrid';
import { MonoChip } from './chips';
import AttributionChip from './AttributionChip';
import { tenorLabel } from './CurveBoard';
import { downloadTextFile } from '@/lib/download';

/** Prefer a forward curve as the default focus, else the first published curve. */
function preferredCurve(curves: YieldCurveViewRead[]): YieldCurveViewRead | undefined {
  return curves.find((curve) => curve.curveType === 'forward') ?? curves[0];
}

function parseDate(value: string): Date | null {
  if (!value) return null;
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

type GridRow = {
  idx: number;
  label: string;
  start: Date | null;
  end: Date | null;
  days: number | null;
  df: number;
  publishedYieldPct: number;
  convertedYieldPct: number;
};

const selectClass =
  'w-full px-2.5 py-1.5 text-body bg-surface border border-border rounded text-navy';

const DAY_COUNT_OPTIONS: { value: DayCountBasis; label: string }[] = [
  { value: 'act360', label: 'MM Act/360' },
  { value: 'act365', label: 'Act/365' },
  { value: 'actact', label: 'Act/Act' },
  { value: 'thirty360', label: '30/360' },
];

function AssumptionCell({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0 border-l border-border-light pl-3 first:border-l-0 first:pl-0">
      <p className="text-micro font-medium uppercase tracking-wider text-slate">{label}</p>
      <p className={`mt-1 truncate text-caption text-navy ${mono ? 'font-mono' : ''}`} title={value}>
        {value}
      </p>
    </div>
  );
}

function csvCell(value: string | number): string {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export default function ForwardTab({
  bankId,
  curves,
  asOf,
  selectedCurveName,
  onSelectCurve,
}: {
  bankId: string;
  curves: YieldCurveViewRead[];
  asOf: string | null;
  selectedCurveName: string | null;
  onSelectCurve: (curveName: string) => void;
}) {
  const [basis, setBasis] = useState<DayCountBasis>('act360');
  const [workspaceView, setWorkspaceView] = useState<'published' | 'pull'>('published');
  const [resultView, setResultView] = useState<'grid' | 'analysis'>('grid');
  const [hasPulled, setHasPulled] = useState(false);
  const [frequency, setFrequency] = useState('3M');
  const [pullAsOf, setPullAsOf] = useState(() => asOf ?? new Date().toISOString().slice(0, 10));
  const [curveType, setCurveType] = useState<'forward' | 'discount'>('forward');

  const forwardCurves = curves.filter((curve) => curve.curveType === 'forward');
  const pullableCurves = curves.filter(
    (curve) => curve.curveType === 'forward' || curve.curveType === 'discount'
  );
  const curvesForType = pullableCurves.filter((curve) => curve.curveType === curveType);

  const selectedCurve =
    curvesForType.find((curve) => curve.curveName === selectedCurveName) ??
    preferredCurve(curvesForType) ??
    forwardCurves.find((curve) => curve.curveName === selectedCurveName) ??
    preferredCurve(forwardCurves);
  const effectiveName = selectedCurve?.curveName ?? null;

  useEffect(() => {
    setHasPulled(false);
    setResultView('grid');
  }, [effectiveName, pullAsOf, frequency, basis]);

  useEffect(() => {
    if (asOf) setPullAsOf(asOf);
  }, [asOf]);

  const grid = useForwardGrid(
    bankId,
    effectiveName,
    pullAsOf,
    frequency,
    workspaceView === 'pull'
  );

  if (forwardCurves.length === 0) {
    return (
      <EmptyState
        Icon={GitBranch}
        title="No published forward curves"
        description="The desk has not published a forward curve for this bank at the selected date. Published forward curves become available here after desk approval."
      />
    );
  }

  if (!selectedCurve) return null;

  const currencies = [...new Set(pullableCurves.map((curve) => curve.currency))].sort();
  const curvesInCurrency = curvesForType
    .filter((curve) => curve.currency === selectedCurve.currency)
    .sort((a, b) => a.curveName.localeCompare(b.curveName));

  const rows: GridRow[] = (grid.data?.rows ?? []).map((row, idx) => {
    const start = parseDate(row.start);
    const end = parseDate(row.end);
    const df = num(row.discountFactor);
    const publishedYieldPct = num(row.forwardYield) * 100;
    const days = start && end ? actualDays(start, end) : null;
    const tau = start && end ? yearFraction(start, end, basis) : 0;
    const convertedYieldPct = tau > 0 && df > 0 ? yieldFromDf(df, tau) * 100 : publishedYieldPct;
    return {
      idx,
      label: end ? fmtDateUTC(end) : `#${idx + 1}`,
      start,
      end,
      days,
      df,
      publishedYieldPct,
      convertedYieldPct,
    };
  });

  const columns: Column<GridRow>[] = [
    {
      key: 'start',
      header: 'Period start',
      render: (row) => (
        <span className="font-mono text-caption text-slate">
          {row.start ? fmtDateUTC(row.start) : '—'}
        </span>
      ),
    },
    {
      key: 'end',
      header: 'Period end',
      render: (row) => (
        <span className="font-mono text-caption text-navy/90">
          {row.end ? fmtDateUTC(row.end) : '—'}
        </span>
      ),
    },
    {
      key: 'days',
      header: 'Days',
      numeric: true,
      width: '8%',
      render: (row) => <span className="text-caption">{row.days ?? '—'}</span>,
    },
    {
      key: 'df',
      header: 'Discount factor',
      numeric: true,
      render: (row) => <span className="tnum">{row.df.toFixed(6)}</span>,
    },
    {
      key: 'forward',
      header: 'Forward yield',
      numeric: true,
      render: (row) => (
        <span className="tnum text-navy font-medium">{fmtPct(row.publishedYieldPct, 3)}</span>
      ),
    },
    {
      key: 'converted',
      header: `Yield · ${BASIS_LABELS[basis]}`,
      numeric: true,
      render: (row) => <span className="tnum text-slate">{fmtPct(row.convertedYieldPct, 3)}</span>,
    },
  ];

  const meta = grid.data;
  const assumptions = meta?.assumptions;
  const publishedInterpolation = meta?.interpolation ?? 'Resolved on pull';
  const chartRows = rows.filter((row) => row.idx > 0);
  const frequencyOptions = ['1D', '1M', '3M', '6M', '1Y'];
  const availableFrequencies = meta?.availableFrequencies ?? frequencyOptions;
  const publishedTenors = [...selectedCurve.points]
    .sort((left, right) => left.tenorMonths - right.tenorMonths)
    .map((point) => ({
      tenorMonths: point.tenorMonths,
      tenor: tenorLabel(point.tenorMonths),
      ratePct: num(point.rate) * 100,
    }));

  function exportCsv() {
    if (!meta || rows.length === 0) return;
    const provenance = [
      ['AequorOS forward curve pull'],
      ['Curve', meta.curveName],
      ['As of', meta.asOf],
      ['Methodology', meta.methodologyRef ?? 'Not supplied'],
      ['Interpolation', meta.interpolation ?? 'Not supplied'],
      ['Grid frequency', meta.frequency],
      ['Grid evidence', meta.gridIsAuthoritative ? 'Exact published grid' : 'Historical reconstruction'],
      ['Output basis', BASIS_LABELS[basis]],
      ['Definition version', meta.assumptions ? `v${meta.assumptions.version}` : 'Not supplied'],
      [],
      ['period_start', 'period_end', 'days', 'discount_factor', 'published_forward_yield_pct', `yield_${basis}_pct`],
      ...rows.map((row) => [
        row.start ? fmtDateUTC(row.start) : '',
        row.end ? fmtDateUTC(row.end) : '',
        row.days ?? '',
        row.df.toFixed(12),
        row.publishedYieldPct.toFixed(6),
        row.convertedYieldPct.toFixed(6),
      ]),
    ];
    const content = provenance.map((row) => row.map(csvCell).join(',')).join('\n');
    downloadTextFile(
      `${meta.curveName.replaceAll('.', '_')}_${meta.asOf}_forward-grid.csv`,
      content,
      'text/csv;charset=utf-8',
    );
  }

  return (
    <div className="space-y-4">
      <SubTabs
        items={[
          { key: 'published', label: 'Published curves' },
          { key: 'pull', label: 'Curve pull' },
        ]}
        active={workspaceView}
        onChange={(key) => setWorkspaceView(key as 'published' | 'pull')}
      />

      {workspaceView === 'published' && (
        <section className="overflow-hidden rounded-lg border border-border bg-surface-raised">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate">Desk publication surface</p>
              <h2 className="mt-1 text-h2 text-navy">Published forward curves</h2>
              <p className="mt-1 text-caption text-slate">
                Every published tenor on the selected desk forward curve, as approved for this institution.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <MonoChip>{selectedCurve.currency}</MonoChip>
              <AttributionChip attribution={selectedCurve.attribution} />
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 border-b border-border px-5 py-4 md:grid-cols-[minmax(16rem,1fr)_auto] md:items-end">
            <label className="block space-y-1 max-w-xl">
              <span className="text-micro font-medium uppercase tracking-wider text-slate">Published curve</span>
              <select
                value={selectedCurve.curveName}
                onChange={(event) => onSelectCurve(event.target.value)}
                className={`${selectClass} font-mono`}
              >
                {forwardCurves.map((curve) => (
                  <option key={curve.curveName} value={curve.curveName}>
                    {curve.curveName} · {curve.currency} · {fmtDateUTC(curve.asOfDate)}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setWorkspaceView('pull')}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary whitespace-nowrap"
            >
              Pull historical or forecast curve
              <ArrowRight size={13} aria-hidden />
            </button>
          </div>
          <div className="grid grid-cols-1 gap-5 p-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(24rem,1.1fr)]">
            <ChartFrame
              title={<span className="font-mono">{selectedCurve.curveName}</span>}
              subtitle={`Published ${fmtDateUTC(selectedCurve.asOfDate)} · ${publishedTenors.length} tenor points`}
              height={300}
            >
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={publishedTenors} margin={chartMargins}>
                  <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
                  <XAxis {...axisProps} dataKey="tenor" minTickGap={20} />
                  <YAxis
                    {...axisProps}
                    tickFormatter={(value: number) => `${value.toFixed(1)}%`}
                    width={52}
                  />
                  <Tooltip
                    {...chartTooltipProps}
                    formatter={(value: number | string) => [fmtPct(Number(value), 3), 'Forward yield']}
                  />
                  <Line
                    type="monotone"
                    dataKey="ratePct"
                    name="Forward yield"
                    stroke={seriesColor(0)}
                    strokeWidth={2}
                    dot={{ r: 2.5 }}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </ChartFrame>
            <div className="overflow-hidden rounded border border-border bg-surface">
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div>
                  <h3 className="text-body font-semibold text-navy">Published tenor values</h3>
                  <p className="mt-0.5 text-caption text-slate">Complete tenor surface carried by this published curve.</p>
                </div>
                <span className="text-caption font-mono text-slate">{publishedTenors.length} rows</span>
              </div>
              <DataTable
                columns={[
                  {
                    key: 'tenor',
                    header: 'Tenor',
                    render: (row: (typeof publishedTenors)[number]) => (
                      <span className="font-mono text-caption text-navy">{row.tenor}</span>
                    ),
                  },
                  {
                    key: 'rate',
                    header: 'Forward yield',
                    numeric: true,
                    render: (row: (typeof publishedTenors)[number]) => (
                      <span className="font-mono font-medium text-navy">{fmtPct(row.ratePct, 3)}</span>
                    ),
                  },
                ]}
                rows={publishedTenors}
                density="compact"
                stickyHeader
                maxHeight={300}
              />
            </div>
          </div>
        </section>
      )}

      {workspaceView === 'pull' && (
        <>
      <section className="overflow-hidden rounded-lg border border-border bg-surface-raised">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Information retrieval</p>
            <h2 className="mt-1 text-h2 text-navy">Curve pull</h2>
            <p className="mt-1 text-caption text-slate">
              Retrieve a historical published grid or the selected curve&apos;s implied future forward periods.
            </p>
          </div>
          {meta && (
            <span
              className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-micro font-medium uppercase tracking-wider ${
                meta.gridIsAuthoritative
                  ? 'border-success/30 bg-success-light text-success'
                  : 'border-warning/30 bg-warning-light text-warning'
              }`}
            >
              {meta.gridIsAuthoritative ? 'Exact published grid' : 'Historical reconstruction'}
            </span>
          )}
          <button
            type="button"
            onClick={() => setHasPulled(true)}
            disabled={grid.isFetching}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {grid.isFetching ? 'Pulling curve…' : 'Pull curve'}
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 border-b border-border px-5 py-4 sm:grid-cols-2 xl:grid-cols-3">
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Curve type</span>
            <select
              value={curveType}
              onChange={(event) => {
                const nextType = event.target.value as 'forward' | 'discount';
                setCurveType(nextType);
                const first = pullableCurves.find((curve) => curve.curveType === nextType);
                if (first) onSelectCurve(first.curveName);
              }}
              className={selectClass}
            >
              <option value="forward">Regular forward curve</option>
              <option value="discount">OIS / discount curve</option>
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Currency</span>
            <select
              value={selectedCurve.currency}
              onChange={(event) => {
                const first = forwardCurves.find((curve) => curve.currency === event.target.value);
                if (first) onSelectCurve(first.curveName);
              }}
              className={selectClass}
            >
              {currencies.map((ccy) => (
                <option key={ccy} value={ccy}>{ccy}</option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Curve definition</span>
            <select
              value={selectedCurve.curveName}
              onChange={(event) => onSelectCurve(event.target.value)}
              className={`${selectClass} font-mono`}
            >
              {curvesInCurrency.map((curve) => (
                <option key={curve.curveName} value={curve.curveName}>
                  {curve.curveName} · {curve.curveType}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Grid frequency</span>
            <select
              value={frequency}
              onChange={(event) => setFrequency(event.target.value)}
              className={`${selectClass} font-mono`}
            >
              {frequencyOptions.map((option) => (
                <option
                  key={option}
                  value={option}
                  disabled={meta !== undefined && !availableFrequencies.includes(option)}
                >
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Day count</span>
            <select
              value={basis}
              onChange={(event) => setBasis(event.target.value as DayCountBasis)}
              className={selectClass}
            >
              {DAY_COUNT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Interpolation type</span>
            <select value={publishedInterpolation} disabled className={`${selectClass} disabled:cursor-not-allowed disabled:opacity-80`}>
              <option value={publishedInterpolation}>{publishedInterpolation}</option>
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Valuation date</span>
            <div className="relative">
              <CalendarDays size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-slate" aria-hidden />
              <input
                type="date"
                value={pullAsOf}
                max={new Date().toISOString().slice(0, 10)}
                onChange={(event) => setPullAsOf(event.target.value)}
                className="w-full rounded border border-border bg-surface py-1.5 pl-8 pr-2.5 text-body font-mono text-navy"
              />
            </div>
          </label>
        </div>

        <p className="border-b border-border bg-surface/45 px-5 py-2.5 text-caption text-slate">
          Day count changes the displayed and exported yield convention. Curve type and interpolation
          resolve an approved published methodology; alternative interpolation methods require a separate
          desk-published curve, not a client-side recalculation.
        </p>

        {assumptions && (
          <div className="bg-surface/45 px-5 py-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <span className="inline-flex items-center gap-2 text-caption text-slate">
                <LockKeyhole size={13} aria-hidden />
                Governed construction assumptions
              </span>
              <span className="text-micro text-slate">Track-2 changes are made at the research desk</span>
            </div>
            <div className="grid grid-cols-2 gap-y-4 md:grid-cols-3 xl:grid-cols-6">
              <AssumptionCell label="Calendar" value={assumptions.calendarName} />
              <AssumptionCell label="Forward curve" value={assumptions.projectionIndex ?? 'Self-discounting'} mono />
              <AssumptionCell label="Discount curve" value={assumptions.discountCurveCode ?? 'Self-discounting'} mono />
              <AssumptionCell label="Payment frequency" value={assumptions.paymentFrequency ?? 'Definition default'} />
              <AssumptionCell label="Payment interval" value={`${assumptions.paymentIntervalMonths} months`} />
              <AssumptionCell label="Curve frequency" value={assumptions.curveFrequency} mono />
              <AssumptionCell label="Output basis" value={assumptions.outputDaycount} mono />
              <AssumptionCell label="Interpolation" value={assumptions.interpolationMethod} mono />
              <AssumptionCell label="Instrument set" value={assumptions.instrumentSetRef} mono />
              <AssumptionCell label="Spot lag" value={`${assumptions.spotLagDays} business days`} />
              <AssumptionCell label="Roll convention" value={assumptions.rollConvention} mono />
              <AssumptionCell label="Definition version" value={`v${assumptions.version}`} mono />
            </div>
          </div>
        )}
      </section>

      {meta && availableFrequencies.length < frequencyOptions.length && (
        <p className="text-caption text-warning">
          This historical publication contains only {availableFrequencies.join(', ')}. Rebuild and
          republish it through the multi-frequency desk construction workflow to make the full
          frequency family available.
        </p>
      )}

      {meta && (meta.methodologyRef || meta.interpolation) && (
        <div className="flex flex-wrap items-center gap-2 text-caption text-slate">
          <BookOpen size={13} aria-hidden className="text-slate" />
          {meta.methodologyRef && (
            <span className="inline-flex items-center gap-1.5 rounded border border-border bg-surface px-2 py-1">
              Methodology <MonoChip>{meta.methodologyRef}</MonoChip>
            </span>
          )}
          {meta.interpolation && (
            <span className="inline-flex items-center gap-1.5 rounded border border-action/35 bg-action-light px-2 py-1 font-medium text-action">
              Published interpolation
              <span className="font-mono text-micro uppercase tracking-wider">{meta.interpolation}</span>
            </span>
          )}
        </div>
      )}

      {!hasPulled ? (
        <div className="rounded-lg border border-border bg-surface-raised px-5 py-8 text-center">
          <p className="text-body font-medium text-navy">Ready to retrieve a published curve</p>
          <p className="mt-1 text-caption text-slate">
            Select the valuation date and approved curve definition, then pull the exact historical or implied forward periods.
          </p>
        </div>
      ) : grid.isError ? (
        isApiError(grid.error) && grid.error.status === 404 ? (
          <EmptyState
            Icon={GitBranch}
            title="No published forward grid"
            description={`The desk has not published a forward grid for ${selectedCurve.curveName} at this date. Pick another curve, or check back once the desk approves its determination.`}
          />
        ) : (
          <ErrorPanel error={grid.error} onRetry={() => grid.refetch()} />
        )
      ) : !grid.data ? (
        <div className="card p-10 flex items-center justify-center text-caption text-slate">
          <span className="inline-flex items-center gap-2">
            <Info size={13} aria-hidden />
            Loading the published forward grid…
          </span>
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          Icon={GitBranch}
          title="Empty forward grid"
          description="The published grid for this curve has no rows at the selected as-of date."
        />
      ) : (
        <>
          <SubTabs
            items={[
              { key: 'grid', label: 'Pulled grid' },
              { key: 'analysis', label: 'Analysis' },
            ]}
            active={resultView}
            onChange={(key) => setResultView(key as 'grid' | 'analysis')}
          />

          {resultView === 'analysis' && (
          <ChartFrame
            title={
              <span className="inline-flex items-center gap-2 flex-wrap">
                <LineChartIcon size={15} className="text-action" aria-hidden />
                <span>{selectedCurve.curveName}</span>
                <MonoChip>{selectedCurve.currency}</MonoChip>
              </span>
            }
            subtitle="Forward yields across the desk-published periods"
            height={260}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartRows} margin={chartMargins}>
                <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
                <XAxis {...axisProps} dataKey="label" minTickGap={24} />
                <YAxis
                  {...axisProps}
                  tickFormatter={(value: number) => `${value.toFixed(1)}%`}
                  width={52}
                />
                <Tooltip
                  {...chartTooltipProps}
                  labelFormatter={(value) => `Period end ${value}`}
                  formatter={(value: number | string) => [fmtPct(Number(value), 3), 'Forward yield']}
                />
                <Line
                  type="monotone"
                  dataKey="publishedYieldPct"
                  name="Forward yield"
                  stroke={seriesColor(0)}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  connectNulls
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>
          )}

          {resultView === 'grid' && (
          <SectionCard
            title="Forward grid"
            subtitle="The desk's published Start / End / discount-factor / forward-yield rows"
            actions={
              <div className="flex items-center gap-2">
                <BasisSwitcher value={basis} onChange={setBasis} />
                <button
                  type="button"
                  onClick={exportCsv}
                  className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-caption font-medium text-navy hover:bg-surface"
                >
                  <Download size={13} aria-hidden />
                  CSV
                </button>
              </div>
            }
            noPadding
            footer={
              <span className="inline-flex items-start gap-1.5 text-caption text-slate">
                <Info size={12} className="mt-0.5 shrink-0" aria-hidden />
                <span>
                  <strong>Forward yield</strong> is the desk&rsquo;s published value (decimal
                  fraction ×100). <strong>Convert to</strong> re-expresses it on the chosen
                  day-count while holding the published <strong>discount factor</strong> invariant.
                </span>
              </span>
            }
          >
            <DataTable columns={columns} rows={rows} density="compact" stickyHeader maxHeight={420} />
          </SectionCard>
          )}

          {resultView === 'analysis' && meta && meta.pillars.length > 0 && (
            <SectionCard
              title="Reference pillars"
              subtitle="The instruments and quotes backing the published grid"
            >
              <div className="flex flex-wrap gap-2">
                {meta.pillars.map((pillar, index) => (
                  <span
                    key={`${pillar.tenor}-${index}`}
                    className="inline-flex items-center gap-2 rounded border border-border-light bg-surface px-2.5 py-1"
                  >
                    <MonoChip>{pillar.tenor}</MonoChip>
                    <span className="text-caption text-slate">{pillar.instrument}</span>
                    <span className="text-caption font-mono text-navy tnum">{pillar.quote}</span>
                  </span>
                ))}
              </div>
            </SectionCard>
          )}
        </>
      )}
        </>
      )}
    </div>
  );
}
