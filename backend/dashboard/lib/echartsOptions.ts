/**
 * Shared ECharts option builders for the module dashboards.
 *
 * `lib/chartTheme.ts` was the Recharts counterpart of this file and is gone.
 * The two libraries differ in three ways that matter to a bank's numbers, and
 * each is handled here once rather than in forty chart files:
 *
 * 1. **A gap must draw as a gap.** Recharts' `connectNulls` defaulted to off but
 *    was switched on in several charts, which drew a straight segment across a
 *    period the platform never measured. ECharts leaves a gap by default, and
 *    `connectNulls: false` is stated explicitly at every series here so it
 *    cannot drift. The remaining hazard is the opposite one: a reading whose
 *    neighbours are both absent is an ISOLATED point, and a line series with no
 *    symbols draws nothing at all for it — the honest gap would have hidden the
 *    one figure that exists. `gapAwareData` gives exactly those points a symbol.
 *
 * 2. **A canvas has no CSS cascade.** Recharts rendered SVG, so it accepted the
 *    literal string `var(--chart-1)`. ECharts paints with Canvas2D, which
 *    cannot resolve `var()`. Series colours therefore come from
 *    `components/bi/echartsTheme.ts`, which resolves the tokens against the live
 *    document — see `useChartTokens` below for the hook every chart uses.
 *    The TOOLTIP is the exception: ECharts renders it as DOM, so the CSS
 *    variables in `tooltipHtml` do resolve.
 *
 * 3. **A tooltip is HTML, not React.** Recharts formatters returned React nodes,
 *    which escape their own text. An ECharts `formatter` returns an HTML string,
 *    so any label that came from tenant data — a product name, a counterparty,
 *    a bucket label — has to be escaped. `tooltipHtml` is the only place a
 *    module chart builds tooltip markup, and it escapes every part.
 *
 * Nothing here decides anything about a figure: these are shapes, colours and
 * strings. Formatting of the figures themselves stays in `lib/format.ts`, which
 * is where the active jurisdiction is bound.
 */

import type { BarSeriesOption, LineSeriesOption } from "echarts/charts";

/** A figure that may be absent. `undefined` and `null` both mean "no reading". */
export type SeriesValue = number | null | undefined;

/**
 * A point in a gap-aware series: a figure, an explicit hole, or a figure that
 * carries its own symbol because it would otherwise be invisible.
 */
export type GapAwareDatum =
  number | null | { value: number; symbol: string; symbolSize: number };

/** Is this index a reading with no reading on either side of it? */
function isIsolated(values: readonly SeriesValue[], index: number): boolean {
  const present = (at: number) =>
    at >= 0 &&
    at < values.length &&
    values[at] !== null &&
    values[at] !== undefined;
  return !present(index - 1) && !present(index + 1);
}

/**
 * Turn a list of possibly-absent figures into series data that tells the truth.
 *
 * An absent figure becomes `null`, which ECharts draws as a break in the line.
 * A present figure with no neighbour on either side gets an explicit symbol, so
 * that a series rendered with `showSymbol: false` still shows it — a lone
 * reading is the case where "draw the gap honestly" and "draw the figure at all"
 * pull in opposite directions, and the figure has to win.
 */
export function gapAwareData(
  values: readonly SeriesValue[],
  symbolSize = 5,
): GapAwareDatum[] {
  return values.map((value, index) => {
    if (value === null || value === undefined) return null;
    if (isIsolated(values, index)) {
      return { value, symbol: "circle", symbolSize };
    }
    return value;
  });
}

/** True when a series has at least one figure in it. */
export function hasAnyValue(values: readonly SeriesValue[]): boolean {
  return values.some((value) => value !== null && value !== undefined);
}

/** The series defaults every module line shares. State them, never assume them. */
export const LINE_SERIES_BASE = {
  type: "line",
  // The gap rule, stated at the series rather than relied on as a default.
  connectNulls: false,
  showSymbol: false,
  // Freshness refreshes re-render these charts; re-animating that is noise.
  animation: false,
} as const;

/** The series defaults every module bar shares. */
export const BAR_SERIES_BASE = {
  type: "bar",
  animation: false,
} as const;

/** Escape text destined for a tooltip's HTML. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** One line of a tooltip: a name, an already-formatted figure, and its swatch. */
export type TooltipRow = {
  label: string;
  /** Already formatted for display — this helper never formats a figure. */
  value: string;
  /** The resolved series colour, as ECharts hands it to a formatter. */
  color?: string;
  /** A qualifier shown after the figure (e.g. "inline", "below floor"). */
  note?: string;
};

/**
 * Tooltip markup for a module chart. Every interpolated part is escaped: the
 * labels and values come from tenant data, and an ECharts tooltip is DOM.
 *
 * Colours here are CSS variables on purpose — the tooltip is an HTML element, so
 * unlike the canvas it resolves them against the active theme.
 */
export function tooltipHtml(
  title: string | null,
  rows: readonly TooltipRow[],
): string {
  const head =
    title === null
      ? ""
      : `<div style="color:rgb(var(--heading));font-weight:600;font-size:11px;margin-bottom:4px">${escapeHtml(
          title,
        )}</div>`;
  const body = rows
    .map((row) => {
      const swatch = row.color
        ? `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${escapeHtml(
            row.color,
          )};margin-right:6px"></span>`
        : "";
      const note = row.note
        ? `<span style="color:rgb(var(--text-muted))"> · ${escapeHtml(row.note)}</span>`
        : "";
      return `<div style="font-size:11px;padding:1px 0">${swatch}${escapeHtml(
        row.label,
      )}${row.label ? ": " : ""}<b>${escapeHtml(row.value)}</b>${note}</div>`;
    })
    .join("");
  return `${head}${body}`;
}

/**
 * What ECharts hands a tooltip formatter for one series at one category.
 *
 * `value` is the series' OWN figure even inside a stack, which is what a reader
 * of a stacked band needs; and it is `null` for a category the series has no
 * reading for, which is why `absent` below exists.
 */
export type AxisTooltipParam = {
  dataIndex: number;
  seriesName: string;
  color: string;
  value: number | null;
};

/**
 * An axis-triggered tooltip that names every series at the hovered category.
 *
 * The `absent` text is the whole reason this is a helper rather than a lambda per
 * chart: ECharts renders a null as `-`, which reads as a measured nothing. A
 * category the platform has no figure for has to SAY so, and it says the same
 * thing on every chart in the product.
 */
export function axisTooltip(
  labels: readonly string[],
  format: (value: number, seriesName: string, dataIndex: number) => string,
  options?: Readonly<{
    absent?: string;
    note?: (dataIndex: number, seriesName: string) => string | undefined;
    /** Suppress a series that has no reading, rather than naming it as absent. */
    hideAbsent?: boolean;
  }>,
): (params: unknown) => string {
  const absent = options?.absent ?? "not computed";
  return (params: unknown) => {
    const points = (
      Array.isArray(params) ? params : [params]
    ) as readonly AxisTooltipParam[];
    const first = points[0];
    if (!first) return "";
    const rows = points
      .filter(
        (point) =>
          !options?.hideAbsent ||
          (point.value !== null && point.value !== undefined),
      )
      .map((point) => ({
        label: point.seriesName,
        color: point.color,
        value:
          point.value === null || point.value === undefined
            ? absent
            : format(point.value, point.seriesName, point.dataIndex),
        note: options?.note?.(point.dataIndex, point.seriesName),
      }));
    return tooltipHtml(labels[first.dataIndex] ?? null, rows);
  };
}

/** The same, for a single-series chart whose tooltip triggers per item. */
export function itemTooltip(
  labels: readonly string[],
  rows: (dataIndex: number) => readonly TooltipRow[],
): (params: unknown) => string {
  return (params: unknown) => {
    const point = params as { dataIndex: number };
    return tooltipHtml(labels[point.dataIndex] ?? null, rows(point.dataIndex));
  };
}

/** A dashed reference line across the plot, with an optional label. */
export type ThresholdLine = {
  /** The axis the line is fixed on. */
  axis: "x" | "y";
  value: number;
  label?: string;
  color: string;
  /** Where the label sits relative to the line. ECharts label positions. */
  labelPosition?:
    "start" | "middle" | "end" | "insideEndTop" | "insideEndBottom";
  /** Solid rather than dashed — for a zero baseline. */
  solid?: boolean;
};

type MarkLineOption = NonNullable<LineSeriesOption["markLine"]>;

/**
 * Reference lines as a `markLine`, which is how ECharts expresses what Recharts
 * called `<ReferenceLine>`. Attach it to the FIRST series of a chart only: a
 * markLine per series would draw the same line several times over.
 */
export function thresholdMarkLine(
  lines: readonly ThresholdLine[],
): MarkLineOption {
  return {
    silent: true,
    symbol: "none",
    animation: false,
    data: lines.map((line) => ({
      ...(line.axis === "x" ? { xAxis: line.value } : { yAxis: line.value }),
      lineStyle: {
        color: line.color,
        type: line.solid ? ("solid" as const) : ("dashed" as const),
        width: 1,
      },
      label: line.label
        ? {
            show: true,
            formatter: line.label,
            position: line.labelPosition ?? ("insideEndTop" as const),
            color: line.color,
            fontSize: 11,
          }
        : { show: false },
    })),
  };
}

/** A waterfall's invisible pedestal: the running base each bar floats on. */
export function waterfallBase(
  deltas: readonly number[],
  start = 0,
): { base: number[]; bars: number[] } {
  const base: number[] = [];
  const bars: number[] = [];
  let running = start;
  for (const delta of deltas) {
    base.push(delta >= 0 ? running : running + delta);
    bars.push(Math.abs(delta));
    running += delta;
  }
  return { base, bars };
}

/** The invisible stack segment that lifts a waterfall's visible bars. */
export const WATERFALL_PEDESTAL = {
  ...BAR_SERIES_BASE,
  stack: "waterfall",
  silent: true,
  itemStyle: { color: "transparent" },
  emphasis: { disabled: true },
  tooltip: { show: false },
} as const;

type BarStack = NonNullable<BarSeriesOption["stack"]>;

/** The name every stacked module bar shares, so stacks cannot half-apply. */
export const STACK_ID: BarStack = "total";
