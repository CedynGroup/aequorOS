// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ModuleScope as ModuleScopeFlags } from "../../lib/modules";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ActivatePanel from "../../components/data-engine/ActivatePanel";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

// The component is the real one; the context providers are the mocked
// boundary. Every mutation goes through the MSW network, so a click that must
// not fire a request is proven by the request log, not by a spy on the hook.
const state = vi.hoisted(() => ({
  bank: "BK-SAMP0001",
  moduleScope: {} as Partial<ModuleScopeFlags>,
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    profile: { userId: "actor" },
    isLoading: false,
    effectiveAuthority: { institutionCapabilities: [] },
  }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: state.bank },
    authorityPending: false,
    period: { periodEnd: new Date("2026-09-30") },
    moduleScope: state.moduleScope,
  }),
  useModuleScope: () => state.moduleScope,
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));
vi.mock("@/lib/api/useQueryScope", () => ({
  useQueryAuthorityScope: () =>
    queryAuthorityScope("OR-DEM00001", "actor", 1),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/data-engine",
  useSearchParams: () => new URLSearchParams(),
}));

const base = `${apiBaseUrl}/banks/${state.bank}`;
const server = setupServer();
let client: QueryClient;
const mutations: Array<{ path: string; body: Record<string, unknown> }> = [];

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAccessToken("test-token");
});
beforeEach(() => {
  // computed_at feeds the freshness display; freeze the clock so the fixture
  // reads a deterministic instant instead of the wall clock.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  state.moduleScope = {
    isResolved: true,
    institutionClass: "bank",
    entitledModules: new Set([
      "liquidity",
      "capital",
      "markets",
      "credit",
      "irrbb",
      "fx",
      "ftp",
      "forecasting",
    ]),
  };
  server.use(
    http.get(`${base}/ingestion-batches`, () =>
      HttpResponse.json({ bank_id: state.bank, batches: [] }),
    ),
    http.get(`${base}/live-summary`, () =>
      HttpResponse.json({
        bank_id: state.bank,
        modules: [],
        is_stale: false,
        computed_at: new Date().toISOString(),
      }),
    ),
    http.post(`${base}/refresh`, async ({ request }) => {
      mutations.push({
        path: "refresh",
        body: (await request.json()) as Record<string, unknown>,
      });
      return HttpResponse.json({ job_id: "job", job_type: "pipeline_refresh", status: "queued" });
    }),
    http.post(`${base}/official-runs`, async ({ request }) => {
      mutations.push({
        path: "official-runs",
        body: (await request.json()) as Record<string, unknown>,
      });
      return HttpResponse.json({ job_id: "job", job_type: "official_run", status: "queued" });
    }),
    http.get(`${apiBaseUrl}/jobs/job`, () =>
      HttpResponse.json({ id: "job", status: "succeeded", progress: {} }),
    ),
  );
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
  client.clear();
  server.resetHandlers();
  mutations.length = 0;
});
afterAll(() => {
  server.close();
  setAccessToken(null);
});

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <ActivatePanel />
    </QueryClientProvider>,
  );
}

async function openAdvanced() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Advanced actions" }));
  await user.type(screen.getByLabelText("As-of date"), "2026-10-01");
  return user;
}

it("disables recompute and mint with the missing run authority named", async () => {
  state.moduleScope = {
    ...state.moduleScope,
    dataRun: false,
    capitalRun: false,
    marketsRun: false,
  };
  mount();
  await openAdvanced();
  expect(screen.getByRole("button", { name: "Recompute now" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Mint official run" })).toBeDisabled();
  // The reason is visible in the panel, not hidden behind a hover.
  expect(screen.getByText(/DATA · Confidential · Run/)).toBeInTheDocument();
  expect(screen.getByText(/Capital · Run/)).toBeInTheDocument();
  expect(screen.getByText(/Market Data · Run/)).toBeInTheDocument();
  // And no mutation reached the network.
  expect(mutations).toEqual([]);
});

it("names only the missing Markets run when every other engine holds run", async () => {
  state.moduleScope = {
    ...state.moduleScope,
    dataRun: true,
    capitalRun: true,
    marketsRun: false,
    liquidityRun: true,
    creditRun: true,
    irrbbRun: true,
    fxRun: true,
    ftpRun: true,
    forecastingRun: true,
  };
  mount();
  const user = await openAdvanced();
  const mint = screen.getByRole("button", { name: "Mint official run" });
  expect(mint).toBeDisabled();
  expect(screen.getByText(/Market Data · Run/)).toBeInTheDocument();
  expect(screen.queryByText(/Capital · Run/)).not.toBeInTheDocument();
  // The recompute the same caller MAY do stays enabled — and it fires.
  await user.click(screen.getByRole("button", { name: "Recompute now" }));
  await waitFor(() =>
    expect(mutations).toEqual([
      {
        path: "refresh",
        body: {
          as_of_date: "2026-10-01",
          reason: "Recompute now from the Data Engine console.",
        },
      },
    ]),
  );
});

it("enables both controls and sends both mutations for full run authority", async () => {
  state.moduleScope = {
    ...state.moduleScope,
    dataRun: true,
    capitalRun: true,
    marketsRun: true,
    liquidityRun: true,
    creditRun: true,
    irrbbRun: true,
    fxRun: true,
    ftpRun: true,
    forecastingRun: true,
  };
  mount();
  const user = await openAdvanced();
  expect(screen.getByRole("button", { name: "Recompute now" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Mint official run" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Recompute now" }));
  await waitFor(() => expect(mutations.map((m) => m.path)).toEqual(["refresh"]));
  await user.click(screen.getByRole("button", { name: "Mint official run" }));
  await waitFor(() =>
    expect(mutations.map((m) => m.path)).toEqual(["refresh", "official-runs"]),
  );
  expect(mutations[1].body).toEqual({
    as_of_date: "2026-10-01",
    reason: "Minted official run for filing from the Data Engine console.",
  });
});

it("does not demand the Basel forecast run for an SDI (no s.29 projection authority)", async () => {
  // runs_module excludes forecast for an SDI (sdi_regime: the Basel projection
  // has no s.29 authority) beside the liquidity substitution — the UI gate
  // mirrors both, so a user who may run every engine the SDI actually plans
  // sees an enabled mint control, never a Forecasting · Run demand the
  // backend would not make.
  state.moduleScope = {
    ...state.moduleScope,
    institutionClass: "sdi",
    entitledModules: new Set([
      "liquidity",
      "capital",
      "markets",
      "credit",
      "irrbb",
      "forecasting",
    ]),
    dataRun: true,
    capitalRun: true,
    marketsRun: true,
    liquidityRun: true,
    creditRun: true,
    irrbbRun: true,
    forecastingRun: false,
  };
  mount();
  await openAdvanced();
  const mint = screen.getByRole("button", { name: "Mint official run" });
  expect(mint).toBeEnabled();
  expect(screen.queryByText(/Forecasting · Run/)).not.toBeInTheDocument();
});
