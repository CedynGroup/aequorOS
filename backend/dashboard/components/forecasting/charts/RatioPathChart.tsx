"use client";

/**
 * Regulatory-ratio path with a minimum-threshold reference line, for the
 * forecasting workspace.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
  type ThresholdLine,
} from "@/lib/echartsOptions";

type RatioPoint = {
  label: string;
  value: number;
};

export default function RatioPathChart({
  data,
  threshold = 100,
  thresholdLabel,
  internalBuffer,
  colorIndex = 0,
  label = "Ratio",
  height = 240,
}: {
  data: RatioPoint[];
  threshold?: number;
  thresholdLabel?: string;
  internalBuffer?: number;
  /**
   * Categorical palette index. This replaced a `color: string` prop that pages
   * filled with `seriesColor(n)` from the old Recharts theme — i.e. the literal
   * `var(--chart-n)`, which a canvas cannot resolve and would paint as nothing.
   * Taking an index makes that mistake unreachable.
   */
  colorIndex?: number;
  label?: string;
  height?: number;
}) {
  const tokens = useChartTokens();
  const color = seriesColor(tokens, colorIndex);
  const min = Math.floor(
    Math.min(...data.map((point) => point.value), threshold) - 2,
  );
  const max = Math.ceil(Math.max(...data.map((point) => point.value)) + 2);
  const labels = data.map((point) => point.label);

  const thresholds: ThresholdLine[] = [
    {
      axis: "y",
      value: threshold,
      label: thresholdLabel ?? `Min ${threshold}%`,
      color: tokens.adverse,
      labelPosition: "insideEndBottom",
    },
    ...(internalBuffer === undefined
      ? []
      : [
          {
            axis: "y" as const,
            value: internalBuffer,
            label: `Buffer ${internalBuffer}%`,
            color: tokens.caution,
            labelPosition: "insideEndTop" as const,
          },
        ]),
  ];

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: labels },
    yAxis: {
      type: "value",
      min,
      max,
      axisLine: { show: false },
      axisLabel: { formatter: (value: number) => `${Math.round(value)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: label,
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        markLine: thresholdMarkLine(thresholds),
        data: data.map((point) => point.value),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Projected ${label} across ${data.length} periods against a minimum of ${threshold}%`}
    />
  );
}
