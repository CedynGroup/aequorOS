"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { useChartTokens } from "@/components/bi/echartsTheme";
import { thresholdMarkLine, tooltipHtml } from "@/lib/echartsOptions";
import { fmtCurrencySigned, fmtPct } from "@/lib/format";

export type HedgePoint = {
  hedgeId: string;
  pair: string;
  instrument: string;
  r2Pct: number;
  offsetPct: number;
  mtmGhs: number;
  effective: boolean;
};

/**
 * IFRS 9 dual-test effectiveness scatter: dollar-offset ratio on X,
 * prospective R² on Y, with the pass region (offset within the band AND R²
 * above the floor) shaded. Band edges come from the stored run's parameter
 * snapshot when available.
 *
 * The pass region was a Recharts `<ReferenceArea>`; it is now a `markArea`, which
 * is why `MarkAreaComponent` is registered in `components/bi/EChartCanvas.tsx`.
 */
export default function HedgeScatter({
  data,
  r2MinPct,
  offsetLowPct,
  offsetHighPct,
  height = 300,
}: {
  data: HedgePoint[];
  r2MinPct: number;
  offsetLowPct: number;
  offsetHighPct: number;
  height?: number;
}) {
  const tokens = useChartTokens();
  const xValues = data.map((point) => point.offsetPct);
  const yValues = data.map((point) => point.r2Pct);
  const xMin = Math.min(offsetLowPct - 15, ...xValues, 60);
  const xMax = Math.max(offsetHighPct + 15, ...xValues, 140);
  const yMin = Math.max(0, Math.min(r2MinPct - 25, ...yValues));
  const yMax = 100;

  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 8, bottom: 26, containLabel: true },
    xAxis: {
      type: "value",
      name: "Dollar-offset ratio",
      nameLocation: "middle",
      nameGap: 26,
      nameTextStyle: { color: tokens.muted, fontSize: 11 },
      min: Math.floor(xMin),
      max: Math.ceil(xMax),
      splitLine: { show: true, lineStyle: { color: tokens.grid } },
      axisLabel: { formatter: (value: number) => `${value}%` },
    },
    yAxis: {
      type: "value",
      min: Math.floor(yMin),
      max: yMax,
      axisLabel: { formatter: (value: number) => `${value}%` },
    },
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const index = (params as { dataIndex: number }).dataIndex;
        const point = data[index];
        if (!point) return "";
        return tooltipHtml(`${point.hedgeId} · ${point.pair}`, [
          { label: "", value: point.instrument },
          {
            label: "R²",
            value: `${fmtPct(point.r2Pct, 1)} · offset ${fmtPct(point.offsetPct, 1)}`,
            color: point.effective ? tokens.favourable : tokens.adverse,
          },
          {
            label: "MTM",
            value: fmtCurrencySigned(point.mtmGhs),
            note: point.effective ? "effective" : "ineffective",
          },
        ]);
      },
    },
    series: [
      {
        type: "scatter",
        name: "Hedge effectiveness",
        animation: false,
        symbolSize: 11,
        data: data.map((point) => ({
          value: [point.offsetPct, point.r2Pct],
          itemStyle: {
            color: point.effective ? tokens.favourable : tokens.adverse,
            opacity: 0.85,
          },
        })),
        // The IFRS 9 pass region: offset within the band AND R² above the floor.
        markArea: {
          silent: true,
          animation: false,
          itemStyle: {
            color: tokens.favourable,
            opacity: 0.08,
            borderColor: tokens.favourable,
            borderWidth: 1,
            borderType: "dashed",
          },
          data: [
            [
              { xAxis: offsetLowPct, yAxis: r2MinPct },
              { xAxis: offsetHighPct, yAxis: yMax },
            ],
          ],
        },
        markLine: thresholdMarkLine([
          {
            axis: "y",
            value: r2MinPct,
            color: tokens.favourable,
            // A markLine label is painted on the CANVAS, not rendered as HTML,
            // so it is not escaped here — escaping belongs to `tooltipHtml`.
            label: `R² floor ${r2MinPct}%`,
            labelPosition: "insideEndTop",
          },
        ]),
      },
    ],
  } as BiEChartsOption;

  const ineffective = data.filter((point) => !point.effective).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Hedge effectiveness for ${data.length} relationships plotted by dollar-offset ratio and prospective R squared${
        ineffective > 0 ? `; ${ineffective} fall outside the pass region` : ""
      }`}
    />
  );
}
