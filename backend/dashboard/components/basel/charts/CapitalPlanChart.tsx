"use client";

/**
 * Multi-year capital ratio projection (CAR / Tier 1 / CET1) against the
 * regulatory CAR floors. Feeds from stored forecast-run projection years — the
 * chart does no projection math of its own.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
  type ThresholdLine,
} from "@/lib/echartsOptions";
import { fmtFloorPct } from "@/lib/api/values";
import { regShort } from "@/lib/format";

export type PlanPoint = {
  label: string;
  car: number;
  tier1: number;
  cet1: number;
};

export default function CapitalPlanChart({
  data,
  carMin,
  earlyWarning,
  earlyWarningLabel = "Early warning",
  height = 300,
}: {
  data: PlanPoint[];
  /**
   * The institution's CAR minimum. `null` when it did not resolve: no floor
   * line is drawn and the projection is shown unjudged, because a stand-in
   * would draw a labelled regulatory floor across a five-year plan.
   */
  carMin: number | null;
  earlyWarning?: number | null;
  earlyWarningLabel?: string;
  height?: number;
}) {
  const tokens = useChartTokens();
  const values = data.flatMap((point) => [point.car, point.tier1, point.cet1]);
  const floors = [
    ...(carMin === null ? [] : [carMin]),
    ...(earlyWarning === null || earlyWarning === undefined
      ? []
      : [earlyWarning]),
  ];
  const scale = [...values, ...floors];
  const min = scale.length > 0 ? Math.floor(Math.min(...scale) - 1.5) : 0;
  const max = scale.length > 0 ? Math.ceil(Math.max(...scale) + 1.5) : 100;
  const labels = data.map((point) => point.label);

  const thresholds: ThresholdLine[] = [
    ...(carMin === null
      ? []
      : [
          {
            axis: "y" as const,
            value: carMin,
            label: `${regShort()} min ${fmtFloorPct(carMin)}`,
            color: tokens.adverse,
            labelPosition: "insideEndBottom" as const,
          },
        ]),
    ...(earlyWarning === undefined || earlyWarning === null
      ? []
      : [
          {
            axis: "y" as const,
            value: earlyWarning,
            label: `${earlyWarningLabel} ${fmtFloorPct(earlyWarning)}`,
            color: tokens.caution,
            labelPosition: "insideEndTop" as const,
          },
        ]),
  ];

  const series = [
    {
      name: "CAR",
      values: data.map((point) => point.car),
      color: tokens.accent,
      width: 2,
      dashed: false,
      symbolSize: 6,
    },
    {
      name: "Tier 1",
      values: data.map((point) => point.tier1),
      color: seriesColor(tokens, 1),
      width: 1.5,
      dashed: false,
      symbolSize: 5,
    },
    {
      name: "CET1",
      values: data.map((point) => point.cet1),
      color: seriesColor(tokens, 2),
      width: 1.5,
      dashed: true,
      symbolSize: 5,
    },
  ];

  const option: BiEChartsOption = {
    grid: { left: 4, right: 24, top: 12, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "category", data: labels },
    yAxis: {
      type: "value",
      min,
      max,
      axisLine: { show: false },
      axisLabel: { formatter: (value: number) => `${value}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`),
    },
    series: series.map((entry, index) => ({
      ...LINE_SERIES_BASE,
      name: entry.name,
      smooth: true,
      showSymbol: true,
      symbolSize: entry.symbolSize,
      lineStyle: {
        color: entry.color,
        width: entry.width,
        ...(entry.dashed ? { type: "dashed" as const } : {}),
      },
      itemStyle: { color: entry.color },
      data: entry.values,
      ...(index === 0 && thresholds.length > 0
        ? { markLine: thresholdMarkLine(thresholds) }
        : {}),
    })),
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Projected CAR, Tier 1 and CET1 across ${data.length} plan years${
        carMin === null
          ? ", with no regulatory minimum resolved"
          : `, against a minimum of ${fmtFloorPct(carMin)}`
      }`}
    />
  );
}
