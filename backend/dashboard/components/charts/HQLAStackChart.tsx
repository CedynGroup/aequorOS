"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import { BAR_SERIES_BASE, itemTooltip } from "@/lib/echartsOptions";
import { currencyCode } from "@/lib/format";

type Item = {
  level: string;
  shareGHS: number;
  pct: number;
  /**
   * Categorical palette index — see `DonutSlice.colorIndex` for why this is an
   * index and not a CSS colour string.
   */
  colorIndex: number;
};

export default function HQLAStackChart({
  data,
  height = 220,
}: {
  data: Item[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((item) => item.level);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: {
      type: "value",
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
        const item = data[index];
        return item === undefined
          ? []
          : [
              {
                label: "Amount",
                value: `${currencyCode()} ${(item.shareGHS / 1_000_000).toFixed(1)}M`,
                color: seriesColor(tokens, item.colorIndex),
              },
            ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Weighted amount",
        barMaxWidth: 32,
        data: data.map((item) => ({
          value: item.shareGHS,
          itemStyle: {
            color: seriesColor(tokens, item.colorIndex),
            borderRadius: [0, 2, 2, 0],
          },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Weighted high-quality liquid assets across ${data.length} instruments`}
    />
  );
}
