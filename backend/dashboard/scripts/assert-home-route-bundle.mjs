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

console.log(
  `Command Center initial JS: ${rawBytes} B raw, ${gzipBytes} B gzip; Recharts deferred to ${deferredChartChunks.join(", ")}.`,
);
