// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FxDashboardReadFromJSON } from "@aequoros/risk-service-api";
import FxModuleFrame from "../components/fx/FxModuleFrame";

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
      metrics: {},
      positions: [],
      standalone_vars: [],
      hedges: [],
      scenarios: [],
      trend: [],
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

afterEach(cleanup);
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
