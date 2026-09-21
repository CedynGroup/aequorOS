"use client";

/**
 * The ONLY import path to the Tiptap editor.
 *
 * `dynamic(..., { ssr: false })` keeps ProseMirror out of every initial bundle
 * — most importantly the Command Center's, which `pnpm build` asserts with
 * `scripts/assert-home-route-bundle.mjs`: that guard fails if a ProseMirror
 * runtime marker appears in the home entry graph, AND fails if this deferred
 * chunk stops containing one (so it cannot pass vacuously). Importing
 * `./editor/SectionEditor` anywhere else defeats both halves.
 *
 * The editor's commands come back through its `onReady` prop, not a ref:
 * `next/dynamic` keeps the ref of the component it wraps for its own retry
 * handle.
 */

import dynamic from "next/dynamic";

const SectionEditorLoader = dynamic(() => import("./editor/SectionEditor"), {
  ssr: false,
  loading: () => (
    <div className="card overflow-hidden">
      <div className="h-11 border-b border-border-light bg-surface/60" />
      <div
        className="min-h-[24rem] animate-pulse bg-surface"
        aria-busy="true"
        aria-label="Loading the editor"
      />
    </div>
  ),
});

export default SectionEditorLoader;
