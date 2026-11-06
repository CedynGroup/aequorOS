// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fc from "fast-check";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import SettingsPage from "../../app/(app)/submissions/settings/page";
import ProfileProvider from "../../components/profile/ProfileProvider";
import BankProvider from "../../components/shell/BankContext";
import { IMPERSONATION_MARKER_COOKIE } from "../../lib/impersonation-cookies";
import { leaveImpersonation } from "../../lib/api/impersonation";
import { apiBaseUrl } from "../../lib/api/client";
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
 * authority). These specs render the real settings page through the real
 * ProfileProvider and BankProvider, with the authority projection and every
 * read/write answered at the MSW network boundary, and pin that the page
 * mirrors both gates: the two save controls are disabled with an explained
 * state for everyone else — including a bank-scoped Configurator, who can save
 * the channel configuration but not a signing policy — while the read surfaces
 * stay available and the save refetches the view-gated read.
 */

const BANK = "BK-SAMP0001";
const OTHER_BANK = "BK-OTHER000";

type CapabilityDims = {
  module: EffectiveCapabilityRead["module"];
  permission: EffectiveCapabilityRead["permission"];
  sensitivity: EffectiveCapabilityRead["sensitivity"];
  requiresContextualAuthorization: boolean;
  scope: EffectiveCapabilityRead["dataScope"];
};

const state = {
  bankCapabilities: [] as EffectiveCapabilityRead[],
  otherBankCapabilities: [] as EffectiveCapabilityRead[],
  orgCapabilities: [] as EffectiveCapabilityRead[],
  authError: false,
  impersonating: false,
  /** Drives the channel-config read: what the backend would have stored. */
  storedConfig: false,
  reads: { channelConfig: 0, signingPolicies: 0 },
  writes: [] as { method: string; path: string; body: unknown }[],
};

function wireCapability(capability: EffectiveCapabilityRead) {
  return {
    module: capability.module,
    permission: capability.permission,
    sensitivity: capability.sensitivity,
    requires_contextual_authorization: capability.requiresContextualAuthorization,
    data_scope: {
      kind: capability.dataScope.kind,
      branches: capability.dataScope.branches,
      regions: capability.dataScope.regions,
    },
  };
}

function authorityBody() {
  return {
    user_id: "actor",
    effective_authority: {
      authv: 1,
      organization_capabilities: state.orgCapabilities.map(wireCapability),
      institution_capabilities: [
        {
          institution_id: BANK,
          capabilities: state.bankCapabilities.map(wireCapability),
        },
        {
          institution_id: OTHER_BANK,
          capabilities: state.otherBankCapabilities.map(wireCapability),
        },
      ],
    },
  };
}

const server = setupServer(
  http.get(`${apiBaseUrl}/auth/me`, () =>
    state.authError
      ? HttpResponse.json({ detail: "unavailable" }, { status: 500 })
      : HttpResponse.json(authorityBody()),
  ),
  http.get(`${apiBaseUrl}/banks`, () =>
    HttpResponse.json({
      banks: [
        {
          id: BANK,
          name: "Fixture Bank",
          currency: "GHS",
          jurisdiction_code: "GH",
          license_type: "universal",
          institution_type: "universal_bank",
        },
      ],
    }),
  ),
  http.get(`${apiBaseUrl}/feature-flags`, () =>
    HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
  ),
  http.get(`${apiBaseUrl}/banks/${BANK}/reporting-periods`, () =>
    HttpResponse.json({ periods: [] }),
  ),
  http.get(`${apiBaseUrl}/regulatory-reporting/templates`, () =>
    HttpResponse.json({
      templates: [
        {
          code: "BSD3",
          family: "liquidity",
          title: "Liquidity position",
          instrumentStatus: "active",
          effective_from: "2026-01-01",
        },
      ],
    }),
  ),
  http.get(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/channel-configs/orass_sandbox`,
    () => {
      state.reads.channelConfig += 1;
      if (!state.storedConfig)
        return HttpResponse.json(
          { detail: "No configuration stored for this channel." },
          { status: 404 },
        );
      return HttpResponse.json({
        bank_id: BANK,
        channel: "orass_sandbox",
        config: { institution_code: "SBL-001", basis: "solo" },
        has_credentials: true,
        credential_fingerprint: "fingerprint-1010",
        updated_at: "2026-11-06T00:00:00Z",
        created: false,
      });
    },
  ),
  http.put(
    `${apiBaseUrl}/banks/${BANK}/regulatory-reporting/channel-configs/orass_sandbox`,
    async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      state.writes.push({ method: "PUT", path: "channel-config", body });
      // The write-only acknowledgement never echoes the stored config or
      // credential state; the pill must come from the refetched read.
      state.storedConfig = true;
      return HttpResponse.json({
        bank_id: BANK,
        channel: "orass_sandbox",
        credentials_rotated: false,
        created: true,
      });
    },
  ),
  http.get(`${apiBaseUrl}/attestation/signing-policies`, () => {
    state.reads.signingPolicies += 1;
    return HttpResponse.json({
      policies: [],
      signingSuspendedDeploymentWide: false,
      icaapSigningSuspended: false,
    });
  }),
  http.put(`${apiBaseUrl}/attestation/signing-policies`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    state.writes.push({ method: "PUT", path: "signing-policies", body });
    return HttpResponse.json({
      id: "11111111-1111-1111-1111-111111111111",
      bank_id: BANK,
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
  }),
);

let client: QueryClient;
beforeEach(() => {
  state.bankCapabilities = [];
  state.otherBankCapabilities = [];
  state.orgCapabilities = [];
  state.authError = false;
  state.impersonating = false;
  state.storedConfig = false;
  state.reads = { channelConfig: 0, signingPolicies: 0 };
  state.writes = [];
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("synthetic-session");
  server.listen({ onUnhandledRequest: "warn" });
});
afterEach(async () => {
  server.resetHandlers();
  server.close();
  // The impersonation store is a module-level singleton: drop the marker
  // cookie and reset it so no later test inherits an active inspection.
  document.cookie =
    IMPERSONATION_MARKER_COOKIE + "=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  await leaveImpersonation();
  cleanup();
  client.clear();
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function Providers({ children }: React.PropsWithChildren) {
  return (
    <SessionProvider
      session={{
        expires: "2099-01-01",
        accessToken: "synthetic-session",
        user: {
          email: "actor@example.test",
          organizationId: "OR-DEM00001",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("OR-DEM00001", "actor", 1)}
        >
          <PathnameContext.Provider value="/submissions/settings">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>{children}</BankProvider>
              </ProfileProvider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}

function mountPage() {
  return render(<SettingsPage />, { wrapper: Providers });
}

const BANK_GATE_REASON =
  /Requires Regulatory Reporting configure access with Restricted data for this institution/i;
const ORG_GATE_REASON =
  /Requires organization-scoped Regulatory Reporting configure access with Restricted data/i;
const IMPERSONATION_REASON =
  /Configuration is not available while inspecting as another principal/i;

/** The one capability row the exact-bank channel gate admits. */
function gateAdmits(
  row: EffectiveCapabilityRead,
  bankId: string,
): boolean {
  return (
    !row.requiresContextualAuthorization &&
    row.module === "reg" &&
    row.sensitivity === "restricted" &&
    row.permission === "configure" &&
    row.dataScope.kind === "all" &&
    bankId === BANK
  );
}

// ---------------------------------------------------------------------------
// Channel configuration: exact-bank reg configure
// ---------------------------------------------------------------------------

it("explains a disabled channel-config save for a reader", async () => {
  state.bankCapabilities = [capability("reg", { permission: "view" })];
  mountPage();
  await expectChannelSaveDisabled(BANK_GATE_REASON);
});

it("saves the channel configuration and refetches the view-gated read", async () => {
  state.bankCapabilities = [
    capability("reg", { permission: "view" }),
    capability("reg"),
  ];
  mountPage();
  const save = await screen.findByRole("button", { name: "Save configuration" });
  await waitFor(() => expect(save).toBeEnabled());
  await userEvent.setup().click(save);
  await waitFor(() =>
    expect(state.writes.some((w) => w.path === "channel-config")).toBe(true),
  );
  // The save hook discards the write-only acknowledgement and refetches the
  // read: the credential-status pill reflects the refetched GET, not the PUT.
  await waitFor(() =>
    expect(screen.getByText("Credentials stored")).toBeVisible(),
  );
  expect(state.reads.channelConfig).toBeGreaterThanOrEqual(2);
});

it("never offers the channel-config save while inspecting as another principal", async () => {
  state.bankCapabilities = [capability("reg")];
  state.impersonating = true;
  plantImpersonationMarker();
  mountPage();
  await expectChannelSaveDisabled(IMPERSONATION_REASON);
});

it("shows the shell's unreachable panel when the profile read fails", async () => {
  // With the real shell providers, a failed /auth/me never reaches the page's
  // own guard: BankProvider renders its risk-service-unreachable state.
  state.authError = true;
  mountPage();
  expect(await screen.findByText("Risk service unreachable")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Signing policies: organization-scope reg configure (VAL-CFG-008)
// ---------------------------------------------------------------------------

it("explains a disabled signing-policy save for a reader", async () => {
  state.bankCapabilities = [capability("reg", { permission: "view" })];
  mountPage();
  await expectPolicySaveDisabled(ORG_GATE_REASON);
});

it("lets a bank-scoped Configurator save the channel but not a signing policy", async () => {
  // One bank's Configurator grant is deliberately not organization-wide
  // authority: signing policies govern how EVERY bank's returns attest.
  state.bankCapabilities = [capability("reg", { permission: "view" }), capability("reg")];
  mountPage();
  await waitFor(async () =>
    expect(
      screen.getByRole("button", { name: "Save configuration" }),
    ).toBeEnabled(),
  );
  await expectPolicySaveDisabled(ORG_GATE_REASON);
});

it("saves a signing policy with an organization-scope reg configure projection", async () => {
  state.bankCapabilities = [capability("reg", { permission: "view" })];
  state.orgCapabilities = [capability("reg")];
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
    expect(state.writes.some((w) => w.path === "signing-policies")).toBe(true),
  );
});

it("never offers the signing-policy save while inspecting as another principal", async () => {
  state.orgCapabilities = [capability("reg")];
  state.impersonating = true;
  plantImpersonationMarker();
  mountPage();
  await expectPolicySaveDisabled(IMPERSONATION_REASON);
});

// ---------------------------------------------------------------------------
// The gate dimensions, as properties over the real rendered page
// ---------------------------------------------------------------------------

it("enables the channel save exactly for a whole-institution reg/configure/restricted row", async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.array(dimsArb(), { maxLength: 4, minLength: 1 }),
      fc.constantFrom(BANK, OTHER_BANK),
      async (dims, holderBank) => {
        cleanup();
        resetState();
        const row = dims.map(toCapability);
        if (holderBank === BANK) state.bankCapabilities = row;
        else state.otherBankCapabilities = row;
        const expected = row.some((r) => gateAdmits(r, holderBank));
        mountPage();
        const save = await screen.findByRole("button", {
          name: "Save configuration",
        });
        if (expected) await waitFor(() => expect(save).toBeEnabled());
        else await expectSaveRefused(save, BANK_GATE_REASON);
        cleanup();
      },
    ),
    { numRuns: 24 },
  );
});

it("enables the policy save exactly for an organization-scope reg configure row, never a bank's", async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.array(dimsArb(), { maxLength: 4, minLength: 1 }),
      fc.array(dimsArb(), { maxLength: 4 }),
      async (orgDims, bankDims) => {
        cleanup();
        resetState();
        state.orgCapabilities = orgDims.map(toCapability);
        state.bankCapabilities = bankDims.map(toCapability);
        const expected = orgDims.some(
          (r) =>
            !r.requiresContextualAuthorization &&
            r.module === "reg" &&
            r.sensitivity === "restricted" &&
            r.permission === "configure" &&
            r.scope.kind === "all",
        );
        mountPage();
        const save = await screen.findByRole("button", { name: "Save policy" });
        if (expected) await waitFor(() => expect(save).not.toHaveAccessibleDescription(ORG_GATE_REASON));
        else await expectSaveRefused(save, ORG_GATE_REASON);
        cleanup();
      },
    ),
    { numRuns: 24 },
  );
});

// ---------------------------------------------------------------------------
// Fixtures and helpers
// ---------------------------------------------------------------------------

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

const dimsArb = (): fc.Arbitrary<CapabilityDims> =>
  fc.record({
    module: fc.constantFrom("reg", "liq", "credit", "data", "markets"),
    permission: fc.constantFrom("view", "configure"),
    sensitivity: fc.constantFrom("all", "confidential", "restricted"),
    requiresContextualAuthorization: fc.boolean(),
    scope: fc.record({
      kind: fc.constantFrom("all", "branch", "region", "mixed", "none"),
      branches: fc.constant([]),
      regions: fc.constant([]),
    }),
  }) as fc.Arbitrary<CapabilityDims>;

function toCapability(dims: CapabilityDims): EffectiveCapabilityRead {
  return {
    module: dims.module,
    permission: dims.permission,
    sensitivity: dims.sensitivity,
    requiresContextualAuthorization: dims.requiresContextualAuthorization,
    dataScope: dims.scope,
  };
}

function resetState() {
  state.bankCapabilities = [];
  state.otherBankCapabilities = [];
  state.orgCapabilities = [];
  state.authError = false;
  state.impersonating = false;
  state.storedConfig = false;
  state.reads = { channelConfig: 0, signingPolicies: 0 };
  state.writes = [];
}

function plantImpersonationMarker() {
  document.cookie = IMPERSONATION_MARKER_COOKIE + "=1; path=/";
  server.use(
    http.get("/api/impersonation/status", () =>
      HttpResponse.json({
        impersonating: true,
        expired: false,
        token: "synthetic-inspection-token",
        operator: "operator",
        org: "OR-DEM00001",
        expiresAt: null,
      }),
    ),
    http.get(`${apiBaseUrl}/auth/effective-authority`, () =>
      HttpResponse.json(authorityBody().effective_authority),
    ),
  );
}

async function expectChannelSaveDisabled(reason: RegExp) {
  const save = await screen.findByRole("button", { name: "Save configuration" });
  await expectSaveRefused(save, reason);
}

async function expectPolicySaveDisabled(reason: RegExp) {
  const save = await screen.findByRole("button", { name: "Save policy" });
  await expectSaveRefused(save, reason);
}

async function expectSaveRefused(
  save: HTMLElement,
  reason: RegExp,
) {
  await waitFor(() => expect(save).toBeDisabled());
  expect(save).toHaveAccessibleDescription(reason);
  await userEvent.setup().click(save);
  expect(state.writes).toEqual([]);
}
