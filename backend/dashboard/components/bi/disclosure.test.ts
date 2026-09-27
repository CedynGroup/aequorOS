/**
 * Two properties of the BI widget states that must not erode.
 *
 * 1. A DENIAL NAMES NOTHING. The 403 body from `…/bi/query` lists the member
 *    ids and labels the decision refused, because an operator needs them to
 *    write the grant. A READER must not be shown them: "you may not see
 *    Largest single-name share" tells them the institution tracks one, and on a
 *    filtered view the filter is usually the sensitive half. So
 *    `RestrictedWidget` is given no measure, no dimension, no filter and no
 *    figure, and this test reads its source to prove it still takes none.
 *
 * 2. AN ABSENCE IS NEVER A ZERO. A widget with no rows renders
 *    `NeedsDataWidget`, which says which dataset is missing and links to the
 *    template that accepts it. The renderer must reach that state BEFORE it
 *    reaches a chart, because a chart handed an empty series draws a flat line
 *    on the baseline and a reader cannot tell that from a real run of zeros.
 *
 * Source inspection rather than rendering: this package has no component-test
 * harness, and both properties are about what a file is ALLOWED to reference —
 * which is exactly what a source scan can decide. Comments are stripped first,
 * so prose about the defect is not read as the defect.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

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
  const source = readFileSync(join(ROOT, relativePath), "utf8");
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
}

// --- 1. the denial names nothing --------------------------------------------

const restricted = code("components/bi/RestrictedWidget.tsx");

const FORBIDDEN_IN_A_DENIAL: readonly [RegExp, string][] = [
  [/denied/i, "the denied member ids or labels"],
  [/\bmeasure/i, "the measure that was refused"],
  [/\bdimension/i, "the dimension that was refused"],
  [/\bfilter/i, "the filter the view was narrowed by"],
  [/\brows?\b/i, "rows"],
  [/\bvalues?\b/i, "a figure"],
  [/\bresult\b/i, "the query result"],
  [/\bquery\b/i, "the query"],
  [/\btitle\b/i, "the widget's own title"],
];

for (const [pattern, what] of FORBIDDEN_IN_A_DENIAL) {
  assert.equal(
    pattern.test(restricted),
    false,
    `RestrictedWidget must not reference ${what}: a denial that names what was hidden is a disclosure.`,
  );
}

// It must also be unable to receive one: the whole props type is inspected, not
// just the body, so an unused-but-accepted prop still fails.
const restrictedProps = /\}: \{([\s\S]*?)\n\}\)/.exec(restricted)?.[1] ?? "";
assert.ok(
  restrictedProps.length > 0,
  "could not read RestrictedWidget's props type",
);
const acceptedProps = [...restrictedProps.matchAll(/^\s*(\w+)\??:/gm)].map(
  (match) => match[1],
);
assert.deepEqual(
  acceptedProps.sort(),
  ["className", "height"],
  "RestrictedWidget may accept only presentation props. Anything else can carry what was hidden.",
);

// --- 2. an absence is never a zero ------------------------------------------

const needsData = code("components/bi/NeedsDataWidget.tsx");
assert.ok(
  needsData.includes("Needs data: "),
  'NeedsDataWidget must state "Needs data: <dataset>".',
);
assert.ok(
  /dataset\.href/.test(needsData),
  "NeedsDataWidget must link to the Data Engine template that accepts the dataset.",
);
assert.equal(
  /EChart|Sparkline|recharts/.test(needsData),
  false,
  "NeedsDataWidget must not draw a chart. An empty chart is a picture of a measurement that never happened.",
);

const renderer = code("components/bi/WidgetRenderer.tsx");
for (const required of [
  "RestrictedWidget",
  "NeedsDataWidget",
  "isBiAccessDenied",
]) {
  assert.ok(
    renderer.includes(required),
    `WidgetRenderer must route through ${required}.`,
  );
}

const needsDataAt = renderer.indexOf("<NeedsDataWidget");
const chartAt = renderer.indexOf("<EChart");
const restrictedAt = renderer.indexOf("<RestrictedWidget");
assert.ok(needsDataAt > 0 && chartAt > 0 && restrictedAt > 0);
assert.ok(
  restrictedAt < needsDataAt,
  "WidgetRenderer must decide a refusal before it decides a data gap.",
);
assert.ok(
  needsDataAt < chartAt,
  "WidgetRenderer must decide a data gap before it draws a chart, or an empty answer plots as a flat line at zero.",
);

// The renderer must not substitute a number for an absent cell.
assert.equal(
  /(?:\?\?|\|\|)\s*0\b/.test(renderer),
  false,
  "WidgetRenderer must not default an absent figure to 0.",
);

// --- the charting runtime has exactly one entrance --------------------------

const chartImporters = [
  "components/bi/WidgetRenderer.tsx",
  "components/bi/EChart.tsx",
  "components/bi/InsightStrip.tsx",
  "components/bi/DashboardCanvas.tsx",
];
for (const file of chartImporters) {
  if (file === "components/bi/EChart.tsx") continue;
  assert.equal(
    /from\s+["'][^"']*EChartCanvas["']/.test(code(file)),
    false,
    `${file} must import ./EChart, never ./EChartCanvas — the dynamic wrapper is what keeps the charting runtime out of the initial bundles.`,
  );
}
assert.ok(
  /dynamic\(\s*\(\)\s*=>\s*import\(["']\.\/EChartCanvas["']\)/.test(
    code("components/bi/EChart.tsx"),
  ),
  "EChart must load the canvas through next/dynamic.",
);
assert.ok(
  /ssr:\s*false/.test(code("components/bi/EChart.tsx")),
  "EChart must load the canvas with ssr:false.",
);
assert.equal(
  /EChart/.test(code("components/bi/InsightStrip.tsx")),
  false,
  "InsightStrip must stay on the SVG Sparkline: it is meant for the Command Center, whose bundle the home-route guard protects.",
);

console.log(
  "disclosure.test.ts: BI refusals name nothing, absences never render as zero.",
);
