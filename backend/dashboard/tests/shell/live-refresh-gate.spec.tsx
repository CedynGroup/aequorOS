// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ModuleScope as ModuleScopeFlags } from "../../lib/modules";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import Header from "../../components/shell/Header";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

// The real header over an MSW network boundary; the context providers are the
// only mocked boundary. The freshness pill is aged (a module last recomputed
// three hours ago), so it offers the manual recompute button — the control
// #177 part B gates on DATA/confidential run.
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
    bank: {
      id: state.bank,
      name: "Validation Bank",
      licenseType: "universal_bank",
      jurisdiction: { timezone: "Africa/Accra", locale: "en-GH" },
      institutionTypeDetail: { displayName: "Universal Bank" },
    },
    authorityPending: false,
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
vi.mock("@/components/shell/ThemeProvider", () => ({
  useTheme: () => ({ resolvedTheme: "light", toggle: vi.fn() }),
}));
vi.mock("next-auth/react", () => ({
  useSession: () => ({
    data: { expires: "", user: { name: "Actor", email: "actor@example.test" } },
    status: "authenticated",
  }),
  signOut: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

const base = `${apiBaseUrl}/banks/${state.bank}`;
const server = setupServer();
let client: QueryClient;
const refreshBodies: Record<string, unknown>[] = [];

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  setAccessToken("test-token");
});
beforeEach(() => {
  // The Header derives staleness from the gap between the current instant and
  // computed_at; freeze the clock so the fixture is an exact offset, not a
  // moving one.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  state.moduleScope = {
    isResolved: true,
    institutionClass: "bank",
    organizationModules: new Set(),
    hasInstitutionAuthority: true,
  };
  const aged = new Date("2026-10-01T09:00:00Z").toISOString();
  server.use(
    http.get(`${base}/live-summary`, () =>
      HttpResponse.json({
        bank_id: state.bank,
        is_stale: true,
        computed_at: aged,
        source_as_of_date: "2026-10-01",
        period_label: "2026-10",
        modules: [
          { module: "liquidity", status: "green", computed_at: aged, metrics: {} },
        ],
      }),
    ),
    http.get(`${base}/alerts`, () =>
      HttpResponse.json({
        bank_id: state.bank,
        items: [],
        total: 0,
        by_severity: {},
        by_module: {},
      }),
    ),
    http.get(`${apiBaseUrl}/regulatory-reporting/templates`, () =>
      HttpResponse.json({ bank_id: state.bank, templates: [] }),
    ),
    http.get(`${apiBaseUrl}/notifications`, () =>
      HttpResponse.json({ items: [], total: 0, unread_count: 0 }),
    ),
    http.post(`${base}/refresh`, async ({ request }) => {
      refreshBodies.push(
        (await request.json()) as Record<string, unknown>,
      );
      return HttpResponse.json({ job_id: "job", job_type: "pipeline_refresh", status: "queued" });
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
  refreshBodies.length = 0;
});
afterAll(() => {
  server.close();
  setAccessToken(null);
});

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <Header />
    </QueryClientProvider>,
  );
}

it("disables the aged refresh control with the run-authority reason", async () => {
  state.moduleScope = { ...state.moduleScope, dataRun: false };
  mount();
  const refresh = await screen.findByRole("button", { name: /Refresh delayed/ });
  expect(refresh).toBeDisabled();
  // The reason is visible in the control itself and spelled out in the
  // DisabledWithReason tooltip (hovered — a disabled button takes no focus).
  expect(refresh).toHaveTextContent(/DATA/);
  fireEvent.mouseEnter(refresh.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "DATA · Confidential · Run",
  );
  // A disabled control fires nothing.
  const user = userEvent.setup();
  await user.click(refresh);
  expect(refreshBodies).toEqual([]);
});

it("enables the refresh control and enqueues the recompute with run authority", async () => {
  state.moduleScope = { ...state.moduleScope, dataRun: true };
  mount();
  const refresh = await screen.findByRole("button", { name: /Refresh delayed/ });
  expect(refresh).toBeEnabled();
  const user = userEvent.setup();
  await user.click(refresh);
  await waitFor(() =>
    expect(refreshBodies).toEqual([
      {
        as_of_date: "2026-10-01",
        reason: "Recompute live figures (header)",
      },
    ]),
  );
});
