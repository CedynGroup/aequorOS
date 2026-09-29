"use client";

/**
 * Earnings bridge over the forecast horizon — stacked income bars
 * (NII + fees) with opex and credit losses as negative bars and net income
 * as a line. Every series is a persisted field on the projection path.
 *
 * Recharts needed `stackOffset="sign"` to stack the positive and negative bars
 * in opposite directions from the axis; ECharts does that by default for a
 * signed stack, so there is nothing to configure.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  LINE_SERIES_BASE,
  STACK_ID,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrency, fmtCurrencySigned } from "@/lib/format";

export type EarningsPoint = {
  label: string;
  nii: number;
  fees: number;
  /** Negative values (costs drawn below the axis). */
  opex: number;
  creditLosses: number;
  netIncome: number;
};

export default function EarningsChart({
  data,
  height = 320,
}: {
  data: EarningsPoint[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.label);

  const bars = [
    {
      name: "Net interest income",
      color: seriesColor(tokens, 0),
      opacity: 1,
      values: data.map((point) => point.nii),
    },
    {
      name: "Fee income",
      color: seriesColor(tokens, 2),
      opacity: 1,
      values: data.map((point) => point.fees),
    },
    {
      name: "Operating expenses",
      color: tokens.caution,
      opacity: 0.75,
      values: data.map((point) => point.opex),
    },
    {
      name: "Credit losses",
      color: tokens.adverse,
      opacity: 0.75,
      values: data.map((point) => point.creditLosses),
    },
  ];

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 30, bottom: 4, containLabel: true },
    legend: { top: 0, right: 0, type: "scroll" },
    xAxis: { type: "category", data: labels, axisLabel: { interval: 0 } },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisLabel: {
        hideOverlap: true,
        formatter: (value: number) =>
          fmtCurrency(value, undefined, { decimals: 1 }),
      },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip(labels, (value) => fmtCurrencySigned(value)),
    },
    series: [
      ...bars.map((bar, index) => ({
        ...BAR_SERIES_BASE,
        name: bar.name,
        stack: STACK_ID,
        barMaxWidth: 42,
        itemStyle: { color: bar.color, opacity: bar.opacity },
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
        name: "Net income",
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color: tokens.favourable, width: 2.25 },
        itemStyle: { color: tokens.favourable },
        data: data.map((point) => point.netIncome),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Earnings bridge across ${data.length} forecast periods: net interest income and fees against operating expenses and credit losses, with net income traced`}
    />
  );
}
