import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  CapitalComparisonSeries,
  ForecastTrajectorySeries,
  LiquidityCoverageSeries,
} from "./analysis-chart-adapters";
import {
  CapitalComparisonChart,
  ForecastTrajectoryChart,
  formatChartDate,
  LiquidityCoverageChart,
  representativeTicks,
} from "./analysis-charts";

describe("formatChartDate", () => {
  it("preserves a UTC date-only value in negative UTC offsets", () => {
    expect(formatChartDate(new Date("2027-12-31T00:00:00Z"))).toBe(
      "12/31/2027",
    );
  });
});

describe("ForecastTrajectoryChart", () => {
  it("renders accessible SVG points and preserves a missing-period gap", () => {
    const series: ForecastTrajectorySeries = {
      availability: "ready",
      reason: null,
      points: [
        forecastPoint(1, "100.0000", "60.0000", "40.0000"),
        {
          periodNumber: 2,
          periodEnd: null,
          currency: null,
          assets: null,
          liabilities: null,
          equity: null,
        },
        forecastPoint(3, "120.0000", "70.0000", "50.0000"),
      ],
      unavailableSpans: [
        { periodNumber: 2, reason: "Forecast period 2 is missing." },
      ],
    };

    const { container } = render(<ForecastTrajectoryChart series={series} />);

    const svg = container.querySelector(
      'svg[aria-label="Balance-sheet trajectory chart"]',
    );
    expect(svg).toBeInTheDocument();
    expect(svg?.tagName.toLowerCase()).toBe("svg");
    expect(svg?.querySelector("title")).toHaveTextContent(
      "Balance-sheet trajectory",
    );
    expect(
      container.querySelectorAll("[data-chart-point^='forecast-']"),
    ).toHaveLength(6);
    expect(screen.getByText("Assets")).toBeInTheDocument();
    expect(screen.getByText("Liabilities")).toBeInTheDocument();
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(
      screen.getByText(/Period 2: Forecast period 2 is missing/),
    ).toBeInTheDocument();
    for (const path of container.querySelectorAll(".recharts-line-curve")) {
      expect(path.getAttribute("d")).not.toMatch(/L[^M]+L/);
    }
  });
});

describe("representativeTicks", () => {
  it("limits dense axes while retaining evenly distributed endpoints", () => {
    expect(
      representativeTicks(Array.from({ length: 36 }, (_, index) => index)),
    ).toEqual([0, 7, 14, 21, 28, 35]);
  });

  it("retains every value on a sparse axis", () => {
    expect(representativeTicks([1, 2, 3])).toEqual([1, 2, 3]);
  });
});

describe("LiquidityCoverageChart", () => {
  it("positions a threshold ReferenceLine and omits the unavailable point", () => {
    const series: LiquidityCoverageSeries = {
      availability: "ready",
      reason: null,
      threshold: { decimal: "1.20", pixel: 1.2 },
      thresholdRuleVersion: "liquidity-v1.0.0",
      points: [
        coveragePoint(1, "1.3000"),
        { periodNumber: 2, periodEnd: null, coverage: null },
        coveragePoint(3, "0.9000"),
      ],
      unavailableSpans: [
        { periodNumber: 2, reason: "Coverage is not meaningful." },
      ],
    };

    const { container } = render(<LiquidityCoverageChart series={series} />);

    expect(
      container.querySelector(
        'svg[aria-label="Liquidity sources coverage chart"]',
      ),
    ).toBeInTheDocument();
    expect(
      container.querySelectorAll("[data-chart-point='liquidity-coverage']"),
    ).toHaveLength(2);
    const threshold = container.querySelector(".liquidity-threshold-line line");
    expect(threshold).toBeInTheDocument();
    expect(threshold?.getAttribute("y1")).toBe(threshold?.getAttribute("y2"));
    expect(screen.getByText("1.20x threshold")).toBeInTheDocument();
    expect(screen.getByText(/liquidity-v1\.0\.0/)).toBeInTheDocument();
    expect(
      screen.getByText(/Period 2: Coverage is not meaningful/),
    ).toBeInTheDocument();
  });

  it("renders an explicit unavailable state instead of an empty SVG", () => {
    render(
      <LiquidityCoverageChart
        series={{
          availability: "unavailable",
          reason: "Projected uses are not positive.",
          points: [],
          unavailableSpans: [],
          threshold: { decimal: "1.20", pixel: 1.2 },
          thresholdRuleVersion: "liquidity-v1.0.0",
        }}
      />,
    );
    expect(
      screen.getByText("Liquidity coverage unavailable"),
    ).toBeInTheDocument();
    expect(document.querySelector("svg[aria-label]")).not.toBeInTheDocument();
  });
});

describe("CapitalComparisonChart", () => {
  it("renders one baseline and one downside point per compatible period", () => {
    const series: CapitalComparisonSeries = {
      availability: "ready",
      reason: null,
      points: [
        capitalPoint(1, "0.10000000", "0.08000000"),
        capitalPoint(2, "0.09000000", "0.05000000"),
      ],
      unavailableSpans: [],
    };

    const { container } = render(<CapitalComparisonChart series={series} />);

    const svg = container.querySelector(
      'svg[aria-label="Baseline versus downside capital chart"]',
    );
    expect(svg).toBeInTheDocument();
    expect(svg?.querySelector("title")).toHaveTextContent(
      "Baseline versus downside capital",
    );
    expect(
      container.querySelectorAll("[data-chart-point='capital-baseline']"),
    ).toHaveLength(2);
    expect(
      container.querySelectorAll("[data-chart-point='capital-downside']"),
    ).toHaveLength(2);
    expect(container.querySelectorAll(".recharts-line-curve")).toHaveLength(2);
    expect(screen.getByText("Baseline")).toBeInTheDocument();
    expect(screen.getByText("Downside")).toBeInTheDocument();
  });
});

function value(decimal: string) {
  return { decimal, pixel: Number(decimal) };
}

function forecastPoint(
  periodNumber: number,
  assets: string,
  liabilities: string,
  equity: string,
) {
  return {
    periodNumber,
    periodEnd: new Date(`202${periodNumber}-12-31T00:00:00Z`),
    currency: "USD",
    assets: value(assets),
    liabilities: value(liabilities),
    equity: value(equity),
  };
}

function coveragePoint(periodNumber: number, coverage: string) {
  return {
    periodNumber,
    periodEnd: new Date(`202${periodNumber}-12-31T00:00:00Z`),
    coverage: value(coverage),
  };
}

function capitalPoint(
  periodNumber: number,
  baseline: string,
  downside: string,
) {
  return {
    periodNumber,
    baselineRatio: value(baseline),
    downsideRatio: value(downside),
  };
}
