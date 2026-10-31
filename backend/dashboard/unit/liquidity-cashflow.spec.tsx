// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
} from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import CashflowWindowPanel from "../features/liquidity/components/CashflowWindowPanel";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { setActiveJurisdiction } from "../lib/format";

const bankId = "BK-SAMP0001";
const endpoint = `${apiBaseUrl}/banks/:bankId/analytics/cashflow-window`;
const server = setupServer();
let client: QueryClient;

function windowData() {
  return {
    bank_id: bankId,
    start_date: "2026-01-01",
    end_date: "2026-02-28",
    position_count: 3,
    no_maturity_count: 1,
    months: [
      {
        month: "2026-01-01",
        currency: "EUR",
        inflows: "1250",
        outflows: "500",
        net: "750",
      },
      {
        month: "2026-02-01",
        currency: "EUR",
        inflows: "250",
        outflows: "1000",
        net: "-750",
      },
      {
        month: "2026-01-01",
        currency: "USD",
        inflows: "2000",
        outflows: "3500",
        net: "-1500",
      },
      {
        month: "2026-02-01",
        currency: "USD",
        inflows: "0",
        outflows: "0",
        net: "0",
      },
    ],
    totals: [
      { currency: "EUR", inflows: "1500", outflows: "1500", net: "0" },
      { currency: "USD", inflows: "2000", outflows: "3500", net: "-1500" },
    ],
    overall: {
      currency: "EUR",
      inflows: "3000",
      outflows: "4000",
      net: "-1000",
      unconverted_count: 1,
    },
  };
}

function view(selectedBank: string | undefined, version = 1) {
  return (
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope(
          "OR-DEM00001",
          "fixture@example.test",
          version,
        )}
      >
        <CashflowWindowPanel bankId={selectedBank} />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>
  );
}

async function dates(
  user: ReturnType<typeof userEvent.setup>,
  start = "2026-01-01",
  end = "2026-02-28",
) {
  await user.type(screen.getByLabelText("Cash-flow window start date"), start);
  await user.type(screen.getByLabelText("Cash-flow window end date"), end);
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  setAccessToken("fixture-access-token");
  setActiveJurisdiction({ currencyCode: "EUR", locale: "en-US" });
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  setActiveJurisdiction({});
});

describe("contractual liquidity cash-flow computation", () => {
  it("requires both dates and a selected institution before enabling Compute", async () => {
    const user = userEvent.setup();
    const rendered = render(view(undefined));
    const compute = screen.getByRole("button", { name: "Compute" });
    expect(compute).toBeDisabled();
    await dates(user);
    expect(compute).toBeDisabled();
    rendered.rerender(view(bankId));
    expect(compute).toBeEnabled();
    await user.clear(screen.getByLabelText("Cash-flow window end date"));
    expect(compute).toBeDisabled();
    expect(screen.getByText(/Pick a start and end date/)).toBeVisible();
  });

  it("submits the chosen dates through the real client and displays monthly and currency totals", async () => {
    server.use(
      http.get(endpoint, ({ request, params }) => {
        const url = new URL(request.url);
        expect(params.bankId).toBe(bankId);
        expect(url.searchParams.get("start_date")).toBe("2026-01-01");
        expect(url.searchParams.get("end_date")).toBe("2026-02-28");
        expect(request.headers.get("Authorization")).toBe(
          "Bearer fixture-access-token",
        );
        return HttpResponse.json(windowData());
      }),
    );
    const user = userEvent.setup();
    render(view(bankId));
    await dates(user);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    const table = await screen.findByRole("table");
    expect(
      within(table).getByRole("row", {
        name: "Jan 2026 EUR 1.3K EUR 500 +EUR 750",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "Feb 2026 EUR 250 EUR 1.0K -EUR 750",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "Total EUR 1.5K EUR 1.5K +EUR 0",
      }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "USD" }));
    expect(
      within(table).getByRole("row", {
        name: "Jan 2026 $2.0K $3.5K -$1.5K",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "Feb 2026 $0 $0 +$0",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "Total $2.0K $3.5K -$1.5K",
      }),
    ).toBeVisible();
    expect(
      screen.getByText(/1 position carries no contractual maturity — excluded/),
    ).toBeVisible();
    expect(
      screen.getByText(/1 position without an ingested conversion excluded/),
    ).toBeVisible();
    expect(screen.getByText("-EUR 1.0K")).toBeVisible();
  });

  it("shows an honest empty window and the excluded-maturity count", async () => {
    server.use(
      http.get(endpoint, () =>
        HttpResponse.json({
          ...windowData(),
          months: [],
          totals: [],
          position_count: 0,
          no_maturity_count: 2,
        }),
      ),
    );
    const user = userEvent.setup();
    render(view(bankId));
    await dates(user);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(
      await screen.findByText("No contractual maturities in this window"),
    ).toBeVisible();
    expect(
      screen.getByText("2 positions carry no contractual maturity — excluded."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Base-equivalent totals/),
    ).not.toBeInTheDocument();
  });

  it("keeps the applied figures until Compute and chooses an available currency in the new window", async () => {
    server.use(
      http.get(endpoint, ({ request }) => {
        if (
          new URL(request.url).searchParams.get("end_date") === "2026-02-28"
        ) {
          return HttpResponse.json(windowData());
        }
        return HttpResponse.json({
          ...windowData(),
          end_date: "2026-03-31",
          months: [
            {
              month: "2026-01-01",
              currency: "GBP",
              inflows: "0",
              outflows: "0",
              net: "0",
            },
            {
              month: "2026-02-01",
              currency: "GBP",
              inflows: "0",
              outflows: "0",
              net: "0",
            },
            {
              month: "2026-03-01",
              currency: "GBP",
              inflows: "50",
              outflows: "25",
              net: "25",
            },
          ],
          totals: [
            { currency: "GBP", inflows: "50", outflows: "25", net: "25" },
          ],
          overall: {
            currency: "EUR",
            inflows: "60",
            outflows: "30",
            net: "30",
            unconverted_count: 0,
          },
        });
      }),
    );
    const user = userEvent.setup();
    render(view(bankId));
    await dates(user);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "USD" }));
    await user.clear(screen.getByLabelText("Cash-flow window end date"));
    await user.type(
      screen.getByLabelText("Cash-flow window end date"),
      "2026-03-31",
    );
    expect(
      screen.getByRole("row", { name: "Jan 2026 $2.0K $3.5K -$1.5K" }),
    ).toBeVisible();
    expect(screen.queryByText("Mar 2026")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(
      await screen.findByRole("row", {
        name: "Mar 2026 GBP 50 GBP 25 +GBP 25",
      }),
    ).toBeVisible();
    for (const month of ["Jan", "Feb"]) {
      expect(
        screen.getByRole("row", {
          name: `${month} 2026 GBP 0 GBP 0 +GBP 0`,
        }),
      ).toBeVisible();
    }
    expect(
      screen.getByRole("row", { name: "Total GBP 50 GBP 25 +GBP 25" }),
    ).toBeVisible();
    for (const name of [
      "Jan 2026 $2.0K $3.5K -$1.5K",
      "Feb 2026 $0 $0 +$0",
      "Total $2.0K $3.5K -$1.5K",
    ]) {
      expect(screen.queryByRole("row", { name })).not.toBeInTheDocument();
    }
    expect(
      screen.queryByRole("button", { name: "USD" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "EUR" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/without an ingested conversion excluded/),
    ).not.toBeInTheDocument();
  });

  it("does not reuse another bank's cached amounts for the same dates", async () => {
    server.use(
      http.get(endpoint, ({ params }) =>
        HttpResponse.json(
          params.bankId === bankId
            ? windowData()
            : {
                ...windowData(),
                bank_id: params.bankId,
                months: [],
                totals: [],
                position_count: 0,
                no_maturity_count: 0,
              },
        ),
      ),
    );
    const user = userEvent.setup();
    const rendered = render(view(bankId));
    await dates(user);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByRole("table")).toBeVisible();
    rendered.rerender(view("BK-SAMP0002"));
    expect(
      await screen.findByText("No contractual maturities in this window"),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    rendered.rerender(view(bankId));
    expect(
      await screen.findByRole("row", {
        name: "Jan 2026 EUR 1.3K EUR 500 +EUR 750",
      }),
    ).toBeVisible();
  });

  it("displays server date validation and recovers after the user corrects the window", async () => {
    server.use(
      http.get(endpoint, ({ request }) => {
        const url = new URL(request.url);
        return url.searchParams.get("start_date")! >
          url.searchParams.get("end_date")!
          ? HttpResponse.json(
              {
                error: {
                  code: "validation_error",
                  message: "End date must be on or after start date.",
                },
              },
              { status: 422 },
            )
          : HttpResponse.json(windowData());
      }),
    );
    const user = userEvent.setup();
    render(view(bankId));
    await dates(user, "2026-03-01");
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(
      await screen.findByText("End date must be on or after start date."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Cash-flow window start date"));
    await user.type(
      screen.getByLabelText("Cash-flow window start date"),
      "2026-01-01",
    );
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByRole("table")).toBeVisible();
    expect(
      screen.queryByText("End date must be on or after start date."),
    ).not.toBeInTheDocument();
  });

  it("removes cached figures when authorization changes and the API refuses access", async () => {
    server.use(http.get(endpoint, () => HttpResponse.json(windowData())));
    const user = userEvent.setup();
    const rendered = render(view(bankId));
    await dates(user);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByRole("table")).toBeVisible();
    server.use(
      http.get(endpoint, () =>
        HttpResponse.json(
          {
            error: {
              code: "forbidden",
              message: "Liquidity confidential view access is required.",
            },
          },
          { status: 403 },
        ),
      ),
    );
    rendered.rerender(view(bankId, 2));
    expect(
      await screen.findByText(
        "Liquidity confidential view access is required.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    server.use(http.get(endpoint, () => HttpResponse.json(windowData())));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeVisible();
    expect(
      screen.queryByText("Liquidity confidential view access is required."),
    ).not.toBeInTheDocument();
  });
});
