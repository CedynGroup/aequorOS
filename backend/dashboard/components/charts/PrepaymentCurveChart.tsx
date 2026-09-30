"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { useChartTokens } from "@/components/bi/echartsTheme";
import {
  itemTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";

export type IncentivePoint = { incentiveBps: number; cpr: number };

/** Partial-dependence curve: modelled annual CPR vs rate incentive (note − refi). */
export default function PrepaymentCurveChart({
  curve,
  height = 260,
}: {
  curve: IncentivePoint[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const signed = (value: number) => `${value > 0 ? "+" : ""}${value}`;
  const labels = curve.map((point) => signed(point.incentiveBps));
  // The zero-incentive marker only exists if the curve actually has a point at
  // zero; a category axis indexes positions, and index −1 is no position at all.
  const zeroIndex = curve.findIndex((point) => point.incentiveBps === 0);

  const option: BiEChartsOption = {
    grid: { left: 0, right: 16, top: 8, bottom: 20, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      name: "Rate incentive (bps)",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: tokens.muted, fontSize: 11 },
      axisLine: { show: true, lineStyle: { color: tokens.axis } },
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(
        curve.map((point) => `Incentive ${signed(point.incentiveBps)} bps`),
        (index) => {
          const point = curve[index];
          return point === undefined
            ? []
            : [
                {
                  label: "Annual CPR",
                  value: `${(point.cpr * 100).toFixed(1)}%`,
                  color: tokens.accent,
                },
              ];
        },
      ),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: "Annual CPR",
        smooth: true,
        lineStyle: { color: tokens.accent, width: 2 },
        itemStyle: { color: tokens.accent },
        data: curve.map((point) => point.cpr * 100),
        ...(zeroIndex < 0
          ? {}
          : {
              markLine: thresholdMarkLine([
                { axis: "x", value: zeroIndex, color: tokens.axis },
              ]),
            }),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Modelled annual prepayment rate across ${curve.length} rate-incentive levels`}
    />
  );
}
