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

const GUARD = join("scripts", "assert-home-route-bundle.mjs");

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
      'import PivotGrid from "@/features/bi/components/PivotGrid";',
      'import EChart from "./EChart";',
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
    const relative = file
      .slice(ROOT.length + 1)
      .split("\\")
      .join("/");
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
  return !/\.(?:test|spec)\.tsx?$/.test(relative);
}

// --- 1. the built-in export is off ------------------------------------------

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

test("the import scanner rejects entrances outside the grid wrapper and canvas", () => {
  assert.deepEqual(
    offenders(
      "the grid runtime is imported outside its one entrance",
      (relative) =>
        appSource(relative) &&
        ![
          "features/bi/components/PivotGrid.tsx",
          "features/bi/components/PivotGridCanvas.tsx",
        ].includes(relative),
    ),
    [],
  );
});

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
     * — i.e. `features/bi/components/EChart.tsx` stopped deferring the canvas, so the home
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
    filler +
      (options.agGridDeferred ? '"ag-root-wrapper";"ag-header-cell"' : ""),
  );
  const builderChunk = chunk(
    "builder.js",
    filler +
      (options.gridLayoutDeferred !== false
        ? '"react-grid-item";"react-resizable-handle"'
        : ""),
  );

  const manifest = (route: string, entries: Record<string, string[]>) => {
    const file = join(
      root,
      "server",
      "app",
      `${route}_client-reference-manifest.js`,
    );
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

function runBundleGuard(
  distDir: string,
): Readonly<{ code: number; text: string }> {
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
  // deferral inside features/bi/components/EChart.tsx stopped happening. The Command
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

// --- 4. one palette, one formatter ------------------------------------------

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
  `pivotGrid.test.ts: bundle guards and ${RULES.length} boundary rules each proven to convict ` +
    "their own violation.",
);
