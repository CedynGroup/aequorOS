"use client";

/**
 * The dashboards this client can open, and how one is read.
 *
 * THERE IS NO PACK DEFINITION IN THE BROWSER. A pack binds widgets to catalogue
 * measures, declares each widget's window relative to the reporting date, and is
 * validated server-side by `PackSpec`; a copy here would be a second definition,
 * free to disagree with the one the exports, the scheduled reports and the AI
 * commentary all read. So this module holds no pack, no widget and no query — it
 * reads `GET …/bi/packs` and `GET …/bi/packs/{pack}` and translates the answer
 * into the shapes the canvas draws.
 *
 * The translation is where the disclosure property becomes structural. A widget
 * the server refused arrives carrying its id, its geometry and `access:
 * "restricted"` and NOTHING else, and `packWidgetView` turns it into the one
 * variant of `BiPackWidgetView` that has no field for a title, a caption, a
 * measure, a dimension, a filter or a figure. A refusal therefore cannot render
 * one — not because this file remembers to omit it, but because there is nowhere
 * to put it.
 *
 * Saved dashboards (owner, visibility, version history) are the other half of
 * this module. They are a separate set of routes and a separate list, and they
 * parse through the SAME `packWidgetView` into the SAME view shape — which is
 * both why `certification` is on it and why the refusal property below holds for
 * a shared document without a second implementation of it.
 */

import type {
  BiDashboardListRead,
  BiDashboardRead,
  BiDashboardSummaryRead,
  BiPackRead,
  BiPackWidgetRead,
} from "@aequoros/risk-service-api";
import {
  useBiPack,
  useBiPacks,
  useBiSavedDashboard,
  useBiSavedDashboards,
} from "@/lib/api/bi";
import { savedCertification } from "./builder";
import { datasetRequirement, panelSurface } from "./labels";
import type {
  BiDashboardVisibility,
  BiGridItem,
  BiPackView,
  BiPackWidgetView,
  BiPendingCapability,
  BiSavedDashboardSummary,
  BiSavedDashboardView,
  BiWidgetKind,
} from "./types";

/**
 * The Data Engine dataset a figure-bearing widget names when its answer is
 * empty, for a widget whose pack file names none. The marts are built from the
 * canonical position book, so that is the dataset to supply — the same answer
 * Explore gives for the same reason.
 */
const DEFAULT_DATASET_KEY = "positions";

/**
 * Shapes this client draws. A pack may author a shape the browser has no
 * renderer for; it is shown as the answer's own table rather than dropped,
 * because the figures are the point and a table states all of them. Nothing is
 * invented and nothing is omitted — only the drawing differs from the authored
 * intent.
 */
const DRAWABLE_KINDS: readonly string[] = [
  "kpi",
  "kpi_row",
  "line",
  "area",
  "bar",
  "stacked_bar",
  "pie",
  "donut",
  "table",
];

function widgetKind(kind: string | null | undefined): BiWidgetKind {
  return DRAWABLE_KINDS.includes(kind ?? "") ? (kind as BiWidgetKind) : "table";
}

function layoutOf(widget: BiPackWidgetRead): BiGridItem {
  const { i, x, y, w, h } = widget.layout;
  return { i, x, y, w, h };
}

function pendingCapability(
  value: string | null | undefined,
): BiPendingCapability | null {
  return value === "catalogue_member" ||
    value === "governed_limit" ||
    value === "mart_field"
    ? value
    : null;
}

/**
 * One widget of a resolved pack, as the canvas will draw it.
 *
 * The order of the branches is the order the properties depend on:
 *
 *  1. a REFUSAL is decided first and carries only geometry;
 *  2. a widget with a resolved QUERY is a figure, and its `needs_data` key
 *     becomes the dataset its empty state names — the answer is still asked for,
 *     because a bank that has supplied the data must see the figure;
 *  3. a PANEL embeds a platform surface, which authorizes its own reader;
 *  4. PLATFORM WORK outstanding is stated as platform work. It is never
 *     collapsed into "needs data": telling a bank it has not supplied something
 *     it pushes every night is a false statement about its own book;
 *  5. only then is a named DATA GAP a data gap.
 */
export function packWidgetView(widget: BiPackWidgetRead): BiPackWidgetView {
  const layout = layoutOf(widget);
  if (widget.access === "restricted") {
    return { state: "restricted", id: widget.id, layout };
  }

  const title = widget.title ?? "";
  const caption = widget.caption ?? "";

  if (widget.query) {
    return {
      state: "figure",
      id: widget.id,
      layout,
      spec: {
        id: widget.id,
        title,
        subtitle: caption.length > 0 ? caption : undefined,
        kind: widgetKind(widget.kind),
        query: widget.query,
        dataset: datasetRequirement(widget.needsData ?? DEFAULT_DATASET_KEY),
        layout,
      },
    };
  }

  if (widget.panel) {
    const surface = panelSurface(widget.panel);
    if (surface !== null) {
      return { state: "panel", id: widget.id, layout, title, caption, surface };
    }
  }

  const capability = pendingCapability(widget.pendingCapability);
  if (capability !== null) {
    return {
      state: "pending",
      id: widget.id,
      layout,
      title,
      caption,
      capability,
    };
  }

  return {
    state: "needs_data",
    id: widget.id,
    layout,
    title,
    caption,
    dataset: datasetRequirement(widget.needsData ?? DEFAULT_DATASET_KEY),
  };
}

/** A resolved pack, as the dashboard surface renders it. */
export function packView(pack: BiPackRead): BiPackView {
  return {
    id: pack.id,
    title: pack.title,
    description: pack.description,
    version: pack.version,
    // Every pack this route serves ships in the platform's own validated
    // catalogue. A bank-promoted or personal view arrives from the saved
    // dashboards route and carries its own standing.
    certification: "platform",
    message: pack.message,
    everyFigureRefused: pack.access === "restricted",
    restrictedWidgets: pack.restrictedWidgets ?? 0,
    widgets: pack.widgets.map(packWidgetView),
  };
}

/**
 * How many widgets this reader will be shown something for, other than a
 * refusal.
 *
 * The server's own sentence for a pack whose every figure was refused says the
 * figures are "not shown", which is true of the FIGURES — but a pack also
 * carries embedded surfaces and named gaps, and those still appear. This count is
 * what lets the page say so instead of leaving a reader looking at four tiles
 * under a sentence that reads as "there is nothing here".
 */
export function shownBesideRefusals(view: BiPackView): number {
  return view.widgets.filter((widget) => widget.state !== "restricted").length;
}

/** Every certified dashboard this reader may open, for one reporting date. */
export function useDashboardList(
  bankId: string | undefined,
  asOf: string | null | undefined,
) {
  const packs = useBiPacks(bankId, asOf);
  return {
    ...packs,
    dashboards: (packs.data?.packs ?? []).map(packView),
  };
}

/** One certified dashboard, resolved for this reader and this reporting date. */
export function useDashboard(
  bankId: string | undefined,
  id: string | null,
  asOf: string | null | undefined,
) {
  const pack = useBiPack(bankId, id, asOf);
  return {
    ...pack,
    dashboard: pack.data ? packView(pack.data) : null,
  };
}

export const CERTIFICATION_LABELS = {
  platform: "Platform-certified",
  bank: "Bank-certified",
  personal: "Personal",
} as const;

// ---------------------------------------------------------------------------
// Saved dashboards
// ---------------------------------------------------------------------------

/**
 * The wire's reachability value as this client's own union.
 *
 * An identity at run time and a CHECK at compile time: the two vocabularies are
 * declared separately — one generated from `bi_content.py`, one written in
 * `types.ts` — and this is where they are required to agree. Widen either without
 * the other and this stops compiling, which is the point.
 */
function visibilityOf(
  value: BiDashboardSummaryRead["visibility"],
): BiDashboardVisibility {
  return value;
}

/** One saved dashboard as its tile needs it. */
export function savedDashboardSummary(
  row: BiDashboardSummaryRead,
): BiSavedDashboardSummary {
  return {
    id: row.id,
    title: row.title,
    description: row.description,
    certification: savedCertification(row.badge),
    ownerDisplayName: row.ownerDisplayName,
    ownedByCaller: row.ownedByCaller,
    visibility: visibilityOf(row.visibility),
    visibilityRole: row.visibilityRole,
    version: row.version,
    widgetCount: row.widgetCount,
    sourcePack: row.sourcePack,
    updatedAt: row.updatedAt,
  };
}

/**
 * A saved dashboard, resolved, as the canvas draws it.
 *
 * Every widget goes through `packWidgetView` — the same adapter a certified pack
 * uses — so a refused widget on a SHARED document lands in the one variant of
 * `BiPackWidgetView` that has no field for a title, a caption, a measure, a
 * dimension, a filter or a figure. That is what makes "sharing never shares
 * data" structural on the client as well as on the wire: there is nowhere for the
 * owner's view of a refused tile to be put.
 */
export function savedDashboardView(
  read: BiDashboardRead,
): BiSavedDashboardView {
  return {
    id: read.id,
    title: read.title,
    description: read.description,
    certification: savedCertification(read.badge),
    ownerDisplayName: read.ownerDisplayName,
    ownedByCaller: read.ownedByCaller,
    visibility: visibilityOf(read.visibility),
    visibilityRole: read.visibilityRole,
    version: read.version,
    sourcePack: read.sourcePack,
    message: read.message,
    everyFigureRefused: read.access === "restricted",
    restrictedWidgets: read.restrictedWidgets ?? 0,
    widgets: read.widgets.map(packWidgetView),
  };
}

/** How many widgets of a saved dashboard will draw something other than a refusal. */
export function shownBesideRefusalsOnSaved(view: BiSavedDashboardView): number {
  return view.widgets.filter((widget) => widget.state !== "restricted").length;
}

/** Every saved dashboard of this institution this reader may open. */
export function useSavedDashboardList(bankId: string | undefined) {
  const saved = useBiSavedDashboards(bankId);
  return {
    ...saved,
    dashboards: (
      (saved.data as BiDashboardListRead | undefined)?.dashboards ?? []
    ).map(savedDashboardSummary),
  };
}

/** One saved dashboard, resolved for this reader and this reporting date. */
export function useSavedDashboard(
  bankId: string | undefined,
  id: string | null,
  asOf: string | null | undefined,
) {
  const saved = useBiSavedDashboard(bankId, id, asOf);
  return {
    ...saved,
    dashboard: saved.data ? savedDashboardView(saved.data) : null,
  };
}
