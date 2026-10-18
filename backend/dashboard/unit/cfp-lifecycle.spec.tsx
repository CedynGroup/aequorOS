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
import CfpLifecycle from "../components/liquidity/CfpLifecycle";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";

const context = vi.hoisted(() => ({
  actor: "maker",
  permissions: ["create", "edit", "approve"],
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SAMP0001" },
    period: { id: "period", periodEnd: new Date("2026-03-31") },
    authorityPending: false,
    institutionCapabilities: context.permissions.map((permission) => ({
      module: "liq",
      sensitivity: "confidential",
      dataScope: { kind: "all", branches: [], regions: [] },
      permission,
      requiresContextualAuthorization: permission === "approve",
    })),
  }),
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ profile: { userId: context.actor } }),
}));
const server = setupServer();
let client: QueryClient;
const url = `${apiBaseUrl}/banks/BK-SAMP0001/liquidity/cfp`;
const plan = {
  id: "plan",
  bank_id: "BK-SAMP0001",
  version: 1,
  status: "draft",
  prepared_by: "maker",
  content: {},
  created_at: "2026-03-31T00:00:00Z",
  updated_at: "2026-03-31T00:00:00Z",
  active: false,
  approval_overdue: false,
  approval_expires_at: "2027-03-31",
};
function mount() {
  return render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("org", "fixture", 1)}
      >
        <CfpLifecycle />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  context.actor = "maker";
  context.permissions = ["create", "edit", "approve"];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("fixture");
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});

it("saves an authored draft through the generated client and reloads persisted content", async () => {
  let current: typeof plan | null = null;
  server.use(
    http.get(url, () => HttpResponse.json({ current, approved: null })),
    http.put(url, async ({ request }) => {
      const body = (await request.json()) as {
        content: { funding_options: { source: string }[] };
        reason: string;
      };
      expect(body.reason).toBe("Treasury evidence");
      expect(body.content.funding_options[0].source).toBe("Committed facility");
      current = { ...plan, content: body.content };
      return HttpResponse.json(current);
    }),
  );
  const user = userEvent.setup();
  mount();
  await user.click(
    await screen.findByRole("button", { name: "Add funding options" }),
  );
  await user.type(screen.getByLabelText("source"), "Committed facility");
  await user.type(
    screen.getByLabelText("Reason / evidence"),
    "Treasury evidence",
  );
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText(/Current plan: v1 · draft/);
  expect(screen.getByDisplayValue("Committed facility")).toBeVisible();
});

it("blocks maker approval, then an independent approver can approve, activate and stand down", async () => {
  let current = { ...plan };
  let active = false;
  server.use(
    http.get(url, () =>
      HttpResponse.json({
        current: { ...current, active },
        approved: current.status === "approved" ? { ...current, active } : null,
      }),
    ),
    http.post(`${url}/approve`, async ({ request }) => {
      expect(await request.json()).toEqual({
        approval_reference: "Board 42",
        reason: "Reviewed",
      });
      current = {
        ...current,
        status: "approved",
        updated_at: "2026-04-01T00:00:00Z",
      };
      return HttpResponse.json(current);
    }),
    ...["activate", "de-escalate"].map((action) =>
      http.post(`${url}/${action}`, async ({ request }) => {
        expect(await request.json()).toEqual({
          reporting_period_id: "period",
          reason: "Evidence",
        });
        active = action === "activate";
        return HttpResponse.json({
          id: action,
          cfp_id: "plan",
          cfp_version: 1,
          event_type: active ? "activated" : "de_escalated",
          reason: "Evidence",
          ewi_snapshot: [],
          approval_overdue: false,
          created_at: "2026-04-01T00:00:00Z",
        });
      }),
    ),
  );
  const user = userEvent.setup();
  const view = mount();
  await user.type(
    await screen.findByLabelText("Reason / evidence"),
    "Reviewed",
  );
  await user.type(
    screen.getByLabelText("Board approval reference"),
    "Board 42",
  );
  expect(
    screen.getByRole("button", { name: "Record Board approval" }),
  ).toBeDisabled();
  context.actor = "checker";
  view.rerender(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("org", "fixture", 1)}
      >
        <CfpLifecycle />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
  await user.click(
    screen.getByRole("button", { name: "Record Board approval" }),
  );
  await screen.findByText(/Current plan: v1 · approved/);
  await user.type(screen.getByLabelText("Reason / evidence"), "Evidence");
  await user.click(screen.getByRole("button", { name: "Activate CFP" }));
  await waitFor(() => expect(active).toBe(true));
  await screen.findByText(
    "CFP action recorded. The plan and event log have been refreshed.",
  );
  expect(screen.getByRole("button", { name: "Stand down CFP" })).toBeDisabled();
  await user.clear(screen.getByLabelText("Reason / evidence"));
  await user.type(screen.getByLabelText("Reason / evidence"), "Evidence");
  await user.click(screen.getByRole("button", { name: "Stand down CFP" }));
  await waitFor(() => expect(active).toBe(false));
});

it("keeps readers' mutation controls disabled with scoped reasons", async () => {
  context.permissions = ["view"];
  server.use(
    http.get(url, () => HttpResponse.json({ current: plan, approved: null })),
  );
  mount();
  const save = await screen.findByRole("button", { name: "Save draft" });
  expect(save).toBeDisabled();
  const user = userEvent.setup();
  await user.hover(save.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "Liquidity · Confidential · Create",
  );
  expect(
    screen.getByRole("button", { name: "Record Board approval" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: "Activate CFP" })).toBeDisabled();
});
