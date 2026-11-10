// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionProvider } from "next-auth/react";
import {
  PathnameContext,
  SearchParamsContext,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ComparePage from "../../app/(app)/submissions/compare/page";
import ProfileProvider from "../../components/profile/ProfileProvider";
import BankProvider from "../../components/shell/BankContext";
import { apiBaseUrl } from "../../lib/api/client";
import { setAccessToken } from "../../lib/api/token";
import { queryAuthorityScope } from "../../lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "../../lib/api/useQueryScope";
import { setActiveJurisdiction } from "../../lib/format";

const bankId = "BK-SAMP0001";
const base = `${apiBaseUrl}/banks/${bankId}`;
const server = setupServer();
let client: QueryClient;

const september = {
  id: "september",
  bank_id: bankId,
  label: "September 2026",
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  status: "open",
};
const october = {
  id: "october",
  bank_id: bankId,
  label: "October 2026",
  period_start: "2026-10-01",
  period_end: "2026-10-31",
  status: "open",
};

/** Two versions of the September return — the minimum viable version diff. */
function run(
  id: string,
  createdAt: string,
  periodId = "september",
  scenarioCode = "baseline",
) {
  return {
    id,
    reporting_period_id: periodId,
    period_label: periodId === "september" ? "September 2026" : "October 2026",
    module: "liquidity",
    scenario_code: scenarioCode,
    status: "succeeded",
    created_at: createdAt,
    metrics: {},
  };
}
const septemberRuns = [
  run("run-a", "2026-10-01T09:00:00Z"),
  run("run-b", "2026-10-02T09:00:00Z"),
];

function side(patch: Record<string, unknown>) {
  return {
    run_id: null,
    version: null,
    label: "",
    period_label: "September 2026",
    reporting_date: "2026-09-30",
    reporting_period_id: "september",
    scenario_code: "baseline",
    engine_version: "",
    ...patch,
  };
}

/**
 * One line per rendering branch the ratchet must stay honest about: a ccy
 * gain, a pct drop, a flat ratio, a brand-new line (no base to compare) and a
 * count whose relative change is undefined because the base is zero.
 */
function comparisonBody() {
  return [
    {
      title: "Liquidity ratios",
      lines: [
        {
          key: "lcr",
          label: "Liquidity coverage ratio",
          unit: "ccy",
          left_value: "500",
          right_value: "1000",
          delta_ccy: "500",
          delta_pct: "100",
          direction: "up",
          favorability: "favorable",
          new: false,
        },
        {
          key: "nsfr",
          label: "Net stable funding ratio",
          unit: "pct",
          left_value: "12.5",
          right_value: "5",
          delta_ccy: "-7.5",
          delta_pct: "-60",
          direction: "down",
          favorability: "adverse",
          new: false,
        },
        {
          key: "ltr",
          label: "Loan-to-deposit ratio",
          unit: "ratio",
          left_value: "1.5",
          right_value: "1.5",
          delta_ccy: "0",
          delta_pct: "0",
          direction: "flat",
          favorability: "neutral",
          new: false,
        },
        {
          key: "collateral",
          label: "New cash collateral",
          unit: "count",
          left_value: null,
          right_value: "42",
          delta_ccy: null,
          delta_pct: null,
          direction: "up",
          favorability: "favorable",
          new: true,
        },
        {
          key: "breaches",
          label: "Breaches",
          unit: "count",
          left_value: "0",
          right_value: "10",
          delta_ccy: "10",
          delta_pct: null,
          direction: "up",
          favorability: "adverse",
          new: false,
        },
      ],
    },
  ];
}

function comparisonResponse(mode: "version" | "period") {
  return {
    mode,
    module: "liquidity",
    left:
      mode === "version"
        ? side({ run_id: "run-a", version: 1 })
        : side({ period_label: "September 2026" }),
    right:
      mode === "version"
        ? side({
            run_id: "run-b",
            version: 2,
            label: "",
            period_label: "September 2026",
          })
        : side({ period_label: "October 2026" }),
    groups: comparisonBody(),
    favorable_count: 2,
    adverse_count: 2,
    neutral_count: 1,
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

/**
 * Periods and runs are the page's two selector reads; `comparisons` counts the
 * server-computed diff requests so guards can be asserted not to fetch.
 */
function fixture(options: {
  periods?: typeof september | (typeof september | typeof october)[];
  runs?: ReturnType<typeof run>[];
}) {
  const periods = options.periods ?? [september];
  const runs = options.runs ?? septemberRuns;
  const reads = { periods: 0, runs: 0, comparisons: 0 };
  server.use(
    http.get(`${apiBaseUrl}/auth/me`, () =>
      HttpResponse.json(authority(["confidential"])),
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
    http.get(`${base}/reporting-periods`, () => {
      reads.periods++;
      return HttpResponse.json({ periods });
    }),
    http.get(`${base}/regulatory-runs`, () => {
      reads.runs++;
      return HttpResponse.json({
        runs,
        total: runs.length,
        limit: 100,
        offset: 0,
      });
    }),
    http.get(`${base}/reports/comparison`, () => {
      reads.comparisons++;
      return HttpResponse.json(comparisonResponse("version"));
    }),
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

it("formats the version diff in each line's native unit", async () => {
  fixture({});
  view(<ComparePage />);
  expect(
    await screen.findByText("Comparison summary"),
  ).toBeInTheDocument();

  // Currency line: compact currency cells and a favorable absolute delta.
  const lcr = screen.getByText("Liquidity coverage ratio").closest("tr")!;
  expect(within(lcr).getByText("GHS 500")).toBeInTheDocument();
  expect(within(lcr).getByText("GHS 1.0K")).toBeInTheDocument();
  expect(within(lcr).getByText("+GHS 500")).toBeInTheDocument();
  expect(within(lcr).getByText("+100.0%")).toBeInTheDocument();

  // Percentage line: two-decimal cells and a signed percentage-point delta.
  const nsfr = screen.getByText("Net stable funding ratio").closest("tr")!;
  expect(within(nsfr).getByText("12.50%")).toBeInTheDocument();
  expect(within(nsfr).getByText("5.00%")).toBeInTheDocument();
  expect(within(nsfr).getByText("-7.50 pp")).toBeInTheDocument();
  expect(within(nsfr).getByText("-60.0%")).toBeInTheDocument();

  // Flat ratio line: a zero delta stays unsigned, the relative stays signed.
  const ltr = screen.getByText("Loan-to-deposit ratio").closest("tr")!;
  expect(within(ltr).getAllByText("1.50%")).toHaveLength(2);
  expect(within(ltr).getByText("0.00 pp")).toBeInTheDocument();
  expect(within(ltr).getByText("0.0%")).toBeInTheDocument();

  // A new line has no baseline value and no defined absolute or relative change.
  const fresh = screen.getByText("New cash collateral").closest("tr")!;
  expect(within(fresh).getAllByText("—")).toHaveLength(2);
  expect(within(fresh).getByText("42")).toBeInTheDocument();
  expect(within(fresh).getByText("new")).toBeInTheDocument();

  // A zero base leaves the relative change undefined without being "new".
  const breaches = screen.getByText("Breaches").closest("tr")!;
  expect(within(breaches).getByText("+10")).toBeInTheDocument();
  expect(within(breaches).getByText("—")).toBeInTheDocument();

  // The summary header counts each direction, and the columns name versions.
  expect(screen.getByText("favorable").previousElementSibling).toHaveTextContent(
    "2",
  );
  expect(screen.getByText("adverse").previousElementSibling).toHaveTextContent(
    "2",
  );
  expect(screen.getByText("neutral").previousElementSibling).toHaveTextContent(
    "1",
  );
  expect(
    screen.getByRole("columnheader", { name: "v1" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "v2" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Baseline (left)")).toBeInTheDocument();
  expect(screen.getByText("Compared (right)")).toBeInTheDocument();
});

it("keeps the selectors interactive while the diff loads", async () => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  fixture({});
  server.use(
    http.get(`${base}/reports/comparison`, async () => {
      await pending;
      return HttpResponse.json(comparisonResponse("version"));
    }),
  );
  view(<ComparePage />);
  // Once the selectors settle on their defaults the diff body shows its
  // skeletons (selectors stay interactive while the diff refetches).
  await waitFor(() => {
    expect(
      screen.getByRole("combobox", { name: "Baseline version" }),
    ).toHaveValue("run-a");
    expect(
      screen.getByRole("combobox", { name: "Compared version" }),
    ).toHaveValue("run-b");
  });
  expect(screen.getAllByLabelText("Loading")).toHaveLength(2);
  expect(
    screen.getByRole("combobox", { name: "Compared version" }),
  ).toHaveValue("run-b");
  await act(async () => release());
  expect(await screen.findByText("Comparison summary")).toBeInTheDocument();
});

it.each(["not_comparable", "missing", "failed"] as const)(
  "explains a %s comparison failure instead of a generic error",
  async (outcome) => {
    fixture({});
    server.use(
      http.get(`${base}/reports/comparison`, () => {
        if (outcome === "not_comparable")
          return HttpResponse.json(
            {
              detail: {
                error_code: "not_comparable",
                message: "The two runs are not comparable.",
              },
            },
            { status: 422 },
          );
        return HttpResponse.json(
          { detail: "Run not found" },
          { status: outcome === "missing" ? 404 : 500 },
        );
      }),
    );
    view(<ComparePage />);
    expect(
      await screen.findByText(
        outcome === "not_comparable"
          ? "These two returns aren't comparable"
          : outcome === "missing"
            ? "One of the selected runs or periods no longer exists"
            : "Could not load the comparison",
      ),
    ).toBeInTheDocument();
  },
);

it("defaults period mode to the two newest periods and labels columns by period", async () => {
  const user = userEvent.setup();
  fixture({
    periods: [september, october],
    runs: [...septemberRuns, run("run-c", "2026-10-20T09:00:00Z", "october")],
  });
  server.use(
    http.get(`${base}/reports/comparison`, () =>
      HttpResponse.json(comparisonResponse("period")),
    ),
  );
  view(<ComparePage />);
  await screen.findByText("Only one version for this period");
  await user.click(screen.getByRole("tab", { name: "Period" }));
  expect(
    await screen.findByText("Comparison summary"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("combobox", { name: "Baseline period" }),
  ).toHaveValue("september");
  expect(
    screen.getByRole("combobox", { name: "Compared period" }),
  ).toHaveValue("october");
  expect(
    screen.getByRole("columnheader", { name: "September 2026" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "October 2026" }),
  ).toBeInTheDocument();
});

it("refuses an equal-sided comparison without fetching a diff", async () => {
  const user = userEvent.setup();
  const reads = fixture({});
  view(<ComparePage />);
  await screen.findByText("Comparison summary");
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Compared version" }),
    "run-a",
  );
  expect(
    await screen.findByText("Pick two different sides"),
  ).toBeInTheDocument();
  expect(reads.comparisons).toBe(1);
});

it("shows an empty diff when the two runs share no comparable lines", async () => {
  fixture({});
  server.use(
    http.get(`${base}/reports/comparison`, () =>
      HttpResponse.json({ ...comparisonResponse("version"), groups: [] }),
    ),
  );
  view(<ComparePage />);
  expect(
    await screen.findByText("No line items in this diff"),
  ).toBeInTheDocument();
});

it("switches to the return-version surface for send-back comparisons", async () => {
  const user = userEvent.setup();
  const reads = fixture({});
  server.use(
    http.get(`${apiBaseUrl}/regulatory-reporting/templates`, () =>
      HttpResponse.json({ templates: [] }),
    ),
    http.get(`${base}/regulatory-packages`, () =>
      HttpResponse.json({ packages: [], total: 0, limit: 100, offset: 0 }),
    ),
  );
  view(<ComparePage />);
  await screen.findByText("Comparison summary");
  await user.click(screen.getByRole("tab", { name: "Return version" }));
  expect(
    await screen.findByText(
      "Two versions of the same return and reporting date",
    ),
  ).toBeInTheDocument();
  expect(reads.comparisons).toBe(1);
});

it("explains that a period comparison needs two periods with runs", async () => {
  const user = userEvent.setup();
  fixture({ periods: [september, october], runs: septemberRuns });
  view(<ComparePage />);
  await screen.findByText("Comparison summary");
  await user.click(screen.getByRole("tab", { name: "Period" }));
  expect(
    await screen.findByText("Only one period has runs"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText("Comparison summary"),
  ).not.toBeInTheDocument();
});

it("retries the period selectors and then the diff after failures", async () => {
  const user = userEvent.setup();
  const reads = fixture({});
  let periodCalls = 0;
  server.use(
    http.get(`${base}/reporting-periods`, () => {
      periodCalls++;
      if (periodCalls === 1)
        return HttpResponse.json({ detail: "Periods failed" }, { status: 500 });
      reads.periods++;
      return HttpResponse.json({ periods: [september] });
    }),
    http.get(`${base}/reports/comparison`, () => {
      reads.comparisons++;
      if (reads.comparisons === 1)
        return HttpResponse.json({ detail: "Diff failed" }, { status: 500 });
      return HttpResponse.json(comparisonResponse("version"));
    }),
  );
  view(<ComparePage />);
  // The selectors' own failure surfaces first with a retry affordance.
  expect(await screen.findByText("Could not load data")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  // With selectors loaded the diff fetch fails once and offers its own retry.
  expect(
    await screen.findByText("Could not load the comparison"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Comparison summary")).toBeInTheDocument();
  expect(reads.periods).toBe(1);
});

it.each(["not_comparable", "failed"] as const)(
  "retries a %s diff and recovers when the pair becomes comparable",
  async (outcome) => {
    const user = userEvent.setup();
    const reads = fixture({});
    server.use(
      http.get(`${base}/reports/comparison`, () => {
        reads.comparisons++;
        if (reads.comparisons === 1)
          return outcome === "not_comparable"
            ? HttpResponse.json(
                {
                  detail: {
                    error_code: "not_comparable",
                    message: "The two runs are not comparable.",
                  },
                },
                { status: 422 },
              )
            : HttpResponse.json({ detail: "Diff failed" }, { status: 500 });
        return HttpResponse.json(comparisonResponse("version"));
      }),
    );
    view(<ComparePage />);
    expect(
      await screen.findByText(
        outcome === "not_comparable"
          ? "These two returns aren't comparable"
          : "Could not load the comparison",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Comparison summary")).toBeInTheDocument();
  },
);

it("orders scenarios baseline-first and re-scopes runs when the family changes", async () => {
  const user = userEvent.setup();
  const reads = fixture({
    runs: [
      ...septemberRuns,
      run("run-d", "2026-10-03T09:00:00Z", "september", "adverse"),
      run("run-e", "2026-10-04T09:00:00Z", "september", "severe"),
    ],
  });
  view(<ComparePage />);
  await screen.findByText("Comparison summary");
  const scenario = screen.getByRole("combobox", { name: "Scenario" });
  expect(scenario).not.toBeDisabled();
  expect([...scenario.querySelectorAll("option")].map((o) => o.value)).toEqual([
    "baseline",
    "adverse",
    "severe",
  ]);
  const runsBefore = reads.runs;
  const comparisonsBefore = reads.comparisons;
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Return family" }),
    "capital",
  );
  // The module-scoped runs query and the diff both re-query for the family.
  await waitFor(() => expect(reads.runs).toBeGreaterThan(runsBefore));
  await waitFor(() =>
    expect(reads.comparisons).toBeGreaterThan(comparisonsBefore),
  );
  expect(await screen.findByText("Comparison summary")).toBeInTheDocument();
});

it("keeps period mode honest when no runs exist at all", async () => {
  const user = userEvent.setup();
  fixture({ runs: [] });
  view(<ComparePage />);
  expect(await screen.findByText("No runs to compare")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Period" }));
  expect(screen.getByText("No runs to compare")).toBeInTheDocument();
  expect(
    screen.getByRole("combobox", { name: "Baseline period" }),
  ).toBeDisabled();
});
