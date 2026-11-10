// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React, { type ReactNode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fc from "fast-check";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import ThresholdRegisterCard from "../../components/institution/registers/ThresholdRegisterCard";
import CreditThresholdCard from "../../components/institution/registers/CreditThresholdCard";
import CrmHaircutCard from "../../components/institution/registers/CrmHaircutCard";
import EclAssumptionCard from "../../components/institution/registers/EclAssumptionCard";
import EwiRegisterCard from "../../components/institution/registers/EwiRegisterCard";
import BoardRegistersPage from "../../app/(app)/institution/registers/page";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { setAccessToken } from "../../lib/api/token";

const authority = vi.hoisted((): {
  bankId: string;
  capabilities: EffectiveCapabilityRead[];
  loading: boolean;
  error: Error | null;
  inspecting: boolean;
  roles: string[];
} => ({
  bankId: "BK-SAMP0001",
  capabilities: [],
  loading: false,
  error: null,
  inspecting: false,
  roles: ["approver", "admin"],
}));
vi.mock("../../components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: authority.loading,
    error: authority.error,
    effectiveAuthority: {
      institutionCapabilities: [{
        institutionId: authority.bankId,
        capabilities: authority.capabilities,
      }],
    },
  }),
}));
vi.mock("../../components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: authority.inspecting }),
}));
vi.mock("../../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SAMP0001" },
    institutionCapabilities: authority.capabilities,
  }),
  useModuleScope: () => ({ institutionClass: "bank" }),
}));

const cards: readonly {
  name: string;
  module: "liq" | "credit";
  Card: React.ComponentType<{ bankId: string; periodId: string | undefined }>;
}[] = [
  { name: "liquidity thresholds", module: "liq", Card: ThresholdRegisterCard },
  { name: "EWI triggers", module: "liq", Card: EwiRegisterCard },
  { name: "credit thresholds", module: "credit", Card: CreditThresholdCard },
  { name: "CRM haircuts", module: "credit", Card: CrmHaircutCard },
  { name: "ECL assumptions", module: "credit", Card: EclAssumptionCard },
];
function capability(
  module: EffectiveCapabilityRead["module"],
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module, sensitivity: "restricted", permission: "configure",
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
    ...overrides,
  };
}
let client: QueryClient;
let writes: { path: string; body: unknown }[];
const register = {
  bank_id: "BK-SAMP0001", jurisdiction_code: "SYN", as_of_date: "2026-09-30",
  thresholds: [{
    threshold_code: "narrow_to_volatile", threshold_pct: "40",
    institution_class: "bank", source: "regulatory_default",
  }],
  history: [], haircuts: [], assumptions: [], indicators: [],
};
beforeEach(() => {
  authority.bankId = "BK-SAMP0001";
  authority.capabilities = [];
  authority.loading = false;
  authority.error = null;
  authority.inspecting = false;
  authority.roles = ["approver", "admin"];
  writes = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("synthetic-session");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input)).pathname;
    if (init?.method === "PUT") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      if (path.endsWith("/liquidity/ewis")) return new Response(null, { status: 204 });
      return Response.json({
        bank_id: "BK-SAMP0001", institution_class: "bank",
        effective_from: "2026-09-30", approved_by: "Board minute",
        approval_timestamp: "2026-09-30T12:00:00Z", notes: null,
        written: [{
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          threshold_code: "narrow_to_volatile", threshold_pct: "45",
        }],
      });
    }
    if (path.endsWith("/templates")) return Response.json({ templates: [] });
    return Response.json(path.endsWith("/credit-thresholds")
      ? { as_of: "2026-09-30", thresholds: [] } : register);
  }));
});
afterEach(() => {
  cleanup();
  client.clear();
  setAccessToken(null);
  vi.unstubAllGlobals();
});
function Providers({ children }: { children: ReactNode }) {
  return (
    <SessionProvider session={{
      expires: "2099-01-01", user: { roles: authority.roles },
    }}>
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider scope={{ tenantId: "test", authorityId: "actor" }}>
          {children}
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
it.each(cards)("explains disabled $name for a reader despite scalar roles", async ({ Card, module }) => {
  authority.capabilities = [capability(module, { permission: "view", sensitivity: "confidential" })];
  render(<Card bankId="BK-SAMP0001" periodId="period" />, { wrapper: Providers });
  const edit = await screen.findByRole("button", { name: /Edit (register|trigger levels)/ });
  expect(edit).toBeDisabled();
  expect(edit).toHaveAccessibleDescription(/Requires.*configure.*Restricted.*institution/i);
  await userEvent.click(edit);
  expect(writes).toEqual([]);
  expect(screen.queryByRole("button", { name: "Record generation" })).toBeNull();
});
it.each(cards)("allows $name with an exact configure projection", async ({ Card, module }) => {
  authority.roles = ["viewer"];
  authority.capabilities = [
    capability(module),
    capability(module, { permission: "view", sensitivity: "confidential" }),
  ];
  render(<Card bankId="BK-SAMP0001" periodId="period" />, { wrapper: Providers });
  const edit = await screen.findByRole("button", { name: /Edit (register|trigger levels)/ });
  expect(edit).toBeEnabled();
  await userEvent.click(edit);
  expect(screen.getByRole("button", { name: /Record (generation|trigger levels)/ })).toBeVisible();
});
it("saves a changed credit threshold through the generated client with Board evidence", async () => {
  authority.roles = ["viewer"];
  authority.capabilities = [
    capability("credit"),
    capability("credit", { permission: "view", sensitivity: "confidential" }),
  ];
  render(<CreditThresholdCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await userEvent.click(await screen.findByRole("button", { name: "Edit register" }));
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("NPL Board trigger"), "5");
  await user.type(screen.getByLabelText(/Effective from/), "2026-11-08");
  await user.type(screen.getByLabelText(/Approved by/), "Board minute");
  await user.type(screen.getByLabelText(/Reason/), "Record approved trigger");
  await user.click(screen.getByRole("button", { name: "Record generation" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({
    path: "/api/v1/banks/BK-SAMP0001/credit-thresholds",
    body: {
      thresholds: { npl_board_trigger_pct: "5" },
      approved_by: "Board minute",
      reason: "Record approved trigger",
    },
  });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Record generation" })).toBeNull(),
  );
});
it("retains the credit editor and displays the backend denial if authority changes before save", async () => {
  authority.roles = ["viewer"];
  authority.capabilities = [capability("credit")];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input)).pathname;
    if (init?.method === "PUT") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      return Response.json(
        { error: { code: "forbidden", message: "Configure binding revoked" } },
        { status: 403 },
      );
    }
    return Response.json({ as_of: "2026-11-08", thresholds: [] });
  }));
  render(<CreditThresholdCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await userEvent.click(await screen.findByRole("button", { name: "Edit register" }));
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("NPL Board trigger"), "5");
  await user.type(screen.getByLabelText(/Effective from/), "2026-11-08");
  await user.type(screen.getByLabelText(/Approved by/), "Board minute");
  await user.type(screen.getByLabelText(/Reason/), "Record approved trigger");
  await user.click(screen.getByRole("button", { name: "Record generation" }));
  expect(await screen.findByText("Configure binding revoked")).toBeVisible();
  expect(screen.getByLabelText("NPL Board trigger")).toHaveValue("5");
});
it("does not turn configure-only authority into register read/navigation access", () => {
  authority.capabilities = [capability("liq"), capability("credit")];
  render(<BoardRegistersPage />, { wrapper: Providers });
  expect(screen.getByRole("heading", { name: "Board Registers" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Board threshold register" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Credit early-warning thresholds" })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
it("never combines incomplete capability rows into configuration authority", async () => {
  await fc.assert(fc.asyncProperty(
    fc.array(fc.constantFrom(
      capability("liq", { permission: "view" }),
      capability("liq", { sensitivity: "confidential" }),
      capability("credit"),
      capability("liq", { requiresContextualAuthorization: true }),
    ), { maxLength: 12 }),
    async (grants) => {
      authority.capabilities = grants;
      const view = render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
      expect(await screen.findByRole("button", { name: "Edit register" })).toBeDisabled();
      view.unmount();
    },
  ), { numRuns: 30 });
});
it.each([
  { name: "wrong bank", bankId: "BK-OTHER001", grants: [capability("liq")] },
  { name: "wrong module", grants: [capability("credit")] },
  { name: "wrong sensitivity", grants: [capability("liq", { sensitivity: "confidential" })] },
  { name: "partial rows", grants: [capability("liq", { permission: "view" }), capability("credit")] },
  { name: "contextual", grants: [capability("liq", { requiresContextualAuthorization: true })] },
  { name: "branch scope", grants: [capability("liq", { dataScope: { kind: "branch", branches: ["A"], regions: [] } })] },
  { name: "region scope", grants: [capability("liq", { dataScope: { kind: "region", branches: [], regions: ["A"] } })] },
  { name: "loading", loading: true, grants: [capability("liq")] },
  { name: "failed authority refresh", error: new Error("Unavailable"), grants: [capability("liq")] },
  { name: "staff inspection", inspecting: true, grants: [capability("liq")] },
])("fails closed for $name", async ({ grants, ...state }) => {
  Object.assign(authority, state, { capabilities: grants });
  render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  expect(await screen.findByRole("button", { name: "Edit register" })).toBeDisabled();
});
it("closes an open editor when configure authority is removed", async () => {
  authority.capabilities = [capability("liq")];
  const view = render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await userEvent.click(await screen.findByRole("button", { name: "Edit register" }));
  expect(screen.getByRole("button", { name: "Record generation" })).toBeVisible();
  authority.capabilities = [];
  view.rerender(<ThresholdRegisterCard bankId="BK-SAMP0001" />);
  expect(screen.queryByRole("button", { name: "Record generation" })).toBeNull();
  expect(screen.getByRole("button", { name: "Edit register" })).toBeDisabled();
});
async function submitThreshold() {
  await userEvent.click(await screen.findByRole("button", { name: "Edit register" }));
  const user = userEvent.setup();
  await user.clear(screen.getByLabelText("Narrow liquid assets / volatile funds"));
  await user.type(screen.getByLabelText("Narrow liquid assets / volatile funds"), "45");
  await user.type(screen.getByLabelText(/Effective from/), "2026-09-30");
  await user.type(screen.getByLabelText(/Approved by/), "Board minute");
  await user.type(screen.getByLabelText(/Reason/), "Record approved threshold");
  await user.click(screen.getByRole("button", { name: "Record generation" }));
}
it("saves a changed threshold through the generated client with Board evidence", async () => {
  authority.capabilities = [capability("liq")];
  render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await submitThreshold();
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({
    path: "/api/v1/banks/BK-SAMP0001/liquidity-thresholds",
    body: { thresholds: { narrow_to_volatile: "45" }, approved_by: "Board minute", reason: "Record approved threshold" },
  });
});
it.each([false, true])("refetches the register after acknowledgement (read denied: %s)", async (readDenied) => {
  authority.capabilities = [
    capability("liq"),
    capability("liq", { permission: "view", sensitivity: "confidential" }),
  ];
  let written = false;
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "PUT") {
      written = true;
      return Response.json({
        bank_id: "BK-SAMP0001", institution_class: "bank",
        effective_from: "2026-09-30", approved_by: "Board minute",
        approval_timestamp: "2026-09-30T12:00:00Z", notes: null,
        written: [{
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          threshold_code: "narrow_to_volatile", threshold_pct: "45",
        }],
      });
    }
    reads += 1;
    if (written && readDenied) {
      return Response.json({
        error: { code: "forbidden", message: "Register read grant revoked" },
      }, { status: 403 });
    }
    return Response.json(written ? {
      ...register,
      // A stored Board value can resolve to a stricter regulatory floor.
      thresholds: [{
        ...register.thresholds[0], threshold_pct: "80",
        source: "board_register", approved_by: "Read-authorized Board evidence",
      }],
    } : register);
  }));
  render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await submitThreshold();
  await waitFor(() => expect(reads).toBeGreaterThan(1));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Record generation" })).toBeNull());
  if (readDenied) {
    expect(await screen.findByText("Register read grant revoked")).toBeVisible();
    expect(screen.queryByText("80.00%")).toBeNull();
  } else {
    expect(await screen.findByText("80.00%")).toBeVisible();
    expect(screen.getByText("Read-authorized Board evidence")).toBeVisible();
  }
  expect(screen.queryByText("45.00%")).toBeNull();
});
it("retains the editor and displays the backend denial if authority changes before save", async () => {
  authority.capabilities = [capability("liq")];
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "PUT"
      ? Response.json({ error: { code: "forbidden", message: "Configure binding revoked" } }, { status: 403 })
      : Response.json(register),
  ));
  render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await submitThreshold();
  expect(await screen.findByText("Configure binding revoked")).toBeVisible();
  expect(screen.getByLabelText("Narrow liquid assets / volatile funds")).toHaveValue("45");
});
it("does not carry edited values into another institution", async () => {
  authority.capabilities = [capability("liq")];
  const view = render(<ThresholdRegisterCard bankId="BK-SAMP0001" />, { wrapper: Providers });
  await userEvent.click(await screen.findByRole("button", { name: "Edit register" }));
  await userEvent.clear(screen.getByLabelText("Narrow liquid assets / volatile funds"));
  await userEvent.type(screen.getByLabelText("Narrow liquid assets / volatile funds"), "61");
  authority.bankId = "BK-OTHER001";
  view.rerender(<ThresholdRegisterCard bankId="BK-OTHER001" />);
  await waitFor(() =>
    expect(screen.getByLabelText("Narrow liquid assets / volatile funds")).toHaveValue("40"),
  );
});
