"use client";

/**
 * 30-day net-outflow decomposition: one stacked bar of weighted outflows by
 * category against a second bar of capped inflows, so the net figure the LCR
 * divides by is visible at a glance. All values come straight from the
 * dashboard line items — no client-side regulatory math.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  STACK_ID,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

export type OutflowCategory = { name: string; weighted: number };

const ROWS = ["Outflows", "Inflows (capped)"] as const;

export default function NetOutflowChart({
  outflows,
  cappedInflows,
  netOutflows,
  height = 220,
}: {
  outflows: OutflowCategory[];
  cappedInflows: number | null;
  netOutflows: number | null;
  height?: number;
}) {
  const tokens = useChartTokens();

  // Two category rows: the stacked outflow categories on the first, capped
  // inflows alone on the second. A category a series does not belong to is
  // `null`, not 0 — a zero-width bar and "no such figure" look identical, and
  // only one of them is true.
  const series = [
    ...outflows.map((category, index) => ({
      ...BAR_SERIES_BASE,
      name: category.name,
      stack: STACK_ID,
      barMaxWidth: 34,
      itemStyle: { color: seriesColor(tokens, index) },
      data: [category.weighted, null],
    })),
    {
      ...BAR_SERIES_BASE,
      name: "Capped inflows",
      stack: STACK_ID,
      barMaxWidth: 34,
      itemStyle: { color: tokens.favourable },
      data: [null, cappedInflows],
      markLine:
        netOutflows === null
          ? undefined
          : thresholdMarkLine([
              {
                axis: "x",
                value: netOutflows,
                label: `Net ${fmtCurrency(netOutflows)}`,
                color: tokens.muted,
                labelPosition: "start",
              },
            ]),
    },
  ];

  const option: BiEChartsOption = {
    grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "value",
      axisLabel: {
        hideOverlap: true,
        formatter: (value: number) =>
          fmtCurrency(value, undefined, { decimals: 0 }),
      },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: [...ROWS],
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip([...ROWS], (value) => fmtCurrency(value), {
        hideAbsent: true,
      }),
    },
    series,
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Weighted outflows across ${outflows.length} categories against capped inflows, with the net outflow figure marked at ${netOutflows === null ? "Unavailable" : fmtCurrency(netOutflows)}`}
    />
  );
}
