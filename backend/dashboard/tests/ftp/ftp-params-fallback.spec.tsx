// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import FtpRulesPage from "../../app/(app)/ftp/rules/page";
import ProfileProvider from "../../components/profile/ProfileProvider";
import BankProvider from "../../components/shell/BankContext";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../../lib/format";

const bankId = "BK-SAMP0001";
const base = `${apiBaseUrl}/banks/${bankId}`;
const server = setupServer();
let client: QueryClient;

const period = {
  id: "period-1",
  bank_id: bankId,
  label: "October 2026",
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  status: "open",
};

const metrics = {
  blended_assigned_ftp_pct: "11.2",
  ltp_buffer_cost_pct: null,
  ltp_total_charge_ghs: null,
  min_product_margin_pct: "3.0",
  nmd_core_max_pct: "75",
  nmd_core_min_pct: "55",
  nmd_core_pct: "61.4",
  nmd_core_status: "within_policy",
  portfolio_nim_pct: "4.1",
  products_below_min_margin: 0,
  total_balance_ghs: "1000",
  total_branch_contribution_ghs: "10",
  total_contribution_ghs: "10",
  total_products: 4,
  weighted_asset_yield_pct: "13.2",
  weighted_funding_credit_pct: "9.0",
};

function runBody(withheld: boolean) {
  return {
    id: "run-1",
    organization_id: "org",
    bank_id: bankId,
    reporting_period_id: period.id,
    module: "ftp",
    scenario_code: "baseline",
    status: "succeeded",
    engine_version: "ftp-test",
    input_schema_version: "ftp-inputs-v1",
    output_schema_version: "ftp-metrics-v1",
    input_hash: "a".repeat(64),
    inputs: withheld
      ? null
      : {
          parameters: {
            thresholds: {
              ftp_target_roe_pct: "18",
              ftp_min_product_margin_pct: "3",
              ftp_liquidity_premium_max_bps: "150",
              ftp_funding_spread_max_bps: "200",
              nmd_core_min_pct: "55",
              nmd_core_max_pct: "75",
            },
            stress_overlays_bps: { rates_up_200: "200", funding_stress: "100" },
          },
        },
    inputs_available: !withheld,
    inputs_unavailable_reason: withheld ? "restricted_view_required" : null,
    metrics: {},
    assumption_provenance: null,
    started_at: "2026-10-02T00:00:00Z",
    completed_at: "2026-10-02T00:01:00Z",
    error: null,
    metric_results: [],
    line_items: [],
    validations: [],
    created_by: "00000000-0000-0000-0000-000000000001",
    created_at: "2026-10-02T00:00:00Z",
    updated_at: "2026-10-02T00:01:00Z",
  };
}

function capability(sensitivity: string) {
  return {
    module: "ftp",
    permission: "view",
    sensitivity,
    requires_contextual_authorization: false,
    data_scope: { kind: "all", branches: [], regions: [] },
  };
}

function fixture(withheld: boolean) {
  const runRequests: string[] = [];
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json({
        user_id: "reader",
        effective_authority: {
          authv: 1,
          organization_capabilities: [],
          institution_capabilities: [
            {
              institution_id: bankId,
              capabilities: [
                {
                  module: "data",
                  permission: "view",
                  sensitivity: "confidential",
                  requires_contextual_authorization: false,
                  data_scope: { kind: "all", branches: [], regions: [] },
                },
                capability("aggregated"),
                capability("confidential"),
              ],
            },
          ],
        },
      }),
    ),
    http.get(`${apiBaseUrl}/banks`, () =>
      HttpResponse.json({
        banks: [
          {
            id: bankId,
            name: "Fixture Bank",
            currency: "GHS",
            jurisdiction_code: "GH",
            license_type: "universal",
            institution_type: "universal_bank",
          },
        ],
      }),
    ),
    http.get(`${apiBaseUrl}/feature-flags`, () =>
      HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
    ),
    http.get(`${base}/reporting-periods`, () =>
      HttpResponse.json({ periods: [period] }),
    ),
    http.get(`${base}/freshness`, () =>
      HttpResponse.json({
        bank_id: bankId,
        is_stale: false,
        modules: [],
        period_label: period.label,
        reporting_period_id: period.id,
      }),
    ),
    http.get(`${base}/live-summary`, () => HttpResponse.json({})),
    http.get(`${base}/ftp/dashboard`, () =>
      HttpResponse.json({
        bank: {
          id: bankId,
          name: "Fixture Bank",
          currency: "GHS",
          jurisdiction_code: "GH",
        },
        branches: [],
        curve: [],
        latest_run_id: "run-1",
        metrics,
        nmd_segments: [],
        period,
        products: [],
        stored: true,
        trend: [],
        validations: [],
      }),
    ),
    http.get(`${base}/regulatory-runs/:runId`, ({ params }) => {
      runRequests.push(String(params.runId));
      return HttpResponse.json(runBody(withheld));
    }),
  );
  return { runRequests };
}

function view() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("fixture");
  return render(
    <SessionProvider
      session={{
        expires: "2099-01-01T00:00:00Z",
        accessToken: "fixture",
        user: {
          email: "reader@example.test",
          organizationId: "org",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("org", "reader@example.test", 1)}
        >
          <PathnameContext.Provider value="/ftp/rules">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>
                  <FtpRulesPage />
                </BankProvider>
              </ProfileProvider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>,
  );
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  setActiveJurisdiction({ currencyCode: "GHS", locale: "en-GH" });
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
});

it("says the stored inputs are withheld instead of implying they were never recorded", async () => {
  const { runRequests } = fixture(true);
  view();

  // One hint per snapshot-backed card (target ROE, both caps, the overlays).
  const hints = await screen.findAllByText(
    "Run inputs are not available for your access level",
  );
  expect(hints).toHaveLength(4);
  // The run was read through its real route; only the snapshot is withheld.
  await waitFor(() => expect(runRequests).toEqual(["run-1"]));
  // No parameter value is fabricated: every snapshot-backed figure stays blank.
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
});

it("shows the stored parameters when the reader holds restricted view", async () => {
  const { runRequests } = fixture(false);
  view();

  const hints = await screen.findAllByText(
    "From the stored run parameter snapshot",
  );
  expect(hints).toHaveLength(4);
  await waitFor(() => expect(runRequests).toEqual(["run-1"]));
  expect(screen.getByText("18.0%")).toBeInTheDocument();
  expect(screen.getByText("150 bp")).toBeInTheDocument();
});
