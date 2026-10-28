// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  CanonicalPositionReadFromJSON,
  PackageComparisonReadFromJSON,
} from "@aequoros/risk-service-api";
import CreditConcentrationPage from "../app/(app)/credit/concentration/page";
import CanonicalPositionsPage from "../app/(app)/data-engine/positions/page";
import PositionDrawer from "../components/positions/PositionDrawer";
import SnapshotPreview from "../components/submissions/SnapshotPreview";
import { ComparisonPanel } from "../components/submissions/PriorVersionsCard";
import { setAccessToken } from "../lib/api/token";
import {
  clearProtectedData,
  protectedDataMessages,
} from "../lib/api/protectedData";
import { setActiveJurisdiction } from "../lib/format";

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SAMP0001" } }),
}));
vi.mock("../components/credit/CreditWorkspace", () => ({
  default: ({ children }: { children: () => ReactNode }) => children(),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setAccessToken(null);
  clearProtectedData();
  setActiveJurisdiction({});
});

function view(children: ReactNode, payload: (url: string) => object) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  setAccessToken("synthetic-token");
  setActiveJurisdiction({ currencyCode: "EUR", locale: "en-US" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        payload(input instanceof Request ? input.url : String(input)),
      ),
    ),
  );
  return {
    ...render(
      <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    ),
    client,
  };
}

it("keeps concentration figures visible and discloses locked Board limits as not evaluated", async () => {
  view(<CreditConcentrationPage />, () => ({
    as_of: "2026-09-30",
    capital_basis: "tier1",
    capital_base_ghs: "1000",
    total_book_ghs: "2500",
    limit_count: 1,
    breaches: [],
    locked_limits: [
      {
        bucket_key: "🔒 Protected data",
        dimension: "single_name",
        limit_kind: "share_of_capital",
        value: "25",
      },
    ],
    dimensions: [
      {
        dimension: "single_name",
        hhi: "1600",
        bucket_count: 1,
        coverage_pct: "100",
        buckets: [
          {
            key: "group:🔒 Protected data",
            loan_count: 2,
            exposure_ghs: "2500",
            share_of_book_pct: "100",
            share_of_capital_pct: "250",
            limit_value: null,
            utilization_pct: null,
            limit_status: "not_set",
          },
        ],
      },
    ],
  }));
  expect(await screen.findByText(/— not evaluated/)).toHaveTextContent("25");
  expect(screen.getByRole("heading", { name: "Locked limits" })).toBeVisible();
  const table = screen.getByRole("table");
  expect(within(table).getByText("250.00%")).toBeVisible();
  expect(within(table).getByText("100.00%")).toBeVisible();
  expect(within(table).getByText("🔒 Protected data")).toBeVisible();
});

const position = {
  id: "synthetic-position",
  source_system: "EXCEL_CSV",
  source_reference: "🔒 Protected data",
  position_type: "LOAN",
  currency: "EUR",
  validation_status: "accepted",
  as_of_date: "2026-09-30",
  snapshot_id: "synthetic-snapshot",
  balance: "1250.5",
  interest_rate: "0.0525",
  rate_type: "FIXED",
  contractual_maturity: "2027-09-30",
  lineage_id: "synthetic-lineage",
};
const lineage = {
  nodes: [
    {
      id: "synthetic-lineage",
      operation_type: "ADAPTER_EXTRACT",
      operation_ref: "🔒 Protected data",
      occurred_at: "2026-09-30T10:00:00Z",
      ingestion_batch_id: "synthetic-batch",
    },
  ],
};

it("renders numeric position cells and expands lineage when identity labels are locked", async () => {
  view(<CanonicalPositionsPage />, (url) =>
    url.includes("/lineage/")
      ? lineage
      : { positions: [position], total: 1, limit: 100, offset: 0 },
  );
  await userEvent.click(await screen.findByText("🔒 Protected data"));
  expect(await screen.findByText("ADAPTER EXTRACT")).toBeVisible();
  expect(screen.getByText("1,250.5")).toBeVisible();
  expect(screen.getByText("5.25%")).toBeVisible();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
});

it("keeps numeric details and the source batch link in the position drawer", async () => {
  const close = vi.fn();
  view(
    <PositionDrawer
      position={CanonicalPositionReadFromJSON(position)}
      onClose={close}
    />,
    () => lineage,
  );
  expect(
    await screen.findByRole("link", { name: "View source batch" }),
  ).toHaveAttribute("href", "/data-engine/batches/synthetic-batch");
  expect(screen.getByText("5.25% · FIXED")).toBeVisible();
  expect(screen.getByText("1,250.5 EUR")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Close details" }));
  expect(close).toHaveBeenCalledOnce();
});

it.each(["revoked", "unavailable", "support"] as const)(
  "renders the %s snapshot refusal instead of trying to render encrypted sections",
  (reason) => {
    render(<SnapshotPreview snapshot={{ _aequoros_protected_data: reason }} />);
    expect(screen.getByText(protectedDataMessages[reason])).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  },
);

it("renders ordinary snapshot totals, units and changed rows", () => {
  render(
    <SnapshotPreview
      snapshot={{
        totals: [{ code: "total", description: "Assets", value: "1250" }],
        sections: [
          {
            code: "BS",
            title: "Balance sheet",
            unit: "thousands",
            rows: [
              {
                code: "cash",
                description: "Cash",
                value: "12.5",
                unit: "pct",
                previous: "10",
              },
            ],
            total: { code: "sum", description: "Total", value: "12.5" },
          },
        ],
      }}
      changedLineKeys={new Set(["BS::cash"])}
      changedNote="Changed since review"
    />,
  );
  expect(screen.getByText("1,250")).toBeVisible();
  expect(screen.getByText("12.5%")).toBeVisible();
  expect(screen.getByText("Changed since review")).toBeVisible();
  expect(screen.getByLabelText("Changed since the last round")).toBeVisible();
  expect(screen.getByText(/GHS '000/)).toBeVisible();
});

it("retains configured named limits as unevaluated through revocation and restores evaluation", async () => {
  let locked = false;
  const { client } = view(<CreditConcentrationPage />, () => ({
    as_of: "2026-09-30",
    capital_basis: "tier1",
    capital_base_ghs: "1000",
    total_book_ghs: "2500",
    limit_count: 1,
    breaches: locked
      ? []
      : [
          {
            key: "group:Synthetic named group",
            exposure_ghs: "2500",
            loan_count: 2,
            share_of_book_pct: "100",
            share_of_capital_pct: "250",
            limit_value: "25",
            limit_kind: "share_of_capital",
            limit_status: "above_limit",
          },
        ],
    locked_limits: locked
      ? [
          {
            dimension: "single_name",
            limit_kind: "share_of_capital",
            value: "25",
            limit_status: "locked",
          },
        ]
      : [],
    dimensions: [
      {
        dimension: "single_name",
        hhi: "1600",
        bucket_count: 1,
        coverage_pct: "100",
        buckets: [
          {
            key: locked
              ? "group:🔒 Protected data"
              : "group:Synthetic named group",
            exposure_ghs: "2500",
            loan_count: 2,
            share_of_book_pct: "100",
            share_of_capital_pct: "250",
            limit_value: locked ? null : "25",
            limit_kind: "share_of_capital",
            limit_status: locked ? "locked" : "above_limit",
            utilization_pct: locked ? null : "1000",
          },
        ],
      },
    ],
  }));
  expect(await screen.findByText("Synthetic named group")).toBeVisible();
  expect(screen.getByText("Above limit")).toBeVisible();
  locked = true;
  await act(async () => {
    await client.invalidateQueries();
  });
  expect(
    await screen.findByRole("heading", { name: "Locked limits" }),
  ).toBeVisible();
  expect(screen.getByText("Not fully evaluated")).toBeVisible();
  expect(screen.getByText("Locked · not evaluated")).toBeVisible();
  expect(screen.queryByText("Synthetic named group")).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("table")).queryByText("Not set"),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/— not evaluated/)).toHaveTextContent("25");
  locked = false;
  await act(async () => {
    await client.invalidateQueries();
  });
  expect(await screen.findByText("Synthetic named group")).toBeVisible();
  expect(screen.getByText("Above limit")).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Locked limits" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Not fully evaluated")).not.toBeInTheDocument();
});

it("renders the support comparison lock from the generated contract without exposing diff content", () => {
  const side = {
    package_id: "synthetic-package",
    version: 1,
    status: "generated",
    reporting_date: "2026-03-31",
    basis: "solo",
    generated_at: "2026-04-01T00:00:00Z",
    snapshot_sha256: null,
    content_digest: null,
  };
  const comparison = PackageComparisonReadFromJSON({
    base: side,
    target: { ...side, version: 2 },
    content_locked: true,
    identical: null,
    changed_count: null,
    added_count: null,
    removed_count: null,
    unchanged_section_count: null,
    sections: [
      {
        code: "exposures",
        title: "Private exposures",
        origin: "section",
        change: "changed",
        unchanged_line_count: 0,
        lines: [
          {
            code: "1",
            description: "Private borrower",
            change: "changed",
            base_value: "1234567",
            target_value: "7654321",
            delta: "6419754",
            delta_pct: "520",
            is_total: false,
          },
        ],
      },
    ],
  });
  render(<ComparisonPanel comparison={comparison} currentVersion={2} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    protectedDataMessages.support,
  );
  expect(screen.getByText("Protected data locked")).toBeVisible();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText("Private borrower")).toBeNull();
  expect(screen.queryByText("1234567")).toBeNull();
  expect(screen.queryByText(/No figure differs/)).toBeNull();
});
