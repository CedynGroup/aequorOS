"use client";

/**
 * Net-cash-flow history and forecast, and the cumulative view beside it.
 *
 * The confidence band changed construction. Recharts painted the upper area and
 * then MASKED it below the lower edge with an opaque surface-coloured fill — a
 * trick that only works because SVG paints in document order over an opaque card.
 * ECharts stacks two line series instead: an invisible floor at `lower`, and the
 * band's own thickness (`upper − lower`) filled on top. That is honest about the
 * geometry and needs no masking, and a day with no interval leaves no band rather
 * than a zero-height one at the base line.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  gapAwareData,
  LINE_SERIES_BASE,
  thresholdMarkLine,
  type SeriesValue,
  type ThresholdLine,
} from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

export type HistoryPoint = {
  /** Day offset relative to the as-of date (≤ 0). */
  day: number;
  netFlow: number;
};

export type ForecastPoint = {
  /** Day offset after the as-of date (≥ 1). */
  day: number;
  netFlow: number;
  lower: number;
  upper: number;
};

export type CumulativeForecastPoint = {
  day: number;
  central: number;
  lower: number;
  upper: number;
};

const BAND_FLOOR = "Interval floor";
const BAND_SPAN = "95% interval";

const dayLabel = (day: number) => (day > 0 ? `D+${day}` : `D${day}`);
const dayTitle = (day: number) => (day > 0 ? `Day +${day}` : `Day ${day}`);

/** The two stacked series that draw a confidence band. */
function bandSeries(
  lower: readonly SeriesValue[],
  upper: readonly SeriesValue[],
  color: string,
) {
  const span = upper.map((high, index) => {
    const low = lower[index];
    return high === null ||
      high === undefined ||
      low === null ||
      low === undefined
      ? null
      : high - low;
  });
  return [
    {
      ...LINE_SERIES_BASE,
      name: BAND_FLOOR,
      stack: "band",
      smooth: true,
      silent: true,
      lineStyle: { opacity: 0 },
      itemStyle: { opacity: 0 },
      tooltip: { show: false },
      data: gapAwareData(lower),
    },
    {
      ...LINE_SERIES_BASE,
      name: BAND_SPAN,
      stack: "band",
      smooth: true,
      lineStyle: { opacity: 0 },
      itemStyle: { color },
      areaStyle: { color, opacity: 0.08 },
      tooltip: { show: false },
      data: gapAwareData(span),
    },
  ];
}

export default function CashFlowForecastChart({
  history,
  forecast,
  showBand = true,
  forecastLabel = "LSTM forecast",
}: {
  history: HistoryPoint[];
  forecast: ForecastPoint[];
  showBand?: boolean;
  forecastLabel?: string;
}) {
  const tokens = useChartTokens();
  const days = [
    ...history.map((point) => point.day),
    ...forecast.map((point) => point.day),
  ];
  const labels = days.map(dayLabel);
  const titles = days.map(dayTitle);
  const historyLength = history.length;

  // Actuals occupy the history slots, the forecast the rest; each series is null
  // where it does not apply, so neither is drawn across the other's span.
  const actual: SeriesValue[] = days.map((_, index) =>
    index < historyLength ? history[index].netFlow : null,
  );
  const forecastValues: SeriesValue[] = days.map((_, index) =>
    index < historyLength ? null : forecast[index - historyLength].netFlow,
  );
  const lower: SeriesValue[] = days.map((_, index) =>
    index < historyLength ? null : forecast[index - historyLength].lower,
  );
  const upper: SeriesValue[] = days.map((_, index) =>
    index < historyLength ? null : forecast[index - historyLength].upper,
  );

  const horizon = forecast.length || 30;
  const asOfIndex = days.findIndex((day) => day === 0);
  const thresholds: ThresholdLine[] = [
    { axis: "y", value: 0, color: tokens.axis, solid: true },
    ...(asOfIndex < 0
      ? []
      : [
          {
            axis: "x" as const,
            value: asOfIndex,
            label: "As of",
            color: tokens.axis,
            labelPosition: "start" as const,
          },
        ]),
  ];

  const option: BiEChartsOption = {
    grid: { left: 0, right: 24, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        hideOverlap: true,
        formatter: (value: number) =>
          fmtCurrency(value, undefined, { decimals: 0 }),
      },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(titles, (value) => fmtCurrency(value), {
        hideAbsent: true,
      }),
    },
    series: [
      ...(showBand ? bandSeries(lower, upper, tokens.accent) : []),
      {
        ...LINE_SERIES_BASE,
        name: "Actual net flow",
        smooth: true,
        lineStyle: { color: tokens.muted, width: 1.5 },
        itemStyle: { color: tokens.muted },
        markLine: thresholdMarkLine(thresholds),
        data: gapAwareData(actual),
      },
      {
        ...LINE_SERIES_BASE,
        name: `${forecastLabel} (${horizon}d)`,
        smooth: true,
        lineStyle: { color: tokens.accent, width: 2 },
        itemStyle: { color: tokens.accent },
        data: gapAwareData(forecastValues),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={340}
      ariaLabel={`Daily net cash flow: ${history.length} observed days and a ${horizon}-day forecast${
        showBand ? " with its 95% interval shaded" : ""
      }`}
    />
  );
}

export function CumulativeCashFlowChart({
  data,
  showBand = true,
}: {
  data: CumulativeForecastPoint[];
  showBand?: boolean;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => `D+${point.day}`);
  const titles = data.map((point) => `Day +${point.day}`);

  const option: BiEChartsOption = {
    grid: { left: 0, right: 24, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        hideOverlap: true,
        formatter: (value: number) =>
          fmtCurrency(value, undefined, { decimals: 0 }),
      },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(titles, (value) => fmtCurrency(value), {
        hideAbsent: true,
      }),
    },
    series: [
      ...(showBand
        ? bandSeries(
            data.map((point) => point.lower),
            data.map((point) => point.upper),
            tokens.accent,
          )
        : []),
      {
        ...LINE_SERIES_BASE,
        name: "Cumulative central",
        smooth: true,
        lineStyle: { color: tokens.accent, width: 2 },
        itemStyle: { color: tokens.accent },
        markLine: thresholdMarkLine([
          { axis: "y", value: 0, color: tokens.axis, solid: true },
        ]),
        data: gapAwareData(data.map((point) => point.central)),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={280}
      ariaLabel={`Cumulative forecast net cash flow over ${data.length} days${
        showBand ? " with its 95% interval shaded" : ""
      }`}
    />
  );
}
