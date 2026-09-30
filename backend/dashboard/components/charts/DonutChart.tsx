"use client";

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import { itemTooltip } from "@/lib/echartsOptions";
import { currencyCode } from "@/lib/format";

export type DonutSlice = {
  name: string;
  value: number;
  /**
   * Categorical palette index. It used to be a CSS colour string, which pages
   * filled from the Recharts theme with `var(--chart-n)` — a value a canvas
   * cannot resolve and paints as nothing. Pages that also draw a legend swatch
   * resolve the same index through `cssSeriesColor` in `lib/svgChartPalette.ts`.
   */
  colorIndex: number;
};

const formatters: Record<string, (value: number) => string> = {
  percent: (value) => `${value}%`,
  raw: (value) => value.toString(),
  "ccy-m": (value) => `${currencyCode()} ${value.toFixed(1)}M`,
};

export default function DonutChart({
  data,
  centerLabel,
  centerValue,
  height = 240,
  format = "raw",
}: {
  data: DonutSlice[];
  centerLabel?: string;
  centerValue?: string;
  height?: number;
  format?: "percent" | "raw" | "ccy-m";
}) {
  const tokens = useChartTokens();
  const formatter = formatters[format] ?? formatters.raw;
  const labels = data.map((slice) => slice.name);

  const option: BiEChartsOption = {
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const slice = data[index];
        return slice === undefined
          ? []
          : [
              {
                label: "",
                value: formatter(slice.value),
                color: seriesColor(tokens, slice.colorIndex),
              },
            ];
      }),
    },
    series: [
      {
        type: "pie",
        animation: false,
        radius: ["62%", "92%"],
        startAngle: 90,
        padAngle: 1,
        label: { show: false },
        labelLine: { show: false },
        data: data.map((slice) => ({
          name: slice.name,
          value: slice.value,
          itemStyle: {
            color: seriesColor(tokens, slice.colorIndex),
            borderColor: tokens.surface,
            borderWidth: 2,
          },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <div className="relative">
      <EChart
        option={option}
        height={height}
        ariaLabel={`${centerLabel ?? "Composition"}${
          centerValue ? ` of ${centerValue}` : ""
        } across ${data.length} components: ${data
          .map((slice) => `${slice.name} ${formatter(slice.value)}`)
          .join(", ")}`}
      />
      {(centerLabel || centerValue) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          {centerLabel && (
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              {centerLabel}
            </p>
          )}
          {centerValue && (
            <p className="font-mono text-h1 font-semibold text-navy tabular-nums mt-0.5">
              {centerValue}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
