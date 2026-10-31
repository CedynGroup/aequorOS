"use client";

/**
 * Cumulative net position per ladder horizon for one currency: contractual
 * (assets − contractual liabilities, cumulated) vs the behaviourally-stressed
 * cumulative from the LRMD ¶50–54 run-off schedule. Categorical series
 * colors only — the blobs carry no statuses, so the chart asserts none.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrency, fmtCurrencySigned } from "@/lib/format";

export type LadderPoint = {
  horizon: string;
  contractual: number | null;
  stressed: number | null;
};

export default function StressedLadderChart({
  data,
  showStressed,
  height = 260,
}: {
  data: LadderPoint[];
  /** Hide the stressed series entirely when the run carries no ladder. */
  showStressed: boolean;
  height?: number;
}) {
  const tokens = useChartTokens();
  const horizons = data.map((point) => point.horizon);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 8, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "category", data: horizons },
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
      axisPointer: { type: "shadow" },
      // A horizon the run produced no figure for says so: an absent cumulative
      // position is not a balanced one.
      formatter: axisTooltip(horizons, (value) => fmtCurrencySigned(value), {
        absent: "no ladder figure",
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Contractual cumulative net",
        barMaxWidth: 40,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [2, 2, 0, 0],
        },
        data: data.map((point) => point.contractual),
        markLine: thresholdMarkLine([
          { axis: "y", value: 0, color: tokens.axis, solid: true },
        ]),
      },
      ...(showStressed
        ? [
            {
              ...BAR_SERIES_BASE,
              name: "Behaviourally-stressed cumulative net",
              barMaxWidth: 40,
              itemStyle: {
                color: seriesColor(tokens, 1),
                borderRadius: [2, 2, 0, 0],
              },
              data: data.map((point) => point.stressed),
            },
          ]
        : []),
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Cumulative net position across ${data.length} ladder horizons, contractual${
        showStressed
          ? " and behaviourally stressed"
          : " only — this run carries no stressed ladder"
      }`}
    />
  );
}
