"use client";

/**
 * Stacked asset-composition projection (loans / securities / cash).
 * Used exclusively by the Balance Sheet Forecasting workspace.
 *
 * ECharts expresses a stacked area as stacked LINE series with `areaStyle`, which
 * keeps the gap rule: a period with no figure for one component leaves a hole
 * rather than filling the band down to zero.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import { axisTooltip, LINE_SERIES_BASE, STACK_ID } from "@/lib/echartsOptions";
import { currencyCode, fmtLocale } from "@/lib/format";

export type BalanceSheetPoint = {
  /** X-axis label (e.g. "Y0", "2027-03"). */
  month: string;
  /** Millions of the reporting currency. */
  loans: number;
  securities: number;
  cash: number;
};

export default function BalanceSheetProjectionChart({
  data,
  height = 340,
}: {
  data: BalanceSheetPoint[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.month);
  const bands = [
    {
      name: "Loans",
      color: seriesColor(tokens, 0),
      values: data.map((point) => point.loans),
    },
    {
      name: "Securities",
      color: seriesColor(tokens, 1),
      values: data.map((point) => point.securities),
    },
    {
      name: "Cash & central bank reserves",
      color: seriesColor(tokens, 2),
      values: data.map((point) => point.cash),
    },
  ];

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 30, bottom: 4, containLabel: true },
    legend: { top: 0, right: 0, type: "scroll" },
    xAxis: {
      type: "category",
      data: labels,
      boundaryGap: false,
      axisLabel: { interval: 0 },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        formatter: (value: number) => `${value.toLocaleString(fmtLocale())}M`,
      },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(
        labels,
        (value) => `${currencyCode()} ${value.toLocaleString(fmtLocale())}M`,
      ),
    },
    series: bands.map((band) => ({
      ...LINE_SERIES_BASE,
      name: band.name,
      stack: STACK_ID,
      smooth: true,
      lineStyle: { color: band.color, width: 1 },
      itemStyle: { color: band.color },
      areaStyle: { color: band.color, opacity: 0.8 },
      data: band.values,
    })),
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Projected asset composition across ${data.length} periods: loans, securities, and cash with central bank reserves`}
    />
  );
}
