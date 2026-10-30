// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import fc from "fast-check";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import LiveStatusCard from "@/components/live/LiveStatusCard";
import ForecastingPage from "@/app/(app)/forecasting/page";
import RiskPage from "@/app/(app)/risk/page";
import { apiBaseUrl } from "@/lib/api/client";
import { setAccessToken } from "@/lib/api/token";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/shell/BankContext", () => {
  const scope = {
    modules: new Set(["forecasting"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    hasInstitutionAuthority: true,
    isResolved: true,
    forecastingAggregatedView: true,
    forecastingConfidentialView: true,
    forecastingRun: false,
  };
  return {
    useBankContext: () => ({
      bank: { id: "BK-SYNTH001" },
      period: { id: "period", periodEnd: new Date("2026-09-30T00:00:00Z") },
      moduleScope: scope,
    }),
    useModuleScope: () => scope,
  };
});

const server = setupServer();
let client: QueryClient;
let failSummary = false;
let failMetadata = false;
let empty = false;
let releaseSummary: (() => void) | undefined;
let initialResponse: Promise<void>;
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  focusManager.setFocused(true);
  failSummary = false;
  failMetadata = false;
  empty = false;
  initialResponse = new Promise((resolve) => {
    releaseSummary = resolve;
  });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  setAccessToken("synthetic-token");
  server.use(
    http.get(`${apiBaseUrl}/banks/BK-SYNTH001/live-summary`, async () => {
      await initialResponse;
      if (failSummary)
        return HttpResponse.json(
          { message: "Synthetic live-summary outage" },
          { status: 503 },
        );
      return HttpResponse.json({
        bank_id: "BK-SYNTH001",
        computed_at: "2026-09-30T12:00:00Z",
        source_as_of_date: "2026-09-30",
        reporting_period_id: "period",
        period_label: "September 2026",
        is_stale: false,
        modules: empty
          ? []
          : ["forecast", "liquidity", "irr"].map((module) => ({
              module,
              computed_at: "2026-09-30T12:00:00Z",
              source_as_of_date: "2026-09-30",
              calculation_generation: 1,
              computed_from_input_hash: "a".repeat(64),
              engine_version: "synthetic",
              source_fact_period_id: "period",
              pipeline_state: "succeeded",
              pipeline_error: null,
              status: "green",
              metrics: {
                year5_car_pct: "18",
                year5_lcr_pct: "120",
                year5_nsfr_pct: "115",
                avg_roe_pct: "14",
              },
            })),
      });
    }),
    http.get(`${apiBaseUrl}/regulatory-reporting/templates`, () =>
      failMetadata
        ? HttpResponse.json(
            { message: "Synthetic metadata outage" },
            { status: 503 },
          )
        : HttpResponse.json({
            templates: ["LCR-NSFR", "IRRBB-PILOT"].map((code) => ({
              code,
              instrument_status: "exposure_draft",
              calculations: [],
            })),
          }),
    ),
    http.get(`${apiBaseUrl}/banks/BK-SYNTH001/alerts`, () =>
      HttpResponse.json({
        items: [],
        total: 0,
        by_severity: {},
        by_module: {},
      }),
    ),
    http.get(`${apiBaseUrl}/banks/BK-SYNTH001/forecast/runs`, () =>
      HttpResponse.json({ runs: [] }),
    ),
    http.get(`${apiBaseUrl}/feature-flags`, () =>
      HttpResponse.json({ bi_enabled: false }),
    ),
  );
});
afterEach(() => {
  releaseSummary?.();
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

const surfaces = [
  ["live card", () => <LiveStatusCard />, "Balance Sheet Forecast"],
  ["forecasting", () => <ForecastingPage />, "Current live forecast baseline"],
  ["risk", () => <RiskPage />, "Live module status"],
] as const;

function mount(component: () => React.ReactNode) {
  render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("OR-SYNTH001", "synthetic@example.test", 1)}
      >
        {component()}
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}

function expectNoEmptyInstruction() {
  for (const text of [
    /No live metrics yet/,
    /upload data to populate/,
    /Live forecast is not available yet/,
    /Ingest current financial data/,
  ]) {
    for (const element of screen.queryAllByText(text))
      expect(element).not.toBeVisible();
  }
}

async function expectReady(expected: string) {
  await waitFor(() => expect(screen.getByText(expected)).toBeVisible());
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expectNoEmptyInstruction();
  for (const element of screen.queryAllByText(
    /^(Liquidity|Interest Rate Risk|liquidity|irr)$/,
  )) {
    expect(element).not.toBeVisible();
  }
}

it.each(surfaces)(
  "recovers %s after loaded metrics encounter a failed live-summary poll",
  async (_name, component, expected) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    mount(component);
    await waitFor(() =>
      expect(screen.getAllByLabelText(/^Loading/)[0]).toBeVisible(),
    );
    expectNoEmptyInstruction();
    releaseSummary?.();
    await expectReady(expected);
    failSummary = true;
    await act(async () => {
      vi.advanceTimersByTime(22_000);
    });
    expect(
      await screen.findByText("Synthetic live-summary outage"),
    ).toBeVisible();
    expect(screen.getByText(expected)).not.toBeVisible();
    expectNoEmptyInstruction();
    failSummary = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await expectReady(expected);
    if (_name === "forecasting")
      expect(
        screen.getByRole("button", { name: "Run forecast" }),
      ).toBeDisabled();
  },
);

it.each(surfaces)(
  "retries both composite sources for %s",
  async (_name, component, expected) => {
    releaseSummary?.();
    mount(component);
    await expectReady(expected);
    failSummary = true;
    failMetadata = true;
    await act(async () => {
      await Promise.all([
        client.refetchQueries({ queryKey: ["live-summary"] }),
        client.refetchQueries({ queryKey: ["rr-templates"] }),
      ]);
    });
    expect(await screen.findByRole("button", { name: "Retry" })).toBeVisible();
    expectNoEmptyInstruction();
    failSummary = false;
    failMetadata = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await expectReady(expected);
  },
);

it.each(surfaces)(
  "preserves the successful empty state for %s",
  async (name, component) => {
    empty = true;
    releaseSummary?.();
    mount(component);
    if (name === "live card")
      expect(await screen.findByText(/No live metrics yet/)).toBeVisible();
    else if (name === "forecasting")
      expect(
        await screen.findByText("Live forecast is not available yet"),
      ).toBeVisible();
    else {
      await waitFor(() =>
        expect(screen.queryByLabelText("Loading")).toBeNull(),
      );
      expect(screen.queryByText("Live module status")).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  },
);

type Model = { failed: boolean };
type Real = { expected: string };
class SummaryTransition implements fc.AsyncCommand<Model, Real> {
  constructor(
    readonly action: "poll" | "retry",
    readonly fails: boolean,
  ) {}
  check(model: Readonly<Model>) {
    return this.action === "poll" || model.failed;
  }
  async run(model: Model, real: Real) {
    failSummary = this.fails;
    if (this.action === "retry") {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() => {
        expect(client.isFetching({ queryKey: ["live-summary"] })).toBe(0);
        expect(client.isFetching({ queryKey: ["rr-templates"] })).toBe(0);
      });
    } else {
      await act(async () => {
        await client.refetchQueries({ queryKey: ["live-summary"] });
      });
    }
    model.failed = this.fails;
    if (model.failed) {
      await waitFor(() =>
        expect(screen.getByText("Synthetic live-summary outage")).toBeVisible(),
      );
      expect(screen.getByText(real.expected)).not.toBeVisible();
      expectNoEmptyInstruction();
    } else await expectReady(real.expected);
  }
  toString() {
    return `${this.action}(${this.fails ? "outage" : "healthy"})`;
  }
}

it.each(surfaces)(
  "keeps %s truthful across repeated failures and recoveries",
  async (_name, component, expected) => {
    releaseSummary?.();
    await fc.assert(
      fc.asyncProperty(
        fc.commands(
          [
            fc.constant(new SummaryTransition("poll", true)),
            fc.constant(new SummaryTransition("poll", false)),
            fc.constant(new SummaryTransition("retry", true)),
            fc.constant(new SummaryTransition("retry", false)),
          ],
          { maxCommands: 6 },
        ),
        async (commands) => {
          failSummary = false;
          mount(component);
          await expectReady(expected);
          try {
            await fc.asyncModelRun(
              () => ({ model: { failed: false }, real: { expected } }),
              commands,
            );
          } finally {
            cleanup();
            client.clear();
          }
        },
      ),
      { numRuns: 10 },
    );
  },
  20_000,
);
