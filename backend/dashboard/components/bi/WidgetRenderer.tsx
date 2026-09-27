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
import type { BiQueryResult } from "@aequoros/risk-service-api";
import ChartFrame from "@/components/ui/ChartFrame";
import DataTable, { type Column } from "@/components/ui/DataTable";
import KpiStat from "@/components/ui/KpiStat";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import EChart, { type BiEChartsOption } from "./EChart";
import NeedsDataWidget from "./NeedsDataWidget";
import RestrictedWidget from "./RestrictedWidget";
import TrustBadge from "./TrustBadge";
import { isBiAccessDenied } from "@/lib/api/bi";
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

  if (kind === "pie") {
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
  return {
    tooltip: { trigger: "axis" },
    legend:
      measures.length > 1 ? { bottom: 0, type: "scroll" } : { show: false },
    xAxis: { type: "category", data: categories },
    yAxis: { type: "value" },
    series: measureIndices.map((entry) => ({
      name: entry.column.label,
      type: kind === "line" ? "line" : "bar",
      stack: stacked ? "total" : undefined,
      // A null cell stays null: ECharts leaves a gap, which is what an
      // unmeasured point is. Substituting 0 would draw a measurement.
      data: result.rows.map((row) => cellNumber(row[entry.index])),
      connectNulls: false,
    })),
  } as BiEChartsOption;
}

function tableColumns(result: BiQueryResult): Column<RowShape>[] {
  return result.columns.map((column, index) => ({
    key: `${column.id}:${index}`,
    header: column.label,
    align: column.kind === "measure" ? "right" : "left",
    numeric: column.kind === "measure",
    render: (row: RowShape) => formatCell(row.cells[index], column.format),
  }));
}

export default function WidgetRenderer({
  spec,
  result,
  isLoading,
  error,
  onRetry,
  onExplain,
  actions,
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
}) {
  const height = widgetBodyHeight(spec.layout.h);

  const option = useMemo(
    () =>
      result && spec.kind !== "table" && spec.kind !== "kpi"
        ? chartOption(spec.kind, result)
        : null,
    [result, spec.kind],
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

  if (spec.kind === "kpi") {
    const firstRow = result.rows[0] ?? [];
    const index = result.columns.findIndex(
      (column) => column.id === primaryMeasure?.id,
    );
    const raw = index >= 0 ? firstRow[index] : null;
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
        <KpiStat
          label={primaryMeasure?.label ?? spec.title}
          value={formatCell(raw, primaryMeasure?.format ?? "text")}
          className="border-0 shadow-none p-0"
        />
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
          columns={tableColumns(result)}
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
