import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const dashboardRoot = resolve(import.meta.dirname, "..");
const distDir = resolve(dashboardRoot, process.env.NEXT_DIST_DIR || ".next");
const routeDir = resolve(distDir, "server/app/(app)/page");
const clientManifestPath = resolve(
  distDir,
  "server/app/(app)/page_client-reference-manifest.js",
);
const clientManifestSource = readFileSync(clientManifestPath, "utf8").trim();
const assignmentPrefix = 'globalThis.__RSC_MANIFEST["/(app)/page"] = ';
const assignmentStart = clientManifestSource.indexOf(assignmentPrefix);
if (assignmentStart < 0 || !clientManifestSource.endsWith(";")) {
  throw new Error(
    `Could not read the Command Center client reference manifest at ${clientManifestPath}`,
  );
}
const clientManifest = JSON.parse(
  clientManifestSource.slice(assignmentStart + assignmentPrefix.length, -1),
);
const homeEntrySuffixes = [
  "/backend/dashboard/app/layout",
  "/backend/dashboard/app/(app)/layout",
  "/backend/dashboard/app/(app)/page",
];
const homeEntries = homeEntrySuffixes.map((suffix) =>
  Object.entries(clientManifest.entryJSFiles).find(([entryName]) =>
    entryName.endsWith(suffix),
  ),
);
const missingHomeEntries = homeEntrySuffixes.filter(
  (_, index) => !homeEntries[index],
);

if (missingHomeEntries.length > 0) {
  throw new Error(
    `Could not find the Command Center entry graph entries (${missingHomeEntries.join(", ")}) in ${clientManifestPath}`,
  );
}

const initialJavaScript = [
  ...new Set(homeEntries.flatMap((entry) => entry[1])),
].filter((file) => file.endsWith(".js"));
const offendingChunks = [];
const offendingEditorChunks = [];
const offendingBiChunks = [];
let rawBytes = 0;
let gzipBytes = 0;

// Class names ProseMirror writes onto the editable element. They are part of
// its runtime contract (its own stylesheet and behaviour key off them), so
// they survive production minification wherever the library itself is bundled.
const EDITOR_MARKERS = ["ProseMirror-hideselection", "ProseMirror-focused"];

/**
 * Runtimes the BI workspace uses that must never reach the Command Center.
 *
 * `data-zr-dom-id` is the attribute zrender writes onto every canvas layer it
 * creates, and `_echarts_instance_` the one ECharts writes onto a chart's host
 * element; both are DOM contracts, so they survive production minification
 * wherever the libraries are bundled. The AG Grid markers are the class names it
 * writes onto its own root element and onto every header cell, on the same
 * principle.
 *
 * The AG Grid pair was carried here BEFORE the library was a dependency, so that
 * the first import of it could not land in the home bundle unnoticed. It is a
 * dependency now (Phase 3, `ag-grid-community` 36.2.0, Community edition, no
 * licence key — D-030), and one of the two markers has been replaced: the second
 * was `ag-theme-quartz`, which is a class name of the RETIRED CSS-file themes
 * and appears nowhere in the v36 JavaScript bundle. Under the Theming API
 * (`themeQuartz.withParams`) it could never have matched, so it was a guard that
 * could not fail. `ag-header-cell` is written at runtime and is present in the
 * installed bundle, minified and not.
 *
 * The home insight strip uses the lightweight SVG `components/ui/Sparkline`.
 * Any BI chart must be reached through `components/bi/EChart.tsx`, and the BI
 * grid through `components/bi/PivotGrid.tsx`; both load with
 * `dynamic(..., { ssr: false })`.
 */
const BI_RUNTIME_MARKERS = [
  ["ECharts", "_echarts_instance_"],
  ["zrender", "data-zr-dom-id"],
  ["AG Grid", "ag-root-wrapper"],
  ["AG Grid", "ag-header-cell"],
];

/** The AG Grid markers, for the positive half of the rule. */
const AG_GRID_MARKERS = BI_RUNTIME_MARKERS.filter(
  ([library]) => library === "AG Grid",
).map(([, marker]) => marker);

for (const chunk of initialJavaScript) {
  const source = readFileSync(resolve(distDir, chunk));
  rawBytes += source.byteLength;
  gzipBytes += gzipSync(source).byteLength;

  const text = source.toString("utf8");
  // Recharts' rendered SVG/HTML class names are part of its runtime contract,
  // survive production minification, and occur throughout each library chunk.
  if (text.includes("recharts-")) {
    offendingChunks.push(chunk);
  }
  if (EDITOR_MARKERS.some((marker) => text.includes(marker))) {
    offendingEditorChunks.push(chunk);
  }
  for (const [library, marker] of BI_RUNTIME_MARKERS) {
    if (text.includes(marker)) {
      offendingBiChunks.push(`${chunk} (${library})`);
    }
  }
}

if (offendingChunks.length > 0) {
  throw new Error(
    `Command Center initial entry graph contains Recharts chunk(s): ${offendingChunks.join(", ")}`,
  );
}

if (offendingEditorChunks.length > 0) {
  throw new Error(
    "Command Center initial entry graph contains the ICAAP editor's " +
      `ProseMirror runtime: ${offendingEditorChunks.join(", ")}. Tiptap must be ` +
      "reached only through components/icaap/SectionEditorLoader.tsx, which " +
      "imports it with dynamic(..., { ssr: false }).",
  );
}

if (offendingBiChunks.length > 0) {
  throw new Error(
    "Command Center initial entry graph contains a BI charting or grid " +
      `runtime: ${offendingBiChunks.join(", ")}. Charts must be reached only ` +
      "through components/bi/EChart.tsx, which imports the canvas with " +
      "dynamic(..., { ssr: false }); the home insight strip uses the SVG " +
      "components/ui/Sparkline.",
  );
}

const loadableManifestPath = resolve(routeDir, "react-loadable-manifest.json");
const loadableManifest = JSON.parse(readFileSync(loadableManifestPath, "utf8"));
const ratioChartEntries = Object.values(loadableManifest);

if (ratioChartEntries.length !== 1) {
  throw new Error(
    `Expected one deferred RatioTrendChart entry in ${loadableManifestPath}; found ${ratioChartEntries.length}`,
  );
}

const deferredChartChunks = ratioChartEntries[0].files.filter((file) =>
  file.endsWith(".js"),
);
const deferredChunkHasRecharts = deferredChartChunks.some((chunk) =>
  readFileSync(resolve(distDir, chunk), "utf8").includes("recharts-"),
);

if (!deferredChunkHasRecharts) {
  throw new Error(
    `Deferred RatioTrendChart chunks no longer expose a Recharts runtime marker: ${deferredChartChunks.join(", ")}`,
  );
}

// The positive half of the editor rule: prove the deferred chunk EXISTS and
// carries the runtime, so the negative check above cannot pass vacuously (for
// example because the editor was deleted, or its import path changed).
const editorRouteDir = resolve(
  distDir,
  "server/app/(app)/icaap/[cycleId]/sections/[sectionKey]/page",
);
const editorLoadableManifestPath = resolve(
  editorRouteDir,
  "react-loadable-manifest.json",
);
const editorLoadableManifest = JSON.parse(
  readFileSync(editorLoadableManifestPath, "utf8"),
);
const editorEntries = Object.values(editorLoadableManifest);

if (editorEntries.length !== 1) {
  throw new Error(
    `Expected one deferred ICAAP SectionEditor entry in ${editorLoadableManifestPath}; found ${editorEntries.length}`,
  );
}

const deferredEditorChunks = editorEntries[0].files.filter((file) =>
  file.endsWith(".js"),
);
const deferredEditorHasProseMirror = deferredEditorChunks.some((chunk) => {
  const text = readFileSync(resolve(distDir, chunk), "utf8");
  return EDITOR_MARKERS.some((marker) => text.includes(marker));
});

if (!deferredEditorHasProseMirror) {
  throw new Error(
    "Deferred ICAAP SectionEditor chunks no longer expose a ProseMirror " +
      `runtime marker: ${deferredEditorChunks.join(", ")}. Either the markers ` +
      "changed in a prosemirror-view upgrade (update EDITOR_MARKERS after " +
      "checking the new build output) or the editor no longer bundles it.",
  );
}

// The positive half of the BI rule: the ECharts canvas must EXIST in a deferred
// chunk of a route that draws one, so the negative check above cannot pass
// vacuously — for example because the import path changed, or because the chart
// stopped being rendered at all.
const exploreLoadableManifestPath = resolve(
  distDir,
  "server/app/(app)/explore/page",
  "react-loadable-manifest.json",
);
const exploreEntries = Object.values(
  JSON.parse(readFileSync(exploreLoadableManifestPath, "utf8")),
);

if (exploreEntries.length === 0) {
  throw new Error(
    `Expected a deferred BI chart entry in ${exploreLoadableManifestPath}; found none. components/bi/EChart.tsx must load the canvas with next/dynamic.`,
  );
}

const deferredBiChunks = exploreEntries
  .flatMap((entry) => entry.files)
  .filter((file) => file.endsWith(".js"));
const deferredBiHasEcharts = deferredBiChunks.some((chunk) => {
  const text = readFileSync(resolve(distDir, chunk), "utf8");
  return text.includes("data-zr-dom-id") || text.includes("_echarts_instance_");
});

if (!deferredBiHasEcharts) {
  throw new Error(
    "Deferred BI chart chunks no longer expose an ECharts or zrender runtime " +
      `marker: ${deferredBiChunks.join(", ")}. Either the markers changed in ` +
      "an echarts upgrade (update BI_RUNTIME_MARKERS after checking the new " +
      "build output) or the chart no longer bundles it.",
  );
}

// The same positive half for the grid. Explore is the route that draws one, so
// the AG Grid runtime must exist in one of its DEFERRED chunks: if it stopped
// being deferred it would be in the route's initial bundle instead, and the
// negative check above only watches the Command Center.
const deferredBiHasAgGrid = deferredBiChunks.some((chunk) => {
  const text = readFileSync(resolve(distDir, chunk), "utf8");
  return AG_GRID_MARKERS.some((marker) => text.includes(marker));
});

if (!deferredBiHasAgGrid) {
  throw new Error(
    "Deferred BI chunks no longer expose an AG Grid runtime marker " +
      `(${AG_GRID_MARKERS.join(", ")}): ${deferredBiChunks.join(", ")}. Either ` +
      "the markers changed in an ag-grid-community upgrade (update " +
      "BI_RUNTIME_MARKERS after checking the new build output), or the grid is " +
      "no longer loaded through components/bi/PivotGrid.tsx with " +
      "dynamic(..., { ssr: false }) and has moved into an initial bundle.",
  );
}

console.log(
  `Command Center initial JS: ${rawBytes} B raw, ${gzipBytes} B gzip; Recharts deferred to ${deferredChartChunks.join(", ")}; ICAAP editor deferred to ${deferredEditorChunks.join(", ")}; ECharts and AG Grid deferred to ${deferredBiChunks.join(", ")}.`,
);
