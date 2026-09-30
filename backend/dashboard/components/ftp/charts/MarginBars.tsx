"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  thresholdMarkLine,
  tooltipHtml,
} from "@/lib/echartsOptions";
import { fmtCurrencySigned, fmtPct } from "@/lib/format";

export type MarginBarPoint = {
  label: string;
  /** Signed value (margin %, or contribution in the bank's currency). */
  value: number;
  side: "asset" | "liability" | "mixed";
  /** Highlights the bar in the critical tone (e.g. below the margin floor). */
  flagged?: boolean;
};

const SIDE_LABEL: Record<MarginBarPoint["side"], string> = {
  asset: "Asset book",
  liability: "Funding book",
  mixed: "Mixed",
};

/**
 * Horizontal signed bars for product / business-line profitability. Asset books
 * and liability books get distinct series colors; flagged rows (below the margin
 * floor) render in the critical tone.
 *
 * The category axis is `inverse` because ECharts stacks a category axis upwards
 * from the origin, and these rows arrive ranked — the first row belongs at the
 * top, which is where Recharts put it.
 */
export default function MarginBars({
  data,
  mode,
  floorPct,
  height = 300,
}: {
  data: MarginBarPoint[];
  /** 'pct' formats as margin %, 'ghs' as a currency contribution. */
  mode: "pct" | "ghs";
  /** Optional margin-floor reference line (pct mode). */
  floorPct?: number;
  height?: number;
}) {
  const tokens = useChartTokens();
  const fmt = (value: number) =>
    mode === "pct" ? fmtPct(value, 2) : fmtCurrencySigned(value);

  const fill = (point: MarginBarPoint): string => {
    if (point.flagged) return tokens.adverse;
    if (point.side === "asset") return seriesColor(tokens, 0);
    return point.side === "liability"
      ? seriesColor(tokens, 1)
      : seriesColor(tokens, 2);
  };

  const thresholds = [
    { axis: "x" as const, value: 0, color: tokens.axis, solid: true },
    ...(mode === "pct" && floorPct !== undefined
      ? [
          {
            axis: "x" as const,
            value: floorPct,
            label: `Floor ${floorPct.toFixed(1)}%`,
            color: tokens.adverse,
            labelPosition: "end" as const,
          },
        ]
      : []),
  ];

  const option: BiEChartsOption = {
    grid: { left: 8, right: 24, top: 8, bottom: 4, containLabel: true },
    xAxis: {
      type: "value",
      axisLabel: {
        formatter: (value: number) =>
          mode === "pct" ? `${value}%` : `${(value / 1_000_000).toFixed(0)}M`,
      },
      splitLine: { show: true, lineStyle: { color: tokens.grid } },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: data.map((point) => point.label),
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const index = (params as { dataIndex: number }).dataIndex;
        const point = data[index];
        if (!point) return "";
        return tooltipHtml(point.label, [
          {
            label: SIDE_LABEL[point.side],
            value: fmt(point.value),
            color: fill(point),
            note: point.flagged ? "below floor" : undefined,
          },
        ]);
      },
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        barWidth: 14,
        markLine: thresholdMarkLine(thresholds),
        data: data.map((point) => ({
          value: point.value,
          itemStyle: { color: fill(point), borderRadius: 2 },
        })),
      },
    ],
  } as BiEChartsOption;

  const flagged = data.filter((point) => point.flagged).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`${mode === "pct" ? "Margin" : "Contribution"} by book across ${data.length} lines${
        flagged > 0 ? `, ${flagged} below the margin floor` : ""
      }`}
    />
  );
}
