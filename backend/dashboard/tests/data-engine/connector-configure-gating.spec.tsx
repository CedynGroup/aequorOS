// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React, { type ReactNode } from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import MappingPanel from "../../components/data-engine/MappingPanel";
import DatabaseConnectionCard from "../../components/database/ConnectionCard";
import TemenosConnectionCard from "../../components/t24/ConnectionCard";
import MarketDataSourceCard from "../../components/market-data/SourceCards";
import SourcesControlRoom from "../../components/markets/SourcesControlRoom";
import DatabasePage from "../../app/(app)/data-engine/database/page";
import MarketDataPage from "../../app/(app)/data-engine/market-data/page";
import { setAccessToken } from "../../lib/api/token";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

/**
 * Connector-page configure gating (issue #184, VAL-CFG-012).
 *
 * The backend gates DATA mapping/connection lifecycle and MARKETS connection
 * lifecycle + source-preference writes on an exact data|markets /
 * configure / restricted / whole-institution binding. These specs pin that the
 * dashboard mirrors the gate: configuration controls are disabled with an
 * explained state for everyone else (including holders of a capability for a
 * different institution), while read and operational test/discover/sync
 * controls keep their separate gates.
 */

const BANK = "BK-SAMP0001";

const authority = vi.hoisted((): {
  bankId: string;
  capabilities: EffectiveCapabilityRead[];
  foreignCapabilities: EffectiveCapabilityRead[];
  loading: boolean;
  impersonating: boolean;
} => ({
  bankId: "BK-SAMP0001",
  capabilities: [],
  foreignCapabilities: [],
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
          institutionId: "BK-FOREIGN0",
          capabilities: authority.foreignCapabilities,
        },
        {
          institutionId: authority.bankId,
          capabilities: authority.capabilities,
        },
      ],
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
    moduleScope: {
      marketsRestrictedView: true,
      marketsPublishedView: true,
      marketsUpload: true,
    },
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
const dataView = () =>
  capability("data", { permission: "view" });
const marketsView = () =>
  capability("markets", { permission: "view" });

const connection = {
  id: "connection",
  status: "ACTIVE",
  created_at: "2026-01-01T00:00:00Z",
  credential_fingerprint: "fixture-fingerprint",
  credential_expires_at: null,
  last_validated_at: null,
  validation_error: null,
};
const databaseConnection = {
  ...connection,
  display_name: "Reporting replica",
  backend: "oracle",
  host: "replica.example",
  port: 1521,
  database: "reporting",
  service_name: "reporting",
  schemas: ["reports"],
  read_replicas: [],
  prefer_read_replica: false,
  query_timeout_seconds: 30,
  tls_enabled: true,
  tls_verify_server_certificate: true,
  connection_options: {},
  extraction_spec: {},
  last_synced_at: null,
  last_sync_status: null,
};
const temenosConnection = {
  ...connection,
  display_name: "Core banking",
  connection_mode: "iris",
  core_system: "t24",
  endpoint: "https://core.example",
  domains: [],
  companies: [],
  catalog_overrides: {},
  schedule: {},
  default_currency: "EUR",
  last_pull_at: null,
  last_pull_status: null,
};
const marketDataConnection = {
  ...connection,
  display_name: "Bloomberg onboarding",
  vendor: "bloomberg",
  scopes: ["curves"],
  last_pull_at: null,
  last_pull_status: null,
};
const sourcePreferences = {
  curves: { source: "aequor", overlay: false },
  fx: { source: "bank", overlay: true },
  rates: { source: "vendor", overlay: false },
  updated_at: null,
  updated_by: null,
};

let client: QueryClient;
let writes: { method: string; path: string }[];
beforeEach(() => {
  authority.bankId = BANK;
  authority.capabilities = [];
  authority.foreignCapabilities = [];
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
      if (method !== "GET") writes.push({ method, path: url.pathname });
      if (
        method === "GET" &&
        url.pathname.endsWith("/database-direct/connections")
      )
        return Response.json({ connections: [databaseConnection] });
      if (method === "GET" && url.pathname.endsWith("/temenos/connections"))
        return Response.json({ connections: [temenosConnection] });
      if (method === "GET" && url.pathname.endsWith("/mapping-configs"))
        return Response.json({ bank_id: authority.bankId, configs: [] });
      if (method === "POST" && url.pathname.endsWith("/mapping-configs"))
        return Response.json({
          id: "mapping",
          bank_id: authority.bankId,
          name: "Canonical passthrough (API field names)",
          status: "active",
          source_system: "EXCEL_CSV",
          version: 1,
          config: {},
          created_at: "2026-01-01T00:00:00Z",
        });
      if (method === "GET" && url.pathname.endsWith("/source-preferences"))
        return Response.json(sourcePreferences);
      if (method === "PUT" && url.pathname.endsWith("/source-preferences"))
        return Response.json(sourcePreferences);
      if (method === "GET" && url.pathname.endsWith("/planes"))
        return Response.json({
          category: url.searchParams.get("category"),
          as_of: "2026-09-30",
          selected_source: "aequor",
          overlay_enabled: false,
          planes: [],
          overlay: { available: false, delta_preview: [] },
        });
      if (method === "GET" && url.pathname.endsWith("/market-data/connections"))
        return Response.json({ connections: [marketDataConnection] });
      if (method === "GET" && url.pathname.endsWith("/market-data/quota"))
        return Response.json({ vendors: [] });
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
function mount(ui: React.ReactElement) {
  return render(ui, { wrapper: Providers });
}

async function expectDisabledWithExplanation(
  button: HTMLElement,
  module: "Data" | "Markets",
) {
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleDescription(
    new RegExp(
      `Requires ${module} configure access with Restricted data for this institution`,
      "i",
    ),
  );
  await userEvent.setup().click(button);
  expect(writes).toEqual([]);
}

// ---------------------------------------------------------------------------
// DATA: mapping activation (mapping-config creation)
// ---------------------------------------------------------------------------

it("explains disabled mapping activation for a reader despite DATA read access", async () => {
  authority.capabilities = [
    capability("data", { permission: "view", sensitivity: "confidential" }),
  ];
  mount(<MappingPanel />);
  for (const button of await screen.findAllByRole("button", {
    name: "Activate mapping",
  })) {
    await expectDisabledWithExplanation(button, "Data");
  }
});

it("allows mapping activation with an exact DATA configure projection", async () => {
  // Read (confidential) for the mapping status plus configure for the write.
  authority.capabilities = [
    dataView(),
    capability("data", { permission: "view", sensitivity: "confidential" }),
    capability("data"),
  ];
  mount(<MappingPanel />);
  const activate = (
    await screen.findAllByRole("button", { name: "Activate mapping" })
  )[0];
  await waitFor(() => expect(activate).toBeEnabled());
  await userEvent.setup().click(activate);
  await waitFor(() =>
    expect(writes.some((w) => w.path.endsWith("/mapping-configs"))).toBe(true),
  );
});

it("never accepts another institution's DATA configure capability", async () => {
  authority.capabilities = [dataView()];
  authority.foreignCapabilities = [capability("data")];
  mount(<MappingPanel />);
  for (const button of await screen.findAllByRole("button", {
    name: "Activate mapping",
  })) {
    await expectDisabledWithExplanation(button, "Data");
  }
});

// ---------------------------------------------------------------------------
// DATA: database-direct connection lifecycle
// ---------------------------------------------------------------------------

it("explains disabled connection triggers without DATA configure", async () => {
  authority.capabilities = [dataView()];
  mount(<DatabasePage />);
  const triggers = await screen.findAllByRole("button", {
    name: "Connect a database",
  });
  expect(triggers.length).toBeGreaterThan(0);
  for (const trigger of triggers)
    await expectDisabledWithExplanation(trigger, "Data");
});

it("allows the connection trigger with an exact DATA configure projection", async () => {
  authority.capabilities = [dataView(), capability("data")];
  mount(<DatabasePage />);
  const trigger = (
    await screen.findAllByRole("button", { name: "Connect a database" })
  )[0];
  await waitFor(() => expect(trigger).toBeEnabled());
});

const DATABASE_LIFECYCLE = ["Edit", "Disable", "Revoke"] as const;

it.each(DATABASE_LIFECYCLE)(
  "explains disabled database %s without DATA configure while operational controls stay separate",
  async (label) => {
    authority.capabilities = [
      dataView(),
      capability("data", { permission: "validate" }),
    ];
    mount(
      <DatabaseConnectionCard
        bankId={authority.bankId}
        connection={databaseConnection as never}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await expectDisabledWithExplanation(button, "Data");
    // The operational live check keeps its separate VALIDATE gate: enabled.
    expect(screen.getByRole("button", { name: "Test" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Discover schema" }),
    ).toBeEnabled();
  },
);

it.each(DATABASE_LIFECYCLE)(
  "allows database %s with an exact DATA configure projection",
  async (label) => {
    authority.capabilities = [dataView(), capability("data")];
    mount(
      <DatabaseConnectionCard
        bankId={authority.bankId}
        connection={databaseConnection as never}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await waitFor(() => expect(button).toBeEnabled());
  },
);

it("keeps database lifecycle controls disabled for a foreign configure capability", async () => {
  authority.capabilities = [dataView()];
  authority.foreignCapabilities = [capability("data")];
  mount(
    <DatabaseConnectionCard
      bankId={authority.bankId}
      connection={databaseConnection as never}
    />,
  );
  const edit = await screen.findByRole("button", { name: "Edit" });
  await expectDisabledWithExplanation(edit, "Data");
});

// ---------------------------------------------------------------------------
// DATA: Temenos connection lifecycle (credential validation is configure)
// ---------------------------------------------------------------------------

const TEMENOS_LIFECYCLE = [
  "Validate stored credentials",
  "Rotate credentials",
  "Disable",
  "Revoke",
] as const;

it.each(TEMENOS_LIFECYCLE)(
  "explains disabled Temenos %s without DATA configure",
  async (label) => {
    authority.capabilities = [
      dataView(),
      capability("data", { permission: "validate" }),
    ];
    mount(
      <TemenosConnectionCard
        bankId={authority.bankId}
        connection={temenosConnection as never}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await expectDisabledWithExplanation(button, "Data");
    // The operational availability check keeps its separate VALIDATE gate.
    expect(
      screen.getByRole("button", { name: "Check availability" }),
    ).toBeEnabled();
  },
);

it.each(TEMENOS_LIFECYCLE)(
  "allows Temenos %s with an exact DATA configure projection",
  async (label) => {
    authority.capabilities = [dataView(), capability("data")];
    mount(
      <TemenosConnectionCard
        bankId={authority.bankId}
        connection={temenosConnection as never}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await waitFor(() => expect(button).toBeEnabled());
  },
);

// ---------------------------------------------------------------------------
// MARKETS: source cards (connection lifecycle) and source-preference routing
// ---------------------------------------------------------------------------

const MARKETS_LIFECYCLE = [
  "Validate",
  "Rotate credentials",
  "Disable",
  "Revoke",
] as const;

it.each(MARKETS_LIFECYCLE)(
  "explains disabled market source %s without MARKETS configure",
  async (label) => {
    authority.capabilities = [marketsView()];
    mount(
      <MarketDataSourceCard
        bankId={authority.bankId}
        connection={marketDataConnection as never}
        quota={undefined}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await expectDisabledWithExplanation(button, "Markets");
  },
);

it.each(MARKETS_LIFECYCLE)(
  "allows market source %s with an exact MARKETS configure projection",
  async (label) => {
    authority.capabilities = [marketsView(), capability("markets")];
    mount(
      <MarketDataSourceCard
        bankId={authority.bankId}
        connection={marketDataConnection as never}
        quota={undefined}
      />,
    );
    const button = await screen.findByRole("button", { name: label });
    await waitFor(() => expect(button).toBeEnabled());
  },
);

it("keeps market source controls disabled for a foreign configure capability", async () => {
  authority.capabilities = [marketsView()];
  authority.foreignCapabilities = [capability("markets")];
  mount(
    <MarketDataSourceCard
      bankId={authority.bankId}
      connection={marketDataConnection as never}
      quota={undefined}
    />,
  );
  const disable = await screen.findByRole("button", { name: "Disable" });
  await expectDisabledWithExplanation(disable, "Markets");
});

it("explains disabled market source triggers without MARKETS configure", async () => {
  authority.capabilities = [marketsView()];
  mount(<MarketDataPage />);
  const triggers = await screen.findAllByRole("button", {
    name: "Connect a source",
  });
  expect(triggers.length).toBeGreaterThan(0);
  for (const trigger of triggers)
    await expectDisabledWithExplanation(trigger, "Markets");
});

it("allows the market source trigger with MARKETS configure", async () => {
  authority.capabilities = [marketsView(), capability("markets")];
  mount(<MarketDataPage />);
  const trigger = (
    await screen.findAllByRole("button", { name: "Connect a source" })
  )[0];
  await waitFor(() => expect(trigger).toBeEnabled());
});

it("explains disabled source routing without MARKETS configure", async () => {
  authority.capabilities = [marketsView()];
  mount(<SourcesControlRoom bankId={authority.bankId} asOf={null} />);
  // Wait for the preferences to load so the rows exist.
  await screen.findByText("Source routing");
  const groups = await screen.findAllByRole("group", { name: "Base source" });
  expect(groups.length).toBe(3);
  for (const group of groups)
    for (const button of within(group).queryAllByRole("button"))
      expect(button).toBeDisabled();
  for (const toggle of screen.getAllByRole("switch"))
    expect(toggle).toBeDisabled();
  expect(screen.getByText(/Requires Markets configure access/i)).toBeVisible();
  expect(writes).toEqual([]);
});

it("saves source routing with MARKETS configure", async () => {
  authority.capabilities = [marketsView(), capability("markets")];
  mount(<SourcesControlRoom bankId={authority.bankId} asOf={null} />);
  const firstToggle = (await screen.findAllByRole("switch"))[0];
  await waitFor(() => expect(firstToggle).toBeEnabled());
  await userEvent.setup().click(firstToggle);
  await waitFor(() =>
    expect(
      writes.some((w) => w.path.endsWith("/source-preferences")),
    ).toBe(true),
  );
});
