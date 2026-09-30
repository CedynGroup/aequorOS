/**
 * The saved-dashboard builder's rules, with no React and no network in them.
 *
 * Everything here is a pure function over a DRAFT — the canvas a reader is
 * arranging — so the rules that decide what may be saved, what a widget's id is
 * and whether a stored canvas can be edited at all are provable without a
 * browser (`components/bi/disclosure.test.ts` exercises them directly).
 *
 * THREE PROPERTIES LIVE HERE AND MATTER.
 *
 * 1. A DRAFT CARRIES THE AUTHORED DEFINITION, NOT A PROJECTION OF IT. A widget
 *    in the draft is the exact `BiPackWidget` that will be sent, so editing the
 *    figures on one cannot silently drop a filter, a sort or a pivot the reader
 *    never saw a control for. The editable parts are read back out of it; the
 *    rest is carried through untouched.
 *
 * 2. A CANVAS IS SAVED AS A WHOLE, SO IT MUST BE RESTATABLE AS A WHOLE. The read
 *    route serves a dashboard RESOLVED for a reader and a date, and two things in
 *    that answer cannot be turned back into what was authored — a refused widget
 *    (which carries only geometry) and a relative reporting window (which arrives
 *    already resolved into dates). `authoredCanvas` refuses in both cases instead
 *    of approximating, because approximating either one edits somebody's document
 *    without telling them.
 *
 * 3. THE GRID IS TWELVE COLUMNS AND THE SERVER AGREES. `BiLayoutItem` is
 *    react-grid-layout's own item shape, and the same twelve-column geometry is
 *    what the read-only canvas lays a dashboard out with — so a canvas arranged
 *    here is drawn where it was put.
 */

import type {
  BiDashboardRead,
  BiDashboardSpec,
  BiDashboardSummaryRead,
  BiPackWidget,
  BiPackWidgetQuery,
  BiPackWidgetRead,
} from "@aequoros/risk-service-api";
// A RELATIVE path, deliberately: this module is exercised by a plain Node test
// (`components/bi/disclosure.test.ts`), and Node does not resolve the `@/` alias
// at run time. `components/bi/drill.ts` and `result.ts` do the same for the same
// reason.
import { ROLE_OPTIONS } from "../../lib/api/grants";
import type {
  BiCanvasUnrestatable,
  BiCertification,
  BiDashboardVisibility,
  BiGridItem,
} from "./types";

/**
 * The roles a dashboard may be addressed to, and their names.
 *
 * Exactly the vocabulary the grant composer in Access → Members issues, so the
 * words on this form and the words on the grant that satisfies it are the same
 * words. Addressing a dashboard to a role is REACHABILITY: whoever holds that
 * role over this institution may open the document, and every figure on it is
 * still authorized for them individually when they do.
 */
export const ADDRESSABLE_ROLES: readonly string[] = ROLE_OPTIONS.map(
  ([bundle]) => bundle,
);

export function addressableRoleLabel(bundle: string): string {
  return (
    ROLE_OPTIONS.find(([candidate]) => candidate === bundle)?.[1] ?? bundle
  );
}

/** The grid both the builder and the read-only canvas place items on. */
export const BUILDER_COLUMNS = 12;

/**
 * The grip a tile is dragged by, and `dragConfig.handle`'s selector.
 *
 * It lives HERE, in the module with no runtime dependencies, and not beside the
 * grid canvas: a tile is rendered by `DashboardBuilder`, and importing the class
 * name from the canvas would pull react-grid-layout into that component's static
 * graph — which is exactly what `scripts/assert-home-route-bundle.mjs` exists to
 * prevent, and what it caught the first time this was written.
 */
export const BUILDER_DRAG_HANDLE_CLASS = "bi-builder-grip";

/** A new widget's footprint: half the width, four rows — a legible default. */
export const DEFAULT_WIDGET_WIDTH = 6;
export const DEFAULT_WIDGET_HEIGHT = 4;

/** `BiPackWidget.title` is `min_length=1, max_length=120` on the server. */
export const WIDGET_TITLE_MAX = 120;
export const WIDGET_CAPTION_MAX = 280;
/** `bi_dashboards.title` / `.description` / `bi_dashboard_versions.change_note`. */
export const DASHBOARD_TITLE_MAX = 120;
export const DASHBOARD_DESCRIPTION_MAX = 400;
export const CHANGE_NOTE_MAX = 280;
/** `BiDashboardSpec` admits between one and this many widgets (`BI_PACK_MAX_WIDGETS`). */
export const MAX_WIDGETS = 24;

/**
 * The shapes a reader may put a figure in here.
 *
 * The intersection of what the server's widget model admits
 * (`app/schemas/bi.py::BiWidgetKind`) and what `WidgetRenderer` actually draws: a
 * kind with no renderer degrades to the answer's own table, which is honest but
 * not something to offer as a choice. `record_grid`, `heatmap`, `waterfall` and
 * `panel` are the server's and are deliberately absent — the first three have no
 * renderer, and a panel embeds a platform surface rather than reading a figure.
 */
export const BUILDER_WIDGET_KINDS = [
  { kind: "kpi", label: "Single figure" },
  { kind: "kpi_row", label: "Figures side by side" },
  { kind: "table", label: "Table" },
  { kind: "bar", label: "Bars" },
  { kind: "stacked_bar", label: "Stacked bars" },
  { kind: "line", label: "Line" },
  { kind: "area", label: "Line with the area filled" },
  { kind: "donut", label: "Share" },
] as const;

export type BuilderWidgetKind = (typeof BUILDER_WIDGET_KINDS)[number]["kind"];

export function isBuilderWidgetKind(value: string): value is BuilderWidgetKind {
  return BUILDER_WIDGET_KINDS.some((entry) => entry.kind === value);
}

/**
 * The canvas being arranged.
 *
 * `widgets` are the authored definitions, in no particular order; `layout` is
 * where each one sits, keyed by `i === widget.id`. The two lists are kept in step
 * by every operation in this module, because the server refuses a canvas whose
 * layout does not position every widget and nothing else.
 */
export type BuilderDraft = Readonly<{
  title: string;
  description: string;
  visibility: BiDashboardVisibility;
  /** Required when `visibility` is `role`, and meaningless otherwise. */
  visibilityRole: string | null;
  widgets: readonly BiPackWidget[];
  layout: readonly BiGridItem[];
}>;

export function emptyDraft(): BuilderDraft {
  return {
    title: "",
    description: "",
    visibility: "private",
    visibilityRole: null,
    widgets: [],
    layout: [],
  };
}

/**
 * A widget id from the words the reader used for it.
 *
 * The server's pattern is `^[a-z][a-z0-9_]*$` — an id is a key, not a label — so
 * the title is folded down to it and made unique against what is already on the
 * canvas. A title with nothing usable in it (punctuation only, or another script)
 * still gets an id, because refusing to place a widget over its NAME would be a
 * rule about language rather than about the canvas.
 */
export function widgetIdFrom(title: string, taken: readonly string[]): string {
  const folded = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
  const seed = /^[a-z]/.test(folded) ? folded : `view_${folded}`.slice(0, 48);
  const base = seed.replace(/_+$/g, "");
  if (!taken.includes(base)) return base;
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${base}_${index}`;
    if (!taken.includes(candidate)) return candidate;
  }
  return `${base}_${taken.length + 1}`;
}

/** The first row below everything already placed. */
export function nextRow(layout: readonly BiGridItem[]): number {
  return layout.reduce((lowest, item) => Math.max(lowest, item.y + item.h), 0);
}

/** Add a widget, placed on its own row at the authored default size. */
export function addWidget(
  draft: BuilderDraft,
  widget: BiPackWidget,
): BuilderDraft {
  return {
    ...draft,
    widgets: [...draft.widgets, widget],
    layout: [
      ...draft.layout,
      {
        i: widget.id,
        x: 0,
        y: nextRow(draft.layout),
        w: DEFAULT_WIDGET_WIDTH,
        h: DEFAULT_WIDGET_HEIGHT,
      },
    ],
  };
}

/** Take a widget off the canvas, and its place with it. */
export function removeWidget(draft: BuilderDraft, id: string): BuilderDraft {
  return {
    ...draft,
    widgets: draft.widgets.filter((widget) => widget.id !== id),
    layout: draft.layout.filter((item) => item.i !== id),
  };
}

/** Replace one widget's authored definition, keeping its place. */
export function replaceWidget(
  draft: BuilderDraft,
  widget: BiPackWidget,
): BuilderDraft {
  return {
    ...draft,
    widgets: draft.widgets.map((current) =>
      current.id === widget.id ? widget : current,
    ),
  };
}

/**
 * Re-place a widget, clamped to the grid.
 *
 * Used by the width and height controls, which exist beside the drag handles so
 * the canvas can be arranged from a keyboard. A width wider than the grid or a
 * height of zero is not a request the server would accept, so it is clamped here
 * rather than sent and refused.
 */
export function sizeWidget(
  draft: BuilderDraft,
  id: string,
  size: Readonly<{ w?: number; h?: number }>,
): BuilderDraft {
  return {
    ...draft,
    layout: draft.layout.map((item) =>
      item.i === id
        ? {
            ...item,
            w:
              size.w === undefined
                ? item.w
                : Math.min(BUILDER_COLUMNS, Math.max(1, Math.round(size.w))),
            h: size.h === undefined ? item.h : Math.max(1, Math.round(size.h)),
          }
        : item,
    ),
  };
}

/**
 * Accept a layout the grid library produced.
 *
 * Only the items that name a widget already on the canvas are kept, and only
 * their geometry is read: the library owns where things are, this module owns
 * what they are.
 */
export function applyLayout(
  draft: BuilderDraft,
  layout: readonly BiGridItem[],
): BuilderDraft {
  const known = new Set(draft.widgets.map((widget) => widget.id));
  const placed = layout
    .filter((item) => known.has(item.i))
    .map((item) => ({
      i: item.i,
      x: Math.max(0, Math.round(item.x)),
      y: Math.max(0, Math.round(item.y)),
      w: Math.min(BUILDER_COLUMNS, Math.max(1, Math.round(item.w))),
      h: Math.max(1, Math.round(item.h)),
    }));
  const positioned = new Set(placed.map((item) => item.i));
  // A widget the library did not report keeps the place it had: dropping it
  // would delete the view, and inventing a place for it would move it.
  const kept = draft.layout.filter((item) => !positioned.has(item.i));
  return { ...draft, layout: [...placed, ...kept] };
}

/**
 * Whether two layouts place the same widgets in the same places.
 *
 * The grid library reports a layout on mount as well as after a drag, so a
 * handler that set state unconditionally would re-render, be reported to again,
 * and not settle. Comparing first is what makes the drag loop terminate.
 */
export function layoutsEqual(
  left: readonly BiGridItem[],
  right: readonly BiGridItem[],
): boolean {
  if (left.length !== right.length) return false;
  const byId = new Map(right.map((item) => [item.i, item]));
  return left.every((item) => {
    const other = byId.get(item.i);
    return (
      other !== undefined &&
      other.x === item.x &&
      other.y === item.y &&
      other.w === item.w &&
      other.h === item.h
    );
  });
}

/**
 * A widget bound to the catalogue figures a reader chose.
 *
 * Everything not on the form — filters, sorts, a pivot, a row cap, display hints
 * — is carried through from `from` when there is one, so editing the figures on a
 * widget cannot quietly drop part of its definition.
 */
export function figureWidget(
  spec: Readonly<{
    id: string;
    title: string;
    caption: string;
    kind: BuilderWidgetKind;
    measures: readonly string[];
    dimensions: readonly string[];
  }>,
  from?: BiPackWidget,
): BiPackWidget {
  const carried = from?.query;
  const query: BiPackWidgetQuery = {
    ...(carried ?? {}),
    measures: [...spec.measures],
    dimensions: [...spec.dimensions],
    // ONE REPORTING DATE, ALWAYS. See `authoredCanvas` for why this surface
    // authors no other window: a relative one cannot be read back out of a
    // resolved answer, so authoring one here would produce a canvas this builder
    // could never reopen.
    window: "as_of",
    compare: "none",
  };
  return {
    ...(from ?? {}),
    id: spec.id,
    title: spec.title,
    caption: spec.caption,
    kind: spec.kind,
    query,
  };
}

/** The figures and breakdown a widget reads, for the form that edits it. */
export function widgetFigures(
  widget: BiPackWidget,
): Readonly<{ measures: readonly string[]; dimensions: readonly string[] }> {
  return {
    measures: widget.query?.measures ?? [],
    dimensions: widget.query?.dimensions ?? [],
  };
}

/**
 * Everything standing between this draft and a save, in the reader's words.
 *
 * The server refuses each of these too — this is not a second authority, it is
 * the same rules said before the request so a reader is not told about a missing
 * title by a 422.
 */
export function draftProblems(draft: BuilderDraft): readonly string[] {
  const problems: string[] = [];
  if (draft.title.trim().length === 0) {
    problems.push("Give the dashboard a name before saving it.");
  }
  if (draft.title.trim().length > DASHBOARD_TITLE_MAX) {
    problems.push(
      `Shorten the name to ${DASHBOARD_TITLE_MAX} characters or fewer.`,
    );
  }
  if (draft.description.length > DASHBOARD_DESCRIPTION_MAX) {
    problems.push(
      `Shorten the description to ${DASHBOARD_DESCRIPTION_MAX} characters or fewer.`,
    );
  }
  if (draft.widgets.length === 0) {
    problems.push(
      "Add at least one view — a dashboard with nothing on it cannot be saved.",
    );
  }
  if (draft.widgets.length > MAX_WIDGETS) {
    problems.push(
      `Remove ${draft.widgets.length - MAX_WIDGETS} so that no more than ${MAX_WIDGETS} views are on one dashboard.`,
    );
  }
  for (const widget of draft.widgets) {
    if (widget.title.trim().length === 0) {
      problems.push("Every view needs a heading of its own.");
      break;
    }
  }
  for (const widget of draft.widgets) {
    if (widget.query && widget.query.measures.length === 0) {
      problems.push(`"${widget.title}" needs at least one figure to show.`);
      break;
    }
  }
  if (draft.visibility === "role" && !draft.visibilityRole) {
    problems.push("Choose the role this dashboard is shared with.");
  }
  return problems;
}

/** The canvas as the create and update requests want it. */
export function draftSpec(draft: BuilderDraft): BiDashboardSpec {
  const order = new Map(
    draft.widgets.map((widget, index) => [widget.id, index]),
  );
  return {
    widgets: draft.widgets.map((widget) => ({ ...widget })),
    layout: [...draft.layout]
      .filter((item) => order.has(item.i))
      .map((item) => ({ ...item })),
  };
}

// ---------------------------------------------------------------------------
// Reading a stored dashboard back into a draft
// ---------------------------------------------------------------------------

/**
 * ONE WIDGET, BACK IN THE SHAPE IT WAS AUTHORED IN — or a refusal to try.
 *
 * `PUT …/bi/dashboards/{id}` replaces the canvas as a whole, so an edit has to
 * write down every widget's authored definition, including the ones the reader is
 * not changing. Three things cannot be written down from what the read route
 * returns, and none of them may be approximated:
 *
 *  * a REFUSED widget arrives with its geometry and nothing else, so there is no
 *    definition on the page to restate — and saving without it would silently
 *    delete a view from somebody's dashboard;
 *  * a query authored with a RELATIVE window (month to date, trailing twelve
 *    months) comes back with that window already resolved into dates, and the
 *    relative form cannot be recovered: on 31 January, month-to-date,
 *    quarter-to-date and year-to-date all resolve to a window starting
 *    1 January, so picking one would turn a year's total into a month's under
 *    the heading its author wrote. A comparison against a prior period is
 *    refused for the same reason — it arrives as a date, not as "the prior
 *    quarter";
 *  * an INCOMPLETE read — a granted widget with no kind or no heading — is
 *    malformed rather than refused, and substituting either would change what
 *    the view draws.
 */
export function authoredWidget(
  widget: BiPackWidgetRead,
): { widget: BiPackWidget } | { unrestatable: BiCanvasUnrestatable } {
  if (widget.access === "restricted") return { unrestatable: "refused" };
  if (!widget.kind || !widget.title) return { unrestatable: "incomplete" };

  const base: BiPackWidget = {
    id: widget.id,
    kind: widget.kind,
    title: widget.title,
    caption: widget.caption ?? undefined,
    display: widget.display ?? undefined,
    needsData: widget.needsData ?? undefined,
    panel: widget.panel ?? undefined,
    pendingCapability: widget.pendingCapability ?? undefined,
  };

  const resolved = widget.query;
  if (!resolved) return { widget: base };

  const time = resolved.time;
  if (!time.asOf || time.range || time.compareTo) {
    return { unrestatable: "relative_window" };
  }

  const query: BiPackWidgetQuery = {
    measures: [...resolved.measures],
    dimensions: resolved.dimensions ? [...resolved.dimensions] : undefined,
    filters: resolved.filters ? [...resolved.filters] : undefined,
    topN: resolved.topN ?? undefined,
    sort: resolved.sort ? [...resolved.sort] : undefined,
    limit: resolved.limit ?? undefined,
    offset: resolved.offset,
    pivot: resolved.pivot ?? undefined,
    subtotals: resolved.subtotals,
    window: "as_of",
    compare: "none",
  };
  return { widget: { ...base, query } };
}

/**
 * A stored dashboard as a draft to arrange, or the reason it cannot be one.
 *
 * All-or-nothing on purpose: a canvas is saved as a whole, so a partial
 * restatement is a dashboard with a view missing from it.
 */
export function draftFromDashboard(
  read: BiDashboardRead,
): { draft: BuilderDraft } | { unrestatable: BiCanvasUnrestatable } {
  const widgets: BiPackWidget[] = [];
  for (const widget of read.widgets) {
    const restated = authoredWidget(widget);
    if ("unrestatable" in restated) return restated;
    widgets.push(restated.widget);
  }
  return {
    draft: {
      title: read.title,
      description: read.description,
      visibility: read.visibility,
      visibilityRole: read.visibilityRole,
      widgets,
      layout: read.widgets.map((widget) => ({
        i: widget.layout.i,
        x: widget.layout.x,
        y: widget.layout.y,
        w: widget.layout.w,
        h: widget.layout.h,
      })),
    },
  };
}

/** Why this reader cannot rearrange a dashboard, in the reader's words. */
export function unrestatableReason(reason: BiCanvasUnrestatable): string {
  if (reason === "refused") {
    return (
      "This dashboard holds a view your access no longer covers, so it cannot be " +
      "rearranged here — saving it would drop that view without showing you what " +
      "it was. An organization owner can restore the access."
    );
  }
  if (reason === "relative_window") {
    return (
      "This dashboard holds a view that reads a period rather than a single " +
      "reporting date — a month to date, or a trailing year. Those are kept " +
      "exactly as they were published and cannot be rearranged here."
    );
  }
  return (
    "This dashboard could not be read back completely, so it is not safe to " +
    "rearrange. Nothing has been changed."
  );
}

// ---------------------------------------------------------------------------
// Standing
// ---------------------------------------------------------------------------

/**
 * THE BADGE IS THE SERVER'S WORD, MAPPED — never inferred.
 *
 * A certified pack is a validated file the platform publishes. A SAVED dashboard
 * carries its own `badge`, and this mapping is total over the three values the
 * wire declares, so there is no branch in which a document is shown with a
 * standing the server did not state. `bank_certified` is admitted by the
 * database and has no product path yet — there is no maker-checker promotion for
 * a dashboard — so every saved dashboard today is `Personal`, which is the honest
 * badge for a document its owner may still edit.
 */
export function savedCertification(
  badge: BiDashboardSummaryRead["badge"],
): BiCertification {
  if (badge === "platform_certified") return "platform";
  if (badge === "bank_certified") return "bank";
  return "personal";
}

/**
 * WHICH OF THE TWO SURFACES AN ID BELONGS TO.
 *
 * Not a guess about content: it is the two routes' own path types. A saved
 * dashboard's id is the UUID primary key `GET …/bi/dashboards/{dashboard_id}`
 * declares; a certified pack's key is the lower-case slug `GET …/bi/packs/{pack}`
 * declares. Asking the wrong route answers 404 for a document that exists, so the
 * shape decides which one is asked and exactly one of them is.
 */
const UUID_SHAPE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isSavedDashboardId(id: string): boolean {
  return UUID_SHAPE.test(id);
}

/** How a dashboard is reachable, in the reader's words. */
export function visibilityLabel(
  visibility: BiDashboardVisibility,
  role: string | null,
  roleName: (bundle: string) => string,
): string {
  if (visibility === "private") return "Only you";
  if (visibility === "users") return "The people you name";
  if (visibility === "role") {
    return role ? `Everyone holding ${roleName(role)}` : "A role";
  }
  return "Everyone at this organization with access to this institution";
}
