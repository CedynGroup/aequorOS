// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React, { type ReactNode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import BoardRegistersPage from "../app/(app)/institution/registers/page";
import ModuleGuard from "../components/shell/ModuleGuard";
import ProfileProvider from "../components/profile/ProfileProvider";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { setAccessToken } from "../lib/api/token";

const selection = vi.hoisted(() => ({
  pathname: "/institution/registers",
  resolved: true,
  bank: { id: "BK-SAMP0001" } as { id: string } | null,
  capabilities: [] as EffectiveCapabilityRead[],
}));
vi.mock("next/navigation", () => ({
  usePathname: () => selection.pathname,
  redirect: (path: string) => {
    throw new Error(`Redirect: ${path}`);
  },
  notFound: () => {
    throw new Error("Not found");
  },
}));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: selection.bank,
    period: { id: "synthetic-period", periodEnd: new Date("2026-09-30") },
    institutionCapabilities: selection.capabilities,
  }),
  useModuleScope: () => ({
    modules: new Set(["institution", "liquidity", "credit"]),
    entitledModules: new Set(["institution", "liquidity", "credit"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    hasInstitutionAuthority: selection.capabilities.length > 0,
    institutionCapabilities: selection.capabilities,
    isResolved: selection.resolved,
  }),
}));

const liquidityPaths = ["liquidity-thresholds", "liquidity/ewis"];
const creditPaths = ["credit-thresholds", "crm-haircuts", "ecl-assumptions"];
const liquidityTitles = [
  "Board threshold register",
  "EWI trigger-level register",
];
const creditTitles = [
  "Credit early-warning thresholds",
  "CRM haircut register",
  "ECL assumptions register",
];
let client: QueryClient;
let requests: string[];

beforeEach(() => {
  selection.pathname = "/institution/registers";
  selection.resolved = true;
  selection.bank = { id: "BK-SAMP0001" };
  selection.capabilities = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  requests = [];
  setAccessToken("synthetic-session");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(input instanceof Request ? input.url : String(input))
        .pathname;
      requests.push(path);
      if (path === "/api/v1/organization/institutions/access-request")
        return Response.json({ institutions: [] });
      if (path === "/api/v1/authorization/access-requests/mine")
        return Response.json({ requests: [] });
      const register = path.replace("/api/v1/banks/BK-SAMP0001/", "");
      switch (register) {
        case "liquidity-thresholds":
          return Response.json({
            bank_id: "BK-SAMP0001",
            jurisdiction_code: "SYN",
            as_of_date: "2026-09-30",
            thresholds: [],
            history: [],
          });
        case "credit-thresholds":
          return Response.json({ as_of: "2026-09-30", thresholds: [] });
        case "crm-haircuts":
          return Response.json({
            bank_id: "BK-SAMP0001",
            jurisdiction_code: "SYN",
            as_of_date: "2026-09-30",
            haircuts: [],
            history: [],
          });
        case "ecl-assumptions":
          return Response.json({
            bank_id: "BK-SAMP0001",
            jurisdiction_code: "SYN",
            as_of_date: "2026-09-30",
            assumptions: [],
            history: [],
          });
        case "liquidity/ewis":
          return Response.json({
            bank_id: "BK-SAMP0001",
            reporting_period_id: "synthetic-period",
            as_of_date: "2026-09-30",
            escalation_state: "normal",
            indicators: [],
          });
        default:
          throw new Error(`Unexpected request: ${path}`);
      }
    }),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function grant(
  module: EffectiveCapabilityRead["module"],
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module,
    sensitivity: "confidential",
    permission: "view",
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
    ...overrides,
  };
}
function Providers({ children }: { children: ReactNode }) {
  return (
    <SessionProvider session={null}>
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={{
            tenantId: "synthetic-tenant",
            authorityId: "synthetic-actor",
          }}
        >
          <ProfileProvider>{children}</ProfileProvider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
function view(children: ReactNode) {
  return render(children, { wrapper: Providers });
}

it.each([
  {
    name: "Liquidity only",
    modules: ["liq"] as const,
    paths: liquidityPaths,
    titles: liquidityTitles,
    hidden: creditTitles,
  },
  {
    name: "Credit only",
    modules: ["credit"] as const,
    paths: creditPaths,
    titles: creditTitles,
    hidden: liquidityTitles,
  },
  {
    name: "both source modules",
    modules: ["liq", "credit"] as const,
    paths: [...liquidityPaths, ...creditPaths],
    titles: [...liquidityTitles, ...creditTitles],
    hidden: [],
  },
])(
  "shows and fetches only authorized Board registers with $name",
  async ({ modules, paths, titles, hidden }) => {
    selection.capabilities = modules.map((module) => grant(module));
    selection.resolved = false;
    const rendered = view(
      <ModuleGuard>
        <BoardRegistersPage />
      </ModuleGuard>,
    );
    expect(screen.queryByRole("heading")).toBeNull();
    expect(requests).toEqual([]);
    selection.resolved = true;
    rendered.rerender(
      <ModuleGuard>
        <BoardRegistersPage />
      </ModuleGuard>,
    );
    for (const title of titles)
      expect(screen.getByRole("heading", { name: title })).toBeVisible();
    for (const title of hidden)
      expect(screen.queryByRole("heading", { name: title })).toBeNull();
    await waitFor(() =>
      expect(screen.getAllByRole("table")).toHaveLength(modules.length * 2),
    );
    if (paths.includes("ecl-assumptions"))
      expect(await screen.findByText("No assumptions adopted")).toBeVisible();
    await waitFor(() =>
      expect(requests.sort()).toEqual(
        paths.map((path) => `/api/v1/banks/BK-SAMP0001/${path}`).sort(),
      ),
    );
  },
);

it.each([
  { name: "no grants", grants: [] },
  { name: "Institution grants", grants: [grant("institution")] },
  { name: "REG grants", grants: [grant("reg")] },
  {
    name: "partial sensitivity and action rows",
    grants: [
      grant("liq", { sensitivity: "restricted" }),
      grant("liq", { permission: "edit" }),
      grant("credit", { sensitivity: "restricted" }),
      grant("credit", { permission: "edit" }),
    ],
  },
  {
    name: "contextual grants",
    grants: [
      grant("liq", { requiresContextualAuthorization: true }),
      grant("credit", { requiresContextualAuthorization: true }),
    ],
  },
  {
    name: "branch grants",
    grants: [
      grant("liq", {
        dataScope: { kind: "branch", branches: ["A"], regions: [] },
      }),
      grant("credit", {
        dataScope: { kind: "branch", branches: ["A"], regions: [] },
      }),
    ],
  },
  {
    name: "region grants",
    grants: [
      grant("liq", {
        dataScope: { kind: "region", branches: [], regions: ["A"] },
      }),
      grant("credit", {
        dataScope: { kind: "region", branches: [], regions: ["A"] },
      }),
    ],
  },
])("does not mount or fetch source registers with $name", ({ grants }) => {
  selection.capabilities = grants;
  view(<BoardRegistersPage />);
  expect(
    screen.getByRole("heading", { name: "Board Registers" }),
  ).toBeVisible();
  for (const title of [...liquidityTitles, ...creditTitles])
    expect(screen.queryByRole("heading", { name: title })).toBeNull();
  expect(requests).toEqual([]);
});

it("does not fetch registers without a selected institution", () => {
  selection.bank = null;
  selection.capabilities = [grant("liq"), grant("credit")];
  view(<BoardRegistersPage />);
  expect(screen.getByText(/No institution yet/)).toBeVisible();
  expect(requests).toEqual([]);
});

it.each([
  "/institution",
  "/institution/parties",
  "/institution/outlets",
  "/institution/history",
  "/institution/products",
  "/institution/registers",
])(
  "waits for resolved authority at %s before mounting its consumer",
  (path) => {
    selection.pathname = path;
    selection.resolved = false;
    const rendered = view(
      <ModuleGuard>
        <BoardRegistersPage />
      </ModuleGuard>,
    );
    expect(screen.queryByRole("heading")).toBeNull();
    expect(requests).toEqual([]);
    selection.resolved = true;
    rendered.rerender(
      <ModuleGuard>
        <BoardRegistersPage />
      </ModuleGuard>,
    );
    expect(
      screen.getByRole("button", { name: "Request access" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Board Registers" }),
    ).toBeNull();
    expect(requests.every((request) => !request.includes("/banks/"))).toBe(
      true,
    );
  },
);
