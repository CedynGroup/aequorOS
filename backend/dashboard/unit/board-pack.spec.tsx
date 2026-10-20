// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
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
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { mkdirSync, writeFileSync } from "node:fs";
import BoardPackPage from "../app/(app)/reports/board-pack/page";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";

const selection = vi.hoisted(() => ({ period: "Period A" }));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: {
      id: "BK-SAMP0001",
      name: "Synthetic Board Pack Test Bank",
      currency: "EUR",
    },
    period: {
      id: selection.period,
      label: selection.period,
      periodEnd: new Date("2026-01-31"),
    },
  }),
  useModuleScope: () => ({
    modules: new Set(["liquidity", "capital", "credit", "irrbb", "fx", "ftp"]),
    entitledModules: new Set([
      "liquidity",
      "capital",
      "credit",
      "irrbb",
      "fx",
      "ftp",
    ]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    ftpAggregatedView: true,
    fxAggregatedView: true,
    irrbbAggregatedView: true,
    liquidityAggregatedView: true,
    capitalAggregatedView: true,
    institutionCapabilities: ["liq", "cap", "credit", "irrbb", "fx", "ftp"].map(
      (module) => ({
        module,
        sensitivity: "aggregated",
        permission: "view",
        requiresContextualAuthorization: false,
        dataScope: { kind: "all", branches: [], regions: [] },
      }),
    ),
  }),
}));
const server = setupServer();
let client: QueryClient;
let requests: string[];
function generation(date = "2026-02-28", nim = "4.75") {
  return {
    module: "ftp",
    status: "green",
    metrics: { portfolio_nim_pct: nim, total_balance_ghs: "123456" } as Record<
      string,
      unknown
    >,
    computed_at: `${date}T12:00:00Z`,
    computed_from_input_hash: (date === "2026-02-28" ? "b" : "a").repeat(64),
    source_as_of_date: date,
    source_fact_period_id: "11111111-1111-4111-8111-111111111111",
    engine_version: "ftp-test-v2",
    calculation_generation: date === "2026-02-28" ? 22 : 11,
    pipeline_state: "ready",
    pipeline_error: null as string | null,
  };
}
function summary(modules = [generation()]) {
  return {
    bank_id: "BK-SAMP0001",
    reporting_period_id: null,
    period_label: null,
    source_as_of_date: "2026-02-28",
    computed_at: "2026-02-28T12:00:00Z",
    is_stale: false,
    reconciliation: null,
    modules,
  };
}
function view() {
  return (
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("OR-SAMP0001", "fixture@example.test", 1)}
      >
        <BoardPackPage />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>
  );
}
function serve(payload = summary()) {
  server.use(
    http.get(`${apiBaseUrl}/banks/:bankId/live-summary`, () =>
      HttpResponse.json(payload),
    ),
  );
}
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  server.events.on("request:start", ({ request }) =>
    requests.push(new URL(request.url).pathname),
  );
});
afterAll(() => server.close());
beforeEach(() => {
  requests = [];
  selection.period = "Period A";
  setAccessToken("synthetic-test-token");
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  vi.restoreAllMocks();
});

it("prints current baseline figures with their producing generation despite historical selection and unrelated run history", async () => {
  // Period A and B intentionally have different figures and hashes. Registry
  // includes newer stress, a failed attempt after success, and >100 other runs.
  const history = [
    {
      module: "ftp",
      scenario: "stress",
      status: "succeeded",
      input_hash: "stress-hash",
    },
    {
      module: "ftp",
      scenario: "baseline",
      status: "failed",
      input_hash: "failed-hash",
    },
    ...Array.from({ length: 150 }, () => ({
      module: "fx",
      scenario: "baseline",
      status: "succeeded",
    })),
    {
      module: "ftp",
      scenario: "baseline",
      status: "succeeded",
      input_hash: "historical-hash",
    },
  ];
  server.use(
    http.get(`${apiBaseUrl}/banks/:bankId/regulatory-runs`, () =>
      HttpResponse.json({ runs: history, total: history.length }),
    ),
  );
  serve(summary([generation("2026-01-31", "1.25")]));
  const rendered = render(view());
  await screen.findAllByText("1.25%");
  expect(screen.getByText(`Input hash: ${"a".repeat(64)}`)).toBeVisible();
  serve();
  await client.invalidateQueries({ queryKey: ["live-summary"] });
  await screen.findAllByText("4.75%");
  expect(screen.queryByText("1.25%")).not.toBeInTheDocument();
  expect(screen.getByText(`Input hash: ${"b".repeat(64)}`)).toBeVisible();
  expect(screen.getByText("Engine ftp-test-v2")).toBeVisible();
  expect(
    screen.getByText(/Live-generation evidence · Baseline · Generation 22/),
  ).toBeVisible();
  expect(screen.getByText(/Source as of 28 Feb 2026/)).toHaveTextContent(
    "2026-02-28 12:00:00 UTC",
  );
  expect(
    screen.getByText(/reporting-period selector does not apply/),
  ).toBeVisible();
  expect(screen.queryByText(/Period A/)).not.toBeInTheDocument();
  selection.period = "Period B";
  rendered.rerender(view());
  expect(screen.queryByText(/Period B/)).not.toBeInTheDocument();
  expect(
    screen.queryByText(
      /Official run provenance|Synthetic demonstration dataset/,
    ),
  ).not.toBeInTheDocument();
  expect(requests.every((path) => path.endsWith("/live-summary"))).toBe(true);
  const print = vi.spyOn(window, "print").mockImplementation(() => {});
  fireEvent.click(screen.getByRole("button", { name: /Print \/ Save as PDF/ }));
  expect(print).toHaveBeenCalledOnce();
  // Optional proof is the actual rendered component, never a recreated mockup.
  if (process.env.BOARD_PACK_PROOF_DIR) {
    mkdirSync(process.env.BOARD_PACK_PROOF_DIR, { recursive: true });
    writeFileSync(
      `${process.env.BOARD_PACK_PROOF_DIR}/board-pack.html`,
      `<!doctype html><html><head><link rel="stylesheet" href="board-pack.css"></head><body class="font-sans text-body bg-surface-alt text-ink" style="--font-inter:Arial;--font-plex-mono:monospace">${rendered.container.innerHTML}</body></html>`,
    );
  }
});

it.each(["failed", "blocked"])(
  "withholds %s generation figures even when old metrics remain",
  async (state) => {
    serve(
      summary([
        {
          ...generation(),
          pipeline_state: state,
          pipeline_error: "Synthetic input refusal",
        },
      ]),
    );
    render(view());
    await screen.findAllByText(
      new RegExp(
        `${state === "failed" ? "Failed" : "Blocked"} live generation`,
      ),
    );
    expect(screen.queryByText("4.75%")).not.toBeInTheDocument();
    expect(screen.queryByText(/Input hash:/)).not.toBeInTheDocument();
  },
);

it("withholds stale evidence after withdrawal, then recovers only with the new generation", async () => {
  serve({ ...summary(), is_stale: true });
  render(view());
  await screen.findAllByText(/stale after an input change or withdrawal/);
  expect(screen.queryByText("4.75%")).not.toBeInTheDocument();
  serve();
  await client.invalidateQueries({ queryKey: ["live-summary"] });
  await screen.findAllByText("4.75%");
  expect(
    screen.queryByText(/stale after an input change or withdrawal/),
  ).not.toBeInTheDocument();
});

it("shows missing and incomplete evidence visibly without inventing zeros", async () => {
  serve(
    summary([
      {
        ...generation(),
        computed_from_input_hash: "",
        metrics: { portfolio_nim_pct: "", total_balance_ghs: "123456" },
      },
    ]),
  );
  render(view());
  await screen.findAllByText(/Incomplete live-generation evidence/);
  expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
  expect(screen.getAllByText(/Missing live generation/).length).toBeGreaterThan(
    0,
  );
});

it("does not offer printing until evidence loads and reports empty evidence", async () => {
  serve(summary([]));
  render(view());
  expect(
    screen.getByRole("button", { name: /Print \/ Save as PDF/ }),
  ).toBeDisabled();
  await screen.findByText("No live evidence");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Print \/ Save as PDF/ }),
    ).toBeDisabled(),
  );
});

it("renders every brief from its own live payload and preserves unavailable numeric fields", async () => {
  const base = generation();
  const modules = [
    {
      ...base,
      module: "liquidity",
      metrics: {
        lcr_pct: "151.75",
        nsfr_pct: null,
        hqla_total_ghs: "1234567",
        net_outflows_30d_ghs: "813554",
      },
    },
    {
      ...base,
      module: "capital",
      metrics: {
        car_pct: "18.25",
        cet1_ratio_pct: "14.5",
        tier1_ratio_pct: "15.2",
        leverage_ratio_pct: "7.25",
        total_rwa_ghs: "8876543",
        total_capital_ghs: "1619969",
      },
    },
    {
      ...base,
      module: "credit",
      metrics: {
        npl_ratio_pct: "3.25",
        npl_exposure_ghs: "325000",
        provision_coverage_pct: "85",
        gross_loans_ghs: "10000000",
        total_provision_required_ghs: "276250",
      },
    },
    {
      ...base,
      module: "irr",
      metrics: {
        worst_eve_change_pct_tier1: "-8.75",
        worst_scenario: "parallel_up_200",
        duration_gap: "1.5",
        nii_base_ghs: "975000",
      },
    },
    {
      ...base,
      module: "fx",
      metrics: {
        nop_pct_tier1: "4.5",
        nop_ghs: "75000",
        single_ccy_max_pct: "2.5",
        single_ccy_max_currency: "USD",
        var_99_1d_ghs: "12500",
      },
    },
    base,
  ];
  const distinctModules = modules.map((view, index) => ({
    ...view,
    engine_version: `${view.module}-test-v2`,
    computed_from_input_hash: String(index + 1).repeat(64),
    calculation_generation: index + 1,
  }));
  serve(summary(distinctModules));
  const rendered = render(view());
  await screen.findAllByText("151.75%");
  await screen.findAllByText("4.75%");
  expect(screen.getAllByText(/Input hash:/)).toHaveLength(6);
  expect(screen.getAllByRole("table")).toHaveLength(6);
  const liquidity = within(
    screen.getByRole("region", { name: "Liquidity brief" }),
  );
  expect(liquidity.getByText("Engine liquidity-test-v2")).toBeVisible();
  expect(liquidity.getByText(`Input hash: ${"1".repeat(64)}`)).toBeVisible();
  const ftp = within(
    screen.getByRole("region", { name: "Transfer Pricing brief" }),
  );
  expect(ftp.getByText("Engine ftp-test-v2")).toBeVisible();
  expect(ftp.getByText(`Input hash: ${"6".repeat(64)}`)).toBeVisible();
  expect(
    screen.getByRole("row", { name: "Net Stable Funding Ratio Unavailable" }),
  ).toBeVisible();
  expect(screen.getByText("EUR 1,234,567.00")).toBeVisible();
  expect(screen.getByText("Parallel Up 200")).toBeVisible();
  if (process.env.BOARD_PACK_PROOF_DIR) {
    writeFileSync(
      `${process.env.BOARD_PACK_PROOF_DIR}/board-pack.html`,
      `<!doctype html><html><head><title>aequorOS synthetic board pack</title><link rel="stylesheet" href="board-pack.css"></head><body class="font-sans text-body bg-surface-alt text-ink" style="--font-inter:Arial;--font-plex-mono:monospace">${rendered.container.innerHTML}</body></html>`,
    );
  }
});

it.each(["", null, "not-a-number", "   "])(
  "preserves unavailable headline %j instead of printing zero",
  async (value) => {
    serve(
      summary([{ ...generation(), metrics: { portfolio_nim_pct: value } }]),
    );
    render(view());
    await screen.findByRole("region", { name: "Transfer Pricing brief" });
    expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  },
);

it("preserves a measured zero as a figure", async () => {
  serve(summary([{ ...generation(), metrics: { portfolio_nim_pct: "0" } }]));
  render(view());
  await screen.findAllByText("0.00%");
  expect(screen.getAllByText("0.00%")).toHaveLength(2);
});
