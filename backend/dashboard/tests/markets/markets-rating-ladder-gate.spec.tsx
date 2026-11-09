// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

/**
 * The Markets page's rating ladder (#172, W11). The daily ladder reads
 * ``GET /banks/{id}/live-snapshots?module=rating``, whose backend gate is the
 * exact MARKETS/aggregated VIEW binding — the live engine's read grain — not
 * the MARKETS/published view that admits the hub itself. The page therefore
 * passes a bank id gated on that exact capability at the call site, so a
 * published-only reader's browser never sends a /live-snapshots request (and
 * never collects the 403 the backend would answer).
 */

const fixture = vi.hoisted(() => ({
  bankId: "BK-SAMP0001" as string | undefined,
  moduleScope: {} as Record<string, boolean>,
}));

vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: fixture.bankId
      ? { id: fixture.bankId, name: "Sample Bank Ltd" }
      : undefined,
    period: undefined,
    moduleScope: fixture.moduleScope,
  }),
}));

const useLiveSnapshots = vi.hoisted(() =>
  vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
);
vi.mock("@/lib/api/hooks", () => ({
  useLiveSnapshots,
  useLiveSummary: () => ({ data: undefined }),
  useMarketDataSourcePreferences: () => ({ data: undefined }),
  useMarketDataViews: () => ({
    data: undefined,
    isLoading: false,
    error: null,
    refetch: () => undefined,
  }),
}));

// The hub's satellite surfaces render from the canonical store, which this
// fixture leaves empty; stub them to keep the unit focused on the gate.
vi.mock("@/features/bi/components/InsightStrip", () => ({
  LandingInsightStrip: () => null,
}));
vi.mock("@/components/sdi/SdiModuleContext", () => ({
  default: ({ children }: { children: React.ReactNode }) => children,
}));

import MarketsPage from "../../app/(app)/markets/page";

beforeEach(() => {
  fixture.bankId = "BK-SAMP0001";
  fixture.moduleScope = {
    marketsPublishedView: true,
    marketsAggregatedView: false,
  };
});

afterEach(() => {
  cleanup();
  useLiveSnapshots.mockClear();
});

it("passes no bank id to the rating ladder without MARKETS aggregated view", () => {
  render(<MarketsPage />);
  expect(useLiveSnapshots).toHaveBeenCalledWith(undefined, "rating", 45);
});

it("requests the rating ladder for the bank under MARKETS aggregated view", () => {
  fixture.moduleScope = {
    ...fixture.moduleScope,
    marketsAggregatedView: true,
  };
  render(<MarketsPage />);
  expect(useLiveSnapshots).toHaveBeenCalledWith("BK-SAMP0001", "rating", 45);
});

it("keeps the ladder unrequested while the bank is still resolving", () => {
  fixture.bankId = undefined;
  render(<MarketsPage />);
  expect(useLiveSnapshots).toHaveBeenCalledWith(undefined, "rating", 45);
});
