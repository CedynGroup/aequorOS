// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FxDashboardReadFromJSON } from "@aequoros/risk-service-api";
import FxModuleFrame from "../components/fx/FxModuleFrame";
import FxVarPage from "../app/(app)/fx/var/page";

vi.mock("../components/bi/EChart", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => (
    <div role="img" aria-label={ariaLabel} />
  ),
}));

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-TEST" },
    moduleScope: { fxAggregatedView: true },
  }),
}));
vi.mock("../lib/api/hooks", () => ({
  useRegulatoryRun: () => ({ data: undefined }),
  useFxDashboard: () => ({
    data: FxDashboardReadFromJSON({
      bank: {},
      period: { id: "period" },
      metrics: {
        var_99_1d_ghs: "10",
        standalone_var_total_ghs: "10",
        diversification_benefit_ghs: "0",
        stressed_var_ghs: "20",
        var_confidence_pct: "99",
        var_observations: 1,
      },
      positions: [],
      standalone_vars: [],
      hedges: [],
      scenarios: [],
      trend: [
        {
          reporting_period_id: "previous",
          label: "Previous period",
          period_end: "2026-01-31",
          var_99_1d_ghs: "10",
          stored: true,
        },
        {
          reporting_period_id: "latest",
          label: "Latest period",
          period_end: "2026-02-28",
          var_99_1d_ghs: "12",
          stored: false,
        },
      ],
      stored: true,
      validations: [
        {
          rule_code: "var_sample_evidence",
          passed: false,
          severity: "warning",
          message:
            "FX VaR uses 1/250 exact shared return intervals from 2026-01-02 to 2026-01-03. Insufficient sample: not adequate pilot risk evidence.",
        },
        {
          rule_code: "stressed_var_disclosed",
          passed: true,
          severity: "info",
          message:
            "Stress uses 1 shared interval: indicative management stress estimate.",
        },
        {
          rule_code: "hedges_effective",
          passed: true,
          severity: "info",
          message: "Hedge detail",
        },
      ],
    }),
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows sample and stress qualifications alongside every FX tab's figures", () => {
  render(
    <FxModuleFrame title="FX VaR">
      {() => <p>Portfolio figures</p>}
    </FxModuleFrame>,
  );
  expect(
    screen.getByText(/1\/250 exact shared return intervals/).textContent,
  ).toContain("not adequate pilot risk evidence");
  expect(
    screen.getByText(/indicative management stress estimate/),
  ).toBeTruthy();
  expect(screen.getByText("Portfolio figures")).toBeTruthy();
  expect(screen.queryByText("Hedge detail")).toBeNull();
});

it("explains omitted insufficient-evidence periods beside the VaR trend", () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  render(<FxVarPage />);
  const chart = screen
    .getByRole("heading", {
      name: "Portfolio VaR trend (99%, 1-day)",
    })
    .closest("section");
  expect(chart?.textContent).toContain(
    "Periods with insufficient sample evidence are omitted.",
  );
  expect(
    screen.getByRole("img", {
      name: "Portfolio VaR across 2 periods, 1 computed inline",
    }),
  ).toBeTruthy();
});
