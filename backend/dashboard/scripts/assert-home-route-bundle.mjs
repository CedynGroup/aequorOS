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
let rawBytes = 0;
let gzipBytes = 0;

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
}

if (offendingChunks.length > 0) {
  throw new Error(
    `Command Center initial entry graph contains Recharts chunk(s): ${offendingChunks.join(", ")}`,
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

console.log(
  `Command Center initial JS: ${rawBytes} B raw, ${gzipBytes} B gzip; Recharts deferred to ${deferredChartChunks.join(", ")}.`,
);
