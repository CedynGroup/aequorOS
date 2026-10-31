"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { useChartTokens } from "@/components/bi/echartsTheme";
import {
  BAR_SERIES_BASE,
  itemTooltip,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrencySigned, fmtPct } from "@/lib/format";

export type ExposureBarPoint = {
  currency: string;
  /** Signed net open position in the reporting currency (long +, short −). */
  netGhs: number;
  /** |NOP| as % of Tier 1 — drives the ok / warn / crit coloring. */
  absPctTier1: number;
  withinSingleLimit: boolean;
};

/**
 * Per-currency signed NOP bars. Bar direction encodes long / short; color
 * encodes the single-currency limit state (ok, approaching at >= 80% of the
 * limit, breach past it). All figures come straight from the FX payload.
 */
export default function ExposureBars({
  data,
  singleLimitPct,
  height = 300,
}: {
  data: ExposureBarPoint[];
  singleLimitPct: number;
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.currency);

  const color = (point: ExposureBarPoint): string => {
    if (!point.withinSingleLimit) return tokens.adverse;
    if (singleLimitPct > 0 && point.absPctTier1 >= singleLimitPct * 0.8) {
      return tokens.caution;
    }
    return tokens.favourable;
  };

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
        const point = data[index];
        return point === undefined
          ? []
          : [
              {
                label: `${point.currency} ${point.netGhs >= 0 ? "long" : "short"}`,
                value: `${fmtCurrencySigned(point.netGhs)} · ${fmtPct(point.absPctTier1, 2)} of Tier 1`,
                color: color(point),
                note: point.withinSingleLimit
                  ? undefined
                  : "outside the single-currency limit",
              },
            ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Net open position",
        barWidth: 16,
        data: data.map((point) => ({
          value: point.netGhs,
          itemStyle: { color: color(point), borderRadius: 2 },
        })),
        markLine: thresholdMarkLine([
          { axis: "x", value: 0, color: tokens.axis, solid: true },
        ]),
      },
    ],
  } as BiEChartsOption;

  const breaches = data.filter((point) => !point.withinSingleLimit).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Net open position across ${data.length} currencies${
        breaches > 0
          ? `, ${breaches} outside the single-currency limit`
          : ", all within the single-currency limit"
      }`}
    />
  );
}
