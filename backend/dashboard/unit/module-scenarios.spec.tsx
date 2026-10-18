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
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { WorkbenchModule } from "@aequoros/risk-service-api";
import ScenarioWorkbench from "../components/workbench/ScenarioWorkbench";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
const context = vi.hoisted(() => ({ permissions: ["view", "run", "create"] }));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SAMP0001" },
    period: { id: "period", label: "March", periodEnd: new Date("2026-03-31") },
    authorityPending: false,
    institutionCapabilities: ["liq", "cap", "irrbb", "fx", "ftp"].flatMap(
      (module) =>
        context.permissions.map((permission) => ({
          module,
          sensitivity: "confidential",
          dataScope: { kind: "all", branches: [], regions: [] },
          permission,
        })),
    ),
    moduleScope: {
      isResolved: true,
      modules: new Set(["irr", "ftp", "fx", "liquidity", "basel"]),
      organizationModules: new Set(),
      irrbbConfidentialView: true,
    },
  }),
}));
const server = setupServer();
let client: QueryClient;
const base = `${apiBaseUrl}/banks/BK-SAMP0001`;
function mount(module: WorkbenchModule) {
  return render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("org", "fixture", 1)}
      >
        <ScenarioWorkbench module={module} />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}
function result(shift: string) {
  return {
    kind: "system",
    code: "baseline",
    label: `FTP ${shift}`,
    shocks_applied: { curve_shift_bp: shift },
    status: "succeeded",
    metrics: {
      portfolio_nim_pct: shift === "0" ? "4" : "2",
      products_below_min_margin: shift === "0" ? "0" : "3",
    },
    metric_statuses: {},
    refusals: {},
    validations: [],
  };
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  context.permissions = ["view", "run", "create"];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("fixture");
  server.use(
    http.get(`${base}/regulatory-runs`, ({ request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get("reporting_period_id")).toBe("period");
      return HttpResponse.json({ runs: [], total: 0, limit: 100, offset: 0 });
    }),
    http.get(
      `${base}/scenario-workbench/:module/scenarios`,
      ({ params, request }) => {
        expect(
          new URL(request.url).searchParams.get("reporting_period_id"),
        ).toBe("period");
        return HttpResponse.json({
          bank_id: "BK-SAMP0001",
          module: params.module,
          as_of_date: "2026-03-31",
          allowed_keys: [],
          allowed_prefixes: [],
          scenarios: [
            {
              kind: "system",
              code: "baseline",
              name: `${params.module} baseline definition`,
              shocks: {},
              configured: true,
              locked: true,
              is_archived: false,
            },
          ],
        });
      },
    ),
    http.get(`${base}/scenario-workbench/ftp/analyses`, () =>
      HttpResponse.json({ analyses: [], total: 0, limit: 25, offset: 0 }),
    ),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});
it.each(["irr", "liquidity", "capital", "fx", "ftp"] as const)(
  "shows %s definitions without launching enterprise stress",
  async (module) => {
    mount(module);
    expect(
      await screen.findByText(
        `${module} baseline definition · Regulatory · Configured`,
      ),
    ).toBeVisible();
    expect(screen.getByText(/No stored .* scenario runs/)).toBeVisible();
  },
);
it("sends FTP curve and rule overrides to preview and save, then reopens the saved result", async () => {
  let savedBody: unknown;
  server.use(
    http.post(
      `${base}/scenario-workbench/ftp/analysis`,
      async ({ request }) => {
        const body = await request.json();
        expect(body).toEqual({
          reporting_period_id: "period",
          scenarios: [
            { kind: "system", code: "baseline" },
            {
              kind: "system",
              code: "baseline",
              overrides: {
                curve_shift_bp: "250",
                min_product_margin_pct: "10",
              },
            },
          ],
        });
        return HttpResponse.json({
          bank_id: "BK-SAMP0001",
          module: "ftp",
          reporting_period_id: "period",
          period_label: "March",
          as_of_date: "2026-03-31",
          engine_version: "ftp",
          computed_at: "2026-03-31T00:00:00Z",
          results: [result("0"), result("250")],
        });
      },
    ),
    http.post(
      `${base}/scenario-workbench/ftp/analyses`,
      async ({ request }) => {
        savedBody = await request.json();
        return HttpResponse.json({
          id: "saved",
          module: "ftp",
          reporting_period_id: "period",
          name: "ALCO what-if",
          engine_version: "ftp",
          scenarios: [],
          results: [result("0"), result("250")],
          created_at: "2026-03-31T00:00:00Z",
        });
      },
    ),
    http.get(`${base}/scenario-workbench/ftp/analyses/saved`, () =>
      HttpResponse.json({
        id: "saved",
        module: "ftp",
        reporting_period_id: "period",
        name: "ALCO what-if",
        engine_version: "ftp",
        scenarios: [],
        results: [result("0"), result("250")],
        created_at: "2026-03-31T00:00:00Z",
      }),
    ),
  );
  const user = userEvent.setup();
  mount("ftp");
  await screen.findByText("ftp baseline definition · Regulatory · Configured");
  await user.type(screen.getByLabelText("Curve shift (bp)"), "250");
  await user.type(screen.getByLabelText("Product margin floor (%)"), "10");
  await user.click(screen.getByRole("button", { name: "Preview FTP what-if" }));
  expect(
    await screen.findByText("What-if · FTP 250 · succeeded"),
  ).toBeVisible();
  await user.type(
    screen.getByLabelText("Analysis / scenario name"),
    "ALCO what-if",
  );
  await user.click(screen.getByRole("button", { name: "Save FTP analysis" }));
  expect(await screen.findByText("Saved: ALCO what-if")).toBeVisible();
  expect(savedBody).toMatchObject({
    name: "ALCO what-if",
    reporting_period_id: "period",
    scenarios: [
      {},
      { overrides: { curve_shift_bp: "250", min_product_margin_pct: "10" } },
    ],
  });
  await user.type(screen.getByLabelText("Curve shift (bp)"), "1");
  expect(screen.getByText(/Inputs changed/)).toBeVisible();
});
it("offers readers disabled scoped run and save controls", async () => {
  context.permissions = ["view"];
  mount("ftp");
  const button = await screen.findByRole("button", {
    name: "Preview FTP what-if",
  });
  expect(button).toBeDisabled();
  await userEvent.hover(button.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "FTP · Confidential · Run",
  );
  expect(
    screen.getByRole("button", { name: "Save FTP analysis" }),
  ).toBeDisabled();
});
