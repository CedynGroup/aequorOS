// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/**
 * The PulseWall rating ladder's authority gate (W11), through the wall's
 * visible behavior: the real PulseWall renders with real query hooks and only
 * the network boundary is mocked (MSW). Every backend live surface serves the
 * rating module at the MARKETS aggregated grain (live_access), so the wall's
 * plane-2 rating ladder request goes out only for a caller holding the exact
 * MARKETS/aggregated/view binding — never through the `/markets` page entry
 * (MARKETS/published), a DIFFERENT, lower sensitivity. A published-only
 * reader renders the rating card from the backend-filtered live summary and
 * sends no live-snapshots request at all instead of collecting the denial.
 */

const BANK_ID = "BK-SAMP0001";
const SUMMARY_URL = `*/api/v1/banks/${BANK_ID}/live-summary`;
const SNAPSHOTS_URL = `*/api/v1/banks/${BANK_ID}/live-snapshots`;
const DASHBOARD_URLS = [
  `*/api/v1/banks/${BANK_ID}/liquidity/dashboard`,
  `*/api/v1/banks/${BANK_ID}/capital/dashboard`,
  `*/api/v1/banks/${BANK_ID}/irr/dashboard`,
  `*/api/v1/banks/${BANK_ID}/fx/dashboard`,
  `*/api/v1/banks/${BANK_ID}/ftp/dashboard`,
];

/** Snapshot ladders requested, in order, as `module` query values. */
const snapshotRequests: string[] = [];

const server = setupServer(
  http.get(SNAPSHOTS_URL, ({ request }) => {
    snapshotRequests.push(
      new URL(request.url).searchParams.get("module") ?? "",
    );
    return HttpResponse.json({
      bank_id: BANK_ID,
      days: 45,
      module: new URL(request.url).searchParams.get("module"),
      snapshots: [],
    });
  }),
  http.get(SUMMARY_URL, () =>
    HttpResponse.json({
      bank_id: BANK_ID,
      computed_at: "2026-01-31T00:00:00Z",
      is_stale: false,
      modules: [
        ratingSummaryRow(),
        liquiditySummaryRow(),
      ],
    }),
  ),
  http.get("*/api/v1/regulatory-reporting/templates", () =>
    HttpResponse.json({
      templates: [
        {
          code: "LCR-NSFR",
          instrument_status: "in_force",
          title: "LCR / NSFR",
        },
        {
          code: "IRRBB-PILOT",
          instrument_status: "in_force",
          title: "IRRBB pilot",
        },
      ],
    }),
  ),
  // Freshness and the module dashboards are not this spec's subject: the
  // freshness probe settles (errors settle it) so the dashboards stop
  // waiting, and each dashboard answers the platform's availability
  // envelope, rendering the wall's honest "unavailable" state.
  http.get(`*/api/v1/banks/${BANK_ID}/freshness`, () =>
    new HttpResponse(null, { status: 500 }),
  ),
  ...DASHBOARD_URLS.map((url) =>
    http.get(url, () =>
      HttpResponse.json({
        available: false,
        error_code: "current_facts_missing",
        reason: "Synthetic fixture: no computed facts",
      }),
    ),
  ),
);

function ratingSummaryRow() {
  return {
    module: "rating",
    status: "green",
    computed_at: "2026-01-31T00:00:00Z",
    metrics: {
      pit_pd_upper_pct: "2.4",
      pit_rating_grade: "bb",
      sovereign_ceiling: "bb",
    },
    calculation_generation: 1,
    computed_from_input_hash: "a".repeat(64),
    engine_version: "synthetic",
    pipeline_error: null,
    pipeline_state: "ready",
    source_as_of_date: "2026-01-31T00:00:00Z",
    source_fact_period_id: "period",
  };
}

function liquiditySummaryRow() {
  return {
    ...ratingSummaryRow(),
    module: "liquidity",
    metrics: {
      lcr_pct: "105",
      nsfr_pct: "110",
      lcr_status: "green",
      nsfr_status: "green",
    },
  };
}

const scope = vi.hoisted(() => ({
  grants: {
    isResolved: true,
    hasInstitutionAuthority: true,
    institutionClass: "bank",
    marketsPublishedView: false,
    marketsAggregatedView: false,
    liquidityAggregatedView: false,
    // The tenant's engine set: the wall hides hrefs for modules the tenant
    // does not run (docs/sdi.md §3.2), so a universal bank lists all of them.
    modules: new Set([
      "liquidity",
      "capital",
      "credit",
      "irrbb",
      "fx",
      "ftp",
      "markets",
      "forecasting",
    ]),
  },
}));

vi.mock("@/components/shell/BankContext", () => ({
  useModuleScope: () => scope.grants,
}));

import PulseWall from "../../features/home/components/PulseWall";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";
import { setAccessToken } from "../../lib/api/token";

function renderWall() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("OR-SYNTH001", "fixture@example.test", 1)}
      >
        <PulseWall bankId={BANK_ID} moduleOrder="severity" />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
  return view;
}

const snapshotCalls = (module: string) =>
  snapshotRequests.filter((requested) => requested === module).length;

/** Waits until the wall has rendered the rating card, then lets the query
 * microtask queue drain so a zero request count is a settled fact, not a
 * race. */
async function awaitRatingCard() {
  await screen.findByRole("link", { name: "Credit Assessment — open module" });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100));
  });
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  snapshotRequests.length = 0;
  server.resetHandlers();
  setAccessToken(null);
  scope.grants.marketsPublishedView = false;
  scope.grants.marketsAggregatedView = false;
  scope.grants.liquidityAggregatedView = false;
});

it("sends no rating ladder request without MARKETS aggregated view", async () => {
  // The /markets page entry (MARKETS/published) admits the rating card, so
  // only the aggregated gate can hold the ladder back.
  setAccessToken("fixture-access-token");
  scope.grants.marketsPublishedView = true;
  renderWall();
  await awaitRatingCard();
  expect(snapshotCalls("rating")).toBe(0);
});

it("requests the rating ladder under MARKETS aggregated view", async () => {
  setAccessToken("fixture-access-token");
  scope.grants.marketsPublishedView = true;
  scope.grants.marketsAggregatedView = true;
  renderWall();
  await awaitRatingCard();
  await waitFor(() => expect(snapshotCalls("rating")).toBeGreaterThan(0));
});

it("keeps gating the liquidity ladder on its aggregated view", async () => {
  setAccessToken("fixture-access-token");
  scope.grants.liquidityAggregatedView = true;
  renderWall();
  await screen.findByRole("link", { name: "Liquidity — open module" });
  await waitFor(() => expect(snapshotCalls("liquidity")).toBeGreaterThan(0));
  cleanup();
  snapshotRequests.length = 0;
  scope.grants.liquidityAggregatedView = false;
  renderWall();
  // Without the aggregated view the wall hides the card entirely and raises
  // no ladder request.
  await screen.findByText(/regulatory engines/);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100));
  });
  expect(
    screen.queryByRole("link", { name: "Liquidity — open module" }),
  ).toBeNull();
  expect(snapshotCalls("liquidity")).toBe(0);
});
