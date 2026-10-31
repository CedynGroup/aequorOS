"use client";

/**
 * Stressed-ratio deltas vs the baseline run, per scenario. Negative bars
 * (ratio deterioration) are the expected shape under stress; any positive
 * bar means the shock helped the ratio.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";

export type ScenarioDelta = {
  scenario: string;
  lcrDelta: number | null;
  nsfrDelta: number | null;
};

const signedPts = (value: number) =>
  `${value >= 0 ? "+" : ""}${value.toFixed(2)} pts`;

export default function StressDeltaChart({
  data,
  height = 240,
}: {
  data: ScenarioDelta[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const scenarios = data.map((row) => row.scenario);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 8, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "category", data: scenarios },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        formatter: (value: number) => `${value > 0 ? "+" : ""}${value} pts`,
      },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      // A scenario whose stressed ratio did not resolve has no delta. Rendering
      // that as 0.00 pts would report a shock that changed nothing.
      formatter: axisTooltip(scenarios, signedPts, { absent: "not computed" }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "ΔLCR vs baseline",
        barMaxWidth: 36,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [2, 2, 0, 0],
        },
        data: data.map((row) => row.lcrDelta),
        markLine: thresholdMarkLine([
          { axis: "y", value: 0, color: tokens.axis, solid: true },
        ]),
      },
      {
        ...BAR_SERIES_BASE,
        name: "ΔNSFR vs baseline",
        barMaxWidth: 36,
        itemStyle: {
          color: seriesColor(tokens, 1),
          borderRadius: [2, 2, 0, 0],
        },
        data: data.map((row) => row.nsfrDelta),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Change in LCR and NSFR against the baseline run across ${data.length} stress scenarios, in percentage points`}
    />
  );
}
