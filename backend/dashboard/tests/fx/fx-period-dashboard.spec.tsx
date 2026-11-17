// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import FxHedgesPage from "../../app/(app)/fx/hedges/page";
import ProfileProvider from "../../components/profile/ProfileProvider";
import BankProvider from "../../components/shell/BankContext";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../../lib/format";

// The FX workspace must follow the bank context's selected as-of reporting
// period the way every other stored-run module does: the dashboard request
// carries the period, the Hedge Book then reads that period's stored run, and
// a withheld parameter snapshot renders as unavailable instead of the
// hardcoded IFRS 9 defaults. With no period selected the pages fall back to
// current mode exactly as before.

const bankId = "BK-SAMP0001";
const base = `${apiBaseUrl}/banks/${bankId}`;
const server = setupServer();
let client: QueryClient;

vi.mock("@/components/charts/EChart", () => ({
  default: ({
    ariaLabel,
    option,
  }: {
    ariaLabel: string;
    option: Record<string, unknown>;
  }) => (
    <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} />
  ),
}));

const period = {
  id: "period-1",
  bank_id: bankId,
  label: "October 2026",
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  status: "open",
};

// An older month: the bank context sorts periods newest-first, so the
// workspace still opens on `period` and the switch below moves backwards.
const laterPeriod = {
  id: "period-2",
  bank_id: bankId,
  label: "September 2026",
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  status: "open",
};

const metrics = {
  nop_ghs: "30",
  nop_pct_tier1: "30",
  nop_aggregate_limit_pct: "40",
  nop_single_limit_pct: "10",
  nop_status: "green",
  var_99_1d_ghs: "10",
  standalone_var_total_ghs: "10",
  diversification_benefit_ghs: "0",
  stressed_var_ghs: "20",
  var_confidence_pct: "99",
  var_observations: 249,
  hedge_aggregate_mtm_ghs: "5",
  hedge_total_count: 1,
  hedge_effective_count: 1,
};

function runBody(runId: string, withheld: boolean) {
  return {
    id: runId,
    organization_id: "org",
    bank_id: bankId,
    reporting_period_id: runId.slice("run-".length),
    module: "fx",
    scenario_code: "baseline",
    status: "succeeded",
    engine_version: "fx-test",
    input_schema_version: "fx-inputs-v1",
    output_schema_version: "fx-metrics-v1",
    input_hash: "a".repeat(64),
    inputs: withheld
      ? null
      : {
          parameters: {
            hedge_bands_pct: {
              hedge_r2_min_pct: "90",
              hedge_offset_low_pct: "70",
              hedge_offset_high_pct: "130",
            },
          },
        },
    inputs_available: !withheld,
    inputs_unavailable_reason: withheld ? "restricted_view_required" : null,
    metrics: {},
    assumption_provenance: null,
    started_at: "2026-10-02T00:00:00Z",
    completed_at: "2026-10-02T00:01:00Z",
    error: null,
    metric_results: [],
    line_items: [],
    validations: [],
    created_by: "00000000-0000-0000-0000-000000000001",
    created_at: "2026-10-02T00:00:00Z",
    updated_at: "2026-10-02T00:01:00Z",
  };
}

function capability(sensitivity: string) {
  return {
    module: "fx",
    permission: "view",
    sensitivity,
    requires_contextual_authorization: false,
    data_scope: { kind: "all", branches: [], regions: [] },
  };
}

// Mirrors the service: GET /fx/dashboard in current mode (no reporting period
// selected) has no stored run, so latest_run_id is null; period mode returns
// the run stored for the selected period.
function fixture(options: {
  withheld: boolean;
  periods?: typeof period[] | [];
}) {
  const fxRequests: string[] = [];
  const runRequests: string[] = [];
  const periods = options.periods ?? [period];
  const periodById = new Map(periods.map((entry) => [entry.id, entry]));
  const requestedPeriod = (url: string) => {
    const id = new URL(url).searchParams.get("reporting_period_id");
    return id ? periodById.get(id) : null;
  };
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json({
        user_id: "reader",
        effective_authority: {
          authv: 1,
          organization_capabilities: [],
          institution_capabilities: [
            {
              institution_id: bankId,
              capabilities: [
                {
                  module: "data",
                  permission: "view",
                  sensitivity: "confidential",
                  requires_contextual_authorization: false,
                  data_scope: { kind: "all", branches: [], regions: [] },
                },
                capability("aggregated"),
                capability("confidential"),
              ],
            },
          ],
        },
      }),
    ),
    http.get(`${apiBaseUrl}/banks`, () =>
      HttpResponse.json({
        banks: [
          {
            id: bankId,
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
    http.get(`${base}/reporting-periods`, () =>
      HttpResponse.json({ periods }),
    ),
    http.get(`${base}/freshness`, () =>
      HttpResponse.json({
        bank_id: bankId,
        is_stale: false,
        modules: [],
        period_label: period.label,
        reporting_period_id: period.id,
      }),
    ),
    http.get(`${base}/live-summary`, () => HttpResponse.json({})),
    http.get(`${base}/fx/dashboard`, ({ request }) => {
      fxRequests.push(request.url);
      const requested = requestedPeriod(request.url);
      return HttpResponse.json({
        bank: {
          id: bankId,
          name: "Fixture Bank",
          currency: "GHS",
          jurisdiction_code: "GH",
        },
        period: requested ?? period,
        latest_run_id: requested ? `run-${requested.id}` : null,
        metrics,
        positions: [],
        standalone_vars: [],
        hedges: [
          {
            hedge_id: "H1",
            pair: "USD/GHS",
            instrument: "forward",
            prospective_r2_pct: "91",
            dollar_offset_pct: "105",
            mtm_ghs: "5",
            effective: true,
          },
        ],
        scenarios: [],
        trend: [],
        stored: true,
        validations: [],
      });
    }),
    http.get(`${base}/regulatory-runs/:runId`, ({ params }) => {
      const runId = String(params.runId);
      runRequests.push(runId);
      return HttpResponse.json(runBody(runId, options.withheld));
    }),
  );
  return { fxRequests, runRequests };
}

function view() {
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
          <PathnameContext.Provider value="/fx/hedges">
            <SearchParamsContext.Provider value={new URLSearchParams()}>
              <ProfileProvider>
                <BankProvider>
                  <FxHedgesPage />
                </BankProvider>
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
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  setActiveJurisdiction({ currencyCode: "GHS", locale: "en-GH" });
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
  vi.unstubAllGlobals();
});

it("requests the FX dashboard for the selected reporting period and reads its stored run", async () => {
  const { fxRequests, runRequests } = fixture({ withheld: false });
  view();

  await waitFor(() =>
    expect(
      fxRequests.some((url) =>
        url.includes(`reporting_period_id=${period.id}`),
      ),
    ).toBe(true),
  );
  // The Hedge Book then reads the run stored for that period through its own
  // route: the stored snapshot, not a default, drives the bands.
  await waitFor(() => expect(runRequests).toEqual(["run-period-1"]));
  await screen.findByText("Effective");
});

it("shows a withheld stored snapshot as unavailable instead of the hardcoded defaults", async () => {
  const { fxRequests, runRequests } = fixture({ withheld: true });
  view();

  await waitFor(() =>
    expect(
      fxRequests.some((url) =>
        url.includes(`reporting_period_id=${period.id}`),
      ),
    ).toBe(true),
  );
  await waitFor(() => expect(runRequests).toEqual(["run-period-1"]));

  expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  expect(
    screen.getByText(/not available at your access level/),
  ).toBeInTheDocument();
  // No band figure is invented: neither the defaults nor any other number.
  expect(screen.queryByText(/R² ≥/)).toBeNull();
  expect(screen.queryByText(/≥ 80%/)).toBeNull();
  expect(screen.queryByText(/80–125%/)).toBeNull();
});

it("drives the dual-test bands from the stored run snapshot when it is served", async () => {
  const { fxRequests, runRequests } = fixture({ withheld: false });
  view();

  await waitFor(() =>
    expect(
      fxRequests.some((url) =>
        url.includes(`reporting_period_id=${period.id}`),
      ),
    ).toBe(true),
  );
  await waitFor(() => expect(runRequests).toEqual(["run-period-1"]));

  expect((await screen.findAllByText(/R² ≥ 90%/)).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/70–130%/).length).toBeGreaterThan(0);
});

it("falls back to current mode with no reporting period selected", async () => {
  const { fxRequests } = fixture({ withheld: false, periods: [] });
  view();

  await screen.findByText("Effective");
  const urls = fxRequests.filter((url) => url.includes("/fx/dashboard"));
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.every((url) => !url.includes("reporting_period_id"))).toBe(true);
  // Current mode as before: the documented IFRS 9 defaults, no stored run.
  expect(screen.getAllByText(/R² ≥ 80%/).length).toBeGreaterThan(0);
});

it("refetches the dashboard and the stored run when the selection moves to another period", async () => {
  const { fxRequests, runRequests } = fixture({
    withheld: false,
    periods: [period, laterPeriod],
  });
  const { fireEvent } = await import("@testing-library/react");
  view();

  await waitFor(() => expect(runRequests).toEqual(["run-period-1"]));
  // Move the as-of selection through the workspace's own period control.
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: laterPeriod.id },
  });

  // A fresh request carries the new period, and the run read is that
  // period's run — not the previous selection's cached result.
  await waitFor(() =>
    expect(
      fxRequests.some((url) =>
        url.includes(`reporting_period_id=${laterPeriod.id}`),
      ),
    ).toBe(true),
  );
  await waitFor(() =>
    expect(runRequests).toEqual(["run-period-1", "run-period-2"]),
  );
  expect(screen.getByRole("combobox")).toHaveValue(laterPeriod.id);
});
