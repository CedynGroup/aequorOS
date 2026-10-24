// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ForecastRunReadFromJSON } from "@aequoros/risk-service-api";
import ForecastingPage from "@/app/(app)/forecasting/page";

const state = vi.hoisted(() => ({ inForce: false, capitalBreach: true }));
function query<T>(data: T) {
  return { data, error: null, isLoading: false, refetch: vi.fn() };
}
function savedRun() {
  return ForecastRunReadFromJSON({
    id: "saved",
    status: "succeeded",
    scenario_code: "base",
    created_at: "2026-10-23T12:00:00Z",
    inputs: {},
    summary: {
      year5_car_pct: state.capitalBreach ? "8" : "20",
      year5_lcr_pct: "80",
      year5_nsfr_pct: "90",
    },
    path: [0, 5].map((year) => ({
      year,
      period_label: `Synthetic Y${year}`,
      total_assets: "1000",
      loans: "500",
      securities: "300",
      cash: "200",
      deposits: "700",
      equity: "300",
      borrowings: "0",
      nii: "10",
      net_income: "5",
      car_pct: state.capitalBreach ? "8" : "20",
      lcr_pct: "80",
      nsfr_pct: "90",
    })),
    metric_results: [
      {
        metric_code: "year5_car_pct",
        status: state.capitalBreach ? "red" : "green",
        threshold_min: "13",
      },
    ],
    validations: ["car", "lcr", "nsfr"].map((ratio) => ({
      rule_code: `year5_${ratio}_above_minimum`,
      passed: ratio === "car" && !state.capitalBreach,
      severity: "error",
      message: `Recorded ${ratio} assessment`,
    })),
  });
}
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("run=saved"),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SYNTH001" },
    period: { id: "period", periodEnd: new Date("2026-09-30") },
    moduleScope: {
      forecastingAggregatedView: true,
      forecastingConfidentialView: true,
      forecastingRun: false,
    },
  }),
  useModuleScope: () => ({ institutionClass: "bank" }),
}));
vi.mock("@/lib/api/regulatoryRules", () => ({
  useInForceLiveSummary: () => query({ modules: [] }),
  useBankRules: () => ({
    query: { refetch: vi.fn() },
    error: null,
    isLoading: false,
    isModuleInForce: () => state.inForce,
  }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useForecastRuns: () => query({ runs: [] }),
  useForecastRun: () => query(savedRun()),
  useCreateForecastRun: () => ({ isPending: false, mutate: vi.fn() }),
}));
vi.mock("@/components/forecasting/hooks", () => ({
  useScenarioRunSet: () => ({}),
}));
vi.mock("@/components/bi/InsightStrip", () => ({
  LandingInsightStrip: () => null,
  useKpiExplain: () => ({}),
}));
vi.mock("@/components/bi/EChart", () => ({ default: () => <div>Chart</div> }));
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  state.inForce = false;
  state.capitalBreach = true;
});

it.each([false, true])(
  "applies saved-run liquidity applicability while retaining capital failure=%s",
  (capitalBreach) => {
    state.capitalBreach = capitalBreach;
    const view = render(<ForecastingPage />);
    expect(screen.getByText("Recorded car assessment")).toBeVisible();
    expect(screen.queryByText("Recorded lcr assessment")).toBeNull();
    expect(screen.queryByText("Recorded nsfr assessment")).toBeNull();
    expect(screen.queryByText("LCR path")).toBeNull();
    expect(screen.queryByText("NSFR path")).toBeNull();
    const row = screen.getByText("Synthetic Y5").closest("tr")!;
    expect(within(row).getByText("80.0%")).not.toHaveClass("text-critical");
    expect(within(row).getByText("90.0%")).not.toHaveClass("text-critical");
    expect(screen.queryAllByText("Error")).toHaveLength(capitalBreach ? 1 : 0);
    state.inForce = true;
    view.rerender(<ForecastingPage />);
    expect(screen.getByText("Recorded lcr assessment")).toBeVisible();
    expect(screen.getByText("Recorded nsfr assessment")).toBeVisible();
    expect(screen.getByText("LCR path")).toBeVisible();
    expect(screen.getByText("NSFR path")).toBeVisible();
    expect(within(row).getByText("80.0%")).toHaveClass("text-critical");
    expect(within(row).getByText("90.0%")).toHaveClass("text-critical");
  },
);
