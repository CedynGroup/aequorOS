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
 * Audit A360-6 added three more (sections 16–18 below):
 *
 * 16. A WIDGET THAT DOES NOT KNOW WHY IT IS EMPTY SAYS SO. Every empty answer
 *     used to be blamed on a positions upload through a default dataset key.
 * 17. A REFUSAL IS THE SERVER'S DECISION. Only a grant denial is paraphrased;
 *     every other 403 renders the server's own sentence, and the provenance
 *     drawer prints no wire token as prose.
 * 18. A NULL IS NEVER A ZERO OFF THE BI SURFACE EITHER: the stress board's
 *     headroom bridge, which the fail-open guard's field list does not cover.
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
import type {
  BiDashboardRead,
  BiPackWidgetRead,
} from "@aequoros/risk-service-api";
import { namedDataset } from "./labels";
import {
  BI_GRANT_DENIAL_CODES,
  isGrantDenial,
  refusalSentence,
  restrictedWidgetCount,
} from "./refusal";
import { unmeasuredSeriesLabel } from "./result";
import {
  BUILDER_COLUMNS,
  addWidget,
  applyLayout,
  authoredWidget,
  draftFromDashboard,
  draftProblems,
  draftSpec,
  emptyDraft,
  figureWidget,
  removeWidget,
  savedCertification,
  isSavedDashboardId,
  layoutsEqual,
  sizeWidget,
  widgetIdFrom,
} from "./builder";

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
const refusedAt = renderer.indexOf("<RefusedWidget");
assert.ok(needsDataAt > 0 && chartAt > 0 && restrictedAt > 0 && refusedAt > 0);
assert.ok(
  restrictedAt < refusedAt && refusedAt < needsDataAt,
  "WidgetRenderer must decide a grant denial, then any other refusal, before it decides a data gap.",
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

// --- 8. a saved dashboard is a copy of a pack, and its badge is the server's ----
//
// The badge vocabulary is TOTAL over the three values the wire declares, so there
// is no path on which a document is shown with a standing the server did not
// state. `platform_certified` cannot be stored on a tenant row (the CHECK
// constraint excludes it), but it is mapped rather than defaulted, because a
// mapping with a fallback is how a personal dashboard would come to wear a
// platform badge if the vocabulary ever widened.

assert.equal(savedCertification("platform_certified"), "platform");
assert.equal(savedCertification("bank_certified"), "bank");
assert.equal(savedCertification("personal"), "personal");

// The two surfaces are told apart by the two ROUTES' own path types, not by
// content: a saved dashboard's id is a UUID, a certified pack's key is a slug.
assert.equal(isSavedDashboardId("3f6b1d1e-4c5a-4f2b-9e7d-0a1b2c3d4e5f"), true);
for (const packKey of ["alco", "board", "branch_network", "cro"]) {
  assert.equal(
    isSavedDashboardId(packKey),
    false,
    `${packKey} is a certified pack key and must not be asked of the saved-dashboard route`,
  );
}

// --- 9. a canvas is saved as a whole, so it must be RESTATABLE as a whole ------
//
// `PUT …/bi/dashboards/{id}` replaces the canvas, so an edit has to write down
// every view's authored definition — including the ones being left alone. Three
// shapes cannot be written down from what the read route serves, and each must
// REFUSE rather than approximate. Approximating any of them edits somebody's
// document without telling them: dropping a refused view deletes it, and choosing
// a relative window turns a year's total into a month's under the heading its
// author wrote (on 31 January, month-to-date, quarter-to-date and year-to-date
// all resolve to a window starting 1 January).

const asOfWidget: BiPackWidgetRead = {
  id: "deposits_by_product",
  access: "granted",
  kind: "bar",
  title: "Deposits by product",
  caption: "By balance",
  layout: { i: "deposits_by_product", x: 0, y: 0, w: 6, h: 4 },
  query: {
    measures: ["deposits.balance_rc"],
    dimensions: ["deposit.product"],
    time: { asOf: "2026-08-31" },
  },
};

const restatedWidget = authoredWidget(asOfWidget);
assert.ok(
  "widget" in restatedWidget,
  "a granted view reading one reporting date must be restatable",
);
if ("widget" in restatedWidget) {
  assert.equal(restatedWidget.widget.query?.window, "as_of");
  assert.equal(restatedWidget.widget.query?.compare, "none");
  assert.deepEqual(restatedWidget.widget.query?.measures, [
    "deposits.balance_rc",
  ]);
  assert.equal(restatedWidget.widget.title, "Deposits by product");
  // The resolved date must NOT survive into the authored definition: the window
  // is relative to whoever opens the dashboard, so a date written here would
  // freeze the view at the date one person happened to be reading.
  assert.equal(
    JSON.stringify(restatedWidget.widget).includes("2026-08-31"),
    false,
    "a restated view must carry no reporting date of its own",
  );
}

assert.deepEqual(
  authoredWidget({
    id: "npl",
    access: "restricted",
    layout: { i: "npl", x: 0, y: 0, w: 6, h: 4 },
  }),
  { unrestatable: "refused" },
  "a refused view carries no definition, so a canvas holding one cannot be restated",
);

assert.deepEqual(
  authoredWidget({
    ...asOfWidget,
    query: {
      measures: ["deposits.balance_rc"],
      time: {
        range: {
          start: new Date("2026-01-01T00:00:00.000Z"),
          end: new Date("2026-08-31T00:00:00.000Z"),
        },
      },
    },
  }),
  { unrestatable: "relative_window" },
  "a resolved period cannot be turned back into the relative window it was authored with",
);

assert.deepEqual(
  authoredWidget({
    ...asOfWidget,
    query: {
      measures: ["deposits.balance_rc"],
      time: { asOf: "2026-08-31", compareTo: "2026-07-31" },
    },
  }),
  { unrestatable: "relative_window" },
  'a comparison arrives as a date, not as "the prior month", so it cannot be restated either',
);

assert.deepEqual(
  authoredWidget({ ...asOfWidget, kind: undefined }),
  { unrestatable: "incomplete" },
  "substituting a chart kind would change what a view draws under its own heading",
);

// A whole canvas is all-or-nothing: one unrestatable view refuses the document.
const storedDashboard: BiDashboardRead = {
  id: "3f6b1d1e-4c5a-4f2b-9e7d-0a1b2c3d4e5f",
  title: "Weekly funding review",
  description: "",
  ownerUserId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  ownerDisplayName: null,
  ownedByCaller: true,
  visibility: "private",
  visibilityRole: null,
  badge: "personal",
  version: 1,
  sourcePack: null,
  asOf: new Date("2026-08-31T00:00:00.000Z"),
  access: "granted",
  message: "Your access covers every figure this dashboard reads.",
  widgets: [asOfWidget],
  catalogueVersion: "1",
  createdAt: new Date("2026-08-31T00:00:00.000Z"),
  updatedAt: new Date("2026-08-31T00:00:00.000Z"),
};

const restatedCanvas = draftFromDashboard(storedDashboard);
assert.ok(
  "draft" in restatedCanvas,
  "a canvas of as-of views must be restatable",
);
if ("draft" in restatedCanvas) {
  assert.equal(restatedCanvas.draft.title, "Weekly funding review");
  assert.deepEqual(restatedCanvas.draft.layout, [
    { i: "deposits_by_product", x: 0, y: 0, w: 6, h: 4 },
  ]);
}
assert.deepEqual(
  draftFromDashboard({
    ...storedDashboard,
    widgets: [
      asOfWidget,
      {
        id: "npl",
        access: "restricted",
        layout: { i: "npl", x: 6, y: 0, w: 6, h: 4 },
      },
    ],
  }),
  { unrestatable: "refused" },
  "one refused view must refuse the whole canvas — a partial restatement is a dashboard with a view missing",
);

// --- 10. the draft's own rules ------------------------------------------------

// A widget id is a KEY matching the server's `^[a-z][a-z0-9_]*$`, folded from the
// heading and made unique against what is already placed.
const KEY_SHAPE = /^[a-z][a-z0-9_]*$/;
for (const heading of [
  "Deposits by product",
  "Loans — 90+ days",
  "3-month gap",
  "…",
]) {
  assert.match(
    widgetIdFrom(heading, []),
    KEY_SHAPE,
    `"${heading}" must fold to a key the server accepts`,
  );
}
assert.equal(widgetIdFrom("Deposits", ["deposits"]), "deposits_2");
assert.equal(
  widgetIdFrom("Deposits", ["deposits", "deposits_2"]),
  "deposits_3",
);

// A view carries its authored definition, so editing the figures on one cannot
// silently drop a filter it was published with and the reader never saw.
const carried = figureWidget(
  {
    id: "top_depositors",
    title: "Top depositors",
    caption: "",
    kind: "table",
    measures: ["deposits.balance_rc"],
    dimensions: ["deposit.product"],
  },
  {
    id: "top_depositors",
    kind: "table",
    title: "Top depositors",
    query: {
      measures: ["deposits.balance_rc"],
      window: "as_of",
      subtotals: true,
      filters: [{ member: "deposit.product", op: "in", values: ["term"] }],
    },
  },
);
assert.deepEqual(
  carried.query?.filters,
  [{ member: "deposit.product", op: "in", values: ["term"] }],
  "editing a view's figures must carry its filters through — the form has no control for them",
);
assert.equal(carried.query?.subtotals, true);
assert.equal(
  carried.query?.window,
  "as_of",
  "this surface authors one reporting date and no other window",
);

// Adding, placing and removing keep the widget list and the layout in step,
// because the server refuses a canvas whose layout does not position every
// widget and nothing else.
let draft = emptyDraft();
assert.deepEqual(
  draftProblems(draft).length > 0,
  true,
  "an unnamed, empty canvas cannot be saved",
);
draft = { ...draft, title: "Weekly funding review" };
assert.ok(
  draftProblems(draft).some((problem) => /at least one view/.test(problem)),
  "a canvas with nothing on it must say so before the server does",
);
draft = addWidget(
  draft,
  figureWidget({
    id: "a",
    title: "A",
    caption: "",
    kind: "bar",
    measures: ["deposits.balance_rc"],
    dimensions: [],
  }),
);
draft = addWidget(
  draft,
  figureWidget({
    id: "b",
    title: "B",
    caption: "",
    kind: "bar",
    measures: ["deposits.balance_rc"],
    dimensions: [],
  }),
);
assert.deepEqual(draftProblems(draft), []);
assert.equal(draft.layout.length, 2);
assert.equal(draft.layout[1].y, 4, "a new view is placed below what is there");
assert.deepEqual(
  draftSpec(draft)
    .layout.map((item) => item.i)
    .sort(),
  draftSpec(draft)
    .widgets.map((widget) => widget.id)
    .sort(),
  "the layout must position every widget and nothing else",
);
draft = removeWidget(draft, "a");
assert.deepEqual(
  draft.layout.map((item) => item.i),
  ["b"],
);
assert.deepEqual(
  draft.widgets.map((widget) => widget.id),
  ["b"],
);

// A size the server would refuse is clamped here rather than sent.
draft = sizeWidget(draft, "b", { w: 99, h: 0 });
assert.equal(draft.layout[0].w, BUILDER_COLUMNS);
assert.equal(draft.layout[0].h, 1);

// The grid reports a layout on mount as well as after a drag, so the geometry is
// compared before state moves — otherwise the drag never settles. And a widget
// the library did not report keeps the place it had, because dropping it would
// delete the view.
assert.equal(layoutsEqual(draft.layout, [...draft.layout]), true);
assert.equal(
  layoutsEqual(draft.layout, [{ i: "b", x: 1, y: 0, w: 12, h: 1 }]),
  false,
);
const twoUp = applyLayout(
  addWidget(
    draft,
    figureWidget({
      id: "c",
      title: "C",
      caption: "",
      kind: "bar",
      measures: ["deposits.balance_rc"],
      dimensions: [],
    }),
  ),
  [{ i: "b", x: 3, y: 1, w: 4, h: 2 }],
);
assert.deepEqual(
  twoUp.layout.map((item) => item.i).sort(),
  ["b", "c"],
  "a widget the grid did not report keeps its place rather than being dropped",
);
assert.equal(
  applyLayout(draft, [{ i: "ghost", x: 0, y: 0, w: 1, h: 1 }]).layout.some(
    (item) => item.i === "ghost",
  ),
  false,
  "a layout item naming no widget must not be accepted",
);

// --- 11. the builder's grid runtime has exactly one entrance ------------------
//
// react-grid-layout must be reached only through the dynamic wrapper, or it lands
// in an initial bundle. `scripts/assert-home-route-bundle.mjs` asserts both halves
// of that against the real build output; these assertions catch the import before
// a build does, because the failure mode is silent — a page that works, with the
// library shipped to every reader of the Command Center.

const builderSurfaces = [
  "components/bi/DashboardBuilder.tsx",
  "app/(app)/dashboards/new/page.tsx",
  "app/(app)/dashboards/[id]/edit/page.tsx",
];
for (const file of builderSurfaces) {
  assert.equal(
    /from\s+["'][^"']*BuilderGridCanvas["']/.test(code(file)),
    false,
    `${file} must import ./BuilderGrid, never ./BuilderGridCanvas — the dynamic wrapper is what keeps react-grid-layout out of the initial bundles.`,
  );
  assert.equal(
    /from\s+["']react-grid-layout/.test(code(file)),
    false,
    `${file} must not import react-grid-layout directly.`,
  );
}
const builderWrapper = code("components/bi/BuilderGrid.tsx");
assert.ok(
  /dynamic\(\s*\(\)\s*=>\s*import\(["']\.\/BuilderGridCanvas["']\)/.test(
    builderWrapper,
  ),
  "BuilderGrid must load the canvas through next/dynamic.",
);
assert.ok(
  /ssr:\s*false/.test(builderWrapper),
  "BuilderGrid must load the canvas with ssr:false.",
);
// The one file that may touch the library, and the only one.
assert.ok(
  /from\s+["']react-grid-layout["']/.test(
    code("components/bi/BuilderGridCanvas.tsx"),
  ),
  "BuilderGridCanvas must be the file that imports react-grid-layout.",
);

// --- 12. a shared dashboard's refusal is the SAME component and the same words -
//
// A saved dashboard resolves through `packWidgetView`, so the `restricted`
// variant asserted in section 3 covers a shared document too. These assertions
// pin that there is no second adapter and no second refusal tile, because a
// second one is where the title would come back.

const dashboardsModule = code("components/bi/dashboards.ts");
assert.ok(
  /widgets\s*[:=]\s*read\.widgets\.map\(packWidgetView\)/.test(dashboardsModule),
  "a saved dashboard's widgets must go through packWidgetView — the adapter whose refusal variant has nowhere to put a title.",
);
const savedScreen = code("components/bi/SavedDashboardScreen.tsx");
assert.ok(
  /<DashboardCanvas/.test(savedScreen),
  "a saved dashboard must draw through DashboardCanvas, which passes RestrictedWidget only a height.",
);
assert.equal(
  /RestrictedWidget/.test(savedScreen),
  false,
  "a saved dashboard must not render its own refusal tile: the canvas owns that decision.",
);

// --- 13. sharing is explained where it is done -------------------------------
//
// A reader who does not know that sharing grants no access will share a board
// pack believing the recipient sees it. The sentence is on the panel that does
// the sharing, not in a tooltip and not only in a doc comment.

const sharePanel = code("components/bi/SharePanel.tsx");
assert.ok(
  /Sharing a dashboard does not share its figures\./.test(sharePanel),
  "SharePanel must say, in production copy, that sharing does not share figures.",
);
assert.ok(
  /authorized view by view when they open it/.test(sharePanel),
  "SharePanel must say WHY: each view is authorized for the reader who opens it.",
);
assert.equal(
  /\bdenied|deniedMembers|denied_member/.test(sharePanel),
  false,
  "SharePanel must not name a refused field: a denial that names what it hid is a disclosure.",
);

// --- 14. only the owner is offered the controls that only the owner may use ----
//
// The server decides this — reachability first, then ownership — so these
// assertions are about not offering a colleague a button that answers 403.

assert.ok(
  /ownedByCaller/.test(savedScreen),
  "the saved-dashboard screen must gate its owner controls on the server's own ownedByCaller.",
);
// AND THE OWNER'S OWN DELETE MUST NOT LAND ON A NOT-FOUND PAGE. The deleted
// document answers 404 on the next read, which is correct, and that read races
// the navigation away — so the screen has to decide the delete BEFORE it decides
// not-found. Measured, not theoretical: it landed on "This page could not be
// found" until it did.
const deletedAt = savedScreen.indexOf("remove.isPending || remove.isSuccess");
const notFoundAt = savedScreen.indexOf("isBiUnavailable(saved.error)");
assert.ok(
  deletedAt > 0 && notFoundAt > 0 && deletedAt < notFoundAt,
  "the saved-dashboard screen must decide an in-flight or completed delete BEFORE " +
    "it decides not-found, or the owner is shown a 404 for the act they asked " +
    "for. The guard has to open when the delete is SENT: React Query settles the " +
    "invalidation, and therefore the refetched 404, before it marks the mutation " +
    "successful.",
);
const editPage = code("app/(app)/dashboards/[id]/edit/page.tsx");
assert.ok(
  /ownedByCaller === true/.test(editPage),
  "the builder must only mount for the owner the server named.",
);
assert.ok(
  /isSavedDashboardId/.test(editPage) && /notFound\(\)/.test(editPage),
  "a certified pack key must be not-found on the edit route: a pack is never edited in place.",
);

// --- 15. a calculated measure's refusal names the FIGURE, not the formula -------
//
// A formula is authorized as the figures its text names, so the refusal a reader
// sees has to be about a figure. Three ways that could go wrong, all source
// properties:
//
// (a) the surface could name the measure instead of the figures — unactionable,
//     and untrue about what was refused;
// (b) the editor could decide validity itself, which is a second implementation
//     of `app/domain/bi/expr.py` and is worse than no preview the moment the two
//     disagree;
// (c) the surface could show a REFUSED measure as restricted. It cannot: a
//     measure whose figures a reader's access does not cover is ABSENT from the
//     list (`content.readable_measures`), because its label is authored text that
//     can describe the very figure they were refused. So there must be no refusal
//     tile on this surface, and nothing that counts what was withheld.

const measuresPage = code("app/(app)/explore/measures/page.tsx");
const measureComposer = code("components/bi/MeasureComposer.tsx");
const expressionEditor = code("components/bi/ExpressionEditor.tsx");
const measureReview = code("components/bi/MeasureReview.tsx");
const measuresModule = code("components/bi/measures.ts");

// (a) The refusal goes through the helper that names figures.
assert.ok(
  /refusedFiguresSentence\(refusedFigures\(/.test(measuresPage),
  "the measures surface must word a refusal from the FIGURES the server named, " +
    "preferring its labels — never from the measure's own name.",
);

// (b) No parser anywhere on this surface, and the verdict is the server's.
for (const [file, source] of [
  ["app/(app)/explore/measures/page.tsx", measuresPage],
  ["components/bi/MeasureComposer.tsx", measureComposer],
  ["components/bi/ExpressionEditor.tsx", expressionEditor],
  ["components/bi/measures.ts", measuresModule],
] as const) {
  for (const shape of [
    /function\s+\w*[Tt]okeni[sz]e/,
    /\bnew RegExp\(/,
    /SAFE_DIV\s*\(\s*\[m:/,
  ]) {
    assert.equal(
      shape.test(source),
      false,
      `${file} must not implement or evaluate the formula language (${shape}); ` +
        "the server's own parse is the only verdict, and a client parser that " +
        "disagreed would tell a reader their formula is fine and then refuse it.",
    );
  }
}
assert.ok(
  /verdict\.read\.valid/.test(measuresModule) &&
    /read\.valid/.test(expressionEditor),
  "the editor's verdict must come from the validation route's own `valid`.",
);
assert.ok(
  /useValidateBiMeasureExpression/.test(measuresPage),
  "the measures surface must ask POST …/bi/measures/validation for the verdict.",
);

// (c) There is no refused-measure tile, and nothing counts what was withheld.
for (const [file, source] of [
  ["app/(app)/explore/measures/page.tsx", measuresPage],
  ["components/bi/MeasureReview.tsx", measureReview],
] as const) {
  assert.equal(
    /RestrictedWidget|Access restricted/.test(source),
    false,
    `${file} must not draw a refusal tile: a measure this reader may not read is ` +
      "absent from the list, and a tile is where its name would come back.",
  );
  assert.equal(
    /withheldMeasures|hiddenMeasures|withheld_members|measuresWithheld/.test(
      source,
    ),
    false,
    `${file} must not count what was withheld — the server does not say, and the ` +
      "count itself would disclose that the institution has one.",
  );
}

// AND THE DECISION IS NEVER OFFERED ON ANYTHING BUT THE SERVER'S OWN FLAG.
assert.ok(
  /canDecide:\s*measure\.awaitingCallerDecision === true/.test(measuresModule),
  "the decision control must be gated on the server's `awaiting_caller_decision` " +
    "and nothing else — not a role, not `!ownedByCaller`, not the state alone.",
);
assert.ok(
  /controls\.canDecide &&/.test(measureReview),
  "MeasureReview must gate the checker's half on that decision.",
);
// The proposer's control is absent WITH A REASON on screen, which is the repo's
// convention: a control that vanishes silently reads as a fault.
assert.ok(
  /controls\.proposerNotice/.test(measureReview),
  "the proposer must be told why the decision is not theirs to take.",
);
// A certified measure's delete is withheld WITH A REASON, and the reason is
// rendered — not only computed.
assert.ok(
  /controls\.deleteWithheld !== null/.test(measuresPage),
  "the measures surface must render the reason a delete control is withheld.",
);
assert.ok(
  /controls\.canDelete &&/.test(measuresPage),
  "the delete control must be gated on `canDelete`, which is false above a draft.",
);

// --- 16. a widget that does not know why it is empty says so ------------------
//
// Audit A360 H7. `packWidgetView` gave every query widget whose pack file named
// no `needs_data` the dataset "positions", and `NeedsDataWidget` then asserted a
// CAUSE — "this institution has not supplied positions and balances" — with a
// link to the positions template. Twenty-six pack widgets carry no key,
// including the Board pack's capital adequacy, liquidity coverage and net
// interest margin; a board member opening it on a date with no minted official
// run was told the bank had failed to upload positions it pushes nightly. The
// same default lived in Explore under another name.

// The decision is pure and executable: an absent key names NO dataset.
assert.equal(namedDataset(undefined), null);
assert.equal(namedDataset(null), null);
assert.notEqual(namedDataset("positions"), null);

const dashboardsSource = code("components/bi/dashboards.ts");
assert.equal(
  /DEFAULT_DATASET_KEY|\?\?\s*["'`]positions["'`]/.test(dashboardsSource),
  false,
  "components/bi/dashboards.ts must carry no default dataset: a widget that names " +
    "none does not know why it is empty, and a default asserts a cause.",
);
assert.ok(
  /namedDataset\(widget\.needsData\)/.test(dashboardsSource),
  "components/bi/dashboards.ts must resolve the dataset through namedDataset, the one " +
    "place an absent key is decided.",
);
const exploreSource = code("app/(app)/explore/page.tsx");
assert.equal(
  /EXPLORE_DATASET|data-engine\/positions/.test(exploreSource),
  false,
  "Explore must not send every empty answer to the positions template: a composed " +
    "question's measures may be engine copies or a bank formula.",
);
assert.ok(
  /dataset:\s*null/.test(exploreSource) && /dataset=\{null\}/.test(exploreSource),
  "Explore must state that it names no dataset, on both the summary and the grid.",
);

const needsDataSource = code("components/bi/NeedsDataWidget.tsx");
assert.ok(
  /dataset:\s*BiDatasetRequirement \| null/.test(needsDataSource),
  "NeedsDataWidget must accept a null dataset — the unknown case is a real state.",
);
assert.equal(
  /has not supplied/.test(needsDataSource),
  false,
  "NeedsDataWidget must not assert that the institution has not supplied something: " +
    "the platform cannot know that, and the pack's key only names a dependency.",
);
assert.ok(
  /cannot say why/.test(needsDataSource),
  "NeedsDataWidget must say it does not know why, when no dataset was named.",
);
// The Data Engine template link is offered ONLY under a named dataset.
const templateLinkAt = needsDataSource.indexOf("Open the Data Engine template");
const namedBranchAt = needsDataSource.indexOf("{dataset ? (");
assert.ok(
  namedBranchAt > 0 && templateLinkAt > namedBranchAt,
  "the template link must sit inside the named-dataset branch, never under the unknown case.",
);

// The scoped-reader qualification is WIRED, not merely defined: an empty answer
// under a narrowed scope is not an empty book, and `CoverageEmptyMeaning` had
// zero consumers.
assert.ok(
  /CoverageEmptyMeaning/.test(needsDataSource),
  "NeedsDataWidget must render CoverageEmptyMeaning for a scoped reader's empty answer.",
);
for (const surface of [
  ["app/(app)/explore/page.tsx", /<WidgetRenderer\b([\s\S]*?)\n\s*\/>/],
  ["app/(app)/explore/page.tsx", /<PivotGrid\b([\s\S]*?)\n\s*\/>/],
] as const) {
  const tag = surface[1].exec(code(surface[0]))?.[1] ?? "";
  assert.ok(
    tag.split("\n").some((line) => line.trim().startsWith("coverage={")),
    `${surface[0]} must pass the question's coverage to ${surface[1].source.slice(1, 12)} so its empty state can say what an empty answer means.`,
  );
}

// --- 17. a refusal is the server's decision -----------------------------------
//
// Audit A360-6 M1. `isBiAccessDenied` keyed on `status === 403`, so EVERY refusal
// rendered as "Access restricted — an organization owner can grant it": a staff
// act-as-examiner session, refused read-only, was told to go and ask for a grant.
// The paraphrase is justified for a grant denial alone, whose body names the
// refused members; every other 403 carries the server's own sentence.

assert.deepEqual(
  [...BI_GRANT_DENIAL_CODES].sort(),
  ["bi_authorization_denied", "bi_export_denied", "bi_insights_authorization_denied"],
  "the paraphrased family is exactly the routes that re-run the grant decision",
);
assert.equal(isGrantDenial({ status: 403, errorCode: "bi_authorization_denied" }), true);
assert.equal(
  isGrantDenial({ status: 403, errorCode: "bi_human_principal_required" }),
  false,
  "an impersonated session's refusal is not a grant denial",
);
assert.equal(
  isGrantDenial({ status: 403, errorCode: null, message: "Impersonation sessions are read-only." }),
  false,
  "a plain-string 403 (deps.py) is not a grant denial",
);
assert.equal(isGrantDenial({ status: 404, errorCode: "bi_authorization_denied" }), false);
assert.equal(isGrantDenial({ status: 403, errorCode: "bi_data_scope_unsupported" }), false);
assert.equal(isGrantDenial({ status: 403, errorCode: "bi_content_owner_only" }), false);

// The server's sentence travels for every other 403, and for nothing else.
const impersonated = {
  status: 403,
  errorCode: "bi_human_principal_required",
  message: "Business intelligence is not available in an impersonated session.",
};
assert.equal(refusalSentence(impersonated), impersonated.message);
assert.equal(
  refusalSentence({ status: 403, errorCode: null, message: "Impersonation sessions are read-only; this action is not permitted." }),
  "Impersonation sessions are read-only; this action is not permitted.",
);
assert.equal(
  refusalSentence({ status: 403, errorCode: "bi_authorization_denied", message: "Your access does not cover every field this view needs. An Org Owner can grant the fields listed here." }),
  null,
  "a grant denial's sentence must NOT travel — its body names the refused members",
);
assert.equal(refusalSentence({ status: 500, message: "boom" }), null);
assert.equal(
  refusalSentence({ status: 403, errorCode: "bi_data_scope_unsupported", message: "  " }),
  null,
  "a 403 with no sentence falls to the ordinary failure panel, never to one composed here",
);

// And the wrapper in lib/api/bi.ts no longer keys on the status alone.
const biClient = code("lib/api/bi.ts");
const accessDeniedBody =
  /export function isBiAccessDenied\([\s\S]*?\n\}/.exec(biClient)?.[0] ?? "";
assert.ok(accessDeniedBody.length > 0, "could not read isBiAccessDenied");
assert.ok(
  /isGrantDenial\(/.test(accessDeniedBody),
  "isBiAccessDenied must decide through isGrantDenial, by code",
);
assert.equal(
  /status === 403/.test(accessDeniedBody),
  false,
  "isBiAccessDenied must not key on the status alone: a 403 is any refusal, and " +
    "only a grant denial may be paraphrased as one.",
);
assert.ok(
  /export function biRefusalSentence\(/.test(biClient),
  "lib/api/bi.ts must expose the server's sentence for a non-grant 403",
);

// Every surface that paraphrases a grant denial also renders the other kind.
for (const file of [
  "components/bi/WidgetRenderer.tsx",
  "components/bi/PivotGridCanvas.tsx",
  "components/bi/ExplainDrawer.tsx",
  "components/bi/InsightStrip.tsx",
]) {
  const source = code(file);
  assert.ok(
    /biRefusalSentence\(/.test(source) && /<RefusedWidget\b/.test(source),
    `${file} paraphrases a grant denial and must therefore also render the server's ` +
      "sentence for every other refusal through RefusedWidget.",
  );
}
// The insights hub paraphrases nothing of its own: its one data read is the
// strip, which renders both kinds above, and its catalogue read fails to the
// ordinary panel. A paraphrase reappearing here would have to join the list.
{
  const hub = code("app/(app)/insights/page.tsx");
  assert.equal(
    /isBiAccessDenied\(|<RestrictedWidget\b/.test(hub),
    false,
    "app/(app)/insights/page.tsx must not paraphrase a grant denial itself — the strip " +
      "owns the refusal rendering; if it does again, add it to the list above.",
  );
}
// RefusedWidget composes nothing: it renders the sentence it is given.
const refused = code("components/bi/RefusedWidget.tsx");
assert.ok(
  /\{sentence\}/.test(refused),
  "RefusedWidget must render the server's sentence verbatim",
);
assert.equal(
  /organization owner can grant/i.test(refused),
  false,
  "RefusedWidget must not compose a grant instruction — that is the paraphrase for a " +
    "different decision.",
);

// The ask page tells the two 404s apart by WHICH request failed. Only the ask
// itself can say the surface is not here; a poll or run 404 is "Question not
// found", after the question was accepted.
const askPage = code("app/(app)/explore/ask/page.tsx");
assert.equal(
  /isBiUnavailable\(failure\)/.test(askPage),
  false,
  "the ask page must not read every 404 as 'Business intelligence is not available'",
);
assert.ok(
  /isBiUnavailable\(ask\.error\)/.test(askPage),
  "the ask page must key BI availability on the ask request alone",
);

// THE PROVENANCE DRAWER PRINTS NO WIRE TOKEN AS PROSE. It read "advisory
// internal · Aggregation ratio_of_sums · Module liq · role over" to a reviewer.
const drawer = code("components/bi/ExplainDrawer.tsx");
assert.equal(
  /\.replace\(\/_\/g/.test(drawer),
  false,
  "ExplainDrawer must not de-underscore a token into prose; every vocabulary has copy in labels.ts",
);
for (const raw of [
  /value=\{data\.measure\.aggregation\}/,
  /value=\{engine\.module\}/,
  /value=\{engine\.tier\}/,
  /value=\{text\(engine\.regime\)\}/,
  /^\s*\{component\.role(?:\.replace\([^)]*\))?\}\s*$/m,
  /\{data\.measure\.advisoryDesignation\}/,
]) {
  assert.equal(
    raw.test(drawer),
    false,
    `ExplainDrawer renders a wire token raw: ${raw.source}`,
  );
}
for (const label of [
  "aggregationLabel(",
  "componentRoleLabel(",
  "engineTierLabel(",
  "moduleLabel(",
  "regimeLabel(",
  "designationLabel(",
]) {
  assert.ok(drawer.includes(label), `ExplainDrawer must render through ${label}`);
}
// The one identifier that IS shown raw is labelled as one and set as code.
assert.ok(
  /Engine metric code[\s\S]*?<code[^>]*>[\s\S]*?\{engine\.metricId\}/.test(drawer),
  "the engine metric id may be shown only as a labelled code, never as a sentence",
);

// A series that measured nothing says so in the legend, executable.
assert.equal(unmeasuredSeriesLabel("Net interest margin", [null, undefined]), "Net interest margin (not measured)");
assert.equal(unmeasuredSeriesLabel("Net interest margin", [null, 0]), "Net interest margin", "0 IS a reading");
assert.ok(
  /unmeasuredSeriesLabel\(/.test(renderer),
  "WidgetRenderer must name an unmeasured series as such in the legend",
);

// The refusal count never hides the notice when the server's count is absent.
const locked = { state: "restricted" } as const;
const figure = { state: "panel" } as const;
assert.equal(restrictedWidgetCount(undefined, [locked, figure, locked]), 2);
assert.equal(restrictedWidgetCount(null, [figure]), 0);
assert.equal(restrictedWidgetCount(3, [locked]), 3, "the server's count wins when it is the larger");
assert.equal(restrictedWidgetCount(0, [locked]), 1, "a payload refusal is counted even against a reported 0");
assert.equal(
  /restrictedWidgets\s*\?\?\s*0/.test(dashboardsSource),
  false,
  "components/bi/dashboards.ts must not default the refusal count to 0 — it hides the notice over a canvas of locks",
);

// --- 18. a null is never a zero on the stress board either ---------------------
//
// Audit A360-6 M5. `DriverWaterfall` mapped every nullable input to 0 before
// subtracting, so a null final-year post-adverse capital drew the headroom bridge
// to −(CAR target × RWA): a fabricated wipe-out. The fail-open guard's rule P0-23
// lists ten field names and knows none of these, so this is pinned here until
// that rule learns them.

const waterfall = code("components/stress/charts/DriverWaterfall.tsx");
assert.equal(
  /==\s*null\s*\?\s*0|\?\?\s*0\b|\bnum\(/.test(waterfall),
  false,
  "DriverWaterfall must not map a null figure to 0: use numOrNull and refuse the bridge.",
);
assert.ok(
  /numOrNull\(\s*lastStress\.total_regulatory_capital\s*\)/.test(waterfall) &&
    /numOrNull\(\s*lastBase\.total_rwa\s*\)/.test(waterfall),
  "DriverWaterfall must read capital and RWA with numOrNull",
);
assert.ok(
  /cannot be derived for this run/.test(waterfall),
  "DriverWaterfall must say, in words, that a bridge with a missing input is not drawn.",
);

console.log(
  "disclosure.test.ts: BI refusals name nothing, absences never render as zero, " +
    "no pack is defined in the browser, every way out is the governed one, a saved " +
    "canvas is restated exactly or not at all, the builder's grid runtime has " +
    "one entrance, a calculated measure's refusal names the figure rather " +
    "than the formula, an unexplained gap says it is unexplained, a refusal is the " +
    "server's own decision, and the stress bridge draws no null as a zero.",
);
