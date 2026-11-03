// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  BiInsightsReadFromJSON,
  BiPackReadFromJSON,
  BiDashboardReadFromJSON,
  BiCatalogueReadFromJSON,
} from "@aequoros/risk-service-api";
import WidgetRenderer from "../features/bi/components/WidgetRenderer";
import NeedsDataWidget from "../features/bi/components/NeedsDataWidget";
import InsightStrip from "../features/bi/components/InsightStrip";
import DashboardCanvas from "../features/bi/components/DashboardCanvas";
import ExportActions from "../features/bi/components/ExportActions";
import ExplainDrawer from "../features/bi/components/ExplainDrawer";
import ExplorePage from "../app/(app)/explore/page";
import DriverWaterfall from "../components/stress/charts/DriverWaterfall";
import {
  packWidgetView,
  packView,
  savedDashboardView,
  useDashboard,
  useDashboardList,
} from "../features/bi/components/dashboards";
import { namedDataset } from "../features/bi/components/labels";
import { WHOLE_BOOK } from "../lib/api/dataScope";
import type { BiWidgetSpec } from "../features/bi/components/types";
import type { EnterpriseStressRead } from "../components/stress/types";
import {
  providers,
  json,
  query,
  queryWire,
  result,
  refusal,
  catalogueWire,
} from "./biTestSupport";

const chart = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  init: vi.fn(),
  registerTheme: vi.fn(),
}));
vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: chart.init,
  registerTheme: chart.registerTheme,
}));
vi.mock("next-auth/react", () => ({ getSession: async () => null }));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-test" },
    period: { periodEnd: new Date("2026-09-30") },
    authorityPending: false,
    institutionCapabilities: [
      {
        module: "credit",
        sensitivity: "aggregated",
        permission: "view",
        requiresContextualAuthorization: false,
        dataScope: { kind: "branch", branches: ["B001"], regions: [] },
      },
    ],
  }),
  useInstitutionType: () => null,
}));
vi.mock("next/dynamic", async () => {
  const R = await import("react");
  return {
    default: (
      loader: Parameters<typeof R.lazy>[0],
      options: { loading?: React.ComponentType },
    ) => {
      const Canvas = R.lazy(loader);
      const Loading = options?.loading;
      return (props: object) => (
        <R.Suspense fallback={Loading ? <Loading /> : null}>
          <Canvas {...props} />
        </R.Suspense>
      );
    },
  };
});
beforeEach(() => {
  vi.stubGlobal("React", React);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  chart.setOption.mockClear();
  chart.init.mockClear();
  chart.registerTheme.mockClear();
  chart.init.mockReturnValue(chart);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const spec: BiWidgetSpec = {
  id: "loans",
  title: "SECRET TITLE",
  subtitle: "SECRET CAPTION",
  kind: "line",
  query,
  dataset: null,
  layout: { i: "loans", x: 0, y: 0, w: 6, h: 4 },
};
const coverage = {
  ...WHOLE_BOOK,
  qualified: true,
  emptyMeaning: "No rows in the branches your access covers.",
};

it.each([
  "bi_authorization_denied",
  "bi_insights_authorization_denied",
  "bi_export_denied",
])("a grant denial %s reveals no widget or refused member", (code) => {
  const { container } = render(
    <WidgetRenderer
      spec={spec}
      result={result([["SECRET BRANCH", "1234"]])}
      isLoading={false}
      error={refusal(code)}
    />,
  );
  expect(screen.getByText("Access restricted")).toBeTruthy();
  expect(container.textContent).not.toMatch(/SECRET|1234|bi_authorization/);
  expect(screen.queryByRole("img")).toBeNull();
  expect(chart.init).not.toHaveBeenCalled();
});

it("other refusals use the platform sentence in both widget and insight surfaces", () => {
  const error = refusal(
    "bi_human_principal_required",
    "Impersonation sessions are read-only.",
  );
  const { container } = render(
    <>
      <WidgetRenderer
        spec={spec}
        result={null}
        isLoading={false}
        error={error}
      />
      <InsightStrip data={null} isLoading={false} error={error} />
    </>,
  );
  expect(screen.getAllByText(error.message)).toHaveLength(2);
  expect(container.textContent).not.toMatch(
    /Access restricted|SECRET|bi_human_principal_required|can grant/,
  );
});

it.each([{ rows: [] }, { rows: [["B001", null]] }])(
  "missing readings never initialize a chart or draw a table (%j)",
  ({ rows }) => {
    render(
      <WidgetRenderer
        spec={spec}
        result={result(rows)}
        error={null}
        isLoading={false}
        coverage={coverage}
      />,
    );
    expect(screen.getByText("No figure for this date")).toBeTruthy();
    expect(screen.getByText(coverage.emptyMeaning!)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(chart.init).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /template/i })).toBeNull();
  },
);

it("only a named gap offers its Data Engine template", () => {
  const dataset = namedDataset("positions")!;
  render(<NeedsDataWidget dataset={dataset} coverage={coverage} />);
  expect(screen.getByText(`Needs data: ${dataset.label}`)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /template/i }).getAttribute("href"),
  ).toBe(dataset.href);
  expect(screen.getByText(coverage.emptyMeaning!)).toBeTruthy();
});

it("zero is a measured point while null stays a gap and an unmeasured series says so", async () => {
  const answer = result([
    ["B001", null, "0"],
    ["B002", null, "25"],
  ]);
  answer.columns.push({
    ...answer.columns[1],
    id: "other",
    label: "Other balance",
  });
  render(
    <WidgetRenderer
      spec={spec}
      result={answer}
      error={null}
      isLoading={false}
    />,
  );
  await act(async () => {
    await vi.dynamicImportSettled();
  });
  await screen.findByRole("img");
  await waitFor(() => expect(chart.setOption).toHaveBeenCalled());
  const option = chart.setOption.mock.calls.at(-1)![0];
  expect(option.series).toMatchObject([
    {
      name: "Loan balance (not measured)",
      data: [null, null],
      connectNulls: false,
    },
    { name: "Other balance", data: [0, 25], connectNulls: false },
  ]);
});

it("an empty insight strip distinguishes read figures from no computed figures", () => {
  const data = BiInsightsReadFromJSON({
    as_of: "2026-09-30",
    insights: [],
    measures_read: 1,
  });
  const { rerender } = render(<InsightStrip data={data} isLoading={false} />);
  expect(
    screen.getByText("Nothing stands out for this reporting date."),
  ).toBeTruthy();
  rerender(
    <InsightStrip data={{ ...data, measuresRead: 0 }} isLoading={false} />,
  );
  expect(
    screen.getByText(/No analytics figures have been computed/),
  ).toBeTruthy();
  expect(
    screen.queryByText("Nothing stands out for this reporting date."),
  ).toBeNull();
  expect(chart.init).not.toHaveBeenCalled();
});

it("pack and saved adapters consume no refused content and preserve geometry and counts", () => {
  const denied = BiPackReadFromJSON({
    id: "board",
    title: "Board",
    widgets: [{ id: "locked", access: "restricted", layout: spec.layout }],
  }).widgets[0];
  for (const key of ["title", "caption", "query", "panel", "needsData"])
    Object.defineProperty(denied, key, {
      get: () => {
        throw new Error(`read refused ${key}`);
      },
    });
  const adapted = packWidgetView(denied);
  expect(adapted).toEqual({
    state: "restricted",
    id: "locked",
    layout: spec.layout,
  });
  const pack = packView({
    ...BiPackReadFromJSON({ id: "board", title: "Board", widgets: [] }),
    widgets: [denied],
  });
  const saved = savedDashboardView({
    ...BiDashboardReadFromJSON({ id: "saved", title: "Saved", widgets: [] }),
    widgets: [denied],
  });
  expect(pack.restrictedWidgets).toBe(1);
  expect(saved.restrictedWidgets).toBe(1);
  render(
    <DashboardCanvas
      bankId="BK-test"
      widgets={saved.widgets}
      filters={[]}
      catalogue={undefined}
    />,
  );
  expect(screen.getByText("Access restricted")).toBeTruthy();
  expect(chart.init).not.toHaveBeenCalled();
});

it("resolved server packs drive both the list and canvas, without inventing a gap cause", async () => {
  const payload = {
    id: "server_pack",
    title: "Server-only title",
    version: "v3",
    widgets: [
      {
        id: "gap",
        title: "New server figure",
        access: "allowed",
        kind: "line",
        layout: spec.layout,
        query: queryWire,
      },
    ],
  };
  const fetch = vi.fn(async (url: string) =>
    json(
      new URL(url).pathname.endsWith("/packs") ? { packs: [payload] } : payload,
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const p = providers();
  function Probe() {
    const list = useDashboardList("BK-test", "2026-09-30");
    const opened = useDashboard("BK-test", "server_pack", "2026-09-30");
    return (
      <pre>
        {JSON.stringify({ list: list.dashboards, opened: opened.dashboard })}
      </pre>
    );
  }
  const view = render(<Probe />, p);
  await waitFor(() =>
    expect(view.container.textContent).toContain('"dataset":null'),
  );
  expect(view.container.textContent).toContain("Server-only title");
  expect(fetch.mock.calls.map(([url]) => new URL(url).pathname).sort()).toEqual(
    [
      "/api/v1/banks/BK-test/bi/packs",
      "/api/v1/banks/BK-test/bi/packs/server_pack",
    ],
  );
  for (const [url] of fetch.mock.calls)
    expect(new URL(url).searchParams.get("as_of")).toBe("2026-09-30");
  p.client.clear();
});

it("the rendered table carries the submitted question into its exact drill link", () => {
  render(
    <WidgetRenderer
      spec={{ ...spec, kind: "table" }}
      result={result([["B001", "400"]])}
      error={null}
      isLoading={false}
      query={query}
    />,
  );
  const link = screen.getByRole("link", { name: /Loan Book.*B001/i });
  expect(link.getAttribute("href")).toBe(
    "/credit/book?branch=B001&as_of=2026-09-30",
  );
});

it("a dashboard figure carries its resolved date and narrowing into the visible drill action", async () => {
  const fetch = vi.fn(async () =>
    json({
      columns: result().columns.map((column) => ({
        id: column.id,
        member_id: column.memberId,
        label: column.label,
        kind: column.kind,
        format: column.format,
      })),
      rows: [["B001", "400"]],
      truncated: false,
    }),
  );
  vi.stubGlobal("fetch", fetch);
  const p = providers();
  render(
    <DashboardCanvas
      bankId="BK-test"
      widgets={[
        {
          state: "figure",
          id: spec.id,
          layout: spec.layout,
          spec: { ...spec, kind: "table" },
        },
      ]}
      filters={[{ member: "branch.code", op: "eq", values: ["B001"] }]}
      catalogue={BiCatalogueReadFromJSON(catalogueWire)}
    />,
    p,
  );
  const link = await screen.findByRole("link", { name: /Loan Book.*B001/i });
  expect(link.getAttribute("href")).toBe(
    "/credit/book?branch=B001&as_of=2026-09-30",
  );
  const call = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(JSON.parse(String(call[1].body))).toMatchObject({
    ...queryWire,
    filters: [{ member: "branch.code", op: "eq", values: ["B001"] }],
  });
  p.client.clear();
});

it.each([false, true])(
  "Explore carries the chosen query into drill or a scoped empty state (empty=%s)",
  async (empty) => {
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("/catalogue")) return json(catalogueWire);
      if (url.endsWith("/measures")) return json({ measures: [] });
      return json({
        columns: [
          {
            id: "branch",
            member_id: "branch.code",
            label: "Branch",
            kind: "dimension",
            format: "text",
          },
          {
            id: "balance",
            member_id: "loans.balance_rc",
            label: "Loan balance",
            kind: "measure",
            format: "amount",
          },
        ],
        rows: empty ? [] : [["B001", "400"]],
        truncated: false,
      });
    });
    const p = providers();
    render(<ExplorePage />, p);
    fireEvent.click(
      await screen.findByRole("checkbox", { name: /Loan balance/ }),
    );
    fireEvent.click(await screen.findByRole("checkbox", { name: /Branch/ }));
    if (empty) {
      await screen.findByText("No figure for this date");
      expect(
        screen.getByText(
          /Nothing was returned for the branches your access covers/,
        ),
      ).toBeTruthy();
      expect(screen.queryByRole("link", { name: /template/i })).toBeNull();
    } else {
      const link = await screen.findByRole("link", {
        name: /Loan Book.*B001/i,
      });
      expect(link.getAttribute("href")).toBe(
        "/credit/book?branch=B001&as_of=2026-09-30",
      );
    }
    p.client.clear();
  },
);

it.each(["csv", "xlsx", "pdf"])(
  "the %s menu releases only the governed response and cleans its URL",
  async (format) => {
    const fetch = vi.fn(
      async () =>
        new Response("artifact bytes", {
          headers: {
            "Content-Disposition": `attachment; filename="server-choice.${format}"`,
            "X-Bi-Export-Class": "summary",
          },
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const createURL = vi.fn(() => "blob:artifact");
    const revoke = vi.fn();
    URL.createObjectURL = createURL;
    URL.revokeObjectURL = revoke;
    let download: { href: string; filename: string } | undefined;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      download = { href: this.href, filename: this.download };
    });
    const p = providers();
    const view = render(<ExportActions bankId="BK-test" query={query} />, p);
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    const labels = {
      csv: "Comma-separated values",
      xlsx: "Excel workbook",
      pdf: "Watermarked PDF",
    };
    fireEvent.click(
      screen.getByRole("menuitem", {
        name: new RegExp(labels[format as keyof typeof labels]),
      }),
    );
    await waitFor(() =>
      expect(download).toEqual({
        href: "blob:artifact",
        filename: `server-choice.${format}`,
      }),
    );
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(new URL(url).pathname).toBe("/api/v1/banks/BK-test/bi/export");
    expect(JSON.parse(String(init.body))).toEqual({ query: queryWire, format });
    expect(createURL).toHaveBeenCalledOnce();
    view.unmount();
    expect(revoke).toHaveBeenCalledWith("blob:artifact");
    p.client.clear();
  },
);

it("a refused export reveals no members and no download, while print is explicit", async () => {
  vi.stubGlobal("fetch", async () =>
    json(
      {
        detail: {
          error_code: "bi_export_denied",
          message: "SECRET MEMBER",
          members: ["SECRET MEMBER"],
        },
      },
      403,
    ),
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const print = vi.spyOn(window, "print").mockImplementation(() => {});
  const p = providers();
  const { container } = render(
    <ExportActions bankId="BK-test" query={query} />,
    p,
  );
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  fireEvent.click(
    screen.getByRole("menuitem", { name: /Comma-separated values/ }),
  );
  await screen.findByText(
    /Your access does not cover everything this export needs/,
  );
  expect(container.textContent).not.toContain("SECRET MEMBER");
  expect(click).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  fireEvent.click(
    screen.getByRole("menuitem", { name: /Print or save as PDF/ }),
  );
  expect(print).toHaveBeenCalledOnce();
  p.client.clear();
});

it.each(["bi_authorization_denied", "bi_human_principal_required"])(
  "the provenance request renders the correct refusal family: %s",
  async (code) => {
    vi.stubGlobal("fetch", async () =>
      json(
        {
          detail: {
            error_code: code,
            message: "Server-authored refusal.",
            members: ["SECRET MEMBER"],
          },
        },
        403,
      ),
    );
    const p = providers();
    const view = render(
      <ExplainDrawer
        bankId="BK-test"
        measure="loans.balance_rc"
        query={query}
        onClose={() => {}}
      />,
      p,
    );
    await screen.findByText(
      code === "bi_authorization_denied"
        ? "Access restricted"
        : "Server-authored refusal.",
    );
    expect(view.container.textContent).not.toContain("SECRET MEMBER");
    p.client.clear();
  },
);

const stressRun = () => ({
  appendix_ii: {
    table1_summary: {
      car_target_pct: "10",
      pre_adverse: [{ total_regulatory_capital: "30", total_rwa: "100" }],
      post_adverse: [{ total_regulatory_capital: "25", total_rwa: "110" }],
      impact_of_adverse: [],
    },
    table3_profit_and_loss: [
      { label: "base_1", impairment_losses: "1" },
      { label: "stress_1", impairment_losses: "3" },
    ],
  },
});
it.each([
  "baseCapital",
  "stressCapital",
  "baseRwa",
  "stressRwa",
  "baseLoss",
  "stressLoss",
  "noBaseLoss",
  "noStressLoss",
  "requirement",
])(
  "a missing stress bridge input %s cannot become a measured zero",
  (missing) => {
    const run = stressRun();
    const t1 = run.appendix_ii.table1_summary;
    if (missing === "baseCapital")
      Object.assign(t1.pre_adverse[0], { total_regulatory_capital: null });
    if (missing === "stressCapital")
      Object.assign(t1.post_adverse[0], { total_regulatory_capital: null });
    if (missing === "baseRwa")
      Object.assign(t1.pre_adverse[0], { total_rwa: null });
    if (missing === "stressRwa")
      Object.assign(t1.post_adverse[0], { total_rwa: null });
    if (missing === "baseLoss")
      Object.assign(run.appendix_ii.table3_profit_and_loss[0], {
        impairment_losses: null,
      });
    if (missing === "stressLoss")
      Object.assign(run.appendix_ii.table3_profit_and_loss[1], {
        impairment_losses: null,
      });
    if (missing.startsWith("no"))
      run.appendix_ii.table3_profit_and_loss =
        run.appendix_ii.table3_profit_and_loss.filter(
          (row) =>
            row.label !== (missing === "noBaseLoss" ? "base_1" : "stress_1"),
        );
    if (missing === "requirement") Object.assign(t1, { car_target_pct: null });
    render(<DriverWaterfall run={run as unknown as EnterpriseStressRead} />);
    expect(screen.getByText(/headroom bridge cannot be derived/)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(chart.init).not.toHaveBeenCalled();
  },
);

it("complete stress inputs produce the real scaled headroom bridge", async () => {
  render(
    <DriverWaterfall run={stressRun() as unknown as EnterpriseStressRead} />,
  );
  await act(async () => {
    await vi.dynamicImportSettled();
  });
  await screen.findByRole("img");
  await waitFor(() => expect(chart.setOption).toHaveBeenCalled());
  const option = chart.setOption.mock.calls.at(-1)![0];
  expect(option.xAxis.data).toEqual([
    "Base headroom",
    "Credit losses",
    "Earnings / FX / tax",
    "RWA migration",
    "Stress headroom",
  ]);
  expect(option.series[0].data).toEqual([0, 18000, 15000, 14000, 0]);
  expect(
    option.series[1].data.map((point: { value: number }) => point.value),
  ).toEqual([20000, 2000, 3000, 1000, 14000]);
});
