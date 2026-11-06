// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React, { type ReactNode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import SettingsPage from "../../app/(app)/submissions/settings/page";
import { setAccessToken } from "../../lib/api/token";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

/**
 * Regulatory Reporting configure gating (issue #184, VAL-CFG-015).
 *
 * The backend gates the channel-config PUT on an exact reg / configure /
 * restricted / whole-institution binding, and the signing-policy + placement
 * template PUTs on an ORGANIZATION-scope reg / configure / restricted binding
 * (VAL-CFG-008: one bank's grant is deliberately not organization-wide
 * authority). These specs pin that the Submissions settings page mirrors both
 * gates: the two save controls are disabled with an explained state for
 * everyone else — including a bank-scoped Configurator, who can save the
 * channel configuration but not a signing policy — while the read surfaces
 * stay available.
 */

const BANK = "BK-SAMP0001";

const authority = vi.hoisted((): {
  bankId: string;
  capabilities: EffectiveCapabilityRead[];
  orgCapabilities: EffectiveCapabilityRead[];
  loading: boolean;
  impersonating: boolean;
} => ({
  bankId: "BK-SAMP0001",
  capabilities: [],
  orgCapabilities: [],
  loading: false,
  impersonating: false,
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: authority.loading,
    error: null,
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: authority.bankId,
          capabilities: authority.capabilities,
        },
      ],
      organizationCapabilities: authority.orgCapabilities,
    },
  }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: authority.impersonating }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: authority.bankId },
    authorityPending: authority.loading,
  }),
  useModuleScope: () => ({ institutionClass: "bank" }),
}));

function capability(
  module: EffectiveCapabilityRead["module"],
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module,
    sensitivity: "restricted",
    permission: "configure",
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
    ...overrides,
  };
}
const regView = () => capability("reg", { permission: "view" });
const regConfigure = () => capability("reg");
const regOrgConfigure = () =>
  capability("reg", { dataScope: { kind: "all", branches: [], regions: [] } });

let client: QueryClient;
let writes: { method: string; path: string; body: unknown }[];
beforeEach(() => {
  authority.bankId = BANK;
  authority.capabilities = [];
  authority.orgCapabilities = [];
  authority.loading = false;
  authority.impersonating = false;
  writes = [];
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("synthetic-session");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (method !== "GET")
        writes.push({ method, path: url.pathname, body });
      if (method === "GET" && url.pathname.endsWith("/templates"))
        return Response.json({
          templates: [
            {
              code: "BSD3",
              family: "liquidity",
              title: "Liquidity position",
              instrumentStatus: "active",
              effective_from: "2026-01-01",
            },
          ],
        });
      if (
        method === "GET" &&
        url.pathname.endsWith("/channel-configs/orass_sandbox")
      )
        return Response.json(
          { detail: "No configuration stored for this channel." },
          { status: 404 },
        );
      if (
        method === "PUT" &&
        url.pathname.endsWith("/channel-configs/orass_sandbox")
      )
        return Response.json({
          bank_id: authority.bankId,
          channel: "orass_sandbox",
          credentials_rotated: false,
          created: true,
        });
      if (method === "GET" && url.pathname.endsWith("/signing-policies"))
        return Response.json({
          policies: [],
          signingSuspendedDeploymentWide: false,
          icaapSigningSuspended: false,
        });
      if (method === "PUT" && url.pathname.endsWith("/signing-policies"))
        return Response.json({
          id: "11111111-1111-1111-1111-111111111111",
          bank_id: authority.bankId,
          return_code: null,
          return_family: "liquidity",
          basis: null,
          required_signatures: [
            { role: "preparer", min_count: 1, officer_titles: [] },
            { role: "approver", min_count: 1, officer_titles: [] },
          ],
          required_attachments: [],
          require_signature: true,
          require_signed_pdf: false,
          distinct_signers: true,
          ordered_slots: false,
          effective_from: "2026-11-06",
          effective_to: null,
          reason: "Board resolution BR-2026-014",
          updated_by: null,
          updated_at: "2026-11-06T00:00:00Z",
        });
      return Response.json({});
    }),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  setAccessToken(null);
  vi.unstubAllGlobals();
});
function Providers({ children }: { children: ReactNode }) {
  return (
    <SessionProvider
      session={{ expires: "2099-01-01", user: { roles: ["viewer"] } }}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("OR-DEM00001", "actor", 1)}
        >
          {children}
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
function mountPage() {
  return render(<SettingsPage />, { wrapper: Providers });
}

async function expectSaveDisabledWithReason(
  name: string,
  reasonPattern: RegExp,
) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAccessibleDescription(reasonPattern);
  await userEvent.setup().click(button);
  expect(writes).toEqual([]);
}

// ---------------------------------------------------------------------------
// Channel configuration: exact-bank reg configure
// ---------------------------------------------------------------------------

it("explains a disabled channel-config save for a reader", async () => {
  authority.capabilities = [regView()];
  mountPage();
  await expectSaveDisabledWithReason(
    "Save configuration",
    /Requires Regulatory Reporting configure access with Restricted data for this institution/i,
  );
});

it("saves the channel configuration with an exact-bank reg configure projection", async () => {
  authority.capabilities = [regView(), regConfigure()];
  mountPage();
  const save = await screen.findByRole("button", { name: "Save configuration" });
  await waitFor(() => expect(save).toBeEnabled());
  await userEvent.setup().click(save);
  await waitFor(() =>
    expect(
      writes.some((w) => w.path.endsWith("/channel-configs/orass_sandbox")),
    ).toBe(true),
  );
});

it("never offers the channel-config save while inspecting as another principal", async () => {
  authority.capabilities = [regView(), regConfigure()];
  authority.impersonating = true;
  mountPage();
  await expectSaveDisabledWithReason(
    "Save configuration",
    /Configuration is not available while inspecting as another principal/i,
  );
});

// ---------------------------------------------------------------------------
// Signing policies: organization-scope reg configure (VAL-CFG-008)
// ---------------------------------------------------------------------------

it("explains a disabled signing-policy save for a reader", async () => {
  authority.capabilities = [regView()];
  mountPage();
  await expectSaveDisabledWithReason(
    "Save policy",
    /Requires organization-scoped Regulatory Reporting configure access with Restricted data/i,
  );
});

it("lets a bank-scoped Configurator save the channel but not a signing policy", async () => {
  // One bank's Configurator grant is deliberately not organization-wide
  // authority: signing policies govern how EVERY bank's returns attest.
  authority.capabilities = [regView(), regConfigure()];
  mountPage();
  await waitFor(async () =>
    expect(
      screen.getByRole("button", { name: "Save configuration" }),
    ).toBeEnabled(),
  );
  await expectSaveDisabledWithReason(
    "Save policy",
    /Requires organization-scoped Regulatory Reporting configure access with Restricted data/i,
  );
});

it("saves a signing policy with an organization-scope reg configure projection", async () => {
  authority.capabilities = [regView(), regConfigure()];
  authority.orgCapabilities = [regOrgConfigure()];
  mountPage();
  const user = userEvent.setup();
  await user.selectOptions(
    await screen.findByLabelText(/Return family/i),
    "liquidity",
  );
  await user.type(
    screen.getByLabelText(/^Reason/i),
    "Board resolution BR-2026-014",
  );
  const save = screen.getByRole("button", { name: "Save policy" });
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);
  await waitFor(() =>
    expect(
      writes.some((w) => w.path.endsWith("/signing-policies")),
    ).toBe(true),
  );
});

it("never offers the signing-policy save while inspecting as another principal", async () => {
  authority.orgCapabilities = [regOrgConfigure()];
  authority.impersonating = true;
  mountPage();
  await expectSaveDisabledWithReason(
    "Save policy",
    /Configuration is not available while inspecting as another principal/i,
  );
});
