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

import { useState } from 'react';
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
  CalendarDays,
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

  const selectedCurve =
    curves.find((curve) => curve.curveName === selectedCurveName) ?? preferredCurve(curves);
  const effectiveName = selectedCurve?.curveName ?? null;

  const grid = useForwardGrid(bankId, effectiveName, asOf ?? undefined);

  if (curves.length === 0) {
    return (
      <EmptyState
        Icon={GitBranch}
        title="No curves published"
        description="The desk has published no curves for this bank yet, so there is no forward grid to read. Curves arrive from the market research desk or your own ingested curve data."
      />
    );
  }

  if (!selectedCurve) return null;

  const currencies = [...new Set(curves.map((curve) => curve.currency))].sort();
  const curvesInCurrency = curves
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
  const chartRows = rows.filter((row) => row.idx > 0);

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-lg border border-border bg-surface-raised">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">Published forward curve</p>
            <h2 className="mt-1 text-h2 text-navy">Forecast rate workspace</h2>
            <p className="mt-1 text-caption text-slate">
              Select an approved curve definition and pull its desk-published forward periods.
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
        </div>

        <div className="grid grid-cols-1 gap-3 border-b border-border px-5 py-4 sm:grid-cols-3">
          <label className="block space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Currency</span>
            <select
              value={selectedCurve.currency}
              onChange={(event) => {
                const first = curves.find((curve) => curve.currency === event.target.value);
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
          <div className="space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-slate">Valuation date</span>
            <div className="flex h-[34px] items-center gap-2 rounded border border-border bg-surface px-2.5 text-body text-navy">
              <CalendarDays size={14} className="text-slate" aria-hidden />
              <span className="font-mono">{meta?.asOf ?? asOf ?? 'Latest published'}</span>
            </div>
          </div>
        </div>

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

      {grid.isError ? (
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

          <SectionCard
            title="Forward grid"
            subtitle="The desk's published Start / End / discount-factor / forward-yield rows"
            actions={<BasisSwitcher value={basis} onChange={setBasis} />}
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

          {meta && meta.pillars.length > 0 && (
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
    </div>
  );
}
