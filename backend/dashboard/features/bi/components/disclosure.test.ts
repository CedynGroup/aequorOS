import assert from "node:assert/strict";
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

assert.equal(namedDataset(undefined), null);
assert.equal(namedDataset(null), null);
assert.notEqual(namedDataset("positions"), null);

assert.deepEqual(
  [...BI_GRANT_DENIAL_CODES].sort(),
  [
    "bi_authorization_denied",
    "bi_export_denied",
    "bi_insights_authorization_denied",
  ],
  "the paraphrased family is exactly the routes that re-run the grant decision",
);
assert.equal(
  isGrantDenial({ status: 403, errorCode: "bi_authorization_denied" }),
  true,
);
assert.equal(
  isGrantDenial({ status: 403, errorCode: "bi_human_principal_required" }),
  false,
  "an impersonated session's refusal is not a grant denial",
);
assert.equal(
  isGrantDenial({
    status: 403,
    errorCode: null,
    message: "Impersonation sessions are read-only.",
  }),
  false,
  "a plain-string 403 (deps.py) is not a grant denial",
);
assert.equal(
  isGrantDenial({ status: 404, errorCode: "bi_authorization_denied" }),
  false,
);
assert.equal(
  isGrantDenial({ status: 403, errorCode: "bi_data_scope_unsupported" }),
  false,
);
assert.equal(
  isGrantDenial({ status: 403, errorCode: "bi_content_owner_only" }),
  false,
);

// The server's sentence travels for every other 403, and for nothing else.
const impersonated = {
  status: 403,
  errorCode: "bi_human_principal_required",
  message: "Business intelligence is not available in an impersonated session.",
};
assert.equal(refusalSentence(impersonated), impersonated.message);
assert.equal(
  refusalSentence({
    status: 403,
    errorCode: null,
    message:
      "Impersonation sessions are read-only; this action is not permitted.",
  }),
  "Impersonation sessions are read-only; this action is not permitted.",
);
assert.equal(
  refusalSentence({
    status: 403,
    errorCode: "bi_authorization_denied",
    message:
      "Your access does not cover every field this view needs. An Org Owner can grant the fields listed here.",
  }),
  null,
  "a grant denial's sentence must NOT travel — its body names the refused members",
);
assert.equal(refusalSentence({ status: 500, message: "boom" }), null);
assert.equal(
  refusalSentence({
    status: 403,
    errorCode: "bi_data_scope_unsupported",
    message: "  ",
  }),
  null,
  "a 403 with no sentence falls to the ordinary failure panel, never to one composed here",
);

assert.equal(
  unmeasuredSeriesLabel("Net interest margin", [null, undefined]),
  "Net interest margin (not measured)",
);
assert.equal(
  unmeasuredSeriesLabel("Net interest margin", [null, 0]),
  "Net interest margin",
  "0 IS a reading",
);

const locked = { state: "restricted" } as const;
const figure = { state: "panel" } as const;
assert.equal(restrictedWidgetCount(undefined, [locked, figure, locked]), 2);
assert.equal(restrictedWidgetCount(null, [figure]), 0);
assert.equal(
  restrictedWidgetCount(3, [locked]),
  3,
  "the server's count wins when it is the larger",
);
assert.equal(
  restrictedWidgetCount(0, [locked]),
  1,
  "a payload refusal is counted even against a reported 0",
);

console.log(
  "disclosure.test.ts: dashboard drafts and disclosure decisions passed",
);
