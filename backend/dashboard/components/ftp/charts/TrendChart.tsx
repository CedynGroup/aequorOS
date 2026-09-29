"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  LINE_SERIES_BASE,
  thresholdMarkLine,
  tooltipHtml,
} from "@/lib/echartsOptions";

export type TrendChartPoint = {
  label: string;
  value: number;
  /** Persisted-run point (solid) vs inline computation (hollow). */
  stored: boolean;
};

/**
 * Period-over-period trend line for FTP metrics with an optional floor line.
 * Hollow markers flag points computed live (no stored results yet).
 *
 * The hollow/solid distinction is a per-datum `itemStyle` rather than the custom
 * dot renderer Recharts needed — a canvas has no element per point to render.
 */
export default function TrendChart({
  data,
  threshold,
  thresholdLabel,
  valueLabel,
  format,
  height = 260,
  colorIndex = 0,
  yDomain,
}: {
  data: TrendChartPoint[];
  threshold?: number;
  thresholdLabel?: string;
  valueLabel: string;
  format: (value: number) => string;
  height?: number;
  colorIndex?: number;
  yDomain?: [number, number];
}) {
  const tokens = useChartTokens();
  const color = seriesColor(tokens, colorIndex);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 20, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: data.map((point) => point.label) },
    yAxis: {
      type: "value",
      ...(yDomain ? { min: yDomain[0], max: yDomain[1] } : { scale: true }),
      axisLabel: { formatter: (value: number) => format(value) },
    },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const first = (params as ReadonlyArray<{ dataIndex: number }>)[0];
        if (!first) return "";
        const point = data[first.dataIndex];
        if (!point) return "";
        return tooltipHtml(point.label, [
          {
            label: valueLabel,
            value: format(point.value),
            color,
            note: point.stored ? undefined : "inline",
          },
        ]);
      },
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        smooth: true,
        showSymbol: true,
        symbolSize: 7,
        lineStyle: { color, width: 2 },
        markLine:
          threshold === undefined
            ? undefined
            : thresholdMarkLine([
                {
                  axis: "y",
                  value: threshold,
                  label: thresholdLabel,
                  color: tokens.adverse,
                  labelPosition: "insideEndBottom",
                },
              ]),
        data: data.map((point) => ({
          value: point.value,
          itemStyle: {
            color: point.stored ? color : tokens.surface,
            borderColor: color,
            borderWidth: 1.5,
          },
        })),
      },
    ],
  } as BiEChartsOption;

  const inline = data.filter((point) => !point.stored).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`${valueLabel} across ${data.length} periods${
        inline > 0 ? `, ${inline} computed inline` : ""
      }`}
    />
  );
}
