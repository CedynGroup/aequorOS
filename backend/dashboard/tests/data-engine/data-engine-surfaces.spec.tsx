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
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentType } from "react";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import TemplatesPanel from "../../components/data-engine/TemplatesPanel";
import ActivatePanel from "../../components/data-engine/ActivatePanel";
import AddDatabaseConnection from "../../components/database/AddConnectionPanel";
import AddTemenosConnection from "../../components/t24/AddConnectionPanel";
import Overview from "../../app/(app)/data-engine/page";
import DatabasePage from "../../app/(app)/data-engine/database/page";
import T24Page from "../../app/(app)/data-engine/t24/page";
import PositionsPage from "../../app/(app)/positions/page";
import BatchesTable from "../../components/data-engine/BatchesTable";
import CanonicalPositionsPage from "../../app/(app)/data-engine/positions/page";
import UploadPanel from "../../components/data-engine/UploadPanel";
import MappingPanel from "../../components/data-engine/MappingPanel";
import ConnectionHealthPanel from "../../components/data-engine/ConnectionHealthPanel";
import ReconciliationPanel from "../../components/data-engine/ReconciliationPanel";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";

const state = vi.hoisted(() => ({
  capabilities: [] as EffectiveCapabilityRead[],
  bank: "BK-SAMP0001",
  loading: false,
  bankPending: false,
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    profile: { userId: "checker" },
    isLoading: state.loading,
    effectiveAuthority: {
      institutionCapabilities: [
        { institutionId: state.bank, capabilities: state.capabilities },
      ],
    },
  }),
}));
vi.mock("@/components/shell/BankContext", async () => {
  // Project the fixture capabilities into the exact module-scope flags the
  // surfaces read, through the real capability helper (the backend re-decides
  // everything; these flags only decide what the UI offers).
  const { hasEffectiveCapability } = await import("@/lib/modules");
  const scope = () => ({
    isResolved: true,
    institutionClass: "bank",
    organizationModules: new Set(),
    hasInstitutionAuthority: true,
    entitledModules: new Set(),
    marketsRestrictedView: hasEffectiveCapability(
      state.capabilities,
      "markets",
      "restricted",
      "view",
    ),
    liquidityRun: hasEffectiveCapability(state.capabilities, "liq", "confidential", "run"),
    capitalRun: hasEffectiveCapability(state.capabilities, "cap", "confidential", "run"),
    creditRun: hasEffectiveCapability(state.capabilities, "credit", "confidential", "run"),
    marketsRun: hasEffectiveCapability(state.capabilities, "markets", "confidential", "run"),
    irrbbRun: hasEffectiveCapability(state.capabilities, "irrbb", "confidential", "run"),
    fxRun: hasEffectiveCapability(state.capabilities, "fx", "confidential", "run"),
    ftpRun: hasEffectiveCapability(state.capabilities, "ftp", "confidential", "run"),
    forecastingRun: hasEffectiveCapability(state.capabilities, "fcst", "confidential", "run"),
    dataRun: hasEffectiveCapability(state.capabilities, "data", "confidential", "run"),
  });
  return {
    useBankContext: () => ({
      bank: state.bankPending ? null : { id: state.bank },
      authorityPending: state.loading || state.bankPending,
      period: { periodEnd: new Date("2026-09-30") },
      moduleScope: scope(),
    }),
    useModuleScope: () => ({ institutionClass: "bank", modules: new Set() }),
  };
});
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));
vi.mock("@/lib/api/useQueryScope", () => ({
  useQueryAuthorityScope: () =>
    queryAuthorityScope("OR-DEM00001", "checker", 1),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/positions",
  useSearchParams: () => new URLSearchParams(),
}));

function capability(
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"] = "restricted",
): EffectiveCapabilityRead {
  return {
    module: "data",
    permission,
    sensitivity,
    dataScope: { kind: "all", branches: [], regions: [] },
    requiresContextualAuthorization: permission === "approve",
  };
}
const base = `${apiBaseUrl}/banks/${state.bank}`;
const server = setupServer();
let client: QueryClient;
let requests: string[];
function read(path: string, body: object) {
  return http.get(`${base}/${path}`, () => {
    requests.push(path);
    return HttpResponse.json(body);
  });
}
const commonConnection = {
  id: "connection",
  status: "ACTIVE",
  created_at: "2026-01-01T00:00:00Z",
  credential_fingerprint: "fixture-fingerprint",
  credential_expires_at: null,
  last_validated_at: null,
  validation_error: null,
};
const database = {
  ...commonConnection,
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
const temenos = {
  ...commonConnection,
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
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  setAccessToken("fixture-token");
  state.capabilities = [];
  state.loading = state.bankPending = false;
  requests = [];
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  server.use(
    read("database-direct/connections", { connections: [database] }),
    read("temenos/connections", { connections: [temenos] }),
    read("ingestion-batches", { bank_id: state.bank, batches: [] }),
    read("mapping-configs", { bank_id: state.bank, configs: [] }),
    read("ingestion-summary", {
      bank_id: state.bank,
      sources: [],
      activations_count: 0,
      last_activation_at: null,
      canonical_counts: {
        positions: 0,
        counterparties: 0,
        gl_accounts: 0,
        products: 0,
        reference_rows: 0,
      },
    }),
    read("live-summary", { bank_id: state.bank, modules: [], is_stale: false }),
    read("alerts", {
      bank_id: state.bank,
      items: [],
      total: 0,
      by_severity: {},
      by_module: {},
    }),
    read("reconciliation/exceptions", {
      bank_id: state.bank,
      control: "balance_sheet_identity",
      exceptions: [],
    }),
    read("canonical-positions", {
      bank_id: state.bank,
      positions: [],
      total: 0,
      limit: 100,
      offset: 0,
    }),
    read("canonical-positions/facets", {
      bank_id: state.bank,
      total: 0,
      position_types: [],
      currencies: [],
      as_of_dates: [],
    }),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
});
afterAll(() => {
  server.close();
  setAccessToken(null);
});
function mount(Surface: ComponentType) {
  return render(
    <QueryClientProvider client={client}>
      <Surface />
    </QueryClientProvider>,
  );
}

it.each([
  { name: "database", Surface: DatabasePage },
  { name: "T24", Surface: T24Page },
  { name: "positions", Surface: PositionsPage },
])("withholds $name details from confidential readers", async ({ Surface }) => {
  state.capabilities = [capability("view", "confidential")];
  mount(Surface);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "DATA · Restricted · View",
  );
  expect(requests).toEqual([]);
});

it("withholds confidential history and mapping metadata from restricted readers", async () => {
  state.capabilities = [capability("view")];
  mount(() => (
    <>
      <BatchesTable />
      <MappingPanel />
    </>
  ));
  expect(screen.getAllByRole("status")).toHaveLength(2);
  expect(
    screen.getByText(/Confidential · View for ingestion history/),
  ).toBeVisible();
  expect(
    screen.getByText(/Confidential · View for mapping metadata/),
  ).toBeVisible();
  expect(screen.queryByText("Connected")).not.toBeInTheDocument();
  expect(screen.queryByText("None")).not.toBeInTheDocument();
  for (const button of screen.getAllByRole("button", {
    name: "Activate mapping",
  })) {
    expect(button).toBeDisabled();
    await userEvent.setup().click(button);
  }
  expect(requests).toEqual([]);
});

it.each(["authority", "request"] as const)(
  "keeps starter actions disabled during %s loading",
  async (loading) => {
    // Starter activation is a DATA configure duty (#184) plus the
    // confidential read for mapping status.
    state.capabilities = [
      capability("view", "confidential"),
      capability("configure"),
    ];
    state.loading = loading === "authority";
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${base}/mapping-configs`, async () => {
        await pending;
        return HttpResponse.json({ configs: [] });
      }),
    );
    mount(MappingPanel);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading mapping status",
    );
    expect(screen.queryByText("None")).not.toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    for (const button of screen.getAllByRole("button", {
      name: "Activate mapping",
    }))
      expect(button).toBeDisabled();
    release();
    if (loading === "request") {
      await screen.findByText("None");
      for (const button of screen.getAllByRole("button", {
        name: "Activate mapping",
      }))
        expect(button).toBeEnabled();
    }
  },
);

it("keeps starter activation disabled after a failed read and supports retry", async () => {
  state.capabilities = [
    capability("view", "confidential"),
    capability("configure"),
  ];
  server.use(
    http.get(`${base}/mapping-configs`, () =>
      HttpResponse.json({}, { status: 503 }),
    ),
  );
  mount(MappingPanel);
  await screen.findByText("Could not load mapping configurations");
  expect(screen.queryByText("None")).not.toBeInTheDocument();
  for (const button of screen.getAllByRole("button", {
    name: "Activate mapping",
  }))
    expect(button).toBeDisabled();
  server.use(read("mapping-configs", { configs: [] }));
  await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("None");
  for (const button of screen.getAllByRole("button", {
    name: "Activate mapping",
  }))
    expect(button).toBeEnabled();
});

it.each([
  { name: "mapping panel", Surface: MappingPanel, action: "Activate mapping" },
  { name: "templates", Surface: TemplatesPanel, action: "Activate it" },
])(
  "preserves starter creation and loaded active status in $name",
  async ({ Surface, action }) => {
    // Starter creation is a DATA configure duty (#184).
    state.capabilities = [
      capability("view", "confidential"),
      capability("configure"),
    ];
    let active: object | undefined;
    let submitted:
      { name: string; activate: boolean; source_system: string } | undefined;
    server.use(
      http.get(`${base}/mapping-configs`, () =>
        HttpResponse.json({ configs: active ? [active] : [] }),
      ),
      http.post(`${base}/mapping-configs`, async ({ request }) => {
        submitted = (await request.json()) as typeof submitted;
        active = {
          ...submitted,
          id: "mapping",
          bank_id: state.bank,
          status: "active",
          version: 1,
          config: {},
          source_ref: "",
          created_at: "2026-01-01T00:00:00Z",
        };
        return HttpResponse.json(active);
      }),
    );
    mount(Surface);
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: action })[0]).toBeEnabled(),
    );
    await userEvent
      .setup()
      .click(screen.getAllByRole("button", { name: action })[0]);
    await waitFor(() =>
      expect(submitted).toMatchObject({
        activate: true,
        source_system: "EXCEL_CSV",
      }),
    );
    if (Surface === MappingPanel) {
      expect(
        await screen.findByRole("button", { name: "Currently active" }),
      ).toBeDisabled();
      expect(screen.getByText("Connected")).toBeVisible();
      expect(screen.getAllByText(submitted!.name).length).toBeGreaterThan(0);
    } else
      expect(await screen.findByText(/these files ingest as-is/)).toBeVisible();
  },
);

it.each([
  { name: "mapping panel", Surface: MappingPanel },
  { name: "templates", Surface: TemplatesPanel },
])(
  "withholds cached active status after a failed refresh in $name",
  async ({ Surface }) => {
    state.capabilities = [capability("view", "confidential")];
    server.use(
      read("mapping-configs", {
        configs: [
          {
            id: "mapping",
            name: "Canonical passthrough (API field names)",
            status: "active",
            source_system: "EXCEL_CSV",
            version: 1,
            config: {},
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
      }),
    );
    mount(Surface);
    if (Surface === MappingPanel) await screen.findByText("Connected");
    else await screen.findByText(/these files ingest as-is/);
    server.use(
      http.get(`${base}/mapping-configs`, () =>
        HttpResponse.json({}, { status: 503 }),
      ),
    );
    await client.invalidateQueries();
    await screen.findByText(
      Surface === MappingPanel
        ? "Could not load mapping configurations"
        : "Could not load data",
    );
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/these files ingest as-is/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Currently active")).not.toBeInTheDocument();
    for (const button of screen.queryAllByRole("button", { name: /Activate/ }))
      expect(button).toBeDisabled();
  },
);

it.each(["aggregated", "confidential", "restricted", "run"] as const)(
  "renders independently authorized overview panels for %s access",
  async (access) => {
    state.capabilities = [
      access === "run" ? capability("run") : capability("view", access),
    ];
    mount(Overview);
    expect(
      screen.queryByRole("heading", { name: "Integrations" }) !== null,
    ).toBe(access === "aggregated");
    expect(
      screen.queryByRole("heading", { name: "Reconciliation exceptions" }) !==
        null,
    ).toBe(access === "confidential");
    expect(
      screen.queryByRole("heading", { name: "Connection health" }) !== null,
    ).toBe(access === "restricted");
    expect(screen.queryByRole("heading", { name: "Data flow" }) !== null).toBe(
      access === "run",
    );
    if (access === "aggregated")
      await screen.findByText("Positions (current gen)");
    if (access === "confidential")
      await screen.findByText("No ingestion batches yet");
    if (access === "restricted") await screen.findByText("Reporting replica");
    expect(requests.includes("ingestion-summary")).toBe(
      access === "aggregated",
    );
    expect(requests.includes("ingestion-batches")).toBe(
      access === "confidential",
    );
    expect(requests.includes("database-direct/connections")).toBe(
      access === "restricted",
    );
  },
);

it.each(["run", "validate"] as const)(
  "keeps database %s actions separate",
  async (permission) => {
    state.capabilities = [capability("view"), capability(permission)];
    let probes = 0;
    let discoveries = 0;
    server.use(
      http.post(`${base}/database-direct/connections/connection/test`, () => {
        probes++;
        return HttpResponse.json({ reachable: true, latency_ms: 12 });
      }),
      http.get(`${base}/database-direct/connections/connection/schema`, () => {
        discoveries++;
        return HttpResponse.json({ tables: [] });
      }),
    );
    mount(DatabasePage);
    await screen.findByText("Reporting replica");
    const user = userEvent.setup();
    const test = screen.getByRole("button", { name: "Test" });
    const discover = screen.getByRole("button", { name: "Discover schema" });
    const sync = screen.getByRole("button", { name: "Sync" });
    if (permission === "run") {
      expect(test).toBeDisabled();
      expect(discover).toBeDisabled();
      expect(test).toHaveAccessibleDescription(
        /Requires DATA · Restricted · Validate/,
      );
      expect(discover).toHaveAccessibleDescription(
        /Requires DATA · Restricted · Validate/,
      );
      expect(
        screen.getByText(
          /Requires DATA · Restricted · Validate to test this connection or discover its schema/,
        ),
      ).toBeVisible();
      expect(sync).toBeEnabled();
      await user.click(test);
      await user.click(discover);
      await user.click(sync);
      expect(screen.getByRole("button", { name: "Sync today" })).toBeEnabled();
      expect(probes).toBe(0);
      expect(discoveries).toBe(0);
    } else {
      expect(test).toBeEnabled();
      expect(discover).toBeEnabled();
      expect(sync).toBeDisabled();
      await user.click(test);
      await screen.findByText(/12 ms/);
      await user.click(discover);
      await screen.findByText(/introspection returned no tables/);
      expect(probes).toBe(1);
      expect(discoveries).toBe(1);
    }
  },
);

it.each([false, true])(
  "gates T24 live tests with VALIDATE=%s and credential validation with CONFIGURE",
  async (allowed) => {
    // Credential validation is a DATA configure duty (#184); the live
    // availability check keeps its separate VALIDATE gate.
    state.capabilities = [
      capability("view"),
      capability("configure"),
      ...(allowed ? [capability("validate")] : []),
    ];
    let tests = 0;
    let validations = 0;
    server.use(
      http.post(`${base}/temenos/connections/connection/test`, () => {
        tests++;
        return HttpResponse.json({
          success: true,
          sample_values: {},
          error: null,
        });
      }),
      http.post(`${base}/temenos/connections/connection/validate`, () => {
        validations++;
        return HttpResponse.json(temenos);
      }),
    );
    mount(T24Page);
    await screen.findByText("Core banking");
    const user = userEvent.setup();
    const test = screen.getByRole("button", { name: "Check availability" });
    if (allowed) expect(test).toBeEnabled();
    else {
      expect(test).toBeDisabled();
      expect(
        screen.getByText(
          /Requires DATA · Restricted · Validate to check availability/,
        ),
      ).toBeVisible();
      expect(test).toHaveAccessibleDescription(
        /Requires DATA · Restricted · Validate/,
      );
    }
    await user.click(test);
    if (allowed) await screen.findByText("Configuration check complete");
    await user.click(
      screen.getByRole("button", { name: "Validate stored credentials" }),
    );
    await screen.findByText("Credential health check completed.");
    expect(tests).toBe(Number(allowed));
    expect(validations).toBe(1);
  },
);

it.each([false, true])(
  "gates both health-panel probes with VALIDATE=%s",
  async (allowed) => {
    state.capabilities = [
      capability("view"),
      ...(allowed ? [capability("validate")] : []),
    ];
    let probes = 0;
    server.use(
      http.post(`${base}/database-direct/connections/connection/test`, () => {
        probes++;
        return HttpResponse.json({ reachable: true, latency_ms: 12 });
      }),
      http.post(`${base}/temenos/connections/connection/test`, () => {
        probes++;
        return HttpResponse.json({
          success: true,
          sample_values: {},
          error: null,
        });
      }),
    );
    mount(ConnectionHealthPanel);
    await screen.findByText("Reporting replica");
    const user = userEvent.setup();
    for (const test of screen.getAllByRole("button", { name: "Test" })) {
      if (allowed) expect(test).toBeEnabled();
      else expect(test).toBeDisabled();
      await user.click(test);
    }
    if (!allowed)
      expect(
        screen.getAllByText(/Requires DATA · Restricted · Validate/).length,
      ).toBeGreaterThan(0);
    else
      expect(
        screen.queryByText(/Requires DATA · Restricted · Validate/),
      ).not.toBeInTheDocument();
    if (allowed) {
      await screen.findByText("Reachable · 12 ms");
      await screen.findByText("Test pull succeeded.");
    }
    expect(probes).toBe(allowed ? 2 : 0);
  },
);

it("records a separate checker's rejection and reloads the immutable register", async () => {
  state.capabilities = [
    capability("view", "confidential"),
    capability("approve", "confidential"),
  ];
  const proposal = {
    id: "proposal",
    bank_id: state.bank,
    status: "pending",
    control: "balance_sheet_identity",
    requested_by: "maker",
    requested_at: "2026-01-01T00:00:00Z",
    reason: "Known feed gap",
    max_gap_fraction: "0.05",
    effective_from: "2026-01-01",
    effective_to: "2026-12-31",
    approved_by: null,
    approved_by_user_id: null,
    approval_timestamp: null,
    revoked_at: null as string | null,
    revoked_by: null as string | null,
  };
  let body: unknown;
  server.use(
    http.get(`${base}/reconciliation/exceptions`, () =>
      HttpResponse.json({ exceptions: [proposal] }),
    ),
    http.post(
      `${base}/reconciliation/exceptions/proposal/revoke`,
      async ({ request }) => {
        body = await request.json();
        proposal.revoked_at = "2026-10-01T00:00:00Z";
        proposal.revoked_by = "CRO";
        return HttpResponse.json(proposal);
      },
    ),
  );
  mount(ReconciliationPanel);
  await screen.findByText("Known feed gap");
  const user = userEvent.setup();
  const revoke = screen.getByRole("button", { name: "Reject" });
  expect(revoke).toBeDisabled();
  await user.type(
    screen.getByLabelText("Your approval or rejection label"),
    "CRO",
  );
  expect(revoke).toBeDisabled();
  await user.type(
    screen.getByLabelText("Reason for rejection"),
    "Feed repaired",
  );
  await user.click(revoke);
  const register = screen.getByRole("region", {
    name: "Reconciliation exceptions",
  });
  expect(await within(register).findByText("Revoked")).toBeVisible();
  expect(body).toEqual({ revoked_by: "CRO", reason: "Feed repaired" });
  expect(
    within(register).queryByRole("button", { name: "Reject" }),
  ).not.toBeInTheDocument();
});

it("retries an unavailable register and submits both dates of a bounded proposal", async () => {
  state.capabilities = [
    capability("view", "confidential"),
    capability("create", "confidential"),
  ];
  let unavailable = true;
  let body: unknown;
  server.use(
    http.get(`${base}/reconciliation/exceptions`, () =>
      unavailable
        ? HttpResponse.json(
            { error: { message: "Register unavailable" } },
            { status: 503 },
          )
        : HttpResponse.json({ exceptions: [] }),
    ),
    http.post(`${base}/reconciliation/exceptions`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ id: "proposal", status: "pending" });
    }),
  );
  mount(ReconciliationPanel);
  const user = userEvent.setup();
  await screen.findByText("Could not load data");
  unavailable = false;
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.queryByText("Could not load data")).not.toBeInTheDocument(),
  );
  await user.type(screen.getByLabelText("Reason"), "Temporary feed gap");
  await user.type(screen.getByLabelText("Maximum gap fraction (0–1)"), "0.05");
  await user.type(screen.getByLabelText("Effective from"), "2026-10-01");
  await user.type(
    screen.getByLabelText("Effective to (optional)"),
    "2026-10-31",
  );
  await user.click(screen.getByRole("button", { name: "Record proposal" }));
  expect(
    await screen.findByText(
      "Proposal recorded. A different officer must approve it.",
    ),
  ).toBeVisible();
  expect(body).toEqual({
    reason: "Temporary feed gap",
    max_gap_fraction: "0.05",
    effective_from: "2026-10-01",
    effective_to: "2026-10-31",
  });
});

it("keeps template downloads while confidential mapping status is unavailable", async () => {
  state.capabilities = [capability("view"), capability("run")];
  mount(TemplatesPanel);
  expect(screen.getByRole("status")).toHaveTextContent(
    "DATA · Confidential · View",
  );
  expect(
    screen.queryByRole("button", { name: "Activate it" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getAllByRole("button", { name: "Download CSV" }).length,
  ).toBeGreaterThan(0);
  expect(requests).not.toContain("mapping-configs");
});

it.each(["loading", "failed", "empty", "active"] as const)(
  "distinguishes %s mapping metadata before offering replacement",
  async (status) => {
    // The replacement offer activates a starter mapping: DATA configure (#184).
    state.capabilities = [
      capability("view", "confidential"),
      capability("configure"),
    ];
    state.loading = status === "loading";
    server.use(
      http.get(`${base}/mapping-configs`, () => {
        requests.push("mapping-configs");
        return status === "failed"
          ? HttpResponse.json({}, { status: 503 })
          : HttpResponse.json({
              configs:
                status === "active"
                  ? [
                      {
                        id: "mapping",
                        name: "Canonical passthrough (API field names)",
                        status: "active",
                        source_system: "EXCEL_CSV",
                        version: 1,
                        config: {},
                        created_at: "2026-01-01T00:00:00Z",
                      },
                    ]
                  : [],
            });
      }),
    );
    mount(TemplatesPanel);
    if (status === "loading") {
      expect(screen.getByRole("status")).toHaveTextContent(
        "Loading mapping status",
      );
      expect(requests).not.toContain("mapping-configs");
    } else if (status === "failed")
      await screen.findByText("Could not load data");
    else if (status === "empty")
      expect(
        await screen.findByRole("button", { name: "Activate it" }),
      ).toBeEnabled();
    else await screen.findByText(/these files ingest as-is/);
    if (status !== "empty")
      expect(
        screen.queryByRole("button", { name: "Activate it" }),
      ).not.toBeInTheDocument();
  },
);

it("requires an explicit date when upload metadata is denied without blocking manual recompute", async () => {
  state.capabilities = [capability("view"), capability("run", "confidential")];
  let body: unknown;
  server.use(
    http.post(`${base}/refresh`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ job_id: "job" });
    }),
    http.get(`${apiBaseUrl}/jobs/job`, () =>
      HttpResponse.json({ id: "job", status: "succeeded", progress: {} }),
    ),
  );
  mount(ActivatePanel);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Advanced actions" }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "DATA · Confidential · View",
  );
  const date = screen.getByLabelText("As-of date");
  expect(date).toHaveValue("");
  expect(
    screen.queryByText(/Defaults to the latest accepted upload/),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Recompute now" })).toBeDisabled();
  await user.type(date, "2026-10-01");
  await user.click(screen.getByRole("button", { name: "Recompute now" }));
  await waitFor(() =>
    expect(body).toEqual({
      as_of_date: "2026-10-01",
      reason: "Recompute now from the Data Engine console.",
    }),
  );
  expect(requests).not.toContain("ingestion-batches");
});

it.each(["loading", "failed", "empty", "accepted"] as const)(
  "distinguishes %s upload dates in activation defaults",
  async (status) => {
    state.capabilities = [
      capability("view", "confidential"),
      capability("run"),
    ];
    state.loading = status === "loading";
    server.use(
      http.get(`${base}/ingestion-batches`, () => {
        requests.push("ingestion-batches");
        return status === "failed"
          ? HttpResponse.json({}, { status: 503 })
          : HttpResponse.json({
              batches:
                status === "accepted"
                  ? [
                      {
                        id: "batch",
                        status: "accepted",
                        as_of_date: "2026-10-01",
                        created_at: "2026-10-01T12:00:00Z",
                        source_system: "DB_DIRECT",
                      },
                    ]
                  : [],
            });
      }),
    );
    mount(ActivatePanel);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Advanced actions" }));
    if (status === "loading")
      expect(screen.getByRole("status")).toHaveTextContent(
        "Loading upload dates",
      );
    else if (status === "failed")
      await screen.findByText("Could not load data");
    else if (status === "empty")
      await screen.findByText(
        /No accepted uploads. Defaults to the selected reporting period/,
      );
    else await screen.findByText(/Defaults to the latest accepted upload/);
    expect(screen.getByLabelText("As-of date")).toHaveValue(
      status === "accepted"
        ? "2026-10-01"
        : status === "empty"
          ? "2026-09-30"
          : "",
    );
    if (status === "loading")
      expect(requests).not.toContain("ingestion-batches");
  },
);

it.each([false, true])(
  "saves database credentials independently of VALIDATE=%s",
  async (allowed) => {
    // Connection creation is a DATA configure duty (#184); the live test
    // keeps its separate VALIDATE gate.
    state.capabilities = [
      capability("view"),
      capability("configure"),
      ...(allowed ? [capability("validate")] : []),
    ];
    let saves = 0;
    let probes = 0;
    server.use(
      http.post(`${base}/database-direct/connections`, () => {
        saves++;
        return HttpResponse.json(database);
      }),
      http.post(`${base}/database-direct/connections/connection/test`, () => {
        probes++;
        return HttpResponse.json({ reachable: true, latency_ms: 12 });
      }),
    );
    mount(() => (
      <AddDatabaseConnection
        bankId={state.bank}
        existingNames={[]}
        onDone={() => {}}
      />
    ));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Display name"), "Reporting replica");
    await user.type(screen.getByLabelText("Service user"), "reader");
    await user.type(screen.getByLabelText("Password"), "fixture-secret");
    await user.click(
      screen.getByRole("button", {
        name: allowed
          ? "Create, validate & test connection"
          : "Create connection",
      }),
    );
    await screen.findByText("Reporting replica created.");
    if (allowed) await screen.findByText("Reachable");
    else
      expect(screen.getByRole("status")).toHaveTextContent(
        "Live test omitted: requires DATA · Restricted · Validate",
      );
    expect(saves).toBe(1);
    expect(probes).toBe(Number(allowed));
  },
);

it.each([false, true])(
  "saves Temenos credentials independently of VALIDATE=%s",
  async (allowed) => {
    // Core connection creation is a DATA configure duty (#184); the live
    // test keeps its separate VALIDATE gate.
    state.capabilities = [
      capability("view"),
      capability("configure"),
      ...(allowed ? [capability("validate")] : []),
    ];
    let saves = 0;
    let probes = 0;
    server.use(
      read("temenos/domains", { domains: [] }),
      http.post(`${base}/temenos/connections`, () => {
        saves++;
        return HttpResponse.json({ ...temenos, connection_mode: "OFS" });
      }),
      http.post(`${base}/temenos/connections/connection/test`, () => {
        probes++;
        return HttpResponse.json({ success: true, sample_values: {} });
      }),
    );
    mount(() => (
      <AddTemenosConnection
        bankId={state.bank}
        existingNames={[]}
        onDone={() => {}}
      />
    ));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /^OFS / }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(screen.getByLabelText("Service user"), "reader");
    await user.type(
      screen.getByLabelText("Service user password"),
      "fixture-secret",
    );
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(
      screen.getByRole("button", { name: "Save configuration" }),
    );
    await screen.findByText(
      /configuration saved. Live transport remains unavailable/,
    );
    if (allowed) await screen.findByText("Configuration check complete.");
    else
      expect(screen.getByRole("status")).toHaveTextContent(
        "Live test omitted: requires DATA · Restricted · Validate",
      );
    expect(saves).toBe(1);
    expect(probes).toBe(Number(allowed));
  },
);

it.each(["pending", "approved", "revoked"] as const)(
  "shows the reader's %s exception state and provenance without actions",
  async (status) => {
    state.capabilities = [
      capability("view", "confidential"),
      ...(status === "pending"
        ? [capability("validate")]
        : [capability("approve", "confidential")]),
    ];
    server.use(
      read("reconciliation/exceptions", {
        exceptions: [
          {
            id: "proposal",
            bank_id: state.bank,
            status: status === "pending" ? "pending" : "approved",
            control: "balance_sheet_identity",
            requested_by: "maker",
            requested_at: "2026-01-01T00:00:00Z",
            reason: "Known feed gap",
            max_gap_fraction: "0.05",
            effective_from: "2026-01-01",
            effective_to: "2026-12-31",
            approved_by: status === "pending" ? null : "CRO",
            approved_by_user_id: status === "pending" ? null : "checker",
            approval_timestamp:
              status === "pending" ? null : "2026-01-02T00:00:00Z",
            revoked_at: status === "revoked" ? "2026-10-01T00:00:00Z" : null,
            revoked_by: status === "revoked" ? "Risk officer" : null,
          },
        ],
      }),
    );
    mount(ReconciliationPanel);
    await screen.findByText("Known feed gap");
    expect(
      screen.getByText(
        status === "pending"
          ? "Pending"
          : status === "approved"
            ? "Approved"
            : "Revoked",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(/Ceiling 0.05 · 2026-01-01 to 2026-12-31/),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Approve exception" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reject" }),
    ).not.toBeInTheDocument();
    if (status === "pending") {
      expect(
        screen.getByText(
          "Awaiting approval by a different officer with Confidential · Approve.",
        ),
      ).toBeVisible();
      expect(
        screen.getByRole("link", { name: "Request access" }),
      ).toHaveAttribute("href", "/access");
    } else {
      expect(screen.getByText("Approved by CRO")).toBeVisible();
      expect(
        screen.queryByRole("link", { name: "Request access" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/Awaiting approval by/),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Your approval or rejection label"),
      ).not.toBeInTheDocument();
      if (status === "revoked")
        expect(
          screen.getByText(/Revoked by Risk officer · 2026-10-01/),
        ).toBeVisible();
    }
  },
);

it.each([
  ["database", DatabasePage],
  ["Temenos", T24Page],
  ["positions", PositionsPage],
  ["canonical positions", CanonicalPositionsPage],
  ["overview", Overview],
  ["batch history", BatchesTable],
  ["upload", UploadPanel],
  ["mapping", MappingPanel],
] as const)(
  "holds %s while profile or bank authority is unresolved",
  async (_name, Component) => {
    state.capabilities = [
      capability("view", "aggregated"),
      capability("view", "confidential"),
      capability("view"),
      capability("create"),
      capability("run"),
    ];
    state.loading = true;
    const view = render(
      <QueryClientProvider client={client}>
        <Component />
      </QueryClientProvider>,
    );
    expect(
      screen.queryByText(/Ask your organization owner/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryAllByText(/Loading/).length +
        screen.queryAllByLabelText("Loading").length,
    ).toBeGreaterThan(0);
    expect(requests).toEqual([]);
    state.loading = false;
    state.bankPending = true;
    view.rerender(
      <QueryClientProvider client={client}>
        <Component />
      </QueryClientProvider>,
    );
    expect(
      screen.queryByText(/Ask your organization owner/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryAllByText(/Loading/).length +
        screen.queryAllByLabelText("Loading").length,
    ).toBeGreaterThan(0);
    expect(requests).toEqual([]);
  },
);
