"use client";

/**
 * Projection waterfall — opening balance → per-component deltas → closing
 * balance, built with the invisible-pedestal stacked-bar technique. Totals
 * render in the primary series color; deltas in risk-semantic green/red.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  itemTooltip,
  thresholdMarkLine,
  WATERFALL_PEDESTAL,
} from "@/lib/echartsOptions";
import { fmtCurrency, fmtCurrencySigned } from "@/lib/format";

export type WaterfallStep =
  | { kind: "total"; label: string; value: number }
  | { kind: "delta"; label: string; value: number };

type Row = {
  label: string;
  /** Invisible spacer below the visible segment. */
  offset: number;
  /** Visible segment height (always positive). */
  segment: number;
  /** Signed value for the tooltip. */
  signed: number;
  kind: "total" | "delta";
};

function buildRows(steps: WaterfallStep[]): Row[] {
  let running = 0;
  return steps.map((step) => {
    if (step.kind === "total") {
      running = step.value;
      return {
        label: step.label,
        offset: 0,
        segment: step.value,
        signed: step.value,
        kind: "total" as const,
      };
    }
    const start = running;
    running += step.value;
    return {
      label: step.label,
      offset: Math.min(start, running),
      segment: Math.abs(step.value),
      signed: step.value,
      kind: "delta" as const,
    };
  });
}

export default function WaterfallChart({
  steps,
  height = 300,
}: {
  steps: WaterfallStep[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const rows = buildRows(steps);
  const labels = rows.map((row) => row.label);
  const fill = (row: Row) =>
    row.kind === "total"
      ? seriesColor(tokens, 0)
      : row.signed >= 0
        ? tokens.favourable
        : tokens.adverse;

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 8, bottom: 4, containLabel: true },
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
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const row = rows[index];
        return row === undefined
          ? []
          : [
              {
                label: row.kind === "total" ? "Balance" : "Change",
                value:
                  row.kind === "total"
                    ? fmtCurrency(row.signed)
                    : fmtCurrencySigned(row.signed),
                color: fill(row),
              },
            ];
      }),
    },
    series: [
      {
        ...WATERFALL_PEDESTAL,
        name: "Pedestal",
        data: rows.map((row) => row.offset),
        markLine: thresholdMarkLine([
          { axis: "y", value: 0, color: tokens.axis, solid: true },
        ]),
      },
      {
        ...BAR_SERIES_BASE,
        name: "Movement",
        stack: WATERFALL_PEDESTAL.stack,
        barMaxWidth: 48,
        data: rows.map((row) => ({
          value: row.segment,
          itemStyle: {
            color: fill(row),
            opacity: row.kind === "total" ? 0.9 : 0.8,
            borderRadius: [3, 3, 0, 0],
          },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Balance bridge across ${rows.length} steps, from the opening balance through each movement to the closing balance`}
    />
  );
}
