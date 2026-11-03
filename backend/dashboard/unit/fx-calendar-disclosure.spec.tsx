// @vitest-environment jsdom
import React from "react";
import { cleanup, render as renderUi, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FxDashboardReadFromJSON } from "@aequoros/risk-service-api";
import FxModuleFrame from "../features/fx/components/FxModuleFrame";
import FxVarPage from "../app/(app)/fx/var/page";
import FxExposurePage from "../app/(app)/fx/page";
import FxLimitsPage from "../app/(app)/fx/limits/page";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";

let client: QueryClient;
const scope = queryAuthorityScope("org", "fixture", 1);
function render(ui: React.ReactNode) {
  return renderUi(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider scope={scope}>
        {ui}
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}

const evidence = vi.hoisted(() => ({ missingFebruary: false }));

vi.mock("@/features/bi/public", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/bi/public")>()),
  EChart: ({
    ariaLabel,
    option,
  }: {
    ariaLabel: string;
    option: { xAxis: { data?: string[] }; series: { data: unknown[] }[] };
  }) => (
    <div role="img" aria-label={ariaLabel}>
      {JSON.stringify({
        labels: option.xAxis.data,
        series: option.series.map((series) => series.data),
      })}
    </div>
  ),
}));

vi.mock("../lib/api/bi", () => ({
  useBiAvailability: () => ({ biEnabled: false }),
  useBiCatalogue: () => ({ data: undefined }),
  useBiInsights: () => ({}),
}));

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-TEST" },
    moduleScope: { fxAggregatedView: true },
    period: null,
    periods: [],
    setPeriodId: vi.fn(),
    institutionCapabilities: [],
    authorityPending: false,
  }),
}));
vi.mock("../lib/api/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api/hooks")>()),
  useRegulatoryRun: () => ({ data: undefined }),
  useFxDashboard: () => ({
    data: FxDashboardReadFromJSON({
      bank: {},
      period: { id: "march", period_end: "2026-03-31" },
      metrics: {
        nop_ghs: "30",
        nop_pct_tier1: "30",
        nop_aggregate_limit_pct: "40",
        nop_single_limit_pct: "10",
        nop_status: "green",
        var_99_1d_ghs: "10",
        standalone_var_total_ghs: "10",
        diversification_benefit_ghs: "0",
        stressed_var_ghs: "20",
        var_confidence_pct: "99",
        var_observations: 249,
      },
      positions: [],
      standalone_vars: [],
      hedges: [],
      scenarios: [],
      trend: [
        {
          reporting_period_id: "january",
          label: "January",
          period_end: "2026-01-31",
          nop_ghs: "10",
          nop_pct_tier1: "10",
          var_99_1d_ghs: "10",
          stored: true,
        },
        {
          reporting_period_id: "february",
          label: "February",
          period_end: "2026-02-28",
          nop_ghs: "20",
          nop_pct_tier1: "20",
          var_99_1d_ghs: evidence.missingFebruary ? null : "0",
          stored: !evidence.missingFebruary,
        },
        {
          reporting_period_id: "march",
          label: "March",
          period_end: "2026-03-31",
          nop_ghs: "30",
          nop_pct_tier1: "30",
          var_99_1d_ghs: evidence.missingFebruary ? "0" : null,
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
            "FX VaR uses 249/250 exact shared return intervals from 2025-07-01 to 2026-03-31. Insufficient sample: not adequate pilot risk evidence.",
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

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  evidence.missingFebruary = false;
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
});
it("shows sample and stress qualifications alongside every FX tab's figures", () => {
  render(
    <FxModuleFrame title="FX VaR">
      {() => <p>Portfolio figures</p>}
    </FxModuleFrame>,
  );
  expect(
    screen.getByText(/249\/250 exact shared return intervals/).textContent,
  ).toContain("not adequate pilot risk evidence");
  expect(
    screen.getByText(/indicative management stress estimate/),
  ).toBeTruthy();
  expect(screen.getByText("Portfolio figures")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Run all FX scenarios" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(screen.queryByText("Hedge detail")).toBeNull();
});

it.each([false, true])(
  "omits unavailable VaR while keeping measured zero (February gap: %s)",
  (missingFebruary) => {
    evidence.missingFebruary = missingFebruary;
    render(<FxVarPage />);
    const chart = screen
      .getByRole("heading", {
        name: "Portfolio VaR trend (99%, 1-day)",
      })
      .closest("section");
    expect(chart?.textContent).toContain(
      "Periods with insufficient sample evidence are omitted.",
    );
    const plot = screen.getByRole("img", {
      name: missingFebruary
        ? "Portfolio VaR across 2 periods, 1 computed inline"
        : "Portfolio VaR across 2 periods",
    });
    expect(plot.textContent).toContain(
      missingFebruary
        ? '"labels":["January","March"]'
        : '"labels":["January","February"]',
    );
    expect(plot.textContent).toContain('"value":0');
    expect(plot.textContent).not.toContain(
      missingFebruary ? "February" : "March",
    );
  },
);

it.each([false, true])(
  "compares March NOP with February independently of VaR (February gap: %s)",
  (missingFebruary) => {
    evidence.missingFebruary = missingFebruary;
    render(<FxExposurePage />);
    const nop = screen.getByText("Aggregate NOP / Tier 1").closest(".card");
    expect(nop?.textContent).toContain("+10.0 pts");
    expect(nop?.textContent).not.toContain("+20.0 pts");
  },
);

it.each([false, true])(
  "keeps all NOP periods in the limits chart (February gap: %s)",
  (missingFebruary) => {
    evidence.missingFebruary = missingFebruary;
    render(<FxLimitsPage />);
    const plot = screen.getByRole("img", {
      name: missingFebruary
        ? "NOP / Tier 1 across 3 periods, 2 computed inline"
        : "NOP / Tier 1 across 3 periods, 1 computed inline",
    });
    expect(plot.textContent).toContain(
      '"labels":["January","February","March"]',
    );
    expect(plot.textContent).toContain('"value":30');
  },
);
