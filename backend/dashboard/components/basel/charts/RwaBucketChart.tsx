"use client";

/**
 * Horizontal RWA bars per exposure class (credit book, standardized
 * approach). Each row is one stored run line item: exposure × risk weight →
 * RWA. Values are read from the run — no client-side weighting.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import { BAR_SERIES_BASE, itemTooltip } from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

export type RwaBucket = {
  name: string;
  rwa: number;
  exposure: number | null;
  weightPct: number | null;
};

export default function RwaBucketChart({
  data,
  height,
}: {
  data: RwaBucket[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const chartHeight = height ?? Math.max(180, data.length * 34 + 40);
  const labels = data.map((bucket) => bucket.name);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 24, top: 4, bottom: 4, containLabel: true },
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
      data: labels,
      axisLine: { show: false },
      splitLine: { show: false },
    },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const bucket = data[index];
        if (bucket === undefined) return [];
        // A line item with no stored risk weight simply does not quote one —
        // never a 0% weight, which would read as an unweighted exposure.
        const weight =
          bucket.weightPct === null
            ? ""
            : ` @ ${bucket.weightPct.toFixed(0)}% RW`;
        return [
          {
            label: "RWA",
            value: `${fmtCurrency(bucket.rwa)}${weight}`,
            color: seriesColor(tokens, 0),
          },
        ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "RWA",
        barMaxWidth: 22,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((bucket) => bucket.rwa),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={chartHeight}
      ariaLabel={`Risk-weighted assets across ${data.length} exposure classes`}
    />
  );
}
