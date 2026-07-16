/**
 * Recharts theme bridge — reads the semantic chart tokens from globals.css at
 * render time, so charts follow the active dark/light theme without JS theme
 * plumbing.
 *
 * SVG accepts `var(--chart-N)` strings for stroke/fill, and inline tooltip
 * styles resolve `rgb(var(--token))` expressions, so everything here is a
 * plain CSS string. Axis tick / legend typography is themed centrally by the
 * `.recharts-*` overrides in app/globals.css.
 *
 * Usage:
 *   <Line stroke={seriesColor(0)} ... />
 *   <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" />
 *   <XAxis {...axisProps} />
 *   <Tooltip {...chartTooltipProps} />
 */

/** Categorical series palette (6 steps, then cycles). */
export const CHART_SERIES = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
] as const;

export function seriesColor(index: number): string {
  return CHART_SERIES[((index % CHART_SERIES.length) + CHART_SERIES.length) % CHART_SERIES.length];
}

/** Gridline stroke. */
export const CHART_GRID = 'var(--chart-grid)';

/** Risk-semantic strokes/fills for threshold lines and status series. */
export const CHART_OK = 'rgb(var(--ok))';
export const CHART_WARN = 'rgb(var(--warn))';
export const CHART_CRIT = 'rgb(var(--crit))';
export const CHART_ACCENT = 'rgb(var(--accent))';

/** Muted axis line/tick stroke. */
export const CHART_AXIS = 'rgb(var(--line-strong))';

/** Shared axis props: quiet lines, 11px muted ticks (font via CSS override). */
export const axisProps = {
  tickLine: false,
  axisLine: { stroke: 'rgb(var(--line-strong))' },
  tick: { fontSize: 11 },
} as const;

/** Consistent chart margins for module dashboards. */
export const chartMargins = { top: 8, right: 12, bottom: 4, left: 4 } as const;

/** Themed tooltip: dark raised card in dark mode, white card in light. */
export const chartTooltipProps = {
  cursor: { stroke: 'rgb(var(--line-strong))', strokeWidth: 1 },
  contentStyle: {
    background: 'rgb(var(--surface-raised))',
    border: '1px solid rgb(var(--line-strong))',
    borderRadius: 6,
    boxShadow: 'var(--shadow-pop)',
    fontSize: 12,
    color: 'rgb(var(--text))',
    padding: '8px 10px',
  },
  labelStyle: {
    color: 'rgb(var(--heading))',
    fontWeight: 600,
    fontSize: 11,
    marginBottom: 4,
  },
  itemStyle: {
    color: 'rgb(var(--text))',
    fontSize: 11,
    padding: '1px 0',
  },
} as const;

/** Legend props matching the muted text tone (font via CSS override). */
export const chartLegendProps = {
  iconSize: 9,
  wrapperStyle: { fontSize: 11 },
} as const;
