"use client";

/**
 * The SDI capital-review charts. Every figure is a control-plane or engine
 * output; these components only position it.
 *
 * Two properties are load-bearing across the file. An unresolved threshold draws
 * NO line and colours nothing (an SDI whose s.29 minimum did not resolve is shown
 * unjudged, not shown as compliant), and a nullable figure stays absent rather
 * than becoming zero.
 */

import EChart, { type BiEChartsOption } from "@/components/bi/EChart";
import {
  seriesColor,
  useChartTokens,
  type BiChartTokens,
} from "@/components/bi/echartsTheme";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  gapAwareData,
  itemTooltip,
  LINE_SERIES_BASE,
  thresholdMarkLine,
} from "@/lib/echartsOptions";
import { fmtCurrency } from "@/lib/format";

export type SdiCapitalControl = {
  label: string;
  actual: number | null;
  required: number | null;
};

export type SdiRiskWeightBand = {
  label: string;
  exposure: number;
  rwa: number;
  weightPct: number;
};

/** The currency-axis label every one of these charts uses. */
function currencyAxisLabel() {
  return {
    // Currency ticks are wide ("GHS 100M"); ECharts hides overlapping CATEGORY
    // labels by default but not value ones, so say so.
    hideOverlap: true,
    formatter: (value: number) =>
      fmtCurrency(value, undefined, { decimals: 0 }),
  };
}

function horizontalCategoryAxis(labels: readonly string[]) {
  return {
    type: "category" as const,
    inverse: true,
    data: [...labels],
    axisLine: { show: false },
    splitLine: { show: false },
  };
}

export function SdiCarThresholdChart({
  carPct,
  carMinPct,
  height = 180,
}: {
  carPct: number | null;
  /** The s.29 minimum from the control plane, or null when it is unresolved:
   *  no threshold line is drawn and the bar takes no compliance colour. */
  carMinPct: number | null;
  height?: number;
}) {
  const tokens = useChartTokens();
  if (carPct === null) return null;
  // Axis scale only — never a floor. An unresolved minimum simply drops out of
  // the extent rather than entering it as a zero (which would also read as a
  // `?? 0` floor fallback to anyone scanning this file, and to the guard).
  const extents = carMinPct === null ? [carPct] : [carPct, carMinPct];
  const ceiling = Math.max(...extents) * 1.2 || 10;
  const barColor =
    carMinPct === null
      ? seriesColor(tokens, 0)
      : carPct >= carMinPct
        ? tokens.favourable
        : tokens.adverse;
  const labels = ["Capital adequacy ratio"];

  const option: BiEChartsOption = {
    grid: { left: 8, right: 28, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "value",
      min: 0,
      max: ceiling,
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    yAxis: { ...horizontalCategoryAxis(labels), show: false },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, () => [
        { label: "CAR", value: `${carPct.toFixed(2)}%`, color: barColor },
      ]),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "CAR",
        barMaxWidth: 30,
        itemStyle: { color: barColor, borderRadius: [0, 3, 3, 0] },
        data: [carPct],
        ...(carMinPct === null
          ? {}
          : {
              markLine: thresholdMarkLine([
                {
                  axis: "x",
                  value: carMinPct,
                  label: `Minimum ${carMinPct % 1 === 0 ? carMinPct.toFixed(0) : carMinPct.toFixed(2)}%`,
                  color: tokens.adverse,
                  labelPosition: "end",
                },
              ]),
            }),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Capital adequacy ratio ${carPct.toFixed(2)}%${
        carMinPct === null
          ? ", with no s.29 minimum resolved"
          : ` against a minimum of ${carMinPct}%`
      }`}
    />
  );
}

export function SdiRwaCompositionChart({
  data,
  height = 230,
}: {
  data: SdiRiskWeightBand[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((band) => band.label);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 4, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "value", axisLabel: currencyAxisLabel() },
    yAxis: horizontalCategoryAxis(labels),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip(labels, (value, seriesName, index) => {
        const band = data[index];
        return seriesName === "RWA" && band !== undefined
          ? `${fmtCurrency(value)} @ ${band.weightPct.toFixed(0)}% RW`
          : fmtCurrency(value);
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Exposure",
        barMaxWidth: 16,
        itemStyle: {
          color: seriesColor(tokens, 0),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((band) => band.exposure),
      },
      {
        ...BAR_SERIES_BASE,
        name: "RWA",
        barMaxWidth: 16,
        itemStyle: {
          color: seriesColor(tokens, 2),
          borderRadius: [0, 2, 2, 0],
        },
        data: data.map((band) => band.rwa),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Exposure and risk-weighted assets across ${data.length} risk-weight bands`}
    />
  );
}

/** A grouped pair of currency bars over a category axis — used three times below. */
function GroupedCurrencyBars({
  labels,
  series,
  height,
  ariaLabel,
  barMaxWidth,
  tokens,
}: {
  labels: readonly string[];
  series: ReadonlyArray<{
    name: string;
    color: string;
    values: ReadonlyArray<number | null>;
  }>;
  height: number;
  ariaLabel: string;
  barMaxWidth: number;
  tokens: BiChartTokens;
}) {
  const option: BiEChartsOption = {
    grid: { left: 8, right: 8, top: 8, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: { type: "category", data: [...labels], axisLabel: { interval: 0 } },
    yAxis: {
      type: "value",
      axisLine: { show: true, lineStyle: { color: tokens.axis } },
      axisLabel: currencyAxisLabel(),
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip([...labels], (value) => fmtCurrency(value), {
        absent: "not reported",
      }),
    },
    series: series.map((entry) => ({
      ...BAR_SERIES_BASE,
      name: entry.name,
      barMaxWidth,
      itemStyle: { color: entry.color, borderRadius: [3, 3, 0, 0] },
      data: [...entry.values],
    })),
  } as BiEChartsOption;

  return <EChart option={option} height={height} ariaLabel={ariaLabel} />;
}

export function SdiCapitalControlsChart({
  data,
  height = 220,
}: {
  data: SdiCapitalControl[];
  height?: number;
}) {
  const tokens = useChartTokens();
  return (
    <GroupedCurrencyBars
      tokens={tokens}
      labels={data.map((control) => control.label)}
      barMaxWidth={36}
      height={height}
      ariaLabel={`Actual against required capital for ${data.length} statutory controls`}
      series={[
        {
          name: "Actual",
          color: tokens.accent,
          values: data.map((control) => control.actual),
        },
        {
          name: "Required",
          color: seriesColor(tokens, 3),
          values: data.map((control) => control.required),
        },
      ]}
    />
  );
}

export type SdiCapitalTrendPoint = {
  asOf: string;
  carPct: number | null;
  /** null when the ratio was not computed for the date — never 0, which would
   *  plot the best possible reading of an absent figure (audit A360-6). */
  nplPct: number | null;
};

export function SdiCapitalTrendChart({
  data,
  carMinPct,
  height = 240,
}: {
  data: SdiCapitalTrendPoint[];
  /** Null when the s.29 minimum is unresolved — no floor line is drawn. */
  carMinPct: number | null;
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((point) => point.asOf);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 16, top: 12, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    tooltip: {
      trigger: "axis",
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`),
    },
    series: [
      {
        ...LINE_SERIES_BASE,
        name: "CAR",
        smooth: true,
        lineStyle: { color: tokens.favourable, width: 2 },
        itemStyle: { color: tokens.favourable },
        // A snapshot with no computed CAR leaves a GAP. The Recharts version
        // carried `connectNulls`, which drew a straight segment across an
        // as-of date the engine produced no ratio for.
        data: gapAwareData(data.map((point) => point.carPct)),
        ...(carMinPct === null
          ? {}
          : {
              markLine: thresholdMarkLine([
                {
                  axis: "y",
                  value: carMinPct,
                  label: "CAR floor",
                  color: tokens.adverse,
                  labelPosition: "end",
                },
              ]),
            }),
      },
      {
        ...LINE_SERIES_BASE,
        name: "NPL ratio",
        smooth: true,
        lineStyle: { color: seriesColor(tokens, 3), width: 2 },
        itemStyle: { color: seriesColor(tokens, 3) },
        data: gapAwareData(data.map((point) => point.nplPct)),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Capital adequacy and NPL ratio across ${data.length} snapshots${
        carMinPct === null ? ", with no CAR floor resolved" : ""
      }`}
    />
  );
}

export type SdiLoanQualityBand = {
  grade: string;
  exposure: number;
  provision: number;
};

export function SdiLoanQualityChart({
  data,
  height = 250,
}: {
  data: SdiLoanQualityBand[];
  height?: number;
}) {
  const tokens = useChartTokens();
  return (
    <GroupedCurrencyBars
      tokens={tokens}
      labels={data.map((band) => band.grade)}
      barMaxWidth={34}
      height={height}
      ariaLabel={`Exposure and required provision across ${data.length} loan-classification grades`}
      series={[
        {
          name: "Exposure",
          color: seriesColor(tokens, 0),
          values: data.map((band) => band.exposure),
        },
        {
          name: "Required provision",
          color: seriesColor(tokens, 3),
          values: data.map((band) => band.provision),
        },
      ]}
    />
  );
}

export type SdiDelinquencyBand = {
  label: string;
  exposure: number;
  count: number;
  severity: "current" | "early" | "npl" | "loss";
};

export function SdiDelinquencyChart({
  data,
  height = 250,
}: {
  data: SdiDelinquencyBand[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const labels = data.map((band) => band.label);
  const color = (severity: SdiDelinquencyBand["severity"]) => {
    if (severity === "loss") return tokens.adverse;
    if (severity === "npl") return seriesColor(tokens, 3);
    if (severity === "early") return tokens.caution;
    return seriesColor(tokens, 0);
  };

  const option: BiEChartsOption = {
    grid: { left: 8, right: 8, top: 8, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: labels, axisLabel: { interval: 0 } },
    yAxis: { type: "value", axisLabel: currencyAxisLabel() },
    tooltip: {
      trigger: "item",
      formatter: itemTooltip(labels, (index) => {
        const band = data[index];
        return band === undefined
          ? []
          : [
              {
                label: "Exposure",
                value: `${fmtCurrency(band.exposure)} · ${band.count} loan(s)`,
                color: color(band.severity),
              },
            ];
      }),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Exposure",
        barMaxWidth: 36,
        data: data.map((band) => ({
          value: band.exposure,
          itemStyle: {
            color: color(band.severity),
            borderRadius: [3, 3, 0, 0],
          },
        })),
      },
    ],
  } as BiEChartsOption;

  return (
    <EChart
      option={option}
      height={height}
      ariaLabel={`Loan exposure across ${data.length} delinquency bands`}
    />
  );
}

export type SdiExposureConcentration = {
  name: string;
  pctNetOwnFunds: number;
  limitPct: number;
  aboveLimit: boolean;
};

export function SdiExposureConcentrationChart({
  data,
  height,
}: {
  data: SdiExposureConcentration[];
  height?: number;
}) {
  const tokens = useChartTokens();
  const chartHeight = height ?? Math.max(190, data.length * 36 + 44);
  const labels = data.map((row) => row.name);

  const option: BiEChartsOption = {
    grid: { left: 8, right: 18, top: 4, bottom: 24, containLabel: true },
    legend: { bottom: 0, type: "scroll" },
    xAxis: {
      type: "value",
      axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` },
    },
    yAxis: horizontalCategoryAxis(labels),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: axisTooltip(labels, (value) => `${value.toFixed(2)}%`),
    },
    series: [
      {
        ...BAR_SERIES_BASE,
        name: "Exposure",
        barMaxWidth: 20,
        data: data.map((row) => ({
          value: row.pctNetOwnFunds,
          itemStyle: {
            color: row.aboveLimit ? tokens.adverse : seriesColor(tokens, 0),
            borderRadius: [0, 2, 2, 0],
          },
        })),
      },
      {
        ...BAR_SERIES_BASE,
        name: "Applicable limit",
        barMaxWidth: 20,
        itemStyle: { color: tokens.accent, borderRadius: [0, 2, 2, 0] },
        data: data.map((row) => row.limitPct),
      },
    ],
  } as BiEChartsOption;

  const above = data.filter((row) => row.aboveLimit).length;

  return (
    <EChart
      option={option}
      height={chartHeight}
      ariaLabel={`Large exposures as a percentage of net own funds across ${data.length} counterparties${
        above > 0 ? `, ${above} above the applicable limit` : ""
      }`}
    />
  );
}
