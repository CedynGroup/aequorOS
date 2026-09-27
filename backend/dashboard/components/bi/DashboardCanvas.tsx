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
 * The layout is react-grid-layout's item shape (`i/x/y/w/h` on twelve columns),
 * placed here with CSS grid. Keeping the authored shape means the Phase 3
 * builder — which does use the library, for dragging — and this read-only
 * canvas lay a pack out identically.
 */

import type { BiFilter } from "@aequoros/risk-service-api";
import { useBiQuery } from "@/lib/api/bi";
import WidgetRenderer, { WIDGET_ROW_HEIGHT } from "./WidgetRenderer";
import { effectiveQuery, type BiWindowSelection } from "./query";
import type { BiDashboard, BiWidgetSpec } from "./types";

const COLUMNS = 12;

function DashboardWidget({
  bankId,
  spec,
  selection,
  filters,
  onExplain,
}: {
  bankId: string | undefined;
  spec: BiWidgetSpec;
  selection: BiWindowSelection;
  filters: readonly BiFilter[];
  onExplain?: (
    measureId: string,
    query: ReturnType<typeof effectiveQuery>,
  ) => void;
}) {
  const query = effectiveQuery(spec.query, selection, filters);
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
      // The query AS SUBMITTED, so a drill-through carries the page's date and
      // filters and not just the widget as authored.
      query={query}
    />
  );
}

export default function DashboardCanvas({
  bankId,
  dashboard,
  selection,
  filters,
  onExplain,
}: {
  bankId: string | undefined;
  dashboard: BiDashboard;
  selection: BiWindowSelection;
  filters: readonly BiFilter[];
  onExplain?: (
    measureId: string,
    query: ReturnType<typeof effectiveQuery>,
  ) => void;
}) {
  const ordered = [...dashboard.widgets].sort((left, right) =>
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
      {ordered.map((spec) => (
        <div
          key={spec.id}
          style={{
            gridColumn: `span ${Math.min(COLUMNS, Math.max(1, spec.layout.w))}`,
            gridRow: `span ${Math.max(1, spec.layout.h)}`,
          }}
        >
          <DashboardWidget
            bankId={bankId}
            spec={spec}
            selection={selection}
            filters={filters}
            onExplain={onExplain}
          />
        </div>
      ))}
    </div>
  );
}
