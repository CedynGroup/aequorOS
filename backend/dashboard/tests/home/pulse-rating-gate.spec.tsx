// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

/**
 * The PulseWall rating ladder's authority gate (W11).
 *
 * Every backend live surface serves the rating module at the MARKETS
 * aggregated grain (live_access), so the wall's plane-2 rating ladder request
 * goes out only for a caller holding the exact MARKETS/aggregated/view
 * binding — never through the `/markets` page entry (MARKETS/published), a
 * DIFFERENT, lower sensitivity. A published-only reader renders the rating
 * card from the backend-filtered live summary and sends no live-snapshots
 * request at all instead of collecting the route's denial.
 */

const fixture = vi.hoisted(() => ({
  bankId: "BK-SAMP0001" as string | undefined,
  moduleScope: {} as Record<string, boolean | undefined>,
}));

const useLiveSnapshots = vi.hoisted(() =>
  vi.fn((_bankId?: string, _module?: string, _days?: number) => ({
    data: undefined,
    isLoading: false,
    error: null,
  })),
);

vi.mock("@/lib/api/hooks", () => ({
  useLiveSnapshots,
  useLiquidityDashboard: () => ({
    data: undefined,
    isLoading: false,
    error: null,
  }),
  useCapitalDashboard: () => ({
    data: undefined,
    isLoading: false,
    error: null,
  }),
  useIrrDashboard: () => ({ data: undefined, isLoading: false, error: null }),
  useFxDashboard: () => ({ data: undefined, isLoading: false, error: null }),
  useFtpDashboard: () => ({ data: undefined, isLoading: false, error: null }),
}));

vi.mock("@/components/shell/BankContext", () => ({
  useModuleScope: () => fixture.moduleScope,
}));

vi.mock("@/lib/api/regulatoryRules", () => ({
  useInForceLiveSummary: () => ({
    data: undefined,
    isLoading: false,
    error: null,
  }),
  useBankRules: () => ({
    isLoading: false,
    isReady: true,
    error: null,
    query: { refetch: () => undefined },
    isModuleInForce: () => true,
    isInForce: () => true,
  }),
}));

// The gate under test reads the href map and metric helpers; the constants
// mirror features/live/components/moduleDisplay.ts so the spec stays focused
// on pulse's gate choice, not on the display map itself.
vi.mock("@/features/live/public", () => ({
  DEFAULT_MODULE_ORDER: [
    "liquidity",
    "capital",
    "credit",
    "irr",
    "fx",
    "ftp",
    "rating",
    "forecast",
  ],
  LIVE_MODULE_HREFS: {
    liquidity: "/liquidity",
    capital: "/capital",
    credit: "/credit",
    irr: "/irr/limits",
    fx: "/fx/limits",
    ftp: "/ftp/products",
    rating: "/markets",
    forecast: "/forecasting",
  },
  fixed: (value: number) => String(value),
  livePrimaryMetricChange: () => undefined,
  livePrimaryMetricKey: () => undefined,
}));

import { usePulseCards } from "../../features/home/components/pulse";

function PulseHarness() {
  usePulseCards(fixture.bankId, true);
  return null;
}

const ladderCall = (module: string) =>
  useLiveSnapshots.mock.calls.find((call) => call[1] === module);

beforeEach(() => {
  fixture.bankId = "BK-SAMP0001";
  // A published-only MARKETS reader: the /markets hub entry admits the module
  // (isHrefVisible), so only the aggregated gate can hold the ladder back.
  fixture.moduleScope = {
    isResolved: true,
    hasInstitutionAuthority: true,
    marketsPublishedView: true,
    marketsAggregatedView: false,
    liquidityAggregatedView: true,
  };
});

afterEach(() => {
  cleanup();
  useLiveSnapshots.mockClear();
});

it("sends no rating ladder request without MARKETS aggregated view", () => {
  render(<PulseHarness />);
  expect(ladderCall("rating")?.[0]).toBeUndefined();
});

it("requests the rating ladder under MARKETS aggregated view", () => {
  fixture.moduleScope = {
    ...fixture.moduleScope,
    marketsAggregatedView: true,
  };
  render(<PulseHarness />);
  expect(ladderCall("rating")?.[0]).toBe("BK-SAMP0001");
});

it("keeps gating the other ladders on the wall's existing scope rules", () => {
  // Contrast: liquidity's ladder keeps its aggregated gate AND the wall's
  // scope filter; the rating change does not loosen either.
  render(<PulseHarness />);
  expect(ladderCall("liquidity")?.[0]).toBe("BK-SAMP0001");
  fixture.moduleScope = { ...fixture.moduleScope, liquidityAggregatedView: false };
  useLiveSnapshots.mockClear();
  render(<PulseHarness />);
  expect(ladderCall("liquidity")?.[0]).toBeUndefined();
});
