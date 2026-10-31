// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ProfileProvider from "../components/profile/ProfileProvider";
import BankProvider from "../components/shell/BankContext";
import EnterpriseStressWorkbench from "../components/stress/EnterpriseStressWorkbench";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../lib/format";

const bankId = "BK-SAMP0002";
const otherBankId = "BK-SAMP0001";
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

it.each([false, true])(
  "requires Risk VIEW on the active bank before loading stress data (active grant: %s)",
  async (activeRiskView) => {
    const requests: string[] = [];
    const view = (module: string) => ({
      module,
      sensitivity: "confidential",
      permission: "view",
      requires_contextual_authorization: false,
      data_scope: { kind: "all", branches: [], regions: [] },
    });
    server.use(
      http.get(`${apiBaseUrl}/auth/me`, () =>
        HttpResponse.json({
          effective_authority: {
            authv: 1,
            organization_capabilities: [],
            institution_capabilities: [
              { institution_id: otherBankId, capabilities: [view("risk")] },
              {
                institution_id: bankId,
                capabilities: [
                  view("irrbb"),
                  ...(activeRiskView ? [view("risk")] : []),
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
              name: "Active bank",
              currency: "EUR",
              jurisdiction_code: "EU",
              institution_type: "universal_bank",
            },
          ],
        }),
      ),
      http.get(`${apiBaseUrl}/banks/${bankId}/reporting-periods`, () =>
        HttpResponse.json({
          periods: [
            {
              id: "september",
              label: "September 2026",
              period_end: "2026-09-30",
            },
          ],
        }),
      ),
      http.get(`${apiBaseUrl}/feature-flags`, () =>
        HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
      ),
      http.get(`${apiBaseUrl}/macro-scenarios`, ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json({ scenarios: [], total: 0 });
      }),
      http.get(`${apiBaseUrl}/management-action-plans`, ({ request }) => {
        requests.push(request.url);
        return HttpResponse.json({ plans: [], total: 0 });
      }),
      http.get(
        `${apiBaseUrl}/banks/${bankId}/enterprise-stress/runs`,
        ({ request }) => {
          requests.push(request.url);
          return HttpResponse.json([]);
        },
      ),
    );
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setAccessToken("fixture");
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
            <PathnameContext.Provider value="/irr/enterprise-stress">
              <ProfileProvider>
                <BankProvider>
                  <EnterpriseStressWorkbench moduleLens="irr" />
                </BankProvider>
              </ProfileProvider>
            </PathnameContext.Provider>
          </QueryAuthorityScopeProvider>
        </QueryClientProvider>
      </SessionProvider>,
    );
    if (activeRiskView) {
      expect(
        await screen.findByRole("heading", { name: "Scenario library" }),
      ).toBeVisible();
      await waitFor(() => {
        expect(requests.some((url) => url.includes("/macro-scenarios"))).toBe(
          true,
        );
        expect(
          requests.some((url) => url.includes("/management-action-plans")),
        ).toBe(true);
      });
      expect(
        screen.getByRole("button", { name: "Run enterprise stress" }),
      ).toBeDisabled();
    } else {
      expect(
        await screen.findByText("Stress access required", { exact: true }),
      ).toBeVisible();
      expect(
        screen.getByText(
          "Requires Risk · Confidential · view. Ask your organization owner or admin for access.",
          { exact: true },
        ),
      ).toBeVisible();
      expect(requests).toEqual([]);
      expect(
        screen.queryByRole("heading", { name: "Scenario library" }),
      ).not.toBeInTheDocument();
    }
  },
);
