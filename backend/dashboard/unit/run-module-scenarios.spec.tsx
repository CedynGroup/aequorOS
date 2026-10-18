// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import RunModuleScenarios from "../components/workbench/RunModuleScenarios";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope, scopedQueryKey } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
const context = vi.hoisted(() => ({
  allowed: true,
  scopeKind: "all" as "all" | "branch",
  period: "period-march" as string | undefined,
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SAMP0001" },
    period: context.period
      ? {
          id: context.period,
          label: context.period,
          periodEnd: new Date("2026-03-31"),
        }
      : null,
    authorityPending: false,
    institutionCapabilities: context.allowed
      ? ["liq", "cap", "fx", "credit"].map((module) => ({
          module,
          sensitivity: "confidential",
          dataScope: { kind: context.scopeKind, branches: [], regions: [] },
          permission: "run",
        }))
      : [],
  }),
}));
const server = setupServer();
let client: QueryClient;
const scope = queryAuthorityScope("org", "fixture", 1);
const base = `${apiBaseUrl}/banks/BK-SAMP0001`;
function mount(module: "liquidity" | "capital" | "fx" | "credit") {
  return render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider scope={scope}>
        <RunModuleScenarios module={module} />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}
function run(code: string, status: string) {
  return {
    id: code,
    scenario_code: code,
    status,
    error:
      status === "failed"
        ? { code: "missing_parameter", message: "Configure this scenario" }
        : null,
    created_at: "2026-03-31T00:00:00Z",
    updated_at: "2026-03-31T00:00:00Z",
    line_items: [],
    metric_results: [],
    validations: [],
    metrics: {},
  };
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  context.allowed = true;
  context.scopeKind = "all";
  context.period = "period-march";
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("fixture");
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});
it.each(["liquidity", "capital", "fx", "credit"] as const)(
  "runs %s only on command for the selected period, reports mixed outcomes, and invalidates stored results",
  async (module) => {
    let requests = 0;
    server.use(
      http.post(`${base}/${module}/run-all-scenarios`, async ({ request }) => {
        requests++;
        expect(await request.json()).toEqual({
          reporting_period_id: "period-march",
        });
        return HttpResponse.json({
          bank_id: "BK-SAMP0001",
          reporting_period_id: "period-march",
          runs: [run("baseline", "succeeded"), run("severe", "failed")],
        });
      }),
    );
    const registry = scopedQueryKey(
      "reg-runs",
      scope,
      "BK-SAMP0001",
      module,
      "period-march",
    );
    client.setQueryData(registry, { runs: [] });
    mount(module);
    expect(requests).toBe(0);
    const button = screen.getByRole("button", {
      name: new RegExp(`Run all ${module} scenarios`, "i"),
    });
    await userEvent.click(button);
    expect(await screen.findByText("Baseline · succeeded")).toBeVisible();
    expect(
      screen.getByText("Severe · failed · Configure this scenario"),
    ).toBeVisible();
    await waitFor(() =>
      expect(client.getQueryState(registry)?.isInvalidated).toBe(true),
    );
    expect(requests).toBe(1);
  },
);
it.each(["liquidity", "capital", "fx", "credit"] as const)(
  "shows a disabled scoped control for a %s reader",
  async (module) => {
    context.allowed = false;
    mount(module);
    const button = screen.getByRole("button", { name: /Run all .* scenarios/ });
    expect(button).toBeDisabled();
    await userEvent.hover(button.parentElement!);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /Confidential · Run authority/,
    );
  },
);
it("refuses to run without a selected as-of period", async () => {
  context.period = undefined;
  mount("credit");
  const button = screen.getByRole("button", {
    name: "Run all Credit scenarios",
  });
  expect(button).toBeDisabled();
  await userEvent.hover(button.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "Select an institution and an as-of reporting period",
  );
});
it("surfaces server authority refusal without reporting a successful scenario", async () => {
  server.use(
    http.post(`${base}/fx/run-all-scenarios`, () =>
      HttpResponse.json({ detail: "Scoped authority denied" }, { status: 403 }),
    ),
  );
  mount("fx");
  await userEvent.click(
    screen.getByRole("button", { name: "Run all FX scenarios" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /server refused|403/i,
  );
  expect(screen.queryByText("Baseline · succeeded")).not.toBeInTheDocument();
});

it("refuses a partial data-scope run grant with a whole-institution reason", async () => {
  context.scopeKind = "branch";
  mount("credit");
  const button = screen.getByRole("button", {
    name: "Run all Credit scenarios",
  });
  expect(button).toBeDisabled();
  await userEvent.hover(button.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "whole institution",
  );
});
