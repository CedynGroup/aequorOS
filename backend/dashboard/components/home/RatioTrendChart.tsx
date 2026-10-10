"use client";

/**
 * Ratio trend — LCR, NSFR and CAR across every reporting period, merged from
 * the liquidity and capital dashboard trend series (per-period values the
 * backend computed from stored baseline runs or inline). LCR/NSFR read the
 * left axis, CAR its own right axis so the ~10–25% capital band stays legible
 * next to triple-digit liquidity ratios.
 *
 * This chart is the reason `scripts/assert-home-route-bundle.mjs` exists: it is
 * the only chart on the Command Center, and it is loaded through
 * `components/home/DeferredRatioTrendChart.tsx` so the charting runtime stays
 * out of the home route's initial JavaScript. The guard asserts both halves —
 * no runtime marker in the entry graph, and one present in the deferred chunk.
 */

import { useMemo, useState } from "react";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  gapAwareData,
  LINE_SERIES_BASE,
  tooltipHtml,
} from "@/lib/echartsOptions";
import RangeTabs, {
  RANGE_MONTHS,
  type RangePreset,
} from "@/components/ui/RangeTabs";
import ChartFrame from "@/components/ui/ChartFrame";
import { num, numOrNull } from "@/lib/api/values";
import { useModuleScope } from "@/components/shell/BankContext";
import { useEffectiveRatioDashboards } from "@/lib/api/hooks";

type TrendRow = {
  t: number;
  label: string;
  lcr?: number | null;
  nsfr?: number | null;
  car?: number;
};

export default function RatioTrendChart({
  bankId,
  periodId,
}: {
  bankId: string | undefined;
  periodId: string;
}) {
  const [range, setRange] = useState<RangePreset>("1Y");
  const tokens = useChartTokens();
  // An SDI does not file Basel LCR/NSFR (docs/sdi.md §4.6) — its capital headline
  // is the s.29 CAR; liquidity is supervised via LMTD on the Liquidity page.
  const moduleScope = useModuleScope();
  const isSdi = moduleScope.institutionClass === "sdi";
  const { liquidity: liq, capital: cap } = useEffectiveRatioDashboards(
    bankId,
    periodId,
    moduleScope.capitalAggregatedView === true,
    moduleScope.liquidityAggregatedView === true,
  );

  const rows = useMemo<TrendRow[]>(() => {
    const byPeriod = new Map<string, TrendRow>();
    for (const p of liq.data?.trend ?? []) {
      byPeriod.set(p.reportingPeriodId, {
        t: p.periodEnd.getTime(),
        label: p.label,
        lcr: numOrNull(p.lcrPct),
        nsfr: numOrNull(p.nsfrPct),
      });
    }
    for (const p of cap.data?.trend ?? []) {
      const existing = byPeriod.get(p.reportingPeriodId);
      if (existing) {
        existing.car = num(p.carPct);
      } else {
        byPeriod.set(p.reportingPeriodId, {
          t: p.periodEnd.getTime(),
          label: p.label,
          car: num(p.carPct),
        });
      }
    }
    const all = [...byPeriod.values()].sort((a, b) => a.t - b.t);
    const months = RANGE_MONTHS[range];
    return months === null ? all : all.slice(-months);
  }, [liq.data, cap.data, range]);

  const isLoading = liq.isLoading || cap.isLoading;
  const windowMove = (() => {
    // Narrowed to the readings that exist, so no `?? 0` stand-in is needed for
    // the arithmetic: a window with fewer than two LCR readings has no move.
    const withLcr = rows
      .map((row) => row.lcr)
      .filter(
        (value): value is number => value !== undefined && value !== null,
      );
    if (withLcr.length < 2) return null;
    return withLcr[withLcr.length - 1] - withLcr[0];
  })();
  const storedCount = (liq.data?.trend ?? []).filter((p) => p.stored).length;

  // The capital axis stays on the right even when it is the only axis, so that
  // an SDI's CAR sits where a bank's CAR sits.
  const capitalAxisIndex = isSdi ? 0 : 1;
  const pctAxisLabel = {
    formatter: (value: number) => `${Math.round(value)}%`,
  };

  const option: BiEChartsOption = {
    grid: { left: 4, right: 4, top: 12, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      axisLabel: { hideOverlap: true },
    },
    yAxis: isSdi
      ? [{ type: "value", position: "right", axisLabel: pctAxisLabel }]
      : [
          { type: "value", axisLabel: pctAxisLabel },
          { type: "value", position: "right", axisLabel: pctAxisLabel },
        ],
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const points = params as ReadonlyArray<{
          dataIndex: number;
          seriesName: string;
          color: string;
          value: number | null;
        }>;
        const first = points[0];
        if (!first) return "";
        const row = rows[first.dataIndex];
        return tooltipHtml(
          row ? row.label : null,
          points.map((entry) => ({
            label: entry.seriesName,
            color: entry.color,
            // A period the platform did not compute says so. Printing 0.00%
            // would report a ratio nobody measured.
            value:
              entry.value === null || entry.value === undefined
                ? "not computed"
                : `${entry.value.toFixed(2)}%`,
          })),
        );
      },
    },
    series: [
      ...(isSdi
        ? []
        : (
            [
              { name: "LCR", key: "lcr" as const, index: 0 },
              { name: "NSFR", key: "nsfr" as const, index: 1 },
            ] as const
          ).map((series) => ({
            ...LINE_SERIES_BASE,
            name: series.name,
            yAxisIndex: 0,
            smooth: true,
            lineStyle: { color: seriesColor(tokens, series.index), width: 1.8 },
            itemStyle: { color: seriesColor(tokens, series.index) },
            data: gapAwareData(rows.map((row) => row[series.key])),
          }))),
      {
        ...LINE_SERIES_BASE,
        name: "CAR",
        yAxisIndex: capitalAxisIndex,
        smooth: true,
        lineStyle: { color: seriesColor(tokens, 2), width: 1.8 },
        itemStyle: { color: seriesColor(tokens, 2) },
        data: gapAwareData(rows.map((row) => row.car)),
      },
    ],
  } as BiEChartsOption;

  return (
    <ChartFrame
      title="Ratio trend"
      subtitle={
        isSdi
          ? "CAR (s.29) per reporting period"
          : "LCR & NSFR (left axis) · CAR (right axis) per reporting period"
      }
      height={280}
      loading={isLoading}
      actions={<RangeTabs value={range} onChange={setRange} />}
      footer={
        <>
          <span>
            {rows.length} periods
            {!isSdi &&
              windowMove !== null &&
              ` · LCR ${windowMove >= 0 ? "+" : ""}${windowMove.toFixed(1)}pp over the window`}
            {" · "}
            {storedCount} with stored results
          </span>
        </>
      }
    >
      {rows.length === 0 ? (
        <div className="h-full flex items-center justify-center">
          <p className="text-body text-slate">
            No computed periods yet — activate data in the Data Engine to build
            the history.
          </p>
        </div>
      ) : (
        <EChart
          option={option}
          height={280}
          ariaLabel={`${
            isSdi ? "CAR" : "LCR, NSFR and CAR"
          } across ${rows.length} reporting periods, ${storedCount} of them from stored results`}
        />
      )}
    </ChartFrame>
  );
}
