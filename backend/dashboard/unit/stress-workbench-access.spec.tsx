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
import EnterpriseStressWorkbench from "../components/stress/EnterpriseStressWorkbench";
import type { MacroScenario } from "../components/stress/types";
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

it.each([
  { label: "no active-bank grant", activeRiskView: false, editDraft: false },
  { label: "active-bank viewer", activeRiskView: true, editDraft: false },
  { label: "saved organization draft", activeRiskView: true, editDraft: true },
])(
  "loads stress data and controls with $label",
  async ({ activeRiskView, editDraft }) => {
    const requests: string[] = [];
    const draft: MacroScenario = {
      id: "saved-draft",
      organization_id: "org",
      bank_id: null,
      code: "cold_edit_live",
      name: "Saved severe downturn",
      description: "Saved description",
      narrative: "Saved narrative",
      source: "Saved source",
      scenario_type: "adverse",
      severity: "severe",
      horizon_years: 3,
      status: "draft",
      version: 1,
      created_by: "maker",
      approved_by: null,
      approval_timestamp: null,
      institution_type_applicability: null,
      owner: "organization",
      is_runnable: false,
      is_immutable: false,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
      paths: [
        {
          variable: "gdp_growth",
          year_index: 1,
          base_value: "0.04",
          stress_value: "-0.03",
        },
        {
          variable: "gdp_growth",
          year_index: 2,
          base_value: "0.05",
          stress_value: "-0.02",
        },
        {
          variable: "fx_usd_ghs",
          year_index: 1,
          base_value: "12",
          stress_value: "17",
        },
        {
          variable: "fx_usd_ghs",
          year_index: 2,
          base_value: "13",
          stress_value: "18",
        },
      ],
    };
    let releaseDetail!: () => void;
    const detailReady = new Promise<void>((resolve) => {
      releaseDetail = resolve;
    });
    let detailRequested = false;
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
            organization_capabilities: editDraft
              ? ["view", "create", "edit"].map((permission) => ({
                  ...view("risk"),
                  permission,
                }))
              : [],
            institution_capabilities: [
              { institution_id: otherBankId, capabilities: [view("risk")] },
              {
                institution_id: bankId,
                capabilities: [
                  view("irrbb"),
                  view("data"),
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
        const scenarios =
          editDraft && !new URL(request.url).searchParams.get("status")
            ? [{ ...draft, path_count: draft.paths.length }]
            : [];
        return HttpResponse.json({ scenarios, total: scenarios.length });
      }),
      http.get(`${apiBaseUrl}/macro-scenarios/${draft.id}`, async () => {
        detailRequested = true;
        await detailReady;
        return HttpResponse.json(draft);
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
      if (editDraft) {
        const user = userEvent.setup();
        await user.click(
          await screen.findByRole("button", { name: "Edit draft" }),
        );
        await waitFor(() => expect(detailRequested).toBe(true));
        expect(
          screen.queryByRole("textbox", { name: "Name" }),
        ).not.toBeInTheDocument();
        releaseDetail();
        expect(
          await screen.findByRole("heading", {
            name: `Edit scenario · ${draft.code}`,
          }),
        ).toBeVisible();
        expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
          draft.name,
        );
        expect(screen.getByRole("combobox", { name: "Severity" })).toHaveValue(
          "severe",
        );
        expect(
          screen.getByRole("textbox", { name: "Description" }),
        ).toHaveValue(draft.description);
        expect(
          screen.getByRole("textbox", { name: "Source attribution" }),
        ).toHaveValue(draft.source);
        expect(screen.getByLabelText("gdp_growth year 1 base")).toHaveValue(
          "4",
        );
        expect(screen.getByLabelText("gdp_growth year 1 stress")).toHaveValue(
          "-3",
        );
        expect(screen.getByLabelText("gdp_growth year 2 base")).toHaveValue(
          "5",
        );
        expect(screen.getByLabelText("gdp_growth year 2 stress")).toHaveValue(
          "-2",
        );
        expect(screen.getByLabelText("fx_usd_ghs year 1 base")).toHaveValue(
          "12",
        );
        expect(screen.getByLabelText("fx_usd_ghs year 1 stress")).toHaveValue(
          "17",
        );
        expect(screen.getByLabelText("fx_usd_ghs year 2 base")).toHaveValue(
          "13",
        );
        expect(screen.getByLabelText("fx_usd_ghs year 2 stress")).toHaveValue(
          "18",
        );
        await user.click(screen.getByRole("button", { name: "New scenario" }));
        expect(
          await screen.findByRole("heading", { name: "New macro scenario" }),
        ).toBeVisible();
        expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
        expect(
          screen.queryByLabelText("gdp_growth year 1 base"),
        ).not.toBeInTheDocument();
      }
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
