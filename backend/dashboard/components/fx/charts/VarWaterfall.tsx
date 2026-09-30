"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  itemTooltip,
  WATERFALL_PEDESTAL,
} from "@/lib/echartsOptions";
import { fmtCurrency, fmtCurrencySigned } from "@/lib/format";

export type VarWaterfallInput = {
  /** Per-currency standalone VaR steps (all >= 0). */
  standalone: { currency: string; varGhs: number }[];
  /** Diversification benefit (>= 0; plotted as a downward step). */
  diversificationBenefitGhs: number;
  /** Diversified portfolio VaR (the closing total). */
  portfolioVarGhs: number;
};

type Step = {
  label: string;
  base: number;
  span: number;
  signed: number;
  kind: "currency" | "benefit" | "total";
};

/**
 * Waterfall decomposition of the 99% 1-day VaR: standalone per-currency VaR
 * steps stack up to the undiversified sum, the diversification benefit steps
 * back down, and the diversified portfolio VaR closes the bridge. Bars only
 * position backend figures — no risk math happens here.
 */
export default function VarWaterfall({
  input,
  height = 300,
}: {
  input: VarWaterfallInput;
  height?: number;
}) {
  const tokens = useChartTokens();
  const steps: Step[] = [];
  let running = 0;
  input.standalone.forEach((entry) => {
    steps.push({
      label: entry.currency,
      base: running,
      span: entry.varGhs,
      signed: entry.varGhs,
      kind: "currency",
    });
    running += entry.varGhs;
  });
  steps.push({
    label: "Diversification",
    base: running - input.diversificationBenefitGhs,
    span: input.diversificationBenefitGhs,
    signed: -input.diversificationBenefitGhs,
    kind: "benefit",
  });
  steps.push({
    label: "Portfolio VaR",
    base: 0,
    span: input.portfolioVarGhs,
    signed: input.portfolioVarGhs,
    kind: "total",
  });

  const labels = steps.map((step) => step.label);
  const fill = (step: Step): string =>
    step.kind === "benefit"
      ? seriesColor(tokens, 2)
      : step.kind === "total"
        ? seriesColor(tokens, 1)
        : seriesColor(tokens, 0);
  const stepLabel = (step: Step): string =>
    step.kind === "benefit"
      ? "Diversification benefit"
      : step.kind === "total"
        ? "Diversified portfolio VaR"
        : `${step.label} standalone VaR`;

  const option: BiEChartsOption = {
    grid: { left: 8, right: 12, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: labels, axisLabel: { interval: 0 } },
    yAxis: {
      type: "value",
      axisLabel: {
        formatter: (value: number) => `${(value / 1_000_000).toFixed(1)}M`,
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
                label: stepLabel(step),
                value:
                  step.kind === "total"
                    ? fmtCurrency(step.signed)
                    : fmtCurrencySigned(step.signed),
                color: fill(step),
              },
            ];
      }),
    },
    series: [
      // The invisible pedestal that lifts each visible span to its own level.
      {
        ...WATERFALL_PEDESTAL,
        name: "Pedestal",
        data: steps.map((step) => step.base),
      },
      {
        ...BAR_SERIES_BASE,
        name: "Contribution",
        stack: WATERFALL_PEDESTAL.stack,
        data: steps.map((step) => ({
          value: step.span,
          itemStyle: { color: fill(step), borderRadius: [2, 2, 0, 0] },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Value-at-risk bridge from ${input.standalone.length} standalone currency positions, less the diversification benefit, to a diversified portfolio VaR of ${fmtCurrency(input.portfolioVarGhs)}`}
    />
  );
}
