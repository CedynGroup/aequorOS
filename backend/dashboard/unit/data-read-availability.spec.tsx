// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { BankReportingPeriodRead } from "@aequoros/risk-service-api";
import ProfileProvider from "../components/profile/ProfileProvider";
import BankProvider from "../components/shell/BankContext";
import CommandCenterPage from "../features/home/routes/command-center";
import OperationalFeed from "../features/home/components/OperationalFeed";
import BalanceSheetStrip from "../features/home/components/BalanceSheetStrip";
import EnterpriseStressWorkbench from "../components/stress/EnterpriseStressWorkbench";
import StressBoardPack from "../app/(app)/reports/stress-board-pack/page";
import SavedAnalysesIndex from "../components/reports/SavedAnalysesIndex";
import BalanceSheetForecastPage from "../app/(app)/forecasting/page";
import ComparePage from "../app/(app)/submissions/compare/page";
import RunModuleScenarios from "../features/workbench/components/RunModuleScenarios";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../lib/format";

const bankId = "BK-SAMP0001";
const base = `${apiBaseUrl}/banks/${bankId}`;
const server = setupServer();
let client: QueryClient;
const period: BankReportingPeriodRead = {
  id: "september",
  bankId,
  label: "September 2026",
  periodStart: new Date("2026-09-01"),
  periodEnd: new Date("2026-09-30"),
  status: "open",
};
const periodWire = {
  id: period.id,
  bank_id: bankId,
  label: period.label,
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  status: "open",
};
function facts(populated = true) {
  return {
    period: periodWire,
    balance_sheet: populated
      ? [
          {
            id: "asset",
            amount: "500",
            category: "loans_gross",
            attributes: { side: "asset" },
          },
        ]
      : [],
    capital_components: [],
    cash_flows: [],
    deposit_behavior: [],
    lcr_inflows: [],
    loan_exposures: [],
    market_risk: [],
    off_balance: [],
    operational_income: [],
    securities: [],
  };
}
function authority(sensitivities: string[]) {
  return {
    user_id: "reader",
    effective_authority: {
      authv: 1,
      organization_capabilities: [],
      institution_capabilities: [
        {
          institution_id: bankId,
          capabilities: [
            {
              module: "risk",
              permission: "view",
              sensitivity: "confidential",
              data_scope: { kind: "all" },
            },
            ...sensitivities.map((sensitivity) => ({
              module: "data",
              permission: "view",
              sensitivity,
              data_scope: { kind: "all" },
            })),
          ],
        },
      ],
    },
  };
}
function fixture(sensitivities: string[]) {
  const reads = { periods: 0, facts: 0, batches: 0, activations: 0 };
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json(authority(sensitivities)),
    ),
    http.get(`${apiBaseUrl}/banks`, () =>
      HttpResponse.json({
        banks: [
          {
            id: bankId,
            name: "Fixture Bank",
            currency: "EUR",
            jurisdiction_code: "EU",
            license_type: "universal",
            institution_type: "universal_bank",
          },
        ],
      }),
    ),
    http.get(`${apiBaseUrl}/feature-flags`, () =>
      HttpResponse.json({ bi_enabled: false, bi_nlq_enabled: false }),
    ),
    http.get(`${base}/reporting-periods`, () => {
      reads.periods++;
      return HttpResponse.json({ periods: [periodWire] });
    }),
    http.get(`${base}/reporting-periods/:periodId/facts`, () => {
      reads.facts++;
      return HttpResponse.json(facts());
    }),
    http.get(`${base}/ingestion-batches`, () => {
      reads.batches++;
      return HttpResponse.json({
        bank_id: bankId,
        batches: [
          {
            id: "batch-fixture",
            bank_id: bankId,
            status: "accepted",
            source_system: "DB_DIRECT",
            created_at: "2026-10-01T12:00:00Z",
            records_accepted: 2,
            records_warning: 0,
            records_error: 0,
          },
        ],
      });
    }),
    http.get(`${base}/data-activations`, () => {
      reads.activations++;
      return HttpResponse.json({
        bank_id: bankId,
        activations: [
          {
            activated_at: "2026-10-01T12:00:00Z",
            period_label: period.label,
            facts_created: 2,
          },
        ],
      });
    }),
    http.get(`${base}/live-summary`, () =>
      HttpResponse.json({ bank_id: bankId, modules: [], is_stale: false }),
    ),
    http.get(`${base}/alerts`, () =>
      HttpResponse.json({
        bank_id: bankId,
        items: [],
        by_severity: {},
        by_module: {},
        total: 0,
      }),
    ),
    http.get(`${base}/regulatory-runs`, () =>
      HttpResponse.json({
        runs: [
          {
            id: "run",
            reporting_period_id: period.id,
            period_label: period.label,
            module: "liquidity",
            scenario_code: "baseline",
            status: "succeeded",
            created_at: "2026-10-01",
            metrics: {},
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ),
  );
  return reads;
}
function view(children: ReactNode) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  setAccessToken("fixture");
  return render(
    <SessionProvider
      session={{
        expires: "2099-01-01T00:00:00Z",
        accessToken: "fixture",
        user: {
          email: "reader@example.test",
          organizationId: "org",
          authorizationVersion: 1,
        },
      }}
      refetchOnWindowFocus={false}
    >
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("org", "reader@example.test", 1)}
        >
          <PathnameContext.Provider value="/">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>{children}</BankProvider>
              </ProfileProvider>
            </SearchParamsContext.Provider>
          </PathnameContext.Provider>
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    </SessionProvider>,
  );
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
  vi.unstubAllGlobals();
});

it("renders the Command Center for confidential DATA and Risk readers without fetching restricted facts", async () => {
  const reads = fixture(["confidential"]);
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  view(<CommandCenterPage />);
  expect(
    await screen.findByText(/Restricted · View for balance-sheet facts/),
  ).toHaveAttribute("role", "status");
  expect(await screen.findByText(/Db Direct ingestion/)).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Window analysis" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("No computed data yet")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Loading")).not.toBeInTheDocument();
  expect(reads.facts).toBe(0);
  expect(reads.periods).toBe(1);
  expect(reads.activations).toBe(0);
});

it("keeps independently authorized activations visible when period lists and batches are denied", async () => {
  const reads = fixture(["aggregated"]);
  view(<CommandCenterPage />);
  expect(
    await screen.findByText(/Confidential · View for reporting periods/),
  ).toHaveAttribute("role", "status");
  expect(await screen.findByText("Data activation")).toBeInTheDocument();
  expect(
    screen.getByText(/Confidential · View for ingestion batches/),
  ).toHaveAttribute("role", "status");
  expect(screen.queryByText("No computed data yet")).not.toBeInTheDocument();
  expect(screen.queryByText(/0 batches/)).not.toBeInTheDocument();
  expect(reads).toEqual({ periods: 0, facts: 0, batches: 0, activations: 1 });
});

it("distinguishes pending authority from a denied read without issuing either feed request", async () => {
  const reads = fixture([]);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, async () => {
      await pending;
      return HttpResponse.json(authority([]));
    }),
  );
  view(<OperationalFeed bankId={bankId} />);
  expect(await screen.findByText("Loading ingestion batches…")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.queryByText(/requires DATA/)).not.toBeInTheDocument();
  expect(reads.batches + reads.activations).toBe(0);
  await act(async () => release());
  expect(
    await screen.findByText(/Confidential · View for ingestion batches/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Aggregated · View for data activations/),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(
      /No ingestion|No data activations|0 batches|0 activations/,
    ),
  ).not.toBeInTheDocument();
  expect(reads.batches + reads.activations).toBe(0);
});

it("keeps activation results when batch history fails", async () => {
  fixture(["confidential", "aggregated"]);
  server.use(
    http.get(`${base}/ingestion-batches`, () =>
      HttpResponse.json({ detail: "Read failed" }, { status: 500 }),
    ),
  );
  view(<OperationalFeed bankId={bankId} />);
  expect(
    await screen.findByText("Ingestion batches unavailable"),
  ).toBeInTheDocument();
  expect(await screen.findByText("Data activation")).toBeInTheDocument();
  expect(
    screen.queryByText(/0 batches|No ingestion batches/),
  ).not.toBeInTheDocument();
});

it("reports successful empty feed reads explicitly", async () => {
  fixture(["confidential", "aggregated"]);
  server.use(
    http.get(`${base}/ingestion-batches`, () =>
      HttpResponse.json({ bank_id: bankId, batches: [] }),
    ),
    http.get(`${base}/data-activations`, () =>
      HttpResponse.json({ bank_id: bankId, activations: [] }),
    ),
  );
  view(<OperationalFeed bankId={bankId} />);
  expect(
    await screen.findByText("No ingestion batches on record."),
  ).toBeInTheDocument();
  expect(
    await screen.findByText("No data activations on record."),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/0 batches on record.*0 activations/),
  ).toBeInTheDocument();
});

it.each(["failed", "empty", "populated"] as const)(
  "distinguishes %s balance-sheet reads from loading",
  async (outcome) => {
    fixture(["restricted"]);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${base}/reporting-periods/:periodId/facts`, async () => {
        await pending;
        return outcome === "failed"
          ? HttpResponse.json({ detail: "Read failed" }, { status: 500 })
          : HttpResponse.json(facts(outcome === "populated"));
      }),
    );
    view(<BalanceSheetStrip bankId={bankId} period={period} />);
    expect(
      await screen.findByLabelText("Loading balance-sheet facts"),
    ).toBeInTheDocument();
    await act(async () => release());
    if (outcome === "failed")
      expect(
        await screen.findByText("Balance-sheet facts unavailable"),
      ).toBeInTheDocument();
    else if (outcome === "empty")
      expect(
        await screen.findByText("No balance-sheet facts for this period"),
      ).toBeInTheDocument();
    else expect(await screen.findByText("Total assets")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Loading balance-sheet facts"),
    ).not.toBeInTheDocument();
    if (outcome !== "populated")
      expect(screen.queryByText("Total assets")).not.toBeInTheDocument();
  },
);

it("shows comparison period access instead of falsely claiming authorized runs do not exist", async () => {
  const reads = fixture([]);
  view(<ComparePage />);
  expect(
    await screen.findByText(
      /Confidential · View for comparison reporting periods/,
    ),
  ).toHaveAttribute("role", "status");
  expect(screen.queryByText("No runs to compare")).not.toBeInTheDocument();
  expect(reads.periods).toBe(0);
});

it("loads comparison selectors when confidential period access is granted", async () => {
  fixture(["confidential"]);
  view(<ComparePage />);
  expect(
    await screen.findByText("Only one version for this period"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("combobox", { name: "Reporting period" }),
  ).toHaveValue(period.id);
});

it.each(["denied", "failed", "empty"] as const)(
  "preserves the %s period-list state in scenario controls",
  async (outcome) => {
    const reads = fixture(outcome === "denied" ? [] : ["confidential"]);
    if (outcome !== "denied")
      server.use(
        http.get(`${base}/reporting-periods`, () =>
          outcome === "failed"
            ? HttpResponse.json({ detail: "Periods failed" }, { status: 500 })
            : HttpResponse.json({ periods: [] }),
        ),
      );
    view(<RunModuleScenarios module="liquidity" />);
    if (outcome === "denied") {
      expect(
        await screen.findByText(/Confidential · View for reporting periods/),
      ).toBeInTheDocument();
      expect(reads.periods).toBe(0);
    } else if (outcome === "failed")
      expect(
        await screen.findByText("Reporting periods unavailable", {
          selector: "p",
        }),
      ).toBeInTheDocument();
    else
      expect(
        await screen.findByRole("option", {
          name: "No reporting periods available",
        }),
      ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run all Liquidity scenarios" }),
    ).toBeDisabled();
    if (outcome !== "empty")
      expect(
        screen.queryByRole("option", {
          name: "No reporting periods available",
        }),
      ).not.toBeInTheDocument();
  },
);

it("keeps period-read failures separate from an empty Command Center", async () => {
  fixture(["confidential"]);
  server.use(
    http.get(`${base}/reporting-periods`, () =>
      HttpResponse.json({ detail: "Periods failed" }, { status: 500 }),
    ),
  );
  view(<CommandCenterPage />);
  expect(
    await screen.findByText("Could not resolve a computed reporting period"),
  ).toBeInTheDocument();
  expect(await screen.findByText(/Db Direct ingestion/)).toBeInTheDocument();
  expect(screen.queryByText("No computed data yet")).not.toBeInTheDocument();
});

it("shows the available feed while another authorized request is still loading", async () => {
  fixture(["confidential", "aggregated"]);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.get(`${base}/ingestion-batches`, async () => {
      await pending;
      return HttpResponse.json({ bank_id: bankId, batches: [] });
    }),
  );
  view(<OperationalFeed bankId={bankId} />);
  expect(await screen.findByText("Data activation")).toBeInTheDocument();
  expect(screen.getByText("Loading ingestion batches…")).toBeInTheDocument();
  expect(screen.queryByText(/0 batches/)).not.toBeInTheDocument();
  await act(async () => release());
  expect(
    await screen.findByText("No ingestion batches on record."),
  ).toBeInTheDocument();
});

it("keeps batch results when activation history fails", async () => {
  fixture(["confidential", "aggregated"]);
  server.use(
    http.get(`${base}/data-activations`, () =>
      HttpResponse.json({ detail: "Read failed" }, { status: 500 }),
    ),
  );
  view(<OperationalFeed bankId={bankId} />);
  expect(
    await screen.findByText("Data activations unavailable"),
  ).toBeInTheDocument();
  expect(await screen.findByText(/Db Direct ingestion/)).toBeInTheDocument();
  expect(
    screen.queryByText(/0 activations|No data activations/),
  ).not.toBeInTheDocument();
});

it.each(["fallback", "empty", "failed"] as const)(
  "resolves the %s facts path without confusing it with denied access",
  async (outcome) => {
    fixture(["confidential", "restricted"]);
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
    const older = {
      ...periodWire,
      id: "august",
      label: "August 2026",
      period_end: "2026-08-31",
    };
    const probed: string[] = [];
    server.use(
      http.get(`${base}/reporting-periods`, () =>
        HttpResponse.json({ periods: [periodWire, older] }),
      ),
      http.get(`${base}/reporting-periods/:periodId/facts`, ({ params }) => {
        probed.push(String(params.periodId));
        if (outcome === "failed")
          return HttpResponse.json({ detail: "Facts failed" }, { status: 500 });
        return HttpResponse.json({
          ...facts(outcome === "fallback" && params.periodId === "august"),
          period: params.periodId === "august" ? older : periodWire,
        });
      }),
    );
    view(<CommandCenterPage />);
    if (outcome === "fallback") {
      expect(
        await screen.findByText(/latest computed period/),
      ).toBeInTheDocument();
      expect(await screen.findByText("Total assets")).toBeInTheDocument();
      expect([...new Set(probed)]).toEqual(["september", "august"]);
    } else if (outcome === "empty") {
      expect(
        await screen.findByText("No computed data yet"),
      ).toBeInTheDocument();
      expect(probed).toEqual(["september", "august"]);
    } else {
      expect(
        await screen.findByText(
          "Could not resolve a computed reporting period",
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByText("No computed data yet"),
      ).not.toBeInTheDocument();
      expect(probed).toEqual(["september"]);
    }
  },
);

it.each([
  { name: "stress workbench", content: <EnterpriseStressWorkbench /> },
  { name: "stress board pack", content: <StressBoardPack /> },
  { name: "forecasting", content: <BalanceSheetForecastPage /> },
])("discloses denied period metadata in $name", async ({ content }) => {
  const reads = fixture([]);
  server.use(
    http.get(`${apiBaseUrl}/macro-scenarios`, () =>
      HttpResponse.json({ scenarios: [], total: 0 }),
    ),
    http.get(`${apiBaseUrl}/management-action-plans`, () =>
      HttpResponse.json({ plans: [], total: 0 }),
    ),
  );
  view(content);
  expect(
    await screen.findByText(/Confidential · View for reporting periods/),
  ).toHaveAttribute("role", "status");
  expect(
    screen.queryByText(
      /No derived reporting period|no derived reporting period|No stress run to compose/,
    ),
  ).not.toBeInTheDocument();
  expect(reads.periods).toBe(0);
});

it("keeps saved analyses visible while their period labels are withheld", async () => {
  const reads = fixture([]);
  server.use(
    http.get(`${base}/scenario-workbench/:module/analyses`, () =>
      HttpResponse.json({
        analyses: [
          {
            id: "analysis",
            module: "capital",
            name: "Saved capital analysis",
            reporting_period_id: period.id,
            created_at: "2026-10-01",
            created_by: "reader",
            engine_version: "fixture",
            scenario_count: 1,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ),
  );
  view(<SavedAnalysesIndex />);
  expect(await screen.findByText("Saved capital analysis")).toBeInTheDocument();
  expect(
    screen.getByText(/Confidential · View for reporting periods/),
  ).toHaveAttribute("role", "status");
  expect(reads.periods).toBe(0);
});
