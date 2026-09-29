/**
 * The categorical chart palette as CSS custom-property references, for the
 * places that are NOT a canvas.
 *
 * There are two ways to get a series colour in this app, and mixing them up is a
 * silent bug rather than a loud one:
 *
 * - **This file**, for anything the browser styles: `components/ui/Sparkline`'s
 *   SVG, and the legend swatches the module pages render as `<span>` elements.
 *   The values are literal `var(--chart-N)` strings, so the browser resolves them
 *   against the active theme with no JavaScript at all.
 * - **`seriesColor(useChartTokens(), n)`** in `components/bi/echartsTheme.ts`, for
 *   an ECharts canvas. Canvas2D has no CSS cascade: handing it `var(--chart-1)`
 *   paints NOTHING, and it does so without an error.
 *
 * `lib/chartTheme.ts` used to export both halves under one name, which is exactly
 * how a `var()` string reached a chart during the Recharts-to-ECharts migration.
 * The names here say which medium they are for; the chart components take a
 * palette INDEX rather than a colour string so the wrong one cannot be passed in.
 */

/** Categorical series palette (6 steps, then cycles). */
export const CSS_CHART_SERIES = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

/** The categorical colour at `index`, cycling, as a CSS `var()` reference. */
export function cssSeriesColor(index: number): string {
  const size = CSS_CHART_SERIES.length;
  return CSS_CHART_SERIES[((index % size) + size) % size];
}
