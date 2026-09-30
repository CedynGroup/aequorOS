"use client";

/**
 * Generic multi-series line comparison over the forecast horizon — powers
 * run-vs-run comparisons, base-vs-shocked what-if overlays, and the NII
 * scenario chart.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  gapAwareData,
  LINE_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";

export type ScenarioSeries = {
  /** Data key on each point. */
  key: string;
  name: string;
  /**
   * Categorical palette index. There is deliberately no `color` string escape
   * hatch: the palette has to be RESOLVED for a canvas, and a `var(--chart-n)`
   * string passed in from a page would simply paint nothing.
   */
  colorIndex?: number;
  dashed?: boolean;
};

export type ScenarioPoint = { label: string } & Record<
  string,
  string | number | null
>;

export default function ScenarioLinesChart({
  data,
  series,
  valueFormatter,
  tickFormatter,
  threshold,
  thresholdLabel,
  height = 280,
  yDomain,
}: {
  data: ScenarioPoint[];
  series: ScenarioSeries[];
  valueFormatter: (value: number) => string;
  tickFormatter?: (value: number) => string;
  threshold?: number;
  thresholdLabel?: string;
  height?: number;
  /**
   * Explicit value-axis bounds. Recharts' `'auto' | 'dataMin' | 'dataMax'`
   * sentinels are gone — omit the bound to let ECharts fit the data (`scale`),
   * or give it a number.
   */
  yDomain?: [number | undefined, number | undefined];
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.label);
  const numeric = (point: ScenarioPoint, key: string) => {
    const value = point[key];
    return typeof value === "number" ? value : null;
  };

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 30, bottom: 4, containLabel: true },
    legend: { top: 0, right: 0, type: "scroll" },
    xAxis: { type: "category", data: labels, axisLabel: { interval: 0 } },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      ...(yDomain?.[0] === undefined && yDomain?.[1] === undefined
        ? { scale: true }
        : {}),
      ...(yDomain?.[0] === undefined ? {} : { min: yDomain[0] }),
      ...(yDomain?.[1] === undefined ? {} : { max: yDomain[1] }),
      axisLabel: {
        formatter: (value: number) => (tickFormatter ?? valueFormatter)(value),
      },
    },
    tooltip: {
      trigger: "axis",
      // A scenario with no figure for a year says so rather than reading as nil.
      formatter: axisTooltip(labels, (value) => valueFormatter(value), {
        absent: "not projected",
      }),
    },
    series: series.map((entry, index) => {
      const color = seriesColor(tokens, entry.colorIndex ?? index);
      return {
        ...LINE_SERIES_BASE,
        name: entry.name,
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: {
          color,
          width: 2,
          ...(entry.dashed ? { type: "dashed" as const } : {}),
        },
        itemStyle: { color },
        // A year a scenario did not project stays a gap. The Recharts version
        // carried `connectNulls`, which bridged it with a drawn segment.
        data: gapAwareData(data.map((point) => numeric(point, entry.key))),
        ...(index === 0 && threshold !== undefined
          ? {
              markLine: thresholdMarkLine([
                {
                  axis: "y",
                  value: threshold,
                  label: thresholdLabel ?? `Min ${threshold}`,
                  color: tokens.adverse,
                  labelPosition: "insideEndBottom",
                },
              ]),
            }
          : {}),
      };
    }),
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`${series.map((entry) => entry.name).join(", ")} across ${data.length} forecast periods`}
    />
  );
}
