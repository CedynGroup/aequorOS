"use client";

/**
 * The ONLY import path to the react-grid-layout runtime.
 *
 * `dynamic(..., { ssr: false })` does the same two jobs it does for the charting
 * canvas and the grid. The library measures a real container and installs a
 * resize observer, so it cannot be server-rendered; and it is a runtime that must
 * stay out of every initial bundle — most of all the Command Center's, which
 * `pnpm build` asserts with `scripts/assert-home-route-bundle.mjs`. That guard
 * fails if a react-grid-layout runtime marker appears in the home entry graph,
 * AND fails if the deferred builder chunk stops containing one, so it cannot pass
 * vacuously. Importing `./BuilderGridCanvas` anywhere else defeats both halves.
 */

import dynamic from "next/dynamic";

export type {
  BuilderGridCanvasProps as BuilderGridProps,
  BuilderGridItem,
} from "./BuilderGridCanvas";

const BuilderGrid = dynamic(() => import("./BuilderGridCanvas"), {
  ssr: false,
  loading: () => (
    <div
      className="h-96 w-full animate-pulse rounded bg-surface"
      aria-busy="true"
      aria-label="Preparing the canvas"
    />
  ),
});

export default BuilderGrid;
