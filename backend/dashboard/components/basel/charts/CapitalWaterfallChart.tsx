"use client";

/**
 * Capital-stack waterfall: CET1 components → regulatory deductions → AT1 →
 * Tier 2 → total capital. Rendered as floating bars (a transparent pedestal
 * stacked under the visible segment) so each tier's contribution to the total is
 * legible. All step values come from the stored capital structure — display only.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  itemTooltip,
  thresholdMarkLine,
  WATERFALL_PEDESTAL,
} from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

type Step = {
  name: string;
  base: number;
  delta: number;
  signed: number;
  color: string;
};

export default function CapitalWaterfallChart({
  cet1Gross,
  deductions,
  at1,
  tier2,
  total,
  height = 280,
}: {
  /** CET1 components before deductions. */
  cet1Gross: number;
  /** Regulatory deductions (positive magnitude). */
  deductions: number;
  at1: number;
  tier2: number;
  total: number;
  height?: number;
}) {
  const tokens = useChartTokens();
  const cet1Net = cet1Gross - deductions;
  const tier1 = cet1Net + at1;
  // Categorical palette throughout — the steps are capital-stack categories,
  // not compliance states, so status colors stay out of this chart.
  const steps: Step[] = [
    {
      name: "CET1 components",
      base: 0,
      delta: cet1Gross,
      signed: cet1Gross,
      color: seriesColor(tokens, 0),
    },
    {
      name: "Deductions",
      base: cet1Net,
      delta: deductions,
      signed: -deductions,
      color: seriesColor(tokens, 3),
    },
    {
      name: "AT1",
      base: cet1Net,
      delta: at1,
      signed: at1,
      color: seriesColor(tokens, 1),
    },
    {
      name: "Tier 2",
      base: tier1,
      delta: tier2,
      signed: tier2,
      color: seriesColor(tokens, 2),
    },
    {
      name: "Total capital",
      base: 0,
      delta: total,
      signed: total,
      color: seriesColor(tokens, 4),
    },
  ];
  const labels = steps.map((step) => step.name);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: labels, axisLabel: { interval: 0 } },
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
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const step = steps[index];
        return step === undefined
          ? []
          : [
              {
                label: "Contribution",
                value: `${step.signed < 0 ? "−" : ""}${fmtCurrency(Math.abs(step.signed))}`,
                color: step.color,
              },
            ];
      }),
    },
    series: [
      {
        ...WATERFALL_PEDESTAL,
        name: "Pedestal",
        data: steps.map((step) => step.base),
        markLine: thresholdMarkLine([
          { axis: "y", value: 0, color: tokens.axis, solid: true },
        ]),
      },
      {
        ...BAR_SERIES_BASE,
        name: "Contribution",
        stack: WATERFALL_PEDESTAL.stack,
        barMaxWidth: 56,
        data: steps.map((step) => ({
          value: step.delta,
          itemStyle: { color: step.color, borderRadius: [2, 2, 0, 0] },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Capital stack from CET1 components through deductions, AT1 and Tier 2 to total capital of ${fmtCurrency(total)}`}
    />
  );
}
