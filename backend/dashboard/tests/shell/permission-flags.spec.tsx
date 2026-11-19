// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import BankProvider, { useBankContext } from "../../components/shell/BankContext";
import ProfileProvider from "../../components/profile/ProfileProvider";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import type { ModuleScope } from "../../lib/modules";

// Characterization for the shell's ModuleScope permission flags: every flag the
// BankProvider computes from the effective-authority capability lists, pinned as
// a snapshot so a table-driven rewrite can be proven value-identical. The
// capability lists below are FIXED fixtures; nothing here talks to a real
// server beyond the MSW handlers declared in this file.
//
// Completeness is type-checked: the name list below must cover every optional
// boolean field of ModuleScope except the two deployment flags, which are read
// from GET /feature-flags and are not permission flags.

/**
 * Every ModuleScope permission flag, one entry per flag. `satisfies` makes the
 * list fail to compile the moment ModuleScope grows or loses an optional
 * boolean flag without this fixture learning about it.
 */
const PERMISSION_FLAGS_LISTED = {
  liquidityAggregatedView: true,
  liquidityConfidentialView: true,
  liquidityRun: true,
  riskConfidentialView: true,
  capitalAggregatedView: true,
  capitalConfidentialView: true,
  capitalRestrictedView: true,
  capitalRun: true,
  capitalCreate: true,
  capitalEdit: true,
  capitalExport: true,
  capitalApprove: true,
  creditAggregatedView: true,
  creditRestrictedView: true,
  creditRun: true,
  irrbbAggregatedView: true,
  irrbbConfidentialView: true,
  irrbbRun: true,
  fxAggregatedView: true,
  fxConfidentialView: true,
  fxRun: true,
  ftpAggregatedView: true,
  ftpConfidentialView: true,
  ftpRun: true,
  forecastingAggregatedView: true,
  forecastingConfidentialView: true,
  forecastingRun: true,
  forecastingEdit: true,
  forecastingApprove: true,
  behavioralAggregatedView: true,
  behavioralRestrictedView: true,
  behavioralRun: true,
  marketsPublishedView: true,
  marketsConfidentialView: true,
  marketsRestrictedView: true,
  marketsAggregatedView: true,
  marketsUpload: true,
  marketsOverlayCreate: true,
  marketsOverlayEdit: true,
  marketsRun: true,
  dataRun: true,
  auditCreate: true,
} as const satisfies Record<
  Exclude<
    {
      [K in keyof ModuleScope]-?: [
        ModuleScope[K],
      ] extends [boolean | undefined]
        ? undefined extends ModuleScope[K]
          ? K
          : never
        : never;
    }[keyof ModuleScope],
    "biEnabled" | "nlqEnabled"
  >,
  true
>;

type PermissionFlagName = keyof typeof PERMISSION_FLAGS_LISTED;

const PERMISSION_FLAG_NAMES = Object.keys(
  PERMISSION_FLAGS_LISTED,
) as PermissionFlagName[];

type WireCapability = {
  module: string;
  sensitivity: string;
  permission: string;
  requires_contextual_authorization?: boolean;
  data_scope?: { kind: string; branches?: string[]; regions?: string[] };
};

function capability(
  module: string,
  sensitivity: string,
  permission: string,
  options: { contextual?: boolean; branch?: boolean } = {},
): WireCapability {
  return {
    module,
    sensitivity,
    permission,
    requires_contextual_authorization: options.contextual ?? false,
    data_scope: options.branch
      ? { kind: "branch", branches: ["HQ"], regions: [] }
      : { kind: "all", branches: [], regions: [] },
  };
}

// Every capability each flag reads, projected non-contextual with a
// whole-institution data scope: all 42 flags must be true.
const RICH_INSTITUTION_CAPABILITIES: WireCapability[] = [
  capability("liq", "aggregated", "view"),
  capability("liq", "confidential", "view"),
  capability("liq", "confidential", "run"),
  capability("risk", "confidential", "view"),
  capability("cap", "aggregated", "view"),
  capability("cap", "confidential", "view"),
  capability("cap", "restricted", "view"),
  capability("cap", "confidential", "run"),
  capability("cap", "confidential", "create"),
  capability("cap", "confidential", "edit"),
  capability("cap", "confidential", "export"),
  capability("cap", "confidential", "approve", { contextual: true }),
  capability("credit", "aggregated", "view"),
  capability("credit", "restricted", "view"),
  capability("credit", "confidential", "run"),
  capability("irrbb", "aggregated", "view"),
  capability("irrbb", "confidential", "view"),
  capability("irrbb", "confidential", "run"),
  capability("fx", "aggregated", "view"),
  capability("fx", "confidential", "view"),
  capability("fx", "confidential", "run"),
  capability("ftp", "aggregated", "view"),
  capability("ftp", "confidential", "view"),
  capability("ftp", "confidential", "run"),
  capability("fcst", "aggregated", "view"),
  capability("fcst", "confidential", "view"),
  capability("fcst", "confidential", "run"),
  capability("fcst", "confidential", "edit"),
  capability("fcst", "confidential", "approve", { contextual: true }),
  capability("beh", "aggregated", "view"),
  capability("beh", "restricted", "view"),
  capability("beh", "confidential", "run"),
  capability("markets", "published", "view"),
  capability("markets", "confidential", "view"),
  capability("markets", "restricted", "view"),
  capability("markets", "aggregated", "view"),
  capability("markets", "published", "create"),
  capability("markets", "confidential", "create"),
  capability("markets", "confidential", "edit"),
  capability("markets", "confidential", "run"),
  capability("data", "confidential", "run"),
];

// Audit's create is read against the ORGANIZATION capability list; account
// administer projects the settings module but must not flip any permission flag.
const RICH_ORGANIZATION_CAPABILITIES: WireCapability[] = [
  capability("audit", "confidential", "create"),
  capability("account", "confidential", "administer"),
];

// Only the distinctions: a branch-scoped credit view must NOT satisfy the
// whole-institution credit flags while a branch-scoped liquidity view DOES
// satisfy its institution-scoped flag; the two contextual approve capabilities
// satisfy the structural flags only; an organization audit create still reads.
const DISTINCTION_INSTITUTION_CAPABILITIES: WireCapability[] = [
  capability("credit", "aggregated", "view", { branch: true }),
  capability("liq", "aggregated", "view", { branch: true }),
  capability("cap", "confidential", "approve", { contextual: true }),
  capability("fcst", "confidential", "approve", { contextual: true }),
  capability("markets", "published", "create"),
];

const DISTINCTION_ORGANIZATION_CAPABILITIES: WireCapability[] = [
  capability("audit", "confidential", "create"),
];

const bankId = "BK-FLAG0001";
const server = setupServer();
let client: QueryClient;

function serveCapabilities(
  institution: WireCapability[],
  organization: WireCapability[],
) {
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json({
        user_id: "flag-fixture",
        effective_authority: {
          authv: 1,
          organization_capabilities: organization,
          institution_capabilities: [
            { institution_id: bankId, capabilities: institution },
          ],
        },
      }),
    ),
    http.get(`${apiBaseUrl}/banks`, () =>
      HttpResponse.json({
        banks: [
          {
            id: bankId,
            name: "Flag Fixture Bank",
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
    http.get(`${apiBaseUrl}/banks/${bankId}/reporting-periods`, () =>
      HttpResponse.json({ periods: [] }),
    ),
  );
}

let captured: ModuleScope | undefined;

function ScopeCapture() {
  captured = useBankContext().moduleScope;
  return null;
}

async function renderedScope(): Promise<ModuleScope> {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("fixture");
  render(
    <SessionProvider
      session={{
        expires: "2099-01-01T00:00:00Z",
        accessToken: "fixture",
        user: {
          email: "flags@example.test",
          organizationId: "org",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("org", "flags@example.test", 1)}
        >
          <PathnameContext.Provider value="/">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>
                  <ScopeCapture />
                </BankProvider>
              </ProfileProvider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>,
  );
  await waitFor(() => expect(captured?.isResolved).toBe(true));
  return captured as ModuleScope;
}

function permissionFlagValues(
  scope: ModuleScope,
): Record<PermissionFlagName, boolean | undefined> {
  const record = {} as Record<PermissionFlagName, boolean | undefined>;
  for (const name of PERMISSION_FLAG_NAMES) {
    record[name] = scope[name];
  }
  return record;
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client?.clear();
  server.resetHandlers();
  setAccessToken(null);
});

it("computes every permission flag from a whole-institution capability list", async () => {
  serveCapabilities(RICH_INSTITUTION_CAPABILITIES, RICH_ORGANIZATION_CAPABILITIES);
  const scope = await renderedScope();
  expect(permissionFlagValues(scope)).toMatchSnapshot();
});

it("computes every permission flag as false without any capability", async () => {
  serveCapabilities([], []);
  const scope = await renderedScope();
  expect(permissionFlagValues(scope)).toMatchSnapshot();
});

it("keeps the source and check distinctions of each flag", async () => {
  serveCapabilities(
    DISTINCTION_INSTITUTION_CAPABILITIES,
    DISTINCTION_ORGANIZATION_CAPABILITIES,
  );
  const scope = await renderedScope();
  const values = permissionFlagValues(scope);
  // Branch-scoped credit never satisfies the whole-institution credit flags,
  // while the same branch-scoped liquidity capability satisfies its
  // institution-scoped flag; the contextual approves are structural offers.
  expect(values.creditAggregatedView).toBe(false);
  expect(values.creditRestrictedView).toBe(false);
  expect(values.creditRun).toBe(false);
  expect(values.liquidityAggregatedView).toBe(true);
  expect(values.capitalApprove).toBe(true);
  expect(values.forecastingApprove).toBe(true);
  expect(values.capitalConfidentialView).toBe(false);
  expect(values.forecastingRun).toBe(false);
  expect(values.marketsUpload).toBe(true);
  expect(values.auditCreate).toBe(true);
  expect(values).toMatchSnapshot();
});
