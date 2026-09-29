/**
 * The shapes the BI surfaces render.
 *
 * Two of them mirror a server contract and must keep mirroring it:
 * `BiInsight` is `app/services/bi/insights/statements.py::Insight` in
 * camelCase, and `BiWidgetSpec.query` is the generated `BiQuery` verbatim — a
 * widget binds to a CATALOGUE QUERY and can carry no SQL, no column name and no
 * threshold of its own. The rest describe layout, which is the browser's job.
 *
 * `BiGridItem` is deliberately react-grid-layout's item shape (`i/x/y/w/h` on a
 * twelve-column grid). The dashboard builder that arrives with saved dashboards
 * uses that library; the read-only canvas here places the same items with CSS
 * grid, so a pack authored once lays out identically in both.
 */

import type {
  BiCatalogueMeasureRead,
  BiQuery,
  BiTrustBadge,
} from "@aequoros/risk-service-api";

/**
 * How a widget draws its answer.
 *
 * The first six are the shapes Explore offers; the rest are shapes a certified
 * pack authors (`app/schemas/bi.py::BiWidgetKind`). `kpi_row` is its own kind
 * rather than a `kpi` with several measures on purpose: a `kpi` renderer that
 * showed only the first measure of a row would DROP the others silently, and a
 * board tile called "Capital adequacy" that shows the total ratio and quietly
 * omits Tier 1 is worse than one that shows both plainly.
 */
export type BiWidgetKind =
  | "kpi"
  | "kpi_row"
  | "line"
  | "area"
  | "bar"
  | "stacked_bar"
  | "pie"
  | "donut"
  | "table";

/** react-grid-layout's item shape, on a twelve-column grid. */
export type BiGridItem = Readonly<{
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}>;

/**
 * The Data Engine dataset a widget needs, named the way the bank's own upload
 * screens name it, with the route that accepts it. This is what
 * `NeedsDataWidget` offers instead of a zero.
 */
export type BiDatasetRequirement = Readonly<{
  label: string;
  href: string;
}>;

export type BiWidgetSpec = Readonly<{
  id: string;
  title: string;
  subtitle?: string;
  kind: BiWidgetKind;
  query: BiQuery;
  /**
   * The dataset the widget's author NAMED as the one its figure degrades
   * without, or `null` when none was named. Null is a real state, not a
   * default waiting to be filled: a widget that does not know why it is empty
   * must say so, and every default this used to carry asserted a cause.
   */
  dataset: BiDatasetRequirement | null;
  layout: BiGridItem;
}>;

/**
 * Who stands behind a dashboard's figures.
 *
 * `platform` is reserved for a view built only from filed figures copied out of
 * the sealed tier; `bank` is a view an institution promoted through its own
 * maker-checker; `personal` is one person's working view. Nothing else may be
 * shown as certified.
 */
export type BiCertification = "platform" | "bank" | "personal";

export type BiDashboard = Readonly<{
  id: string;
  title: string;
  description: string;
  certification: BiCertification;
  widgets: readonly BiWidgetSpec[];
}>;

/**
 * A platform surface a certified pack embeds rather than querying itself
 * (`app/schemas/bi.py::BiPanelKey`). The pack names the surface; this client
 * names where it is, and the surface authorizes its own reader when they arrive.
 */
export type BiPanelKey =
  | "credit_migration"
  | "credit_vintages"
  | "return_calendar"
  | "attestation_status"
  | "reconciliation_trust"
  | "ingestion_quality";

/** Platform work a figure is waiting on (`app/schemas/bi.py::BiPendingCapability`). */
export type BiPendingCapability =
  "catalogue_member" | "governed_limit" | "mart_field";

/** Where a reader goes to read a surface a pack embeds. */
export type BiPanelSurface = Readonly<{ label: string; href: string }>;

/**
 * ONE WIDGET OF A PACK, AS THIS CLIENT WILL DRAW IT.
 *
 * The state is decided once, from the server's own answer, and the shape of each
 * variant is what makes the disclosure property structural rather than
 * conventional: **the `restricted` variant has no field that could carry a
 * title, a caption, a measure, a dimension, a filter or a figure**, so a
 * refusal cannot render one even by mistake. Geometry survives because the pack
 * published it and the canvas needs it to keep the authored shape.
 *
 * `figure` is the only variant that holds a query, and that query is the one the
 * SERVER resolved for the requested date — never one composed here.
 */
export type BiPackWidgetView =
  | Readonly<{ state: "restricted"; id: string; layout: BiGridItem }>
  | Readonly<{
      state: "figure";
      id: string;
      layout: BiGridItem;
      spec: BiWidgetSpec;
    }>
  | Readonly<{
      state: "panel";
      id: string;
      layout: BiGridItem;
      title: string;
      caption: string;
      surface: BiPanelSurface;
    }>
  | Readonly<{
      state: "needs_data";
      id: string;
      layout: BiGridItem;
      title: string;
      caption: string;
      /** Null when the pack named no dataset — an unmapped panel key, for one. */
      dataset: BiDatasetRequirement | null;
    }>
  | Readonly<{
      state: "pending";
      id: string;
      layout: BiGridItem;
      title: string;
      caption: string;
      capability: BiPendingCapability;
    }>;

/** A resolved pack as the dashboard surface renders it. */
export type BiPackView = Readonly<{
  id: string;
  title: string;
  description: string;
  version: string;
  certification: BiCertification;
  /** The server's own sentence about what this reader is looking at. */
  message: string;
  /** True when every figure-bearing widget on the pack was refused. */
  everyFigureRefused: boolean;
  restrictedWidgets: number;
  /** Widgets that will draw something, refusals included, in layout order. */
  widgets: readonly BiPackWidgetView[];
}>;

/** Mirrors `insights/statements.py::StatementClass`. */
export type BiStatementClass =
  "movement" | "attribution" | "projection" | "data_gap" | "trust_notice";

/** Mirrors `insights/drivers.py::Favourability`. */
export type BiFavourability = "favourable" | "adverse" | "neutral";

/** Mirrors `insights/statements.py::Emphasis`. */
export type BiEmphasis = "high" | "normal" | "low";

/**
 * One statement the platform is prepared to make about a reporting date.
 *
 * `headline` and `detail` are rendered as given: they are composed server-side
 * from typed facts, with the figures already formatted in the institution's own
 * unit, so the browser never recomputes or re-rounds them.
 */
export type BiInsight = Readonly<{
  id: string;
  ruleId: string;
  statementClass: BiStatementClass;
  headline: string;
  detail: string;
  asOf: string;
  measureIds: readonly string[];
  favourability: BiFavourability;
  emphasis: BiEmphasis;
  /** Reservations that qualify the statement — advisory basis, trust, gaps. */
  qualifiers: readonly string[];
  certified: boolean;
  /**
   * The reconciliation verdict behind the statement. Optional only because the
   * wire model declares it so; an absent badge degrades to "Not assessed" in
   * `TrustBadge` and never to a pass.
   */
  trust?: BiTrustBadge;
  /** A short recent series for the strip's sparkline, when one is published. */
  series?: readonly number[];
  /** Where the reader goes to see the statement's working. */
  href?: string;
}>;

/** A measure as the explain drawer and the widget header need it. */
export type BiMeasure = BiCatalogueMeasureRead;

/**
 * HOW A SAVED DASHBOARD IS REACHABLE — not who may read its figures.
 *
 * Mirrors `app/models/bi_content.py::DASHBOARD_VISIBILITIES`. Reachability only
 * decides whether the document OPENS; every widget on it is authorized for
 * whoever opened it, every time, so widening this can never widen what a reader
 * is shown.
 */
export type BiDashboardVisibility = "private" | "users" | "role" | "org";

/**
 * ONE SAVED DASHBOARD IN A LIST.
 *
 * There is no widget here and no member id, because the LIST is read by everyone
 * the document reaches and the figures it names are only disclosed once they have
 * been authorized for that reader — which happens when it is opened.
 *
 * `certification` is the server's own `badge`, mapped; it is never inferred from
 * who owns the document or from which list it arrived in.
 */
export type BiSavedDashboardSummary = Readonly<{
  id: string;
  title: string;
  description: string;
  certification: BiCertification;
  ownerDisplayName: string | null;
  /** Whether the reader is the owner, and therefore the only identity that may change it. */
  ownedByCaller: boolean;
  visibility: BiDashboardVisibility;
  visibilityRole: string | null;
  version: number;
  widgetCount: number;
  /** The certified pack this was copied from, when it was a copy. */
  sourcePack: string | null;
  updatedAt: Date;
}>;

/** A saved dashboard resolved for one reader and one reporting date. */
export type BiSavedDashboardView = Readonly<{
  id: string;
  title: string;
  description: string;
  certification: BiCertification;
  ownerDisplayName: string | null;
  ownedByCaller: boolean;
  visibility: BiDashboardVisibility;
  visibilityRole: string | null;
  version: number;
  sourcePack: string | null;
  /** The server's own sentence about what this reader is looking at. */
  message: string;
  /** True when every figure-bearing widget on the canvas was refused. */
  everyFigureRefused: boolean;
  restrictedWidgets: number;
  /** Widgets in layout order, refusals included and carrying only geometry. */
  widgets: readonly BiPackWidgetView[];
}>;

/**
 * WHY THIS CLIENT CANNOT RESTATE A SAVED CANVAS, when it cannot.
 *
 * Saving a dashboard replaces its canvas as a whole, so the builder has to be
 * able to write down every widget's authored definition exactly. Two things stop
 * it, and neither may be guessed at:
 *
 * * `refused` — a widget this reader was refused arrives carrying its geometry
 *   and nothing else, so its definition is not on the page to restate. Dropping
 *   it would silently delete a view from somebody's dashboard.
 * * `relative_window` — the read route returns each widget's query with the
 *   reporting window already RESOLVED, and the relative window it was authored
 *   with cannot be recovered from the result: on 31 January, month-to-date,
 *   quarter-to-date and year-to-date all resolve to a window starting 1 January.
 *   Picking one would turn a year's total into a month's under the title the
 *   author wrote.
 * * `incomplete` — a widget the server served as granted arrived without the
 *   chart kind or the title its own definition requires. That is a malformed
 *   read rather than a refusal, and substituting a kind would change what the
 *   view draws under the author's heading.
 */
export type BiCanvasUnrestatable = "refused" | "relative_window" | "incomplete";
