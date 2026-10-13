"use client";

/**
 * Token-themed ratio trend line chart for the liquidity and Basel capital
 * workspaces, with an optional second series and an explicit red-floor line.
 *
 * The hollow/solid point distinction (inline vs persisted) is a per-datum
 * `itemStyle` — a canvas has no element per point for a custom dot renderer.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
  type ThresholdLine,
} from "@/lib/echartsOptions";

export type TrendPoint = {
  label: string;
  primary: number | null;
  secondary?: number | null;
  /** false → computed inline (not persisted) — rendered as a hollow point. */
  stored?: boolean;
};

export default function RatioTrendChart({
  data,
  threshold,
  thresholdLabel = "Min",
  redFloor,
  redFloorLabel = "Red floor",
  primaryLabel = "Ratio",
  secondaryLabel,
  yMin,
  yMax,
  height = 260,
}: {
  data: TrendPoint[];
  /**
   * Regulatory minimum (dashed red line). `null` when the floor did not
   * resolve — no line is drawn and the axis is driven by the data alone. A
   * stand-in number here would print a labelled regulatory floor across a
   * chart nobody set that floor for.
   */
  threshold: number | null;
  thresholdLabel?: string;
  /** Optional lower amber/red boundary (dashed amber line). */
  redFloor?: number | null;
  redFloorLabel?: string;
  primaryLabel?: string;
  secondaryLabel?: string;
  yMin?: number;
  yMax?: number;
  height?: number;
}) {
  const tokens = useChartTokens();
  const values = data.flatMap((point) =>
    point.secondary === undefined
      ? [point.primary]
      : [point.primary, point.secondary],
  );
  const measured = values.filter((value): value is number => value !== null);
  const floors = [
    ...(threshold === null ? [] : [threshold]),
    ...(redFloor === null || redFloor === undefined ? [] : [redFloor]),
  ];
  const scale = [...measured, ...floors];
  const min =
    yMin ?? (scale.length > 0 ? Math.floor(Math.min(...scale) - 5) : 0);
  const max =
    yMax ?? (scale.length > 0 ? Math.ceil(Math.max(...scale) + 5) : 100);
  const primaryColor = tokens.accent;
  const secondaryColor = seriesColor(tokens, 1);
  const labels = data.map((point) => point.label);

  const thresholds: ThresholdLine[] = [
    ...(threshold === null
      ? []
      : [
          {
            axis: "y" as const,
            value: threshold,
            label: `${thresholdLabel} ${threshold}%`,
            color: tokens.adverse,
            labelPosition: "insideEndBottom" as const,
          },
        ]),
    ...(redFloor === undefined || redFloor === null
      ? []
      : [
          {
            axis: "y" as const,
            value: redFloor,
            label: `${redFloorLabel} ${redFloor}%`,
            color: tokens.caution,
            labelPosition: "start" as const,
          },
        ]),
  ];

  const pointStyle = (color: string) => (point: TrendPoint) => ({
    value: point.primary,
    itemStyle: {
      color: point.stored === false ? tokens.surface : color,
      borderColor: color,
      borderWidth: point.stored === false ? 1.5 : 0,
    },
  });

  const option: BiEChartsOption = {
    grid: {
      left: 4,
      right: 24,
      top: 12,
      bottom: secondaryLabel ? 24 : 4,
      containLabel: true,
    },
    ...(secondaryLabel
      ? { legend: { bottom: 0, type: "scroll" as const } }
      : {}),
    xAxis: { type: "category", data: labels },
    yAxis: {
      type: "value",
      min,
      max,
      axisLine: { show: false },
      axisLabel: { formatter: (value: number) => `${Math.round(value)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`, {
        note: (index) => (data[index]?.stored === false ? "inline" : undefined),
      }),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: primaryLabel,
        smooth: true,
        showSymbol: true,
        symbolSize: 6,
        lineStyle: { color: primaryColor, width: 2 },
        markLine:
          thresholds.length > 0 ? thresholdMarkLine(thresholds) : undefined,
        data: data.map(pointStyle(primaryColor)),
      },
      ...(secondaryLabel
        ? [
            {
              ...LINE_SERIES_BASE,
              name: secondaryLabel,
              smooth: true,
              showSymbol: true,
              symbolSize: 5,
              lineStyle: {
                color: secondaryColor,
                width: 1.5,
                type: "dashed" as const,
              },
              data: data.map((point) => ({
                value: point.secondary ?? null,
                itemStyle: {
                  color:
                    point.stored === false ? tokens.surface : secondaryColor,
                  borderColor: secondaryColor,
                  borderWidth: point.stored === false ? 1.5 : 0,
                },
              })),
            },
          ]
        : []),
    ],
  } as BiEChartsOption;

  const inline = data.filter((point) => point.stored === false).length;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`${primaryLabel}${
        secondaryLabel ? ` and ${secondaryLabel}` : ""
      } across ${data.length} periods${
        threshold === null
          ? ", with no regulatory minimum resolved"
          : `, against a minimum of ${threshold}%`
      }${inline > 0 ? `; ${inline} points computed inline` : ""}`}
    />
  );
}
