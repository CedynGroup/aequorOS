"use client";

/**
 * The ONLY import path to an ECharts canvas, for every chart in the dashboard.
 *
 * It began as the BI workspace's chart entrance and is now the whole dashboard's:
 * the module charts moved off Recharts, and they reach the canvas through here so
 * that the two properties below hold for every route, not just for `/explore`.
 *
 * `dynamic(..., { ssr: false })` does two jobs. ECharts and zrender reach for
 * `window` at module scope, so they cannot be server-rendered; and they are a
 * large runtime that must stay out of every initial bundle — most importantly
 * the Command Center's, which `pnpm build` asserts with
 * `scripts/assert-home-route-bundle.mjs`. That guard fails if a zrender or
 * ECharts runtime marker appears in the home entry graph, AND fails if the
 * deferred BI chunk stops containing one, so it cannot pass vacuously.
 * Importing `./EChartCanvas` anywhere else defeats both halves.
 *
 * The Command Center's own trend strip keeps using the lightweight SVG
 * `components/ui/Sparkline`; it is not a BI chart and must not become one.
 */

import dynamic from "next/dynamic";

export type { BiEChartsOption, EChartProps } from "./EChartCanvas";

const EChart = dynamic(() => import("./EChartCanvas"), {
  ssr: false,
  loading: () => (
    <div
      className="h-full w-full animate-pulse rounded-sm bg-surface"
      aria-busy="true"
      aria-label="Drawing the chart"
    />
  ),
});

export default EChart;
