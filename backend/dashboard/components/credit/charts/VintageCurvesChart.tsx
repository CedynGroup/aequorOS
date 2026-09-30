"use client";

/**
 * Cohort PAR30+ curves: x = months on book, one line per cohort. The four
 * most recent cohorts take the series palette; older cohorts render as thin
 * grid-toned context lines. Holes in a cohort's observation history stay
 * holes — never interpolated. `gapAwareData` adds the other half of that rule:
 * a cohort observed in exactly one month still shows that month.
 */

import type { VintageCohortRead } from "@aequoros/risk-service-api";
import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import { seriesColor, useChartTokens } from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  gapAwareData,
  LINE_SERIES_BASE,
  type SeriesValue,
} from "@/lib/echartsOptions";
import { num } from "@/lib/api/values";

export default function VintageCurvesChart({
  cohorts,
  height = 300,
}: {
  cohorts: VintageCohortRead[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const maxAge = Math.max(
    0,
    ...cohorts.flatMap((cohort) =>
      cohort.points.map((point) => point.monthsOnBook),
    ),
  );
  const ages = Array.from({ length: maxAge + 1 }, (_, age) => age);
  const labels = ages.map((age) => String(age));
  const titles = ages.map((age) => `Month ${age} on book`);
  const recent = cohorts.slice(-4).map((cohort) => cohort.cohort);

  const option: BiEChartsOption = {
    grid: { left: 4, right: 12, top: 12, bottom: 24, containLabel: true },
    legend: {
      bottom: 0,
      type: "scroll",
      // Only the highlighted cohorts are named; the context lines are there for
      // shape, and a legend of twenty vintages would be noise.
      data: recent,
    },
    xAxis: { type: "category", data: labels, axisLabel: { hideOverlap: true } },
    yAxis: {
      type: "value",
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(titles, (value) => `${value.toFixed(2)}%`, {
        absent: "not yet observed",
        hideAbsent: true,
      }),
    },
    series: cohorts.map((cohort) => {
      const highlightIndex = recent.indexOf(cohort.cohort);
      const highlighted = highlightIndex >= 0;
      const color = highlighted
        ? seriesColor(tokens, highlightIndex)
        : tokens.grid;
      const byAge = new Map(
        cohort.points.map((point) => [
          point.monthsOnBook,
          num(point.par30Pct) as SeriesValue,
        ]),
      );
      return {
        ...LINE_SERIES_BASE,
        name: cohort.cohort,
        smooth: true,
        lineStyle: { color, width: highlighted ? 2 : 1 },
        itemStyle: { color },
        data: gapAwareData(ages.map((age) => byAge.get(age) ?? null)),
      };
    }),
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`PAR30 plus curves for ${cohorts.length} origination cohorts over up to ${maxAge} months on book`}
    />
  );
}
