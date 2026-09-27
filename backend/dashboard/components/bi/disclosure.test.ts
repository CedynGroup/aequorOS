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

// --- 3. a refused PACK widget is given nothing but its geometry --------------
//
// The server sends a refusal as an id, a layout item and `access: "restricted"`,
// and nothing else. The property that keeps it that way on the client is a TYPE:
// the `restricted` variant of `BiPackWidgetView` declares no field that could
// hold a title, a caption, a measure, a dimension, a filter or a figure, so the
// canvas cannot read one on that branch even by mistake — the compiler refuses
// it. These assertions pin the type and the two places that consume it, because
// a widened variant would compile and would leak.

const types = code("components/bi/types.ts");
const restrictedVariant =
  /\|\s*Readonly<\{\s*state:\s*"restricted";([^}]*)\}>/.exec(types)?.[1] ?? "";
assert.ok(
  restrictedVariant.length > 0,
  "could not read the `restricted` variant of BiPackWidgetView from types.ts",
);
assert.deepEqual(
  [...restrictedVariant.matchAll(/(\w+)\??:/g)].map((match) => match[1]).sort(),
  ["id", "layout"],
  "The refused variant of BiPackWidgetView may carry only its id and its " +
    "geometry. Any other field can hold what the reader was refused.",
);

const dashboards = code("components/bi/dashboards.ts");
const refusalReturn =
  /access === "restricted"\)\s*\{\s*return \{([^}]*)\};/.exec(
    dashboards,
  )?.[1] ?? "";
assert.ok(
  refusalReturn.length > 0,
  "components/bi/dashboards.ts must decide a refusal explicitly",
);
assert.deepEqual(
  // The leading identifier of each comma-separated entry, which is the property
  // being set — never a value read on the right-hand side of one.
  refusalReturn
    .split(",")
    .map((entry) => /^\s*(\w+)/.exec(entry)?.[1] ?? "")
    .filter((name) => name.length > 0)
    .sort(),
  ["id", "layout", "state"],
  "A refused pack widget must be built from its id, its geometry and its state " +
    "alone.",
);
// And the refusal is decided FIRST, before the branch that would read a query,
// a panel or a dataset off the same payload.
const refusalAt = dashboards.indexOf('access === "restricted"');
for (const later of ["widget.query", "widget.panel", "widget.needsData"]) {
  assert.ok(
    refusalAt > 0 && refusalAt < dashboards.indexOf(later),
    `components/bi/dashboards.ts must decide a refusal before it reads ${later}.`,
  );
}

const canvas = code("components/bi/DashboardCanvas.tsx");
const restrictedTag = /<RestrictedWidget([^/>]*)\/>/.exec(canvas)?.[1] ?? "";
assert.ok(
  restrictedTag.length > 0,
  "components/bi/DashboardCanvas.tsx must render RestrictedWidget for a refusal",
);
assert.deepEqual(
  [...restrictedTag.matchAll(/(\w+)=/g)].map((match) => match[1]).sort(),
  ["height"],
  "A refused widget on the canvas may be given its height and nothing else.",
);

// --- 4. no pack is defined in the browser -----------------------------------
//
// A pack binds widgets to catalogue measures, declares each widget's window
// relative to the reporting date, and is validated server-side. A copy here would
// be a second definition, free to disagree with the one the exports, the
// scheduled reports and the commentary read — so the client reads the pack routes
// and holds no pack of its own.

for (const hook of ["useBiPacks", "useBiPack"]) {
  assert.ok(
    dashboards.includes(hook),
    `components/bi/dashboards.ts must read the pack routes through ${hook}.`,
  );
}
for (const literal of [
  "alco",
  "board",
  "branch_network",
  "compliance",
  "cro",
  "finance",
  "balance_sheet_mix",
  "positions.balance_rc",
  "loans.npl_ratio_pct",
]) {
  assert.equal(
    new RegExp(`["'\`]${literal}["'\`]`).test(dashboards),
    false,
    `components/bi/dashboards.ts must not name "${literal}". A pack, a widget ` +
      "or a measure written down here is a second definition of it.",
  );
}

// --- 5. an empty insight strip states which emptiness it is ------------------
//
// "No statements" has two meanings and only the payload separates them: the
// figures were read and nothing moved materially, or nothing was computed at all.
// Rendering the first sentence in the second case is a claim about the
// institution's figures that the platform never made.

const strip = code("components/bi/InsightStrip.tsx");
assert.ok(
  /measuresRead/.test(strip),
  "InsightStrip must decide its empty state on `measuresRead`: an empty list " +
    "over zero measures read is not 'nothing stands out'.",
);
assert.ok(
  /Nothing stands out for this reporting date\./.test(strip) &&
    /No analytics figures have been computed for this reporting date yet/.test(
      strip,
    ),
  "InsightStrip must carry both empty sentences, so the honest one can be shown.",
);
assert.ok(
  /isBiAccessDenied/.test(strip),
  "InsightStrip must render the route's refusal as a refusal. The route answers " +
    "403 rather than an empty list when nothing was readable.",
);

// --- 6. every way out of a BI view is the governed one -----------------------

const exportActions = code("components/bi/ExportActions.tsx");
for (const format of ['"csv"', '"xlsx"', '"pdf"']) {
  assert.ok(
    exportActions.includes(format),
    `ExportActions must offer ${format} through the governed route.`,
  );
}
assert.ok(
  /useBiExport/.test(exportActions),
  "ExportActions must run every artifact through `POST …/bi/export`, which " +
    "authorizes, audits and watermarks what it releases.",
);
assert.ok(
  /printExportOption\(\)/.test(exportActions),
  "ExportActions must keep the browser print option: it is available wherever a " +
    "view is, and it releases nothing the reader is not already looking at.",
);
assert.ok(
  /An organization owner can grant it\./.test(exportActions),
  "A reader without export authority must be told so in the same words a " +
    "refused widget uses — naming no field.",
);

// --- 7. the drill action is reachable on both answering surfaces -------------
//
// `WidgetRenderer` adds the "Rows behind" column only when it is given the query
// AS SUBMITTED. Omit it and the resolver, the destinations and their tests are all
// correct and none of them is reachable in a browser.

for (const surface of [
  "components/bi/DashboardCanvas.tsx",
  "app/(app)/explore/page.tsx",
]) {
  // Up to the self-closing bracket ON ITS OWN LINE, which is this tag's. A lazy
  // match to the first `/>` would stop inside a nested element passed as a prop.
  const tag = /<WidgetRenderer\b([\s\S]*?)\n\s*\/>/.exec(code(surface))?.[1];
  assert.ok(tag, `${surface} must render WidgetRenderer`);
  // A prop of the tag itself — a line whose first token is `query=` — never a
  // `query={…}` nested inside another prop's expression.
  assert.ok(
    tag!.split("\n").some((line) => line.trim().startsWith("query={")),
    `${surface} must pass the submitted query to WidgetRenderer as its own ` +
      "prop, or the drill action never renders.",
  );
}

console.log(
  "disclosure.test.ts: BI refusals name nothing, absences never render as zero, " +
    "no pack is defined in the browser, and every way out is the governed one.",
);
