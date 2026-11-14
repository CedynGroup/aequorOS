// @vitest-environment jsdom
import React from "react";
import { cleanup, render as renderUi, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  FxDashboardReadFromJSON,
  RegulatoryRunReadFromJSON,
} from "@aequoros/risk-service-api";
import FxHedgesPage from "../../app/(app)/fx/hedges/page";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

// A stored run whose parameter snapshot is withheld below restricted view must
// not fall back to the hardcoded IFRS 9 defaults: the governed bands that
// decided effectiveness stay unreadable, so the page shows them as unavailable
// instead of highlighting against bands the engine may not have used.

const state = vi.hoisted(() => ({ withheld: true }));

vi.mock("@/components/charts/EChart", () => ({
  default: ({
    ariaLabel,
    option,
  }: {
    ariaLabel: string;
    option: Record<string, unknown>;
  }) => (
    <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} />
  ),
}));

vi.mock("../../lib/api/bi", () => ({
  useBiAvailability: () => ({ biEnabled: false }),
  useBiCatalogue: () => ({ data: undefined }),
  useBiInsights: () => ({}),
}));

vi.mock("../../components/shell/BankContext", () => ({
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

vi.mock("../../lib/api/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/api/hooks")>()),
  useRegulatoryRun: () => ({ data: run() }),
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
        hedge_aggregate_mtm_ghs: "5",
        hedge_total_count: 1,
        hedge_effective_count: 1,
      },
      positions: [],
      standalone_vars: [],
      hedges: [
        {
          hedge_id: "H1",
          pair: "USD/GHS",
          instrument: "forward",
          prospective_r2_pct: "91",
          dollar_offset_pct: "105",
          mtm_ghs: "5",
          effective: true,
        },
      ],
      scenarios: [],
      trend: [],
      stored: true,
      validations: [],
    }),
  }),
}));

function run() {
  return RegulatoryRunReadFromJSON({
    inputs: state.withheld
      ? null
      : {
          parameters: {
            hedge_bands_pct: {
              hedge_r2_min_pct: "90",
              hedge_offset_low_pct: "70",
              hedge_offset_high_pct: "130",
            },
          },
        },
    inputs_available: !state.withheld,
    inputs_unavailable_reason: state.withheld ? "restricted_view_required" : null,
    metrics: {},
    line_items: [],
    metric_results: [],
    validations: [],
    results: {},
    status: "succeeded",
    created_at: "2026-01-31",
    updated_at: "2026-01-31",
  });
}

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

function scatterOption(): Record<string, unknown> {
  const chart = document.querySelector('[role="img"][data-option]');
  expect(chart).not.toBeNull();
  return JSON.parse(chart!.getAttribute("data-option")!) as Record<string, unknown>;
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows withheld hedge bands as unavailable instead of the hardcoded defaults", () => {
  state.withheld = true;
  render(<FxHedgesPage />);

  expect(screen.getByText("Unavailable").textContent).toContain("Unavailable");
  expect(
    screen.getByText(/not available at your access level/).textContent,
  ).toMatch(/not available at your access level/);
  // No band figure is invented: neither the defaults nor any other number.
  expect(screen.queryByText(/R² ≥/)).toBeNull();
  expect(screen.queryByText(/≥ 80%/)).toBeNull();
  expect(screen.queryByText(/80–125%/)).toBeNull();
  const option = scatterOption();
  const series = option.series as Array<Record<string, unknown>>;
  expect(series[0].markArea).toBeUndefined();
  expect(series[0].markLine).toBeUndefined();
  // The engine's own verdict is still on the row.
  expect(screen.getByText("Effective").textContent).toBe("Effective");
});

it("reads the dual-test bands from the stored snapshot when it is served", () => {
  state.withheld = false;
  render(<FxHedgesPage />);

  expect(screen.getAllByText(/R² ≥ 90%/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/70–130%/).length).toBeGreaterThan(0);
  const option = scatterOption();
  const series = option.series as Array<Record<string, unknown>>;
  expect(series[0].markArea).toBeDefined();
  expect(series[0].markLine).toBeDefined();
});
