"use client";

/**
 * The ONLY import path to the AG Grid runtime.
 *
 * `dynamic(..., { ssr: false })` does two jobs, the same two it does for the
 * charting canvas. AG Grid needs a real DOM and cannot be server-rendered; and
 * it is a large runtime that must stay out of every initial bundle — most of all
 * the Command Center's, which `pnpm build` asserts with
 * `scripts/assert-home-route-bundle.mjs`. That guard fails if an AG Grid runtime
 * marker appears in the home entry graph, AND fails if the deferred grid chunk
 * stops containing one, so it cannot pass vacuously. Importing
 * `./PivotGridCanvas` anywhere else defeats both halves.
 */

import dynamic from "next/dynamic";

export type { PivotGridCanvasProps as PivotGridProps } from "./PivotGridCanvas";

const PivotGrid = dynamic(() => import("./PivotGridCanvas"), {
  ssr: false,
  loading: () => (
    <div
      className="h-80 w-full animate-pulse rounded-sm bg-surface"
      aria-busy="true"
      aria-label="Preparing the grid"
    />
  ),
});

export default PivotGrid;
