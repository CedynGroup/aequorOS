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

/** How a widget draws its answer. */
export type BiWidgetKind =
  "kpi" | "line" | "bar" | "stacked_bar" | "pie" | "table";

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
  dataset: BiDatasetRequirement;
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
  trust: BiTrustBadge;
  /** A short recent series for the strip's sparkline, when one is published. */
  series?: readonly number[];
  /** Where the reader goes to see the statement's working. */
  href?: string;
}>;

/** A measure as the explain drawer and the widget header need it. */
export type BiMeasure = BiCatalogueMeasureRead;
