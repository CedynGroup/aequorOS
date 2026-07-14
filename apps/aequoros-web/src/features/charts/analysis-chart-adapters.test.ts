import type {
  CalculationRunRead,
  CapitalComparisonRead,
  ForecastPeriodRead,
  LiquiditySummaryRead,
} from "@aequoros/risk-service-api";
import { describe, expect, it } from "vitest";

import {
  capitalComparisonToSeries,
  forecastRunToSeries,
  liquidityCoverageToSeries,
} from "./analysis-chart-adapters";

function forecastPeriod(
  periodNumber: number,
  overrides: Partial<ForecastPeriodRead> = {},
): ForecastPeriodRead {
  return {
    id: `period-${periodNumber}`,
    periodNumber,
    periodEnd: new Date(`202${periodNumber}-12-31T00:00:00Z`),
    currency: "USD",
    totalAssets: `${1000 + periodNumber}.0000`,
    totalLiabilities: `${600 + periodNumber}.0000`,
    totalEquity: `${400 + periodNumber}.0000`,
    cash: "100.0000",
    projectedInflows: "120.0000",
    projectedOutflows: "80.0000",
    creditDraw: "0.0000",
    debtRepayment: "20.0000",
    components: {},
    ...overrides,
  };
}

function run(outputs: ForecastPeriodRead[]): CalculationRunRead {
  return {
    id: "run-1",
    organizationId: "org-1",
    caseId: "case-1",
    scenarioId: "scenario-1",
    rerunOfRunId: null,
    status: "succeeded",
    engineVersion: "balance-sheet-v1",
    inputHash: "a".repeat(64),
    forecastPeriods: outputs.length
      ? Math.max(...outputs.map((row) => row.periodNumber))
      : 0,
    asOfDate: new Date("2026-12-31T00:00:00Z"),
    startedAt: new Date("2026-01-01T00:00:00Z"),
    completedAt: new Date("2026-01-01T00:00:01Z"),
    error: null,
    inputSchemaVersion: "calculation-input-v1",
    inputs: {},
    outputSchemaVersion: "forecast-output-v1",
    outputs,
    createdBy: "user-1",
    createdAt: new Date("2026-01-01T00:00:00Z"),
    updatedAt: new Date("2026-01-01T00:00:01Z"),
  };
}

function liquiditySummary(
  metric: LiquiditySummaryRead["metrics"][number] = {
    key: "minimum_sources_coverage",
    label: "Minimum sources coverage",
    value: "1.2000",
    unit: "ratio",
    availability: "available",
    description: "Coverage",
  },
): LiquiditySummaryRead {
  return { metrics: [metric] } as LiquiditySummaryRead;
}

describe("forecastRunToSeries", () => {
  it("preserves decimal strings and parses numbers only for chart positioning", () => {
    const series = forecastRunToSeries(
      run([forecastPeriod(1, { totalAssets: "9999999999999998.9999" })]),
    );

    expect(series.availability).toBe("ready");
    expect(series.points[0].assets).toEqual({
      decimal: "9999999999999998.9999",
      pixel: Number("9999999999999998.9999"),
    });
  });

  it("keeps a single-point run chartable", () => {
    const series = forecastRunToSeries(run([forecastPeriod(1)]));
    expect(series.points).toHaveLength(1);
    expect(series.unavailableSpans).toEqual([]);
  });

  it("inserts an honest gap for a missing period", () => {
    const series = forecastRunToSeries(
      run([forecastPeriod(1), forecastPeriod(3)]),
    );
    expect(series.points).toHaveLength(3);
    expect(series.points[1]).toMatchObject({
      periodNumber: 2,
      assets: null,
      liabilities: null,
      equity: null,
    });
    expect(series.unavailableSpans[0].reason).toContain("period 2 is missing");
  });

  it("returns the explicit empty state", () => {
    expect(forecastRunToSeries(run([])).availability).toBe("empty");
  });
});

describe("liquidityCoverageToSeries", () => {
  it("matches backend four-place half-up coverage rounding", () => {
    const series = liquidityCoverageToSeries(
      run([
        forecastPeriod(1, {
          projectedInflows: "1.0000",
          creditDraw: "0.0000",
          projectedOutflows: "6.0000",
          debtRepayment: "0.0000",
        }),
      ]),
      liquiditySummary(),
    );

    expect(series.points[0].coverage).toEqual({
      decimal: "0.1667",
      pixel: 0.1667,
    });
    expect(series.threshold.decimal).toBe("1.20");
  });

  it("renders non-positive uses as a gap with the persisted diagnostic", () => {
    const diagnostic = "Coverage is unavailable for period 2.";
    const series = liquidityCoverageToSeries(
      run([
        forecastPeriod(1),
        forecastPeriod(2, {
          projectedOutflows: "-20.0000",
          debtRepayment: "20.0000",
        }),
        forecastPeriod(3),
      ]),
      liquiditySummary({
        key: "minimum_sources_coverage",
        label: "Minimum sources coverage",
        value: null,
        unit: "ratio",
        availability: "unavailable",
        diagnostic,
        description: "Coverage",
      }),
    );

    expect(series.availability).toBe("ready");
    expect(
      series.points.map((point) => point.coverage?.decimal ?? null),
    ).toEqual(["1.2000", null, "1.2000"]);
    expect(series.unavailableSpans).toEqual([
      { periodNumber: 2, reason: diagnostic },
    ]);
  });

  it("returns unavailable when every coverage value is not meaningful", () => {
    const series = liquidityCoverageToSeries(
      run([
        forecastPeriod(1, {
          projectedOutflows: "0.0000",
          debtRepayment: "0.0000",
        }),
      ]),
      liquiditySummary(),
    );
    expect(series.availability).toBe("unavailable");
    expect(series.points[0].coverage).toBeNull();
  });
});

describe("capitalComparisonToSeries", () => {
  it("uses only the API-enforced compatible comparison periods", () => {
    const series = capitalComparisonToSeries({
      baseline: { reportingCurrency: "USD" },
      downside: { reportingCurrency: "USD" },
      diagnostic: null,
      periods: [
        {
          periodNumber: 1,
          baselineEquity: "100.0000",
          downsideEquity: "80.0000",
          equityDelta: "-20.0000",
          baselineEquityToAssetsRatio: "0.10000000",
          downsideEquityToAssetsRatio: "0.08000000",
          equityToAssetsRatioDelta: "-0.02000000",
        },
      ],
    } as unknown as CapitalComparisonRead);

    expect(series.availability).toBe("ready");
    expect(series.points[0].baselineRatio?.decimal).toBe("0.10000000");
  });

  it("refuses to chart a basis mismatch", () => {
    const series = capitalComparisonToSeries({
      baseline: {},
      downside: {},
      periods: [],
      diagnostic: { message: "Comparison bases differ." },
    } as unknown as CapitalComparisonRead);
    expect(series).toMatchObject({
      availability: "unavailable",
      reason: "Comparison bases differ.",
      points: [],
    });
  });

  it("inserts a gap when a compatible comparison period is missing", () => {
    const series = capitalComparisonToSeries({
      baseline: {},
      downside: {},
      diagnostic: null,
      periods: [
        {
          periodNumber: 2,
          baselineEquityToAssetsRatio: "0.1",
          downsideEquityToAssetsRatio: "0.08",
        },
      ],
    } as CapitalComparisonRead);
    expect(series.points[0]).toEqual({
      periodNumber: 1,
      baselineRatio: null,
      downsideRatio: null,
    });
    expect(series.unavailableSpans).toHaveLength(1);
  });
});
