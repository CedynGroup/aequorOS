'use client';

/**
 * Curves explorer (FC-5) — the bank/client-facing single-curve workbench inside
 * Markets. The desk publishes forward curves as golden `canonical_yield_curves`
 * under AEQ.* codes; banks consume them read-only with as-of reproduction and
 * their own private overlays. This component lets a bank:
 *
 *   1. pick a published curve by currency + AEQ.* code (the as-of scrubber lives
 *      on the page and reproduces the whole Markets tab as-published-then);
 *   2. read the tenor-adjusted forward grid (ForwardGrid) with a day-count
 *      "Convert to" switch;
 *   3. toggle Official published vs Your adjusted (official solid, adjusted
 *      dashed) and open the overlay editor (reused OverlayDrawer, opened by the
 *      page);
 *   4. open the methodology transparency drawer for model-risk evidence.
 *
 * Reproduction is bitemporal: the curve is served exactly as it was published at
 * the chosen as-of date (backend `list_yield_curves(..., as_of=)`), never
 * re-derived. Only this bank's overlays are ever visible.
 */

import { useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Activity, ArrowRight, BookOpen, CalendarClock, SlidersHorizontal } from 'lucide-react';
import type { YieldCurveViewRead } from '@aequoros/risk-service-api';
import ChartFrame from '@/components/ui/ChartFrame';
import SubTabs from '@/components/ui/SubTabs';
import {
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  CHART_ACCENT,
  CHART_GRID,
  seriesColor,
} from '@/lib/chartTheme';
import { num, fmtDateUTC } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';
import { tenorLabel } from './CurveBoard';
import MethodologyDrawer from './MethodologyDrawer';
import AttributionChip from './AttributionChip';
import { CurveTypeBadge, MonoChip, SyntheticProxyBadge } from './chips';

/** Prefer a forward curve as the default focus, else the first published curve. */
function preferredCurve(curves: YieldCurveViewRead[]): YieldCurveViewRead | undefined {
  return curves.find((c) => c.curveType === 'forward') ?? curves[0];
}

type ChartRow = { tenorMonths: number; official: number; adjusted?: number };

export default function CurvesExplorer({
  curves,
  asOfDate,
  isReproduction,
  selectedCurveName,
  onSelectCurve,
  onOpenForward,
  onEditOverlays,
}: {
  curves: YieldCurveViewRead[];
  asOfDate: Date;
  isReproduction: boolean;
  selectedCurveName: string | null;
  onSelectCurve: (curveName: string) => void;
  onOpenForward: (curveName: string) => void;
  onEditOverlays: (curveName: string) => void;
}) {
  const [view, setView] = useState<'official' | 'adjusted'>('official');
  const [methodologyOpen, setMethodologyOpen] = useState(false);

  // Derive the effective curve every render with a fallback, so an as-of change
  // that drops the selected curve name never strands the view on nothing.
  const selectedCurve =
    curves.find((c) => c.curveName === selectedCurveName) ?? preferredCurve(curves);

  if (!selectedCurve) return null;

  const currencies = [...new Set(curves.map((c) => c.currency))].sort();
  const curvesInCurrency = curves
    .filter((c) => c.currency === selectedCurve.currency)
    .sort((a, b) => a.curveName.localeCompare(b.curveName));

  const hasAdjusted = selectedCurve.adjustedPoints.length > 0;
  const showAdjusted = view === 'adjusted';

  const adjustedByTenor = new Map(
    selectedCurve.adjustedPoints.map((p) => [p.tenorMonths, num(p.rate) * 100])
  );
  const chartData: ChartRow[] = [...selectedCurve.points]
    .sort((a, b) => a.tenorMonths - b.tenorMonths)
    .map((p) => {
      const row: ChartRow = { tenorMonths: p.tenorMonths, official: num(p.rate) * 100 };
      const adj = adjustedByTenor.get(p.tenorMonths);
      if (showAdjusted && adj !== undefined) row.adjusted = adj;
      return row;
    });

  const selectClass =
    'w-full px-2.5 py-1.5 text-body bg-surface border border-border rounded text-navy';

  return (
    <div className="space-y-4">
      {/* Selectors: currency + AEQ.* curve code. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-xl">
        <label className="block space-y-1">
          <span className="text-micro font-medium text-slate uppercase tracking-wider">
            Currency
          </span>
          <select
            value={selectedCurve.currency}
            onChange={(event) => {
              const first = curves.find((c) => c.currency === event.target.value);
              if (first) onSelectCurve(first.curveName);
            }}
            className={selectClass}
          >
            {currencies.map((ccy) => (
              <option key={ccy} value={ccy}>
                {ccy}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-micro font-medium text-slate uppercase tracking-wider">
            Published curve
          </span>
          <select
            value={selectedCurve.curveName}
            onChange={(event) => onSelectCurve(event.target.value)}
            className={`${selectClass} font-mono`}
          >
            {curvesInCurrency.map((c) => (
              <option key={c.curveName} value={c.curveName}>
                {c.curveName} · {c.curveType}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Bitemporal reproduction affirmation + this curve's source/attribution. */}
      <div
        className={`rounded-lg border px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2 ${
          isReproduction
            ? 'border-warning/30 bg-warning-light'
            : 'border-border-light bg-surface'
        }`}
      >
        <span className="inline-flex items-center gap-2 text-body min-w-0">
          {isReproduction ? (
            <CalendarClock size={15} className="text-warning shrink-0" aria-hidden />
          ) : (
            <Activity size={15} className="text-action shrink-0" aria-hidden />
          )}
          <span className="text-navy">
            {isReproduction ? (
              <>
                Reproduced as published on{' '}
                <span className="font-mono font-medium">{fmtDateUTC(asOfDate)}</span> — the golden
                copy as it stood then, not re-derived.
              </>
            ) : (
              <>
                Live — latest published values as of{' '}
                <span className="font-mono font-medium">{fmtDateUTC(asOfDate)}</span>.
              </>
            )}
          </span>
        </span>
        <span className="inline-flex items-center gap-1.5 flex-wrap ml-auto">
          <CurveTypeBadge curveType={selectedCurve.curveType} />
          {selectedCurve.curveType === 'discount' && <SyntheticProxyBadge />}
          <AttributionChip attribution={selectedCurve.attribution} />
        </span>
      </div>

      {/* Official vs Your adjusted + drawer actions. */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <SubTabs
          items={[
            { key: 'official', label: 'Official published' },
            { key: 'adjusted', label: 'Your adjusted' },
          ]}
          active={view}
          onChange={(key) => setView(key as 'official' | 'adjusted')}
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMethodologyOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-slate border border-border rounded hover:bg-surface hover:text-navy whitespace-nowrap"
          >
            <BookOpen size={13} aria-hidden />
            Methodology
          </button>
          <button
            type="button"
            onClick={() => onOpenForward(selectedCurve.curveName)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-action border border-action/30 rounded hover:bg-action-light whitespace-nowrap"
          >
            Forecast grid
            <ArrowRight size={13} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onEditOverlays(selectedCurve.curveName)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-action border border-action/30 rounded hover:bg-action-light whitespace-nowrap"
          >
            <SlidersHorizontal size={13} aria-hidden />
            {selectedCurve.overlayComponents.length > 0
              ? `Edit spreads (${selectedCurve.overlayComponents.length})`
              : 'Edit spreads'}
          </button>
        </div>
      </div>

      {showAdjusted && !hasAdjusted && (
        <p className="text-caption text-slate">
          No active spreads on this curve. Your adjusted curve equals the official published curve
          until you add overlay spreads via &ldquo;Edit spreads&rdquo;.
        </p>
      )}

      {/* Focused single-curve chart: official solid vs adjusted dashed. */}
      <ChartFrame
        title={
          <span className="inline-flex items-center gap-2 flex-wrap">
            <span>{selectedCurve.curveName}</span>
            <MonoChip>{selectedCurve.currency}</MonoChip>
          </span>
        }
        subtitle={
          showAdjusted
            ? 'Official published (solid) vs your adjusted composition (dashed)'
            : 'Published curve at the reproduced as-of date'
        }
        height={280}
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={chartMargins}>
            <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis
              {...axisProps}
              dataKey="tenorMonths"
              tickFormatter={(value: number) => tenorLabel(value)}
            />
            <YAxis
              {...axisProps}
              tickFormatter={(value: number) => `${value.toFixed(1)}%`}
              width={52}
            />
            <Tooltip
              {...chartTooltipProps}
              labelFormatter={(value) => `Tenor ${tenorLabel(Number(value))}`}
              formatter={(value: number | string, name: string) => [fmtPct(Number(value), 2), name]}
            />
            <Legend {...chartLegendProps} />
            <Line
              type="monotone"
              dataKey="official"
              name="Official published"
              stroke={seriesColor(0)}
              strokeWidth={2}
              dot={{ r: 2.5 }}
              connectNulls
            />
            {showAdjusted && hasAdjusted && (
              <Line
                type="monotone"
                dataKey="adjusted"
                name="Your adjusted"
                stroke={CHART_ACCENT}
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={{ r: 2.5 }}
                connectNulls
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </ChartFrame>

      <div className="flex flex-wrap items-center justify-between gap-3 border border-border rounded-lg bg-surface/45 px-4 py-3">
        <div>
          <p className="text-body font-medium text-navy">Need forecast periods or discount factors?</p>
          <p className="mt-0.5 text-caption text-slate">
            Open the published forward grid for the exact calendar-adjusted Start / End / DF / Yield rows.
          </p>
        </div>
        <button
          type="button"
          onClick={() => onOpenForward(selectedCurve.curveName)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium btn-primary whitespace-nowrap"
        >
          Open forecast grid
          <ArrowRight size={13} aria-hidden />
        </button>
      </div>

      {methodologyOpen && (
        <MethodologyDrawer curve={selectedCurve} onClose={() => setMethodologyOpen(false)} />
      )}
    </div>
  );
}
