"use client";

import type { FtpCurvePointRead } from "@aequoros/risk-service-api";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  gapAwareData,
  LINE_SERIES_BASE,
  tooltipHtml,
} from "@/lib/echartsOptions";
import { num } from "@/lib/api/values";
import { fmtPct } from "@/lib/format";

type StackPoint = {
  tenorLabel: string;
  baseYieldPct: number;
  liquidityPremiumPct: number;
  fundingSpreadPct: number;
  ftpRatePct: number;
};

/**
 * The transfer curve as its published composition: base market yield with the
 * liquidity-premium and funding-spread bands stacked on top (bps converted to
 * percentage points for display only), and the resulting FTP rate traced as a
 * line along the top of the stack.
 *
 * The three bands are stacked LINE series with `areaStyle`, which is how ECharts
 * expresses a stacked area. They stay line series so the gap rule keeps holding:
 * an area that bridged an unpriced tenor would fill the gap as well as span it.
 */
export default function TransferCurveChart({
  curve,
  height = 300,
}: {
  curve: FtpCurvePointRead[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const data: StackPoint[] = curve.map((point) => ({
    tenorLabel: point.tenorLabel,
    baseYieldPct: num(point.baseYieldPct),
    liquidityPremiumPct: num(point.liquidityPremiumBps) / 100,
    fundingSpreadPct: num(point.fundingSpreadBps) / 100,
    ftpRatePct: num(point.ftpRatePct),
  }));

  const minBase = Math.min(...data.map((point) => point.baseYieldPct));
  const maxFtp = Math.max(...data.map((point) => point.ftpRatePct));

  const bands = [
    {
      name: "Base market yield",
      values: data.map((point) => point.baseYieldPct),
      color: seriesColor(tokens, 0),
      opacity: 0.25,
      /** Basis points rather than percentage points, in the tooltip. */
      bps: false,
    },
    {
      name: "Liquidity premium",
      values: data.map((point) => point.liquidityPremiumPct),
      color: seriesColor(tokens, 1),
      opacity: 0.35,
      bps: true,
    },
    {
      name: "Funding spread",
      values: data.map((point) => point.fundingSpreadPct),
      color: seriesColor(tokens, 2),
      opacity: 0.35,
      bps: true,
    },
  ];
  const ftpColor = seriesColor(tokens, 3);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 20, top: 8, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "category",
      data: data.map((point) => point.tenorLabel),
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      min: Math.floor(minBase - 1),
      max: Math.ceil(maxFtp + 1),
      axisLabel: { formatter: (value: number) => `${value}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const points = params as ReadonlyArray<{
          dataIndex: number;
          seriesName: string;
          color: string;
          value: number | null;
        }>;
        const first = points[0];
        if (!first) return "";
        const point = data[first.dataIndex];
        return tooltipHtml(
          point ? point.tenorLabel : null,
          points.map((entry) => {
            const band = bands.find((item) => item.name === entry.seriesName);
            const value = entry.value;
            return {
              label: entry.seriesName,
              color: entry.color,
              value:
                value === null || value === undefined
                  ? "not priced"
                  : band?.bps
                    ? `${(value * 100).toFixed(0)} bp`
                    : fmtPct(value, 2),
            };
          }),
        );
      },
    },
    series: [
      ...bands.map((band) => ({
        ...LINE_SERIES_BASE,
        name: band.name,
        stack: "curve",
        smooth: true,
        data: gapAwareData(band.values),
        lineStyle: { color: band.color, width: 1.5 },
        itemStyle: { color: band.color },
        areaStyle: { color: band.color, opacity: band.opacity },
      })),
      {
        ...LINE_SERIES_BASE,
        name: "FTP rate",
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        data: gapAwareData(data.map((point) => point.ftpRatePct)),
        lineStyle: { color: ftpColor, width: 2 },
        itemStyle: { color: ftpColor },
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Transfer curve across ${data.length} tenors: base market yield with liquidity premium and funding spread stacked, and the resulting FTP rate`}
    />
  );
}
