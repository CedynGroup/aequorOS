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
import { cleanup, render, screen, within } from "@testing-library/react";
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
  await user.type(screen.getByLabelText("Analysis name"), "ALCO what-if");
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
  expect(
    screen.queryByLabelText("Reusable scenario code"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Save FTP scenario definition" }),
  ).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Curve shift (bp)"), "1");
  expect(screen.getByText(/Inputs changed/)).toBeVisible();
});

function storedRun(
  id: string,
  module: WorkbenchModule,
  scenarioCode: string,
  metrics: Record<string, unknown>,
) {
  return {
    id,
    module,
    scenario_code: scenarioCode,
    status: "succeeded",
    reporting_period_id: "period",
    period_label: "March",
    engine_version: module,
    input_hash: id,
    metrics,
    error: null,
    created_at: "2026-03-31T00:00:00Z",
  };
}

it.each(["irr", "liquidity", "capital", "fx", "ftp"] as const)(
  "preserves %s result values and finds the latest scenario across all pages",
  async (module) => {
    const structured = {
      stress_path: [{ quarter: "Q1", cet1_pct: "12.25", breached: false }],
      basis: { provisions: "booked", triggers: ["capital_floor"] },
      empty_array: [],
      empty_object: {},
    };
    const runs = [
      storedRun("newest", module, "baseline", {
        nmd_within_policy: true,
        within_aggregate_limit: false,
        margin: "0",
        count: 0,
        missing: null,
        ...structured,
      }),
      ...Array.from({ length: 200 }, (_, i) =>
        storedRun(`older-${i}`, module, "baseline", { margin: "outdated" }),
      ),
      storedRun("historic", module, "historic_shock", {
        historic_result: "8.5",
      }),
    ];
    server.use(
      http.get(`${base}/regulatory-runs`, ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get("module")).toBe(module);
        expect(url.searchParams.get("reporting_period_id")).toBe("period");
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const limit = Number(url.searchParams.get("limit"));
        return HttpResponse.json({
          runs: runs.slice(offset, offset + limit),
          total: runs.length,
          limit,
          offset,
          has_more: offset + limit < runs.length,
        });
      }),
    );
    mount(module);
    expect(await screen.findByText("Historic Shock · succeeded")).toBeVisible();
    expect(screen.getAllByText("Baseline · succeeded")).toHaveLength(1);
    expect(screen.getByText("8.5")).toBeVisible();
    expect(screen.getByText("Yes")).toBeVisible();
    expect(screen.getByText("No")).toBeVisible();
    expect(screen.getAllByText("0")).toHaveLength(2);
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.queryByText("outdated")).not.toBeInTheDocument();
    for (const [key, value] of Object.entries(structured)) {
      const term = screen.getByText(
        new RegExp(`^${key.replaceAll("_", " ")}$`, "i"),
        { selector: "dt" },
      );
      const definition = within(term.parentElement!).getByRole("definition");
      expect(JSON.parse(definition.textContent!)).toEqual(value);
    }
  },
);

it("reopens a selected-period FTP analysis after multiple newer pages", async () => {
  const analyses = Array.from({ length: 51 }, (_, i) => ({
    id: `analysis-${i}`,
    module: "ftp",
    reporting_period_id: i === 50 ? "period" : "april",
    name: i === 50 ? "March funding comparison" : `April comparison ${i}`,
    engine_version: "ftp",
    scenario_count: 2,
    created_at: "2026-04-30T00:00:00Z",
  }));
  server.use(
    http.get(`${base}/scenario-workbench/ftp/analyses`, ({ request }) => {
      const offset = Number(
        new URL(request.url).searchParams.get("offset") ?? 0,
      );
      return HttpResponse.json({
        analyses: analyses.slice(offset, offset + 25),
        total: analyses.length,
        limit: 25,
        offset,
      });
    }),
    http.get(`${base}/scenario-workbench/ftp/analyses/analysis-50`, () =>
      HttpResponse.json({
        ...analyses[50],
        scenarios: [],
        results: [result("0"), result("250")],
      }),
    ),
  );
  mount("ftp");
  const option = await screen.findByRole("option", {
    name: /March funding comparison/,
  });
  const selector = screen.getByRole("combobox", { name: "Saved FTP analyses" });
  expect(within(selector).getAllByRole("option")).toHaveLength(2);
  await userEvent.selectOptions(selector, option);
  expect(
    await screen.findByText("Saved: March funding comparison"),
  ).toBeVisible();
  expect(screen.getByText("What-if · FTP 250 · succeeded")).toBeVisible();
});

it.each(["regulatory-runs", "scenario-workbench/ftp/analyses"])(
  "refuses a partial registry when a later %s page fails",
  async (path) => {
    server.use(
      http.get(`${base}/${path}`, ({ request }) => {
        const offset = Number(
          new URL(request.url).searchParams.get("offset") ?? 0,
        );
        if (offset > 0) {
          return HttpResponse.json(
            { detail: "Registry access refused" },
            { status: 403 },
          );
        }
        return HttpResponse.json({
          runs: [storedRun("partial", "ftp", "partial", { partial: "hidden" })],
          analyses: [
            {
              id: "partial",
              module: "ftp",
              reporting_period_id: "period",
              name: "Partial analysis",
              engine_version: "ftp",
              scenario_count: 1,
              created_at: "2026-03-31T00:00:00Z",
            },
          ],
          total: 2,
          limit: 1,
          offset: 0,
          has_more: true,
        });
      }),
    );
    mount("ftp");
    expect(await screen.findByText("Could not load data")).toBeVisible();
    expect(screen.queryByText("Partial · succeeded")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /Partial analysis/ }),
    ).not.toBeInTheDocument();
  },
);
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
