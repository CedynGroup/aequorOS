"use client";

/**
 * Balance-sheet projection: assets vs liabilities vs equity over the
 * forecast horizon, with an optional base↔adverse range band on total
 * assets when a succeeded adverse-scenario run exists for the same period.
 *
 * Recharts drew the band from a `[low, high]` tuple on one `<Area>`. ECharts has
 * no band series, so it is TWO stacked line series — the low edge invisible, the
 * thickness of the band filled — which is the standard construction. A period
 * with no adverse run has no band: both legs are `null`, so the fill stops rather
 * than collapsing to a zero-width band at the base line.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  gapAwareData,
  LINE_SERIES_BASE,
} from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

export type ProjectionPoint = {
  label: string;
  assets: number;
  liabilities: number;
  equity: number;
  /** Adverse-scenario total assets for the same year (band overlay). */
  adverseAssets?: number | null;
  /** [low, high] of base vs adverse assets — the shaded band. */
  band?: [number, number] | null;
};

const BAND_LOW = "Band floor";
const BAND_SPAN = "Base ↔ adverse assets";

export default function ProjectionChart({
  data,
  hasBand,
  height = 320,
}: {
  data: ProjectionPoint[];
  hasBand: boolean;
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.label);
  const lines = [
    {
      name: "Total assets",
      color: seriesColor(tokens, 0),
      width: 2.25,
      values: data.map((point) => point.assets),
    },
    {
      name: "Liabilities",
      color: seriesColor(tokens, 1),
      width: 2,
      values: data.map((point) => point.liabilities),
    },
    {
      name: "Equity",
      color: seriesColor(tokens, 2),
      width: 2,
      values: data.map((point) => point.equity),
    },
  ];

  const bandLow = data.map((point) => point.band?.[0] ?? null);
  const bandSpan = data.map((point) =>
    point.band ? point.band[1] - point.band[0] : null,
  );

  const option: BiEChartsOption = {
    grid: { left: 4, right: 16, top: 30, bottom: 4, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      type: "scroll",
      // The invisible floor of the band is a construction detail, not a series a
      // reader should be offered.
      data: [
        ...lines.map((line) => line.name),
        ...(hasBand ? [BAND_SPAN, "Adverse assets"] : []),
      ],
    },
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
      trigger: "axis",
      formatter: axisTooltip(
        labels,
        (value, seriesName, index) => {
          if (seriesName !== BAND_SPAN) return fmtCurrency(value);
          const band = data[index]?.band;
          return band
            ? `${fmtCurrency(band[0])} – ${fmtCurrency(band[1])}`
            : fmtCurrency(value);
        },
        { absent: "no adverse run for this period", hideAbsent: true },
      ),
    },
    series: [
      ...(hasBand
        ? [
            {
              ...LINE_SERIES_BASE,
              name: BAND_LOW,
              stack: "band",
              smooth: true,
              silent: true,
              lineStyle: { opacity: 0 },
              itemStyle: { opacity: 0 },
              tooltip: { show: false },
              data: gapAwareData(bandLow),
            },
            {
              ...LINE_SERIES_BASE,
              name: BAND_SPAN,
              stack: "band",
              smooth: true,
              lineStyle: { opacity: 0 },
              itemStyle: { color: tokens.caution },
              areaStyle: { color: tokens.caution, opacity: 0.12 },
              data: gapAwareData(bandSpan),
            },
          ]
        : []),
      ...lines.map((line) => ({
        ...LINE_SERIES_BASE,
        name: line.name,
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color: line.color, width: line.width },
        itemStyle: { color: line.color },
        data: line.values,
      })),
      ...(hasBand
        ? [
            {
              ...LINE_SERIES_BASE,
              name: "Adverse assets",
              smooth: true,
              lineStyle: {
                color: tokens.caution,
                width: 1.5,
                type: "dashed" as const,
              },
              itemStyle: { color: tokens.caution },
              data: gapAwareData(
                data.map((point) => point.adverseAssets ?? null),
              ),
            },
          ]
        : []),
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Projected assets, liabilities and equity across ${data.length} periods${
        hasBand ? ", with the base to adverse asset range shaded" : ""
      }`}
    />
  );
}
