/**
 * Four properties of the self-service grid that must not erode — and the proof
 * that each rule below can still convict a violation of itself.
 *
 * 1. THE BUILT-IN EXPORT IS OFF, AND IT IS OFF TWICE. AG Grid's own CSV export
 *    copies the rows in the browser straight to a file: no authorization
 *    decision, no `bi_query_log` entry, no watermark, no record that anything
 *    left. It bypasses all three, so it is a data-egress path, not a
 *    convenience, and the governed Export menu is the only way out of a BI view.
 *    The grid's own route out is closed by NOT REGISTERING `CsvExportModule` —
 *    from v33 onwards an unregistered module's API does not exist — and,
 *    independently, by `suppressCsvExport` / `suppressExcelExport`. Either alone
 *    would be enough today; both means a module list edited for an unrelated
 *    feature cannot silently reopen it.
 *
 * 2. THE LIBRARY IS COMMUNITY, WITH NO LICENCE KEY (D-030). Grouping, pivot and
 *    subtotals are compiled server-side, so the Enterprise edition buys nothing
 *    the platform needs — and a licence literal committed anywhere is
 *    unrecoverable, because the one required CI check scans the whole history.
 *
 * 3. THE RUNTIME HAS EXACTLY ONE ENTRANCE. `PivotGrid.tsx` loads the canvas with
 *    `dynamic(..., { ssr: false })`, which is what keeps a large DOM-bound
 *    library out of every initial bundle — the home route's included, which
 *    `scripts/assert-home-route-bundle.mjs` asserts from the built output.
 *
 * 4. THE GRID AND THE CHARTS ARE PAINTED FROM ONE RESOLUTION OF THE TOKENS, and
 *    the grid formats no value of its own: both come from the modules that
 *    already own those decisions, so a canvas and a grid on one screen cannot
 *    disagree about the brand or about a figure.
 *
 * WHY THERE IS A SELF-PROOF SECTION. Most of what follows is a NEGATIVE scan —
 * "this string appears nowhere" — and a negative scan is the kind of guard this
 * repository has been bitten by repeatedly: if its regex silently stops matching,
 * or its file walk returns nothing, it reports clean forever, and "clean" reads
 * exactly like a guard that is working. The home-route guard's own AG Grid rule
 * was one of these until this task: it named `ag-theme-quartz`, a class of the
 * retired CSS-file themes that appears nowhere in the v36 bundle, so it could
 * never have fired. Same discipline as `lib/api/fail-open-guard.test.ts` after
 * audit A8-10: every pattern rule has to convict a snippet written to look like
 * its own defect, a rule with no proof fails, a proof naming a dead rule fails,
 * and each rule is also run against correct code so it cannot pass by matching
 * everything.
 *
 * Source inspection rather than rendering, for the same reason
 * `disclosure.test.ts` uses it: this package has no component-test harness, and
 * every property above is about what a file is allowed to reference — which is
 * exactly what a source scan can decide. Comments are stripped first, so prose
 * about a defect is not read as the defect.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

function dashboardRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 8; index += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const ROOT = dashboardRoot();

function code(relativePath: string): string {
  return readFileSync(join(ROOT, relativePath), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
}

/** Every first-party source file, so "nowhere in the app" can be asserted. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const skip = new Set([
    "node_modules",
    ".next",
    ".next-dev",
    ".next-build",
    ".next-e2e",
    ".test-out",
    "playwright-report",
    "test-results",
  ]);
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (skip.has(entry)) continue;
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (/\.(tsx?|mjs|json)$/.test(entry)) out.push(full);
    }
  };
  walk(ROOT);
  return out;
}

const CANVAS = "components/bi/PivotGridCanvas.tsx";
const WRAPPER = "components/bi/PivotGrid.tsx";
const GUARD = join("scripts", "assert-home-route-bundle.mjs");
const canvas = code(CANVAS);
const wrapper = code(WRAPPER);

// ---------------------------------------------------------------------------
// The pattern rules. Each one is a NEGATIVE check — the thing it names must not
// appear — so each declares the violation it must convict and the correct code
// it must acquit. The meta-assertions at the bottom of this file enforce that.
// ---------------------------------------------------------------------------

type PatternRule = {
  id: string;
  pattern: RegExp;
  /** Written to look exactly like the defect. The rule MUST match it. */
  convicts: string;
  /** Correct code. The rule must NOT match it. */
  acquits: readonly string[];
};

const RULES: readonly PatternRule[] = [
  {
    id: "an AG Grid export API is called",
    pattern: /\bexportDataAsCsv\b|\bexportDataAsExcel\b|\bgetDataAsCsv\b/,
    convicts: "const csv = api.exportDataAsCsv({ allColumns: true });",
    acquits: [
      "const options = [printExportOption()];",
      "<ExportMenu options={exportOptions} />",
    ],
  },
  {
    id: "AG Grid Enterprise or a licence key is reached for",
    pattern:
      /ag-grid-enterprise|ag-grid-charts-enterprise|LicenseManager|setLicenseKey|AG_GRID_LICENSE/,
    convicts: 'import { LicenseManager } from "ag-grid-enterprise";',
    acquits: [
      'import { AgGridReact } from "ag-grid-react";',
      'import { themeQuartz } from "ag-grid-community";',
    ],
  },
  {
    id: "the grid runtime is imported outside its one entrance",
    pattern:
      /from\s+["'][^"']*PivotGridCanvas["']|from\s+["']ag-grid-(?:community|react)["']/,
    convicts: 'import PivotGridCanvas from "./PivotGridCanvas";',
    acquits: [
      'import PivotGrid from "@/components/bi/PivotGrid";',
      'import EChart from "./EChart";',
    ],
  },
  {
    id: "the export suppression is turned back off",
    pattern: /suppress(?:Csv|Excel)Export\s*=\s*\{?\s*false/,
    convicts: "<AgGridReact suppressCsvExport={false} />",
    acquits: ["<AgGridReact suppressCsvExport suppressExcelExport />"],
  },
  {
    id: "a colour literal is written into the grid theme",
    pattern: /#[0-9a-fA-F]{6}\b/,
    convicts: 'backgroundColor: "#17202F",',
    acquits: ["backgroundColor: tokens.surface,"],
  },
  {
    id: "the grid page size is hardcoded instead of read from the server",
    pattern: /cacheBlockSize=\{\s*\d/,
    convicts: "<AgGridReact cacheBlockSize={500} />",
    acquits: [
      "<AgGridReact cacheBlockSize={pageSize ?? undefined} />",
      "maxBlocksInCache={PAGES_KEPT}",
    ],
  },
  {
    id: "display code still branches on the retired `ratio` value type",
    pattern: /format === "ratio"/,
    convicts: 'if (format === "ratio") return parsed.toFixed(4);',
    acquits: [
      'if (format === "fraction") return fmtPct(parsed * FRACTION_SCALE);',
      'if (format === "index") return fmtNum(parsed, INDEX_DECIMALS);',
    ],
  },
];

function rule(id: string): PatternRule {
  const found = RULES.find((entry) => entry.id === id);
  assert.ok(found, `no pattern rule named ${id}`);
  return found!;
}

/** Every first-party file whose comment-stripped source violates `id`. */
function offenders(
  id: string,
  include: (relative: string) => boolean,
): string[] {
  const { pattern } = rule(id);
  const found: string[] = [];
  let scanned = 0;
  for (const file of sourceFiles()) {
    const relative = file.slice(ROOT.length + 1).split("\\").join("/");
    if (!include(relative)) continue;
    scanned += 1;
    const text = /\.(tsx?|mjs)$/.test(relative)
      ? code(relative)
      : readFileSync(file, "utf8");
    if (pattern.test(text)) found.push(relative);
  }
  // A scan that read nothing reports clean. The floor is deliberately generous:
  // it only has to prove the walk still reaches the app.
  assert.ok(
    scanned >= 200,
    `the scan for "${id}" read only ${scanned} files, so a clean result proves nothing`,
  );
  return found;
}

/** Not a test file and not this guard: both name the defects in prose. */
function appSource(relative: string): boolean {
  return !/\.test\.tsx?$/.test(relative);
}

// --- 1. the built-in export is off ------------------------------------------

/** The symbols the canvas imports from the grid library, as it imports them. */
function importedGridSymbols(): string[] {
  const match = /import\s*\{([\s\S]*?)\}\s*from\s*["']ag-grid-community["']/.exec(
    canvas,
  );
  assert.ok(match, `${CANVAS} must import its modules from ag-grid-community`);
  const symbols = match![1]
    .split(",")
    .map((entry) => entry.replace(/^\s*type\s+/, "").trim())
    .filter((entry) => entry.length > 0);
  // The positive control on the extraction itself: if this regex ever stops
  // finding the real import list it returns nothing, and "no export module"
  // would pass vacuously. A symbol that must be there proves it read something.
  assert.ok(
    symbols.includes("ModuleRegistry"),
    `could not read ${CANVAS}'s ag-grid-community import list — the extraction ` +
      "found no ModuleRegistry, so every conclusion drawn from it is vacuous.",
  );
  return symbols;
}

test("no export module is registered, so the export API does not exist", () => {
  const symbols = importedGridSymbols();
  const exportModules = symbols.filter((symbol) => /Export/.test(symbol));
  assert.deepEqual(
    exportModules,
    [],
    `${CANVAS} imports ${exportModules.join(", ")}. AG Grid's own CSV/Excel ` +
      "export writes rows out of the browser with no authorization decision, " +
      "no bi_query_log entry and no watermark. Exports go through the governed " +
      "Export menu only.",
  );
  assert.equal(
    symbols.includes("AllCommunityModule"),
    false,
    `${CANVAS} must register its modules individually. AllCommunityModule ` +
      "includes CsvExportModule, which would put exportDataAsCsv back on the " +
      "grid API.",
  );
  assert.ok(
    symbols.includes("InfiniteRowModelModule"),
    `${CANVAS} must register InfiniteRowModelModule — the Community edition ` +
      "has no server-side row model, and the grid pages through …/bi/grid.",
  );
  assert.ok(
    /ModuleRegistry\.registerModules\(/.test(canvas),
    `${CANVAS} must register its modules explicitly.`,
  );
});

test("the grid options suppress the export independently of the module list", () => {
  for (const option of ["suppressCsvExport", "suppressExcelExport"]) {
    assert.ok(
      new RegExp(`\\b${option}\\b`).test(canvas),
      `${CANVAS} must set ${option}. It is the second, independent lock on the ` +
        "ungoverned way out of a BI view.",
    );
  }
  assert.deepEqual(
    offenders("the export suppression is turned back off", appSource),
    [],
    "Neither export suppression may be set to false.",
  );
});

test("nothing in the app calls an AG Grid export API", () => {
  assert.deepEqual(
    offenders("an AG Grid export API is called", appSource),
    [],
    "Every export is a read that has to be authorized, audited and watermarked " +
      "server-side. AG Grid's own export does none of the three.",
  );
});

// --- 2. Community, and no licence key ---------------------------------------

test("the Enterprise edition and its licence machinery are absent", () => {
  assert.deepEqual(
    offenders("AG Grid Enterprise or a licence key is reached for", appSource),
    [],
    "D-030: grouping, pivot and subtotals are compiled server-side, so the " +
      "Community edition is what ships, and a licence literal in any commit " +
      "cannot be taken back out of the history.",
  );
  const manifest = JSON.parse(
    readFileSync(join(ROOT, "package.json"), "utf8"),
  ) as { dependencies?: Record<string, string> };
  const dependencies = Object.keys(manifest.dependencies ?? {});
  assert.ok(dependencies.includes("ag-grid-community"));
  assert.ok(dependencies.includes("ag-grid-react"));
  assert.equal(dependencies.includes("ag-grid-enterprise"), false);
});

// --- 3. one entrance, deferred ----------------------------------------------

test("the grid runtime is reached only through the dynamic wrapper", () => {
  assert.ok(
    /dynamic\(\s*\(\)\s*=>\s*import\(["']\.\/PivotGridCanvas["']\)/.test(wrapper),
    `${WRAPPER} must load the canvas through next/dynamic.`,
  );
  assert.ok(
    /ssr:\s*false/.test(wrapper),
    `${WRAPPER} must load the canvas with ssr:false — AG Grid needs a DOM, and ` +
      "a static import would put it in the initial bundle.",
  );
  assert.deepEqual(
    offenders(
      "the grid runtime is imported outside its one entrance",
      (relative) =>
        appSource(relative) && relative !== WRAPPER && relative !== CANVAS,
    ),
    [],
    `The grid runtime must be reached through ${WRAPPER}, or the library lands ` +
      "in that route's initial bundle and the home-route bundle guard's " +
      "positive half stops meaning anything.",
  );
  // The acquittal half, on real files: the two legitimate importers MUST be
  // convicted by the same rule when they are not excluded, or the exclusion
  // above is doing all the work and the scan would miss a third importer.
  const withoutExclusions = offenders(
    "the grid runtime is imported outside its one entrance",
    appSource,
  );
  assert.deepEqual(
    [...withoutExclusions].sort(),
    [CANVAS, WRAPPER].sort(),
    "The scan must see exactly the two files that are allowed to import the " +
      "grid runtime. Seeing fewer means it is not reading them at all.",
  );
});

test("the bundle guard names a marker the installed library actually emits", () => {
  const guard = code(GUARD);
  const declared = /const BI_RUNTIME_MARKERS = \[([\s\S]*?)\];/.exec(guard);
  assert.ok(
    declared,
    "assert-home-route-bundle.mjs must declare BI_RUNTIME_MARKERS.",
  );
  const markers = [...declared![1].matchAll(/"([^"]+)"\s*\]/g)].map(
    (match) => match[1],
  );
  assert.ok(
    markers.includes("ag-root-wrapper"),
    "The guard must keep AG Grid's own root element class as a marker.",
  );
  assert.equal(
    markers.includes("ag-theme-quartz"),
    false,
    "`ag-theme-quartz` is a class name of the retired CSS-file themes and " +
      "appears nowhere in the v36 JavaScript bundle, so it can never match. A " +
      "marker that cannot match is a guard that cannot fail — use a class the " +
      "library writes onto the DOM at runtime.",
  );

  // Every named marker must exist in the installed package, or the positive
  // half of the rule passes because the string is simply never there.
  const bundle = readFileSync(
    require.resolve("ag-grid-community", { paths: [ROOT] }),
    "utf8",
  );
  const agMarkers = markers.filter((entry) => entry.startsWith("ag-"));
  assert.ok(agMarkers.length >= 2, "expected the AG Grid markers");
  for (const marker of agMarkers) {
    assert.ok(
      bundle.includes(marker),
      `The guard names "${marker}", which the installed ag-grid-community ` +
        "bundle does not contain. Check the new build output and update " +
        "BI_RUNTIME_MARKERS.",
    );
  }

  // The same parity for the saved-dashboard builder's grid. `react-grid-item` and
  // `react-resizable-handle` are class names react-grid-layout writes onto the
  // DOM and its own stylesheet keys off, so they survive minification wherever the
  // library is bundled — but only the installed package can say whether they are
  // still the ones it writes. A marker the library no longer emits is a guard that
  // cannot fire, which is the defect `ag-theme-quartz` was.
  const gridLayoutMarkers = markers.filter((entry) =>
    entry.startsWith("react-"),
  );
  assert.deepEqual(
    [...gridLayoutMarkers].sort(),
    ["react-grid-item", "react-resizable-handle"],
    "The guard must name react-grid-layout's own runtime class names.",
  );
  // Resolved through the package's own entry point rather than through
  // `package.json`, which its `exports` map does not expose. The entry is
  // `dist/index.js`, so its directory is the built bundle set.
  const gridLayoutDist = dirname(
    require.resolve("react-grid-layout", { paths: [ROOT] }),
  );
  const gridLayoutBundles = readdirSync(gridLayoutDist)
    .filter((name) => name.endsWith(".mjs") || name.endsWith(".js"))
    .map((name) => readFileSync(join(gridLayoutDist, name), "utf8"));
  assert.ok(
    gridLayoutBundles.length > 0,
    "expected the installed react-grid-layout build output",
  );
  for (const marker of gridLayoutMarkers) {
    assert.ok(
      gridLayoutBundles.some((text) => text.includes(marker)),
      `The guard names "${marker}", which the installed react-grid-layout ` +
        "build does not contain. Check the new build output and update " +
        "BI_RUNTIME_MARKERS.",
    );
  }
});

/**
 * A build output the bundle guard can be RUN against.
 *
 * Reading the guard's source can only ever prove that some text is present. The
 * property that matters is behavioural — does it throw when AG Grid is in the
 * home bundle, and does it throw when AG Grid is in NO bundle — so the guard is
 * executed against synthetic dist trees that violate one rule each. This is the
 * same standard `lib/api/fail-open-guard.test.ts` holds its own rules to: a rule
 * that cannot be shown to fire is not a rule.
 */
function writeDistFixture(
  root: string,
  options: Readonly<{
    agGridInHome: boolean;
    agGridDeferred: boolean;
    /** react-grid-layout reaching the Command Center's initial entry graph. */
    gridLayoutInHome?: boolean;
    /** react-grid-layout present in a deferred chunk of the builder route. */
    gridLayoutDeferred?: boolean;
    /**
     * The Command Center's deferred chart chunk carries EChart's loading label,
     * which is what proves the chart is still IN that chunk. `false` is what a
     * deleted or statically-imported chart looks like.
     */
    chartDeferred?: boolean;
    /**
     * The Command Center's deferred chart chunk also carries the ECharts runtime
     * — i.e. `components/bi/EChart.tsx` stopped deferring the canvas, so the home
     * route pays for the whole library as soon as it scrolls the chart into view.
     */
    chartRuntimeInChartChunk?: boolean;
  }>,
): void {
  const chunk = (name: string, body: string) => {
    const file = join("static", "chunks", name);
    mkdirSync(dirname(join(root, file)), { recursive: true });
    writeFileSync(join(root, file), body);
    return file;
  };
  // Padding so a chunk is a plausible size; content is all that is inspected.
  const filler = "/* filler */\n".repeat(40);
  const homeChunk = chunk(
    "home.js",
    filler +
      (options.agGridInHome ? 'cls="ag-root-wrapper";' : "") +
      (options.gridLayoutInHome ? 'cls="react-grid-item";' : ""),
  );
  const chartBoundaryChunk = chunk(
    "ratio-chart.js",
    filler +
      (options.chartDeferred === false ? "" : '"Drawing the chart";') +
      (options.chartRuntimeInChartChunk ? '"_echarts_instance_";' : ""),
  );
  const editorChunk = chunk("editor.js", filler + '"ProseMirror-focused"');
  const echartsChunk = chunk("echarts.js", filler + '"_echarts_instance_"');
  const gridChunk = chunk(
    "grid.js",
    filler + (options.agGridDeferred ? '"ag-root-wrapper";"ag-header-cell"' : ""),
  );
  const builderChunk = chunk(
    "builder.js",
    filler +
      (options.gridLayoutDeferred !== false
        ? '"react-grid-item";"react-resizable-handle"'
        : ""),
  );

  const manifest = (route: string, entries: Record<string, string[]>) => {
    const file = join(root, "server", "app", `${route}_client-reference-manifest.js`);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(
      file,
      `globalThis.__RSC_MANIFEST["/${route}"] = ${JSON.stringify({
        entryJSFiles: entries,
      })};`,
    );
  };
  manifest("(app)/page", {
    "/backend/dashboard/app/layout": [homeChunk],
    "/backend/dashboard/app/(app)/layout": [homeChunk],
    "/backend/dashboard/app/(app)/page": [homeChunk],
  });

  const loadable = (route: string, files: string[]) => {
    const file = join(
      root,
      "server",
      "app",
      route,
      "react-loadable-manifest.json",
    );
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, JSON.stringify({ "1": { files } }));
  };
  loadable("(app)/page", [chartBoundaryChunk]);
  loadable("(app)/icaap/[cycleId]/sections/[sectionKey]/page", [editorChunk]);
  loadable("(app)/explore/page", [echartsChunk, gridChunk]);
  loadable("(app)/dashboards/new/page", [builderChunk]);
}

function runBundleGuard(distDir: string): Readonly<{ code: number; text: string }> {
  const result = spawnSync("node", [join(ROOT, GUARD)], {
    cwd: ROOT,
    env: { ...process.env, NEXT_DIST_DIR: distDir },
    encoding: "utf8",
  });
  return {
    code: result.status ?? 1,
    text: `${result.stdout ?? ""}${result.stderr ?? ""}`,
  };
}

test("the bundle guard's AG Grid rule fires on both of its own violations", () => {
  const base = mkdtempSync(join(tmpdir(), "aeq-bundle-guard-"));

  // The control: a correct build must pass, or the two convictions below prove
  // only that the guard rejects everything.
  const good = join(base, "good");
  writeDistFixture(good, { agGridInHome: false, agGridDeferred: true });
  const clean = runBundleGuard(good);
  assert.equal(
    clean.code,
    0,
    `the bundle guard rejected a correct build, so its verdicts mean nothing:\n${clean.text}`,
  );

  // Violation one, the negative half: AG Grid reaches the Command Center.
  const inHome = join(base, "in-home");
  writeDistFixture(inHome, { agGridInHome: true, agGridDeferred: true });
  const home = runBundleGuard(inHome);
  assert.notEqual(
    home.code,
    0,
    "the bundle guard passed a build with an AG Grid runtime marker in the " +
      "Command Center's initial entry graph",
  );
  assert.ok(
    /AG Grid/.test(home.text),
    `the guard failed without naming AG Grid:\n${home.text}`,
  );

  // Violation two, the positive half: AG Grid is in NO deferred chunk — which is
  // what a static import, or a deleted grid, looks like. The negative half above
  // passes just as happily in that state, which is why this half has to exist.
  const notDeferred = join(base, "not-deferred");
  writeDistFixture(notDeferred, { agGridInHome: false, agGridDeferred: false });
  const deferred = runBundleGuard(notDeferred);
  assert.notEqual(
    deferred.code,
    0,
    "the bundle guard passed a build in which no deferred chunk carries an AG " +
      "Grid runtime marker. Its negative half cannot tell that from a clean " +
      "build, so the rule would report clean forever.",
  );
  assert.ok(
    /AG Grid runtime marker/.test(deferred.text),
    `the guard failed without naming the missing AG Grid marker:\n${deferred.text}`,
  );

  rmSync(base, { recursive: true, force: true });
});

test("the bundle guard's react-grid-layout rule fires on both of its own violations", () => {
  const base = mkdtempSync(join(tmpdir(), "aeq-bundle-guard-rgl-"));

  // The control again, for the same reason: a correct build must pass, or the two
  // convictions below prove only that the guard rejects everything.
  const good = join(base, "good");
  writeDistFixture(good, {
    agGridInHome: false,
    agGridDeferred: true,
    gridLayoutInHome: false,
    gridLayoutDeferred: true,
  });
  const clean = runBundleGuard(good);
  assert.equal(
    clean.code,
    0,
    `the bundle guard rejected a correct build, so its verdicts mean nothing:\n${clean.text}`,
  );

  // Violation one, the negative half: the builder's grid reaches the Command
  // Center. It has no chart and no grid on it, so the whole point of loading the
  // library through `dynamic(..., { ssr: false })` is that it never ships there.
  const inHome = join(base, "in-home");
  writeDistFixture(inHome, {
    agGridInHome: false,
    agGridDeferred: true,
    gridLayoutInHome: true,
    gridLayoutDeferred: true,
  });
  const home = runBundleGuard(inHome);
  assert.notEqual(
    home.code,
    0,
    "the bundle guard passed a build with a react-grid-layout runtime marker in " +
      "the Command Center's initial entry graph",
  );
  assert.ok(
    /react-grid-layout/.test(home.text),
    `the guard failed without naming react-grid-layout:\n${home.text}`,
  );

  // Violation two, the positive half: react-grid-layout is in NO deferred chunk of
  // the builder route — which is what a STATIC import looks like, and what the
  // first draft of the builder actually did (one constant imported from the canvas
  // module put the library in `/dashboards/new`'s initial bundle). The negative
  // half above passes just as happily in that state, because it only watches the
  // Command Center.
  const notDeferred = join(base, "not-deferred");
  writeDistFixture(notDeferred, {
    agGridInHome: false,
    agGridDeferred: true,
    gridLayoutInHome: false,
    gridLayoutDeferred: false,
  });
  const deferred = runBundleGuard(notDeferred);
  assert.notEqual(
    deferred.code,
    0,
    "the bundle guard passed a build in which no deferred chunk of the builder " +
      "route carries a react-grid-layout runtime marker. Its negative half " +
      "cannot tell that from a clean build, so the rule would report clean " +
      "forever.",
  );
  assert.ok(
    /react-grid-layout runtime marker/.test(deferred.text),
    `the guard failed without naming the missing react-grid-layout marker:\n${deferred.text}`,
  );

  rmSync(base, { recursive: true, force: true });
});

test("the bundle guard's Command Center chart rule fires on both of its own violations", () => {
  const base = mkdtempSync(join(tmpdir(), "aeq-bundle-guard-chart-"));

  // The control: a build where the chart is deferred AND its chunk is free of the
  // charting runtime. Without this, the two convictions below would prove only
  // that the guard rejects everything it is shown.
  const good = join(base, "good");
  writeDistFixture(good, { agGridInHome: false, agGridDeferred: true });
  const clean = runBundleGuard(good);
  assert.equal(
    clean.code,
    0,
    `the bundle guard rejected a correct build, so its verdicts mean nothing:\n${clean.text}`,
  );

  // Violation one: the chart is no longer in the deferred chunk at all. Every
  // NEGATIVE assertion in the guard passes happily in that state — a route with
  // no chart has no chart runtime in its entry graph — which is exactly why this
  // half has to exist.
  const notDeferred = join(base, "chart-gone");
  writeDistFixture(notDeferred, {
    agGridInHome: false,
    agGridDeferred: true,
    chartDeferred: false,
  });
  const gone = runBundleGuard(notDeferred);
  assert.notEqual(
    gone.code,
    0,
    "the bundle guard passed a build whose Command Center deferred chunk no " +
      "longer contains the chart. Its negative halves cannot tell that from a " +
      "clean build, so the rule would report clean forever.",
  );
  assert.ok(
    /Drawing the chart/.test(gone.text),
    `the guard failed without naming the missing chart boundary:\n${gone.text}`,
  );

  // Violation two: the chart chunk carries the ECharts runtime, i.e. the SECOND
  // deferral inside components/bi/EChart.tsx stopped happening. The Command
  // Center's entry graph is still clean, so nothing else in the guard notices.
  const runtimeInChart = join(base, "runtime-in-chart");
  writeDistFixture(runtimeInChart, {
    agGridInHome: false,
    agGridDeferred: true,
    chartRuntimeInChartChunk: true,
  });
  const carried = runBundleGuard(runtimeInChart);
  assert.notEqual(
    carried.code,
    0,
    "the bundle guard passed a build whose Command Center chart chunk bundles " +
      "the ECharts runtime. The chart would then cost the whole library the " +
      "moment it scrolls into view, which is what the second deferral prevents.",
  );
  assert.ok(
    /carries a charting runtime/.test(carried.text),
    `the guard failed without naming the runtime in the chart chunk:\n${carried.text}`,
  );

  rmSync(base, { recursive: true, force: true });
});

test("the bundle guard carries no stale claim about AG Grid", () => {
  const guard = readFileSync(join(ROOT, GUARD), "utf8");
  assert.equal(
    /not a dependency\s*(?:\n\s*\*)?\s*yet/.test(guard),
    false,
    "The comment saying AG Grid is not a dependency yet is stale — it is one " +
      "now (ag-grid-community 36.2.0). A stale justification beside working " +
      "code is how the next reader concludes the rule is aspirational.",
  );
});

// --- 4. one palette, one formatter ------------------------------------------

test("the grid is themed from the same resolved tokens the charts use", () => {
  assert.ok(
    /from\s+["']\.\/echartsTheme["']/.test(canvas),
    `${CANVAS} must take its palette from ./echartsTheme, which resolves every ` +
      "CSS custom property once. Two resolutions of one token let a chart and a " +
      "grid on the same screen disagree about the brand.",
  );
  assert.ok(
    /themeQuartz\.withParams\(/.test(canvas),
    `${CANVAS} must theme the grid with themeQuartz.withParams (D-030).`,
  );
  assert.equal(
    rule("a colour literal is written into the grid theme").pattern.test(canvas),
    false,
    `${CANVAS} must carry no colour literal; every colour is a resolved token.`,
  );
});

test("the grid formats no value of its own", () => {
  assert.ok(
    /from\s+["']\.\/gridCells["']/.test(canvas),
    `${CANVAS} must format cells through ./gridCells.`,
  );
  const cells = code("components/bi/gridCells.ts");
  assert.ok(
    /from\s+["']\.\/result["']/.test(cells),
    "gridCells.ts must delegate to ./result, the one module that turns a " +
      "catalogue value type into text. A grid with its own formatter would " +
      "eventually disagree with the chart beside it about one member.",
  );
  for (const format of ["fmtCurrency", "fmtPct", "fmtNum"]) {
    assert.equal(
      new RegExp(`\\b${format}\\b`).test(cells),
      false,
      `gridCells.ts must not call ${format} itself — ./result decides units.`,
    );
  }
});

test("every value type the catalogue can emit is rendered on its own terms", () => {
  const result = code("components/bi/result.ts");
  assert.deepEqual(
    offenders(
      "display code still branches on the retired `ratio` value type",
      appSource,
    ),
    [],
    "`ratio` was retired from the catalogue for `fraction` / `index` / " +
      "`duration_years`. A branch on it can never match, so those columns fall " +
      "through and render raw.",
  );
  for (const format of [
    "amount",
    "pct",
    "fraction",
    "index",
    "duration_years",
    "text",
    "date",
    "flag",
  ]) {
    assert.ok(
      new RegExp(`format === "${format}"`).test(result),
      `components/bi/result.ts must render ${format} on its own terms.`,
    );
  }
  // `fraction` is a proportion of one and is stated multiplied by a hundred, in
  // the browser exactly as `app/services/bi/exports/context.py` and the insights
  // layer state it. A member whose figure differs between the screen and the
  // audit twin is unciteable.
  assert.ok(
    /format === "fraction"\)\s*return fmtPct\(parsed \* FRACTION_SCALE\)/.test(
      result,
    ),
    "components/bi/result.ts must show a `fraction` multiplied by a hundred " +
      "with a percent sign.",
  );
  assert.ok(
    /const FRACTION_SCALE = 100;/.test(result),
    "The fraction scale must be a hundred, matching the server's own.",
  );
});

test("truncation is stated, not implied", () => {
  assert.ok(
    /truncationNotice\(/.test(canvas),
    `${CANVAS} must show the truncation notice — a silently truncated grid is ` +
      "a wrong answer, because the reader believes they see the whole book.",
  );
  assert.ok(
    /page\.truncated|next\.truncated/.test(canvas),
    `${CANVAS} must read the server's own truncation flag.`,
  );
});

test("the page size is the server's cap, not a number chosen here", () => {
  assert.deepEqual(
    offenders(
      "the grid page size is hardcoded instead of read from the server",
      appSource,
    ),
    [],
    "The grid asks for one page with no end row, which the server answers at " +
      "its own cap, and pages at that size.",
  );
  assert.ok(
    /startRow:\s*0\s*\}/.test(canvas),
    `${CANVAS} must open with a page that names no end row, so the server's ` +
      "cap is what comes back.",
  );
});

// ---------------------------------------------------------------------------
// The self-proofs. Everything above can report clean while proving nothing if a
// pattern has stopped matching. Each rule must convict its own known violation,
// and must acquit correct code so it cannot pass by matching everything.
// ---------------------------------------------------------------------------

test("every pattern rule still convicts a known violation of itself", () => {
  const inert = RULES.filter(
    (entry) => !entry.pattern.test(entry.convicts),
  ).map((entry) => entry.id);
  assert.deepEqual(
    inert,
    [],
    "These rules did NOT match their own known violation, so they police " +
      `nothing and every clean result above is meaningless: ${inert.join(", ")}`,
  );
});

test("no pattern rule convicts correct code", () => {
  for (const entry of RULES) {
    assert.ok(
      entry.acquits.length > 0,
      `rule ${entry.id} needs at least one sample of correct code, or its ` +
        "self-proof is satisfied by a pattern that matches everything",
    );
    for (const line of entry.acquits) {
      assert.equal(
        entry.pattern.test(line),
        false,
        `rule ${entry.id} matched code that is correct, so it will be disabled ` +
          `by whoever hits the false positive: ${line}`,
      );
    }
  }
});

if (failures > 0) {
  console.error(`\n${failures} grid property (or properties) failed.`);
  process.exit(1);
}
console.log(
  `pivotGrid.test.ts: the grid's own export is off, Community only, deferred, ` +
    `painted from one palette; ${RULES.length} rules each proven to convict ` +
    "their own violation.",
);
