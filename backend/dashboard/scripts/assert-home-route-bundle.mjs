import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const dashboardRoot = resolve(import.meta.dirname, "..");
const distDir = resolve(dashboardRoot, process.env.NEXT_DIST_DIR || ".next");
const manifestPath = resolve(distDir, "app-build-manifest.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const homeEntryNames = ["/(app)/page", "/(app)/layout", "/layout"];
const missingHomeEntries = homeEntryNames.filter(
  (entryName) => !Array.isArray(manifest.pages?.[entryName]),
);

if (missingHomeEntries.length > 0) {
  throw new Error(
    `Could not find the Command Center entry graph entries (${missingHomeEntries.join(", ")}) in ${manifestPath}`,
  );
}

const initialJavaScript = [
  ...new Set(homeEntryNames.flatMap((entryName) => manifest.pages[entryName])),
].filter((file) => file.endsWith(".js"));
const offendingChunks = [];
const offendingEditorChunks = [];
let rawBytes = 0;
let gzipBytes = 0;

// Class names ProseMirror writes onto the editable element. They are part of
// its runtime contract (its own stylesheet and behaviour key off them), so
// they survive production minification wherever the library itself is bundled.
const EDITOR_MARKERS = ["ProseMirror-hideselection", "ProseMirror-focused"];

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
      'imports it with dynamic(..., { ssr: false }).',
  );
}

const loadableManifestPath = resolve(distDir, "react-loadable-manifest.json");
const loadableManifest = JSON.parse(readFileSync(loadableManifestPath, "utf8"));
const ratioChartEntries = Object.entries(loadableManifest).filter(
  ([moduleName]) =>
    moduleName.endsWith("DeferredRatioTrendChart.tsx -> ./RatioTrendChart"),
);

if (ratioChartEntries.length !== 1) {
  throw new Error(
    `Expected one deferred RatioTrendChart entry in ${loadableManifestPath}; found ${ratioChartEntries.length}`,
  );
}

const deferredChartChunks = ratioChartEntries[0][1].files.filter((file) =>
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
const editorEntries = Object.entries(loadableManifest).filter(([moduleName]) =>
  moduleName.endsWith("SectionEditorLoader.tsx -> ./editor/SectionEditor"),
);

if (editorEntries.length !== 1) {
  throw new Error(
    `Expected one deferred ICAAP SectionEditor entry in ${loadableManifestPath}; found ${editorEntries.length}`,
  );
}

const deferredEditorChunks = editorEntries[0][1].files.filter((file) =>
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

console.log(
  `Command Center initial JS: ${rawBytes} B raw, ${gzipBytes} B gzip; Recharts deferred to ${deferredChartChunks.join(", ")}; ICAAP editor deferred to ${deferredEditorChunks.join(", ")}.`,
);
