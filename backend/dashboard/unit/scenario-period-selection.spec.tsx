// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ProfileProvider from "../components/profile/ProfileProvider";
import BankProvider from "../components/shell/BankContext";
import HistoricalPeriodRibbon from "../components/shell/HistoricalPeriodRibbon";
import ScenarioWorkbench from "../components/workbench/ScenarioWorkbench";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { queryAuthorityScope, scopedQueryKey } from "../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../lib/format";

const bankId = "BK-SAMP0001";
const base = `${apiBaseUrl}/banks/${bankId}`;
const scope = queryAuthorityScope("org", "fixture@example.test", 1);
const server = setupServer();
let client: QueryClient;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
});

it("selects a historical period, runs and reloads its stored results without replacing live results", async () => {
  const periods = [
    { id: "september", label: "September 2026", period_end: "2026-09-30" },
    { id: "august", label: "August 2026", period_end: "2026-08-31" },
  ];
  const stored = {
    id: "august-run",
    module: "liquidity",
    scenario_code: "baseline",
    status: "succeeded",
    reporting_period_id: "august",
    period_label: "August 2026",
    engine_version: "liquidity",
    input_hash: "august-inputs",
    metrics: { lcr_pct: "143.25" },
    error: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    line_items: [],
    metric_results: [],
    validations: [],
  };
  let writes = 0;
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json({
        effective_authority: {
          authv: 1,
          organization_capabilities: [],
          institution_capabilities: [
            {
              institution_id: bankId,
              capabilities: ["view", "run"].map((permission) => ({
                module: "liq",
                sensitivity: "confidential",
                permission,
                requires_contextual_authorization: false,
                data_scope: { kind: "all", branches: [], regions: [] },
              })),
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
            name: "Sample bank",
            currency: "EUR",
            jurisdiction_code: "EU",
            institution_type: "universal_bank",
          },
        ],
      }),
    ),
    http.get(`${base}/reporting-periods`, () => HttpResponse.json({ periods })),
    http.get(`${apiBaseUrl}/feature-flags`, () =>
      HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
    ),
    http.get(`${base}/scenario-workbench/liquidity/scenarios`, ({ request }) =>
      HttpResponse.json({
        bank_id: bankId,
        module: "liquidity",
        as_of_date:
          new URL(request.url).searchParams.get("reporting_period_id") ===
          "august"
            ? "2026-08-31"
            : "2026-09-30",
        allowed_keys: [],
        allowed_prefixes: [],
        scenarios: [],
      }),
    ),
    http.get(`${base}/regulatory-runs`, ({ request }) => {
      const historic =
        new URL(request.url).searchParams.get("reporting_period_id") ===
        "august";
      const runs = historic && writes > 0 ? [stored] : [];
      return HttpResponse.json({
        runs,
        total: runs.length,
        limit: 100,
        offset: 0,
      });
    }),
    http.post(`${base}/liquidity/run-all-scenarios`, async ({ request }) => {
      expect(await request.json()).toEqual({ reporting_period_id: "august" });
      writes++;
      return HttpResponse.json({
        bank_id: bankId,
        reporting_period_id: "august",
        runs: [stored],
      });
    }),
  );
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const liveKey = scopedQueryKey("live-summary", scope, bankId);
  const live = {
    asOfDate: "2026-09-30",
    modules: [{ module: "liquidity", metrics: { lcr_pct: "166.75" } }],
  };
  client.setQueryData(liveKey, live);
  setAccessToken("fixture");
  const mount = () =>
    render(
      <SessionProvider
        session={{
          expires: "2099-01-01T00:00:00Z",
          accessToken: "fixture",
          user: {
            email: "fixture@example.test",
            organizationId: "org",
            authorizationVersion: 1,
          },
        }}
        refetchOnWindowFocus={false}
      >
        <QueryClientProvider client={client}>
          <QueryAuthorityScopeProvider scope={scope}>
            <PathnameContext.Provider value="/liquidity/scenarios">
              <ProfileProvider>
                <BankProvider>
                  <HistoricalPeriodRibbon />
                  <ScenarioWorkbench module="liquidity" />
                </BankProvider>
              </ProfileProvider>
            </PathnameContext.Provider>
          </QueryAuthorityScopeProvider>
        </QueryClientProvider>
      </SessionProvider>,
    );
  const user = userEvent.setup();
  const view = mount();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Run all Liquidity scenarios" }),
    ).toBeEnabled(),
  );
  const selector = screen.getByRole("combobox", {
    name: "As-of reporting period",
  });
  expect(selector).toHaveValue("september");
  await user.selectOptions(selector, "august");
  expect(screen.getByText(/Viewing the/)).toHaveTextContent("August 2026");
  expect(writes).toBe(0);
  await user.click(
    screen.getByRole("button", { name: "Run all Liquidity scenarios" }),
  );
  expect(await screen.findByText("143.25")).toBeVisible();
  await user.click(screen.getByRole("button", { name: /Return to live edge/ }));
  await waitFor(() =>
    expect(screen.queryByText("143.25")).not.toBeInTheDocument(),
  );
  expect(screen.queryByText(/scenario outcomes/)).not.toBeInTheDocument();
  expect(client.getQueryData(liveKey)).toEqual(live);
  view.unmount();
  client.removeQueries({ queryKey: ["reg-runs"] });
  mount();
  await user.selectOptions(
    await screen.findByRole("combobox", { name: "As-of reporting period" }),
    "august",
  );
  expect(await screen.findByText("143.25")).toBeVisible();
  expect(writes).toBe(1);
  expect(client.getQueryData(liveKey)).toEqual(live);
});
