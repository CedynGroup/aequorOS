"use client";

/**
 * A dashboard's widgets, laid out and each answered independently.
 *
 * SHARING A DASHBOARD NEVER SHARES DATA. Every widget submits its own query and
 * is authorized for the reader who opened the page, so two people looking at
 * the same dashboard see two different sets of tiles: one may see a portfolio
 * chart where the other sees "Access restricted", and neither learns anything
 * about the other's view. That is why the canvas answers widget by widget
 * rather than fetching a dashboard's worth of rows in one call.
 *
 * AND A REFUSED WIDGET IS GIVEN NOTHING BUT ITS GEOMETRY. The server sends a
 * refusal as an id, a layout item and `access: "restricted"`; the view shape this
 * canvas consumes has no field for anything else on that variant, so the branch
 * below can pass `RestrictedWidget` only a height. A denial that named what it
 * hid would itself be the disclosure.
 *
 * The layout is react-grid-layout's item shape (`i/x/y/w/h` on twelve columns),
 * placed here with CSS grid. Keeping the authored shape means the Phase 3
 * builder — which does use the library, for dragging — and this read-only
 * canvas lay a pack out identically, and it is why the geometry of a refused
 * widget still travels: the dashboard keeps the shape it was published with.
 */

import type { ReactNode } from "react";
import type { BiFilter, BiQuery } from "@aequoros/risk-service-api";
import { useBiQuery } from "@/lib/api/bi";
import NeedsDataWidget from "./NeedsDataWidget";
import PanelWidget from "./PanelWidget";
import PendingWidget from "./PendingWidget";
import RestrictedWidget from "./RestrictedWidget";
import WidgetRenderer, {
  WIDGET_ROW_HEIGHT,
  widgetBodyHeight,
} from "./WidgetRenderer";
import { narrowedQuery } from "./query";
import type { BiPackWidgetView, BiWidgetSpec } from "./types";

const COLUMNS = 12;

/** The pixel height of a tile that draws no chart, from its authored rows. */
function tileHeight(rows: number): number {
  return widgetBodyHeight(rows);
}

function DashboardWidget({
  bankId,
  spec,
  filters,
  onExplain,
  widgetActions,
}: {
  bankId: string | undefined;
  spec: BiWidgetSpec;
  filters: readonly BiFilter[];
  onExplain?: (measureId: string, query: BiQuery) => void;
  widgetActions?: (query: BiQuery) => ReactNode;
}) {
  // The window is the one the SERVER resolved when it served this pack for the
  // reader's date. Only the filter bar's narrowing is merged in — see
  // `query.ts::narrowedQuery` for why the date must not be rewritten here.
  const query = narrowedQuery(spec.query, filters);
  const result = useBiQuery(bankId, query);

  return (
    <WidgetRenderer
      spec={spec}
      result={result.data}
      isLoading={result.isPending}
      error={result.error}
      onRetry={() => void result.refetch()}
      onExplain={
        onExplain ? (measureId) => onExplain(measureId, query) : undefined
      }
      // A governed export is an export of ONE ANSWER, so the control belongs to
      // the widget and carries the widget's own query — not the dashboard's, of
      // which there is no such thing.
      actions={widgetActions?.(query)}
      // The query AS SUBMITTED, so a drill-through carries the widget's own
      // window and the filters the reader narrowed by.
      query={query}
    />
  );
}

export default function DashboardCanvas({
  bankId,
  widgets,
  filters,
  onExplain,
  widgetActions,
}: {
  bankId: string | undefined;
  widgets: readonly BiPackWidgetView[];
  filters: readonly BiFilter[];
  onExplain?: (measureId: string, query: BiQuery) => void;
  /** Controls for one figure's frame, given that figure's own query. */
  widgetActions?: (query: BiQuery) => ReactNode;
}) {
  const ordered = [...widgets].sort((left, right) =>
    left.layout.y === right.layout.y
      ? left.layout.x - right.layout.x
      : left.layout.y - right.layout.y,
  );

  return (
    <div
      className="grid gap-4"
      style={{
        gridTemplateColumns: `repeat(${COLUMNS}, minmax(0, 1fr))`,
        gridAutoRows: `minmax(${WIDGET_ROW_HEIGHT}px, auto)`,
      }}
    >
      {ordered.map((widget) => (
        <div
          key={widget.id}
          style={{
            gridColumn: `span ${Math.min(
              COLUMNS,
              Math.max(1, widget.layout.w),
            )}`,
            gridRow: `span ${Math.max(1, widget.layout.h)}`,
          }}
        >
          {widget.state === "restricted" ? (
            <RestrictedWidget height={tileHeight(widget.layout.h)} />
          ) : widget.state === "figure" ? (
            <DashboardWidget
              bankId={bankId}
              spec={widget.spec}
              filters={filters}
              onExplain={onExplain}
              widgetActions={widgetActions}
            />
          ) : widget.state === "panel" ? (
            <PanelWidget
              title={widget.title}
              caption={widget.caption}
              surface={widget.surface}
              height={tileHeight(widget.layout.h)}
            />
          ) : widget.state === "pending" ? (
            <PendingWidget
              title={widget.title}
              caption={widget.caption}
              capability={widget.capability}
              height={tileHeight(widget.layout.h)}
            />
          ) : (
            <NeedsDataWidget
              dataset={widget.dataset}
              title={widget.title}
              caption={widget.caption}
              height={tileHeight(widget.layout.h)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
