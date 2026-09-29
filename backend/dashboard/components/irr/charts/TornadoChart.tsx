"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  itemTooltip,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrencySigned, fmtPct } from "@/lib/format";

export type TornadoPoint = {
  label: string;
  /** ΔEVE in the bank's currency (signed backend figure). */
  value: number;
  /** ΔEVE as % of Tier 1 (signed backend figure) for the tooltip. */
  pctTier1?: number;
  /** Backend breach flag — colors the bar critical. */
  breach?: boolean;
};

/**
 * Horizontal ΔEVE tornado: signed bars sorted by magnitude (largest loss on
 * top), colored by the backend's per-scenario breach flag. Display-only —
 * every figure is an engine output, never recomputed here.
 */
export default function TornadoChart({
  data,
  height = 300,
  sort = true,
}: {
  data: TornadoPoint[];
  height?: number;
  /**
   * Sort by |ΔEVE| descending (tornado ordering).
   *
   * `categoryWidth` is gone with Recharts: an ECharts category axis sizes its
   * label gutter from the labels themselves via `containLabel`.
   */
  sort?: boolean;
}) {
  const tokens = useChartTokens();
  const rows = sort
    ? [...data].sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    : data;
  const labels = rows.map((row) => row.label);
  const barColor = (row: TornadoPoint) =>
    row.breach ? tokens.adverse : seriesColor(tokens, 0);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 24, top: 8, bottom: 4, containLabel: true },
    xAxis: {
      type: "value",
      splitLine: { show: true, lineStyle: { color: tokens.grid } },
      axisLabel: {
        formatter: (value: number) => `${(value / 1_000_000).toFixed(0)}M`,
      },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: labels,
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const row = rows[index];
        if (row === undefined) return [];
        // A scenario with no Tier-1 percentage simply does not quote one.
        const suffix =
          row.pctTier1 === undefined
            ? ""
            : ` (${fmtPct(row.pctTier1, 2)} of Tier 1)`;
        return [
          {
            label: "ΔEVE",
            value: `${fmtCurrencySigned(row.value)}${suffix}`,
            color: barColor(row),
            note: row.breach ? "breaches the limit" : undefined,
          },
        ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "ΔEVE",
        barMaxWidth: 22,
        data: rows.map((row) => ({
          value: row.value,
          itemStyle: { color: barColor(row), borderRadius: [0, 2, 2, 0] },
        })),
        markLine: thresholdMarkLine([
          { axis: "x", value: 0, color: tokens.axis, solid: true },
        ]),
      },
    ],
  } as BiEChartsOption;

  const breaches = rows.filter((row) => row.breach).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Change in economic value of equity across ${rows.length} rate scenarios${
        breaches > 0 ? `, ${breaches} breaching the supervisory limit` : ""
      }`}
    />
  );
}
