"use client";

/**
 * Curve board — multi-curve aware: one line per PUBLISHED CURVE (keyed by
 * curve_name, never collapsed per currency), so the desk's zero / forward /
 * discounting families render side by side. Rates arrive as decimal
 * fractions (0.185) and render as percentages; every curve carries its
 * AEQ.* ticker chip, curve-type badge, and source attribution + freshness.
 *
 * Base-vs-adjusted: the official published curve is a solid seriesColor
 * line; the bank's private overlay composition ("Your adjusted") renders as
 * a dashed accent line (spec §11b). Adjusted series exist only when the
 * bank has active overlays on that curve name.
 */

import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import type { YieldCurveViewRead } from "@aequoros/risk-service-api";
import ChartFrame from "@/components/ui/ChartFrame";
import SectionCard from "@/components/ui/SectionCard";
import SubTabs from "@/components/ui/SubTabs";
import DataTable, { type Column } from "@/components/ui/DataTable";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  gapAwareData,
  LINE_SERIES_BASE,
} from "@/lib/echartsOptions";
import { num, fmtDateUTC } from "@/lib/api/values";
import { fmtPct } from "@/lib/format";
import PermissionAction from "./PermissionAction";
import AttributionChip from "./AttributionChip";
import { CurveTypeBadge, MonoChip, SyntheticProxyBadge } from "./chips";

/** "1M", "9M", "1Y", "18M", "5Y" — tenor-months axis label. */
export function tenorLabel(months: number): string {
  if (months < 12 || months % 12 !== 0) return `${months}M`;
  return `${months / 12}Y`;
}

const ADJUSTED_SUFFIX = " · yours";

type CurvePoint = { tenorMonths: number } & Record<string, number>;

/** Merge every curve (and any adjusted series) into one tenor-keyed table. */
function mergePoints(
  curves: YieldCurveViewRead[],
  includeAdjusted: boolean,
): CurvePoint[] {
  const byTenor = new Map<number, CurvePoint>();
  const row = (tenorMonths: number): CurvePoint => {
    const existing = byTenor.get(tenorMonths);
    if (existing) return existing;
    const created = { tenorMonths } as CurvePoint;
    byTenor.set(tenorMonths, created);
    return created;
  };
  for (const curve of curves) {
    for (const point of curve.points) {
      row(point.tenorMonths)[curve.curveName] = num(point.rate) * 100;
    }
    if (includeAdjusted) {
      for (const point of curve.adjustedPoints) {
        row(point.tenorMonths)[`${curve.curveName}${ADJUSTED_SUFFIX}`] =
          num(point.rate) * 100;
      }
    }
  }
  return [...byTenor.values()].sort((a, b) => a.tenorMonths - b.tenorMonths);
}

export default function CurveBoard({
  curves,
  onEditOverlays,
  editOverlaysReason,
}: {
  curves: YieldCurveViewRead[];
  onEditOverlays?: (curveName: string) => void;
  /** The grant the user lacks for the spread editor; the controls stay visible. */
  editOverlaysReason?: string;
}) {
  const [view, setView] = useState<"official" | "adjusted">("official");
  const hasAdjusted = curves.some((curve) => curve.adjustedPoints.length > 0);
  const showAdjusted = view === "adjusted";
  const chartData = mergePoints(curves, showAdjusted);

  const tableColumns: Column<CurvePoint>[] = [
    {
      key: "tenor",
      header: "Tenor",
      render: (row) => (
        <span className="font-mono text-caption">
          {tenorLabel(row.tenorMonths)}
        </span>
      ),
      width: "12%",
    },
    ...curves.flatMap((curve) => {
      const columns: Column<CurvePoint>[] = [
        {
          key: curve.curveName,
          header: curve.curveName,
          numeric: true,
          render: (row: CurvePoint) => {
            const value = row[curve.curveName];
            return value === undefined ? "—" : fmtPct(value, 2);
          },
        },
      ];
      if (showAdjusted && curve.adjustedPoints.length > 0) {
        columns.push({
          key: `${curve.curveName}${ADJUSTED_SUFFIX}`,
          header: `${curve.curveName} (yours)`,
          numeric: true,
          render: (row: CurvePoint) => {
            const value = row[`${curve.curveName}${ADJUSTED_SUFFIX}`];
            return value === undefined ? (
              "—"
            ) : (
              <span className="text-action">{fmtPct(value, 2)}</span>
            );
          },
        });
      }
      return columns;
    }),
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <SubTabs
          items={[
            { key: "official", label: "Official published" },
            { key: "adjusted", label: "Your adjusted" },
          ]}
          active={view}
          onChange={(key) => setView(key as "official" | "adjusted")}
        />
        {onEditOverlays && curves.length > 0 && (
          <PermissionAction
            reason={editOverlaysReason}
            onClick={() => onEditOverlays(curves[0].curveName)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-action border border-action/30 rounded-sm hover:bg-action-light whitespace-nowrap"
          >
            <SlidersHorizontal size={13} aria-hidden />
            Edit spreads
          </PermissionAction>
        )}
      </div>

      {showAdjusted && !hasAdjusted && (
        <p className="text-caption text-slate">
          No active spreads configured. Your adjusted curve equals the official
          published curve until you add overlay spreads
          {onEditOverlays ? " via “Edit spreads”" : ""}.
        </p>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <ChartFrame
          title="Yield curves"
          subtitle={
            showAdjusted
              ? "Official published (solid) vs your adjusted composition (dashed)"
              : "Every published curve at the as-of date, keyed by curve name"
          }
          height={300}
          footer={
            <>
              {curves.map((curve) => (
                <span
                  key={curve.curveName}
                  className="inline-flex items-center gap-1.5 flex-wrap"
                >
                  <span className="font-medium text-navy">
                    {curve.currency}
                  </span>
                  <MonoChip>{curve.curveName}</MonoChip>
                  <CurveTypeBadge curveType={curve.curveType} />
                  {curve.curveType === "discount" && <SyntheticProxyBadge />}
                  <AttributionChip attribution={curve.attribution} />
                  {onEditOverlays && (
                    <PermissionAction
                      reason={editOverlaysReason}
                      onClick={() => onEditOverlays(curve.curveName)}
                      className="text-caption text-action hover:underline"
                    >
                      {curve.overlayComponents.length > 0
                        ? `Spreads (${curve.overlayComponents.length})`
                        : "Add spread"}
                    </PermissionAction>
                  )}
                </span>
              ))}
            </>
          }
        >
          <CurveLines
            chartData={chartData}
            curves={curves}
            showAdjusted={showAdjusted}
          />
        </ChartFrame>

        <SectionCard
          title="Curve points"
          subtitle="Per-tenor rates behind the chart"
          noPadding
          footer={
            <span>
              As of{" "}
              <span className="font-mono text-navy">
                {[
                  ...new Set(curves.map((curve) => fmtDateUTC(curve.asOfDate))),
                ].join(" · ")}
              </span>
            </span>
          }
        >
          <DataTable
            columns={tableColumns}
            rows={chartData}
            density="compact"
            stickyHeader
            maxHeight={300}
          />
        </SectionCard>
      </div>
    </div>
  );
}

/**
 * Every curve for one currency on one tenor axis, plus each curve's adjusted
 * series when the "yours" view is on.
 *
 * A tenor one curve quotes and another does not stays a GAP. The Recharts version
 * carried `connectNulls`, which drew a straight segment across a tenor the curve
 * was never published at; `gapAwareData` keeps the hole and gives a lone quote its
 * own marker so it is still visible.
 */
function CurveLines({
  chartData,
  curves,
  showAdjusted,
}: {
  chartData: CurvePoint[];
  curves: YieldCurveViewRead[];
  showAdjusted: boolean;
}) {
  const tokens = useChartTokens();
  const labels = chartData.map((row) => tenorLabel(row.tenorMonths));
  const adjustedCurves = showAdjusted
    ? curves.filter((curve) => curve.adjustedPoints.length > 0)
    : [];

  const option: BiEChartsOption = {
    grid: { left: 4, right: 12, top: 16, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "category", data: labels, axisLabel: { hideOverlap: true } },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: (value: number) => `${value.toFixed(1)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(
        labels.map((label) => `Tenor ${label}`),
        (value) => fmtPct(value, 2),
        { absent: "not published at this tenor", hideAbsent: true },
      ),
    },
    series: [
      ...curves.map((curve, index) => ({
        ...LINE_SERIES_BASE,
        name: curve.curveName,
        smooth: true,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: seriesColor(tokens, index), width: 2 },
        itemStyle: { color: seriesColor(tokens, index) },
        data: gapAwareData(chartData.map((row) => row[curve.curveName])),
      })),
      ...adjustedCurves.map((curve) => ({
        ...LINE_SERIES_BASE,
        name: `${curve.curveName} — your spread`,
        smooth: true,
        showSymbol: true,
        symbolSize: 5,
        lineStyle: { color: tokens.accent, width: 2, type: "dashed" as const },
        itemStyle: { color: tokens.accent },
        data: gapAwareData(
          chartData.map((row) => row[`${curve.curveName}${ADJUSTED_SUFFIX}`]),
        ),
      })),
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={300}
      ariaLabel={`${curves.length} published curves across ${chartData.length} tenors${
        adjustedCurves.length > 0
          ? `, with ${adjustedCurves.length} of them also shown with this bank's spreads applied`
          : ""
      }`}
    />
  );
}
