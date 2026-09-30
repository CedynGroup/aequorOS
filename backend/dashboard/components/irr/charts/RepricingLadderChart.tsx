"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  LINE_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrency, fmtCurrencySigned } from "@/lib/format";

export type LadderBucket = {
  bucket: string;
  /** Rate-sensitive assets (backend figure, bank currency). */
  rsa: number;
  /** Rate-sensitive liabilities (backend figure) — plotted downward. */
  rsl: number;
  /** Period gap RSA − RSL (backend figure). */
  gap: number;
  /** Running cumulative gap (backend figure). */
  cumulative: number;
};

/**
 * Repricing ladder: per-bucket grouped bars (RSA up, RSL down, net gap) with
 * the backend's cumulative gap as a line overlay. `mini` collapses to net gap
 * + cumulative for dashboard tiles. All series are engine outputs — RSL is
 * only negated for the mirror presentation.
 */
export default function RepricingLadderChart({
  data,
  height = 320,
  mini = false,
}: {
  data: LadderBucket[];
  height?: number;
  mini?: boolean;
}) {
  const tokens = useChartTokens();
  const buckets = data.map((bucket) => bucket.bucket);

  const bars = [
    ...(mini
      ? []
      : [
          {
            name: "RSA",
            color: seriesColor(tokens, 0),
            radius: [2, 2, 0, 0] as const,
            values: data.map((bucket) => bucket.rsa),
          },
          {
            name: "RSL",
            color: seriesColor(tokens, 1),
            radius: [0, 0, 2, 2] as const,
            values: data.map((bucket) => -bucket.rsl),
          },
        ]),
    {
      name: "Net gap",
      color: seriesColor(tokens, mini ? 0 : 2),
      radius: [2, 2, 0, 0] as const,
      values: data.map((bucket) => bucket.gap),
    },
  ];

  const option: BiEChartsOption = {
    grid: {
      left: 8,
      right: 16,
      top: 8,
      bottom: mini ? 4 : 24,
      containLabel: true,
    },
    ...(mini ? {} : { legend: { bottom: 0, type: "scroll" as const } }),
    xAxis: {
      type: "category",
      data: buckets,
      axisLabel: { interval: 0 },
      axisLine: { show: true, lineStyle: { color: tokens.axis } },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        formatter: (value: number) => `${(value / 1_000_000).toFixed(0)}M`,
      },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      // RSL is drawn downward for the mirror presentation, so its tooltip quotes
      // the magnitude the engine reported rather than the negated plot value.
      formatter: axisTooltip(buckets, (value, seriesName) =>
        seriesName === "RSL"
          ? fmtCurrency(Math.abs(value))
          : seriesName === "RSA"
            ? fmtCurrency(value)
            : fmtCurrencySigned(value),
      ),
    },
    series: [
      ...bars.map((bar, index) => ({
        ...BAR_SERIES_BASE,
        name: bar.name,
        barMaxWidth: 26,
        itemStyle: { color: bar.color, borderRadius: [...bar.radius] },
        data: bar.values,
        ...(index === 0
          ? {
              markLine: thresholdMarkLine([
                { axis: "y", value: 0, color: tokens.axis, solid: true },
              ]),
            }
          : {}),
      })),
      {
        ...LINE_SERIES_BASE,
        name: "Cumulative gap",
        smooth: true,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: tokens.accent, width: 2 },
        itemStyle: { color: tokens.accent },
        data: data.map((bucket) => bucket.cumulative),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Repricing gap across ${data.length} buckets${
        mini ? "" : ", with rate-sensitive assets and liabilities mirrored"
      }, and the cumulative gap`}
    />
  );
}
