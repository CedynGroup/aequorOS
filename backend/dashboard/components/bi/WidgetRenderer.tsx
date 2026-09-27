"use client";

/**
 * One widget, from one catalogue answer.
 *
 * The renderer decides between four outcomes and nothing else, in this order,
 * because the order carries the properties:
 *
 *   1. the server refused it        → `RestrictedWidget`, which names nothing;
 *   2. the server could not answer  → the failure, in words, with a retry;
 *   3. it answered with no figures  → `NeedsDataWidget`, never a zero;
 *   4. it answered                  → the chart or table the pack asked for.
 *
 * Step 3 comes before step 4 on purpose. A chart handed an empty series draws a
 * flat line on the baseline, which is a picture of a measurement that never
 * happened; a board reader cannot tell it from a real run of zeros.
 *
 * Everything drawn here comes from the answer. The renderer computes no ratio,
 * supplies no threshold and carries no floor: a widget shows what the catalogue
 * measured, badged with the trust verdict the server attached.
 */

import { useMemo, type ReactNode } from "react";
import { Info } from "lucide-react";
import type { BiQuery, BiQueryResult } from "@aequoros/risk-service-api";
import ChartFrame from "@/components/ui/ChartFrame";
import DataTable, { type Column } from "@/components/ui/DataTable";
import KpiStat from "@/components/ui/KpiStat";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import DrillAction from "./DrillAction";
import EChart, { type BiEChartsOption } from "./EChart";
import NeedsDataWidget from "./NeedsDataWidget";
import RestrictedWidget from "./RestrictedWidget";
import TrustBadge from "./TrustBadge";
import { isBiAccessDenied } from "@/lib/api/bi";
import { drillDestinations } from "./drill";
import {
  formatCell,
  cellNumber,
  hasNoMeasuredValue,
  isEmptyResult,
  measureColumns,
  rowLabel,
} from "./result";
import type { BiWidgetKind, BiWidgetSpec } from "./types";

/** Pixel height of one react-grid-layout row, matching the canvas. */
export const WIDGET_ROW_HEIGHT = 76;
const WIDGET_CHROME_HEIGHT = 96;
const MIN_CHART_HEIGHT = 160;

export function widgetBodyHeight(rows: number): number {
  return Math.max(
    MIN_CHART_HEIGHT,
    rows * WIDGET_ROW_HEIGHT - WIDGET_CHROME_HEIGHT,
  );
}

type RowShape = { label: string; cells: readonly unknown[] };

function chartOption(
  kind: BiWidgetKind,
  result: BiQueryResult,
): BiEChartsOption {
  const measures = measureColumns(result);
  const categories = result.rows.map((row) => rowLabel(result, row));
  const measureIndices = result.columns
    .map((column, index) => ({ column, index }))
    .filter((entry) => entry.column.kind === "measure");

  if (kind === "pie" || kind === "donut") {
    const first = measureIndices[0];
    return {
      tooltip: { trigger: "item" },
      legend: { bottom: 0, type: "scroll" },
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          data: result.rows.map((row, rowIndex) => ({
            name: categories[rowIndex],
            value: first ? cellNumber(row[first.index]) : null,
          })),
          label: { show: false },
        },
      ],
    } as BiEChartsOption;
  }

  const stacked = kind === "stacked_bar";
  // An `area` is a line with the space under it filled. It stays a LINE series
  // so `connectNulls: false` keeps holding: an area chart that bridged an
  // unmeasured month would fill the gap as well as span it.
  const line = kind === "line" || kind === "area";
  return {
    tooltip: { trigger: "axis" },
    legend:
      measures.length > 1 ? { bottom: 0, type: "scroll" } : { show: false },
    xAxis: { type: "category", data: categories },
    yAxis: { type: "value" },
    series: measureIndices.map((entry) => ({
      name: entry.column.label,
      type: line ? "line" : "bar",
      areaStyle: kind === "area" ? {} : undefined,
      stack: stacked ? "total" : undefined,
      // A null cell stays null: ECharts leaves a gap, which is what an
      // unmeasured point is. Substituting 0 would draw a measurement.
      data: result.rows.map((row) => cellNumber(row[entry.index])),
      connectNulls: false,
    })),
  } as BiEChartsOption;
}

function tableColumns(
  result: BiQueryResult,
  query: BiQuery | undefined,
): Column<RowShape>[] {
  const columns: Column<RowShape>[] = result.columns.map((column, index) => ({
    key: `${column.id}:${index}`,
    header: column.label,
    align: column.kind === "measure" ? "right" : "left",
    numeric: column.kind === "measure",
    render: (row: RowShape) => formatCell(row.cells[index], column.format),
  }));
  if (!query) return columns;
  // The drill column exists only when at least one row can be carried into a
  // destination exactly. An always-present column of blanks would read as a
  // broken action rather than as an answer this table cannot give.
  const carryable = result.rows.some(
    (row) => drillDestinations(result, row, query).length > 0,
  );
  if (!carryable) return columns;
  return [
    ...columns,
    {
      key: "drill",
      header: "Rows behind",
      align: "right",
      render: (row: RowShape) => (
        <DrillAction
          destinations={drillDestinations(result, row.cells, query)}
          rowLabel={row.label}
        />
      ),
    },
  ];
}

export default function WidgetRenderer({
  spec,
  result,
  isLoading,
  error,
  onRetry,
  onExplain,
  actions,
  query,
}: {
  spec: BiWidgetSpec;
  result: BiQueryResult | null | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry?: () => void;
  /** Opens the explain drawer for one measure of this widget's query. */
  onExplain?: (measureId: string) => void;
  /** Extra controls for the frame's action slot, such as an export menu. */
  actions?: ReactNode;
  /**
   * The query this answer came from, AS SUBMITTED — the widget's own narrowing
   * merged with the page's date and filter bar. Drill-through needs it and
   * `spec.query` will not do: that is the widget as authored, so a drill built
   * from it would carry neither the reader's chosen date nor the filters they
   * narrowed by, and would land on a wider book than the figure they clicked.
   * Omitted, no drill action is offered at all.
   */
  query?: BiQuery;
}) {
  const height = widgetBodyHeight(spec.layout.h);

  const drawsAChart =
    spec.kind !== "table" && spec.kind !== "kpi" && spec.kind !== "kpi_row";
  const option = useMemo(
    () => (result && drawsAChart ? chartOption(spec.kind, result) : null),
    [drawsAChart, result, spec.kind],
  );

  if (isLoading) {
    return (
      <ChartFrame
        title={spec.title}
        subtitle={spec.subtitle}
        height={height}
        loading
      >
        <div />
      </ChartFrame>
    );
  }

  if (isBiAccessDenied(error)) {
    return <RestrictedWidget height={height + WIDGET_CHROME_HEIGHT} />;
  }

  if (error) {
    return (
      <section className="card p-5">
        <ErrorPanel error={error} onRetry={onRetry} title={spec.title} />
      </section>
    );
  }

  if (!result || isEmptyResult(result) || hasNoMeasuredValue(result)) {
    return (
      <NeedsDataWidget
        dataset={spec.dataset}
        // The widget's own words, so a canvas of gaps says WHICH views are
        // waiting rather than only that something is. This is a widget the reader
        // was granted; a refusal is the branch above and names nothing.
        title={spec.title}
        caption={spec.subtitle}
        height={height + WIDGET_CHROME_HEIGHT}
      />
    );
  }

  const measures = measureColumns(result);
  const primaryMeasure = measures[0];
  const frameActions = (
    <>
      <TrustBadge
        status={result.trust?.status}
        failingChecks={result.trust?.failingChecks ?? []}
        size="compact"
      />
      {onExplain && primaryMeasure?.memberId && (
        <button
          type="button"
          onClick={() => onExplain(primaryMeasure.memberId as string)}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-caption font-medium text-slate hover:bg-surface"
        >
          <Info size={12} aria-hidden />
          Explain
        </button>
      )}
      {actions}
    </>
  );

  if (spec.kind === "kpi" || spec.kind === "kpi_row") {
    // A `kpi` states ONE figure; a `kpi_row` states every measure the widget
    // asked for, side by side. Showing only the first of a row would drop the
    // rest silently — a tile headed "Capital adequacy" that omits Tier 1 is
    // worse than one that prints both plainly — so the row renders one stat per
    // measure column and the single KPI renders exactly one.
    const firstRow = result.rows[0] ?? [];
    const shown = spec.kind === "kpi" ? measures.slice(0, 1) : measures;
    return (
      <section
        className="card flex flex-col gap-3 p-4"
        style={{ minHeight: height }}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-h3 text-navy">{spec.title}</p>
            {spec.subtitle && (
              <p className="mt-0.5 text-caption text-slate">{spec.subtitle}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">{frameActions}</div>
        </div>
        <div
          className={
            shown.length > 1
              ? "grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
              : undefined
          }
        >
          {shown.map((measure) => {
            const index = result.columns.findIndex(
              (column) => column.id === measure.id,
            );
            return (
              <KpiStat
                key={measure.id}
                label={measure.label}
                // An absent cell formats to the em dash, never to a number.
                value={formatCell(
                  index >= 0 ? firstRow[index] : null,
                  measure.format,
                )}
                className="border-0 shadow-none p-0"
              />
            );
          })}
        </div>
      </section>
    );
  }

  if (spec.kind === "table") {
    return (
      <section className="card overflow-hidden" style={{ minHeight: height }}>
        <div className="flex items-start justify-between gap-4 border-b border-border-light px-5 py-4">
          <div className="min-w-0">
            <h3 className="text-h3 text-navy">{spec.title}</h3>
            {spec.subtitle && (
              <p className="mt-0.5 text-caption text-slate">{spec.subtitle}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">{frameActions}</div>
        </div>
        <DataTable<RowShape>
          columns={tableColumns(result, query)}
          rows={result.rows.map((row) => ({
            label: rowLabel(result, row),
            cells: row,
          }))}
          density="compact"
          stickyHeader
          maxHeight={height}
          scrollLabel={spec.title}
        />
        {result.truncated && (
          <p className="border-t border-border-light px-5 py-2 text-caption text-slate">
            More rows match than this view shows. Narrow the filters, or open
            Explore to page through the full answer.
          </p>
        )}
      </section>
    );
  }

  return (
    <ChartFrame
      title={spec.title}
      subtitle={spec.subtitle}
      actions={frameActions}
      height={height}
      footer={
        result.truncated ? (
          <span>
            More rows match than this chart shows. Narrow the filters, or open
            Explore to page through the full answer.
          </span>
        ) : undefined
      }
    >
      {option && (
        <EChart
          option={option}
          height={height}
          ariaLabel={`${spec.title}. ${result.rows.length} ${
            result.rows.length === 1 ? "group" : "groups"
          } measured for the selected date.`}
        />
      )}
    </ChartFrame>
  );
}
