// @vitest-environment jsdom
import React, { type ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import {
  createGrid,
  themeQuartz,
  type IGetRowsParams,
  type ValueFormatterParams,
  type ValueGetterParams,
  type GridApi,
} from "ag-grid-community";
import type { AgGridReactProps } from "ag-grid-react";
import type { GridLayoutProps } from "react-grid-layout";
import PivotGrid from "../features/bi/components/PivotGrid";
import PivotGridCanvas from "../features/bi/components/PivotGridCanvas";
import BuilderGrid from "../features/bi/components/BuilderGrid";
import { formatCell } from "../features/bi/components/gridCells";
import { readChartTokens } from "../features/bi/components/echartsTheme";
import { providers, json, query, queryWire, columns } from "./biTestSupport";

const boundary = vi.hoisted(() => ({
  grid: undefined as AgGridReactProps | undefined,
  layout: undefined as GridLayoutProps | undefined,
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: (props: AgGridReactProps) => {
    boundary.grid = props;
    return <div role="grid" />;
  },
}));
vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ width: 1080, containerRef: { current: null } }),
  GridLayout: (props: GridLayoutProps & { children: ReactNode }) => {
    boundary.layout = props;
    return <div>{props.children}</div>;
  },
}));
vi.mock("next-auth/react", () => ({ getSession: async () => null }));
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
  boundary.grid = undefined;
  boundary.layout = undefined;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("style");
});

const page = (truncated = true) => ({
  columns,
  rows: [
    { values: { branch: "B001", balance: "400" }, level: 1, subtotal: false },
    { values: { branch: "B002", balance: "0" }, level: 1, subtotal: false },
    { values: { branch: null, balance: "400" }, level: 0, subtotal: true },
  ],
  dimension_count: 1,
  last_row: truncated ? null : 3,
  truncated,
  used_aggregate: true,
  start_row: 0,
});

it("the deferred grid uses the server cap, closes both export paths and pages through the real transport", async () => {
  let opening: (value: Response) => void = () => {};
  const fetch = vi.fn((url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    return body.start_row === 0
      ? new Promise<Response>((resolve) => {
          opening = resolve;
        })
      : Promise.resolve(json({ ...page(false), start_row: 3, last_row: 6 }));
  });
  vi.stubGlobal("fetch", fetch);
  const p = providers();
  render(<PivotGrid bankId="BK-test" query={query} dataset={null} />, p);
  expect(screen.getByLabelText("Preparing the grid")).toBeTruthy();
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(screen.queryByRole("grid")).toBeNull();
  await act(async () => opening(json(page())));
  await screen.findByRole("grid");
  expect(boundary.grid).toMatchObject({
    suppressCsvExport: true,
    suppressExcelExport: true,
    rowModelType: "infinite",
    cacheBlockSize: 3,
    infiniteInitialRowCount: 3,
  });
  expect(JSON.parse(String(fetch.mock.calls[0][1].body))).toEqual({
    query: queryWire,
    start_row: 0,
  });
  expect(screen.getByRole("status").textContent).toMatch(/3|first|cap/i);
  const success = vi.fn();
  const fail = vi.fn();
  const params = {
    startRow: 0,
    endRow: 3,
    sortModel: [],
    filterModel: {},
    successCallback: success,
    failCallback: fail,
    context: null,
    api: {} as GridApi,
  } satisfies IGetRowsParams;
  await act(async () => boundary.grid!.datasource!.getRows(params));
  expect(fetch).toHaveBeenCalledOnce();
  expect(success.mock.calls[0][0]).toMatchObject([
    { values: { branch: "B001", balance: "400" }, subtotal: false },
    {},
    { level: 0, subtotal: true },
  ]);
  await act(async () =>
    boundary.grid!.datasource!.getRows({
      ...params,
      startRow: 3,
      endRow: 6,
      sortModel: [{ colId: "branch", sort: "desc" }],
    }),
  );
  await waitFor(() => expect(success).toHaveBeenCalledTimes(2));
  expect(new URL(fetch.mock.calls[1][0]).pathname).toBe(
    "/api/v1/banks/BK-test/bi/grid",
  );
  expect(JSON.parse(String(fetch.mock.calls[1][1].body))).toEqual({
    query: {
      ...queryWire,
      sort: [{ member: "branch.code", direction: "desc" }],
    },
    start_row: 3,
    end_row: 6,
  });
  expect(fail).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  p.client.clear();
});

it("the registered Community grid can read infinite rows but cannot export", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const api = createGrid(host, {
    rowModelType: "infinite",
    columnDefs: [],
    datasource: { getRows: (params) => params.successCallback([], 0) },
  });
  try {
    expect(api.isModuleRegistered("InfiniteRowModelModule")).toBe(true);
    expect(api.isModuleRegistered("CsvExportModule")).toBe(false);
    expect(api.isModuleRegistered("ExcelExportModule")).toBe(false);
  } finally {
    api.destroy();
    host.remove();
  }
});

it("every rendered value formatter preserves absent values and uses the shared format", async () => {
  const formats = [
    "text",
    "amount",
    "pct",
    "fraction",
    "index",
    "duration_years",
    "count",
    "int",
    "date",
    "flag",
  ];
  const allColumns = formats.map((format, i) => ({
    id: String(i),
    label: format,
    kind: "measure",
    format,
  }));
  vi.stubGlobal("fetch", async () =>
    json({ ...page(false), columns: allColumns }),
  );
  const p = providers();
  render(<PivotGridCanvas bankId="BK-test" query={query} dataset={null} />, p);
  await screen.findByRole("grid");
  for (const [i, definition] of boundary.grid!.columnDefs!.entries()) {
    if (
      !("valueFormatter" in definition) ||
      typeof definition.valueFormatter !== "function" ||
      typeof definition.valueGetter !== "function"
    )
      throw new Error("Grid did not supply executable cell formatting");
    for (const value of [
      null,
      undefined,
      0,
      "12.5",
      "2026-09-30",
      false,
      "not-a-number",
    ]) {
      expect(definition.valueFormatter({ value } as ValueFormatterParams)).toBe(
        formatCell(value, formats[i]),
      );
    }
    expect(
      definition.valueGetter({ data: { values: {} } } as ValueGetterParams),
    ).toBeNull();
  }
  p.client.clear();
});

it("grid theme consumes resolved chart tokens and refreshes when the document palette changes", async () => {
  const theme = vi.spyOn(themeQuartz, "withParams");
  vi.stubGlobal("fetch", async () => json(page(false)));
  document.documentElement.style.setProperty("--surface-raised", "10 20 30");
  const p = providers();
  render(<PivotGridCanvas bankId="BK-test" query={query} dataset={null} />, p);
  await screen.findByRole("grid");
  const tokens = readChartTokens();
  expect(theme.mock.calls.at(-1)![0]).toMatchObject({
    backgroundColor: tokens.surface,
    accentColor: tokens.accent,
    foregroundColor: tokens.text,
    borderColor: tokens.border,
  });
  const previous = boundary.grid!.theme;
  await act(async () =>
    document.documentElement.style.setProperty("--surface-raised", "40 50 60"),
  );
  await waitFor(() => expect(boundary.grid!.theme).not.toBe(previous));
  expect(theme.mock.calls.at(-1)![0]).toMatchObject({
    backgroundColor: readChartTokens().surface,
  });
  p.client.clear();
});

it.each(["bi_authorization_denied", "bi_human_principal_required"])(
  "the grid's real failed read distinguishes refusal %s",
  async (code) => {
    vi.stubGlobal("fetch", async () =>
      json(
        {
          detail: {
            error_code: code,
            message: "Server-authored refusal.",
            members: ["SECRET"],
          },
        },
        403,
      ),
    );
    const p = providers();
    const view = render(
      <PivotGridCanvas bankId="BK-test" query={query} dataset={null} />,
      p,
    );
    await screen.findByText(
      code === "bi_authorization_denied"
        ? "Access restricted"
        : "Server-authored refusal.",
    );
    expect(screen.queryByRole("grid")).toBeNull();
    expect(view.container.textContent).not.toContain("SECRET");
    p.client.clear();
  },
);

it("an empty grid states scoped absence and does not invent a dataset", async () => {
  vi.stubGlobal("fetch", async () => json({ ...page(false), rows: [] }));
  const p = providers();
  render(
    <PivotGridCanvas
      bankId="BK-test"
      query={query}
      dataset={null}
      coverage={{
        qualified: true,
        determined: true,
        pending: false,
        statement: null,
        detail: null,
        correction: null,
        suffix: null,
        hint: null,
        emptyMeaning: "No rows in your branches.",
      }}
    />,
    p,
  );
  await screen.findByText("No figure for this date");
  expect(screen.getByText("No rows in your branches.")).toBeTruthy();
  expect(screen.queryByRole("grid")).toBeNull();
  expect(screen.queryByRole("link", { name: /template/i })).toBeNull();
  p.client.clear();
});

it("the deferred builder transmits only changed geometry and keeps tile controls reachable", async () => {
  const onLayoutChange = vi.fn();
  const click = vi.fn();
  render(
    <BuilderGrid
      layout={[{ i: "tile", x: 0, y: 0, w: 6, h: 4 }]}
      rowHeight={76}
      items={[
        { id: "tile", node: <button onClick={click}>Tile control</button> },
      ]}
      onLayoutChange={onLayoutChange}
    />,
  );
  expect(screen.getByLabelText("Preparing the canvas")).toBeTruthy();
  const button = await screen.findByRole("button", { name: "Tile control" });
  button.click();
  expect(click).toHaveBeenCalledOnce();
  expect(boundary.layout).toMatchObject({
    gridConfig: { cols: 12, rowHeight: 76 },
    dragConfig: { handle: ".bi-builder-grip" },
  });
  await act(async () =>
    boundary.layout!.onLayoutChange!([
      { i: "tile", x: 3, y: 2, w: 4, h: 5, static: false, minW: 2 },
    ]),
  );
  expect(onLayoutChange).toHaveBeenCalledWith([
    { i: "tile", x: 3, y: 2, w: 4, h: 5 },
  ]);
});
