"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  gapAwareData,
  itemTooltip,
  LINE_SERIES_BASE,
} from "@/lib/echartsOptions";
import { currencyCode } from "@/lib/format";

export type ForwardPoint = {
  tenorLabel: string;
  /** Forward outright in the reporting currency per unit of the base currency. */
  outright: number;
};

/**
 * Forward outright curve by tenor. Rendering-only — points come straight
 * from ingested market data once forward tenors exist in the canonical
 * fx_rates store.
 */
export default function ForwardCurve({
  data,
  height = 280,
}: {
  data: ForwardPoint[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.tenorLabel);
  const color = seriesColor(tokens, 0);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 20, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: labels },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: (value: number) => value.toFixed(2) },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const point = data[index];
        return point === undefined
          ? []
          : [
              {
                label: `Forward outright (${currencyCode()})`,
                value: point.outright.toFixed(4),
                color,
              },
            ];
      }),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: "Forward outright",
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        data: gapAwareData(data.map((point) => point.outright)),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Forward outright curve across ${data.length} tenors, quoted in ${currencyCode()}`}
    />
  );
}
