"use client";

/**
 * The ECharts canvas itself. Reached ONLY through `./EChart`, which imports it
 * with `next/dynamic(..., { ssr: false })`.
 *
 * Two reasons for that single entrance. ECharts and zrender touch `window` and
 * `document` at module scope, so they cannot be server-rendered; and together
 * they are a large runtime that `scripts/assert-home-route-bundle.mjs` keeps
 * out of the Command Center's initial entry graph. A static
 * `import ... from "./EChartCanvas"` anywhere else defeats both.
 *
 * Only the chart types the surfaces actually draw are registered. Adding a
 * chart type here is a deliberate act: the bundle is the sum of what is
 * registered, not of what ECharts can do. `ScatterChart` and `MarkAreaComponent`
 * were added when the module dashboards moved off Recharts — the FX hedge
 * effectiveness plot is a scatter, and its target zone was a `<ReferenceArea>`.
 * This canvas now serves every chart in the dashboard, not only the BI widgets.
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type {
  BarSeriesOption,
  LineSeriesOption,
  PieSeriesOption,
  ScatterSeriesOption,
} from "echarts/charts";
import type {
  DatasetComponentOption,
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import type { ComposeOption } from "echarts/core";
import { useChartTheme } from "./echartsTheme";

export type BiEChartsOption = ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | ScatterSeriesOption
  | DatasetComponentOption
  | GridComponentOption
  | LegendComponentOption
  | TitleComponentOption
  | TooltipComponentOption
>;

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export type EChartProps = {
  option: BiEChartsOption;
  /** Pixel height of the canvas. The width always follows the container. */
  height?: number;
  /**
   * What the chart says, in words. A canvas publishes no text, so this is the
   * only thing a screen reader gets — write the finding, not "chart".
   */
  ariaLabel: string;
  className?: string;
};

export default function EChartCanvas({
  option,
  height = 280,
  ariaLabel,
  className = "",
}: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const optionRef = useRef<BiEChartsOption>(option);
  const chartTheme = useChartTheme();

  // Declared BEFORE the instance effect so that, on mount, the latest option is
  // already in the ref by the time the chart is created.
  useEffect(() => {
    optionRef.current = option;
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // ECharts bakes a theme at init, so a palette change means a new instance.
    // `chartTheme.name` carries a digest of the resolved tokens, which is what
    // makes a theme switch produce a new name and therefore a repaint.
    echarts.registerTheme(chartTheme.name, chartTheme.theme);
    const chart = echarts.init(container, chartTheme.name, {
      renderer: "canvas",
    });
    chartRef.current = chart;
    chart.setOption(optionRef.current, { notMerge: true });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [chartTheme]);

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={ariaLabel}
      style={{ height, width: "100%" }}
      className={className}
    />
  );
}
