"use client";

/**
 * Ratio trend line for the IRR workspace. Colours are resolved from the design
 * tokens by `components/bi/echartsTheme`, so dark and light both work. The
 * threshold here is a CEILING (supervisory ΔEVE/Tier-1 limit), not a floor.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";

export type TrendPoint = {
  label: string;
  value: number;
  /** false → computed inline (not persisted) — rendered as a hollow point. */
  stored?: boolean;
};

export default function TrendChart({
  data,
  threshold,
  thresholdLabel = "Limit",
  yMin,
  yMax,
  colorIndex,
  label = "Ratio",
  height = 260,
}: {
  data: TrendPoint[];
  /** Supervisory ceiling drawn as a critical reference line. */
  threshold?: number;
  thresholdLabel?: string;
  yMin?: number;
  yMax?: number;
  /**
   * Categorical palette index; the accent token when omitted. Deliberately an
   * INDEX and not a colour string: the Recharts version took a CSS string, and
   * `var(--chart-n)` resolves to nothing on a canvas.
   */
  colorIndex?: number;
  label?: string;
  height?: number;
}) {
  const tokens = useChartTokens();
  const lineColor =
    colorIndex === undefined ? tokens.accent : seriesColor(tokens, colorIndex);
  const values = data.map((point) => point.value);
  const min =
    yMin ?? Math.floor(Math.min(...values, threshold ?? Infinity) - 2);
  const max =
    yMax ?? Math.ceil(Math.max(...values, threshold ?? -Infinity) + 2);
  const labels = data.map((point) => point.label);

  const option: BiEChartsOption = {
    grid: { left: 0, right: 24, top: 12, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { show: true, lineStyle: { color: tokens.axis } },
    },
    yAxis: {
      type: "value",
      min,
      max,
      axisLine: { show: false },
      axisLabel: { formatter: (value: number) => `${Math.round(value)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`, {
        note: (index) => (data[index]?.stored === false ? "inline" : undefined),
      }),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: label,
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color: lineColor, width: 2 },
        markLine:
          threshold === undefined
            ? undefined
            : thresholdMarkLine([
                {
                  axis: "y",
                  value: threshold,
                  label: `${thresholdLabel} ${threshold}%`,
                  color: tokens.adverse,
                  labelPosition: "insideEndTop",
                },
              ]),
        data: data.map((point) => ({
          value: point.value,
          itemStyle: {
            color: point.stored === false ? tokens.surface : lineColor,
            borderColor: lineColor,
            borderWidth: point.stored === false ? 1.5 : 0,
          },
        })),
      },
    ],
  } as BiEChartsOption;

  const inline = data.filter((point) => point.stored === false).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`${label} across ${data.length} periods${
        threshold === undefined
          ? ""
          : `, against a supervisory limit of ${threshold}%`
      }${inline > 0 ? `; ${inline} points computed inline` : ""}`}
    />
  );
}
