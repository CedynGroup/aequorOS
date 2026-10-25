// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React, { Suspense, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  BiCatalogueReadFromJSON,
  BiDashboardReadFromJSON,
} from "@aequoros/risk-service-api";
import NewDashboardPage from "../app/(app)/dashboards/new/page";
import EditDashboardPage from "../app/(app)/dashboards/[id]/edit/page";
import BiAlertsPage from "../app/(app)/explore/alerts/page";
import ProtectedDataNotice from "../components/ui/ProtectedDataNotice";
import { useInstitutionProfile } from "../lib/api/hooks";
import { useBiSavedDashboard } from "../lib/api/bi";
import { biCatalogueKey, biDashboardKey } from "../lib/api/biKeys";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { setAccessToken } from "../lib/api/token";
import {
  bindProtectedDataCache,
  clearProtectedData,
  observeProtectedData,
  protectedDataState,
} from "../lib/api/protectedData";
import { catalogueWire, json, providers, queryWire } from "./biTestSupport";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  notFound: () => {
    throw new Error("Not found");
  },
  usePathname: () => "/dashboards",
}));
vi.mock("next-auth/react", () => ({ getSession: async () => null }));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-test" },
    period: { periodEnd: new Date("2026-09-30") },
    institutionCapabilities: [],
    authorityPending: false,
  }),
  useInstitutionType: () => null,
}));
vi.mock("next/dynamic", async () => {
  const R = await import("react");
  return {
    default: (loader: Parameters<typeof R.lazy>[0]) => {
      const Component = R.lazy(loader);
      return (props: object) => (
        <R.Suspense fallback={null}>
          <Component {...props} />
        </R.Suspense>
      );
    },
  };
});

const BANK = "BK-test";
const ID = "00000000-0000-4000-8000-000000000001";
const PROTECTED_ID = "00000000-0000-4000-8000-000000000002";
const DAY = "2026-09-30";
const scope = queryAuthorityScope("OR-test", "reader@example.com", 1);
const aggregateMembers = [
  "counterparty.type",
  "counterparty.country",
  "counterparty.rating",
];
const catalogue = {
  ...catalogueWire,
  dimensions: [
    ...catalogueWire.dimensions,
    ...aggregateMembers.map((id) => ({
      id,
      label: id,
      description: id,
      module: "credit",
      sensitivity: "aggregated",
      value_type: "text",
      values: [],
    })),
  ],
  measures: catalogueWire.measures.map((measure) => ({
    ...measure,
    thresholds_source: "credit.limit",
    allowed_dimensions: [...measure.allowed_dimensions, ...aggregateMembers],
  })),
};
const dashboard = {
  id: ID,
  title: "Saved numeric dashboard",
  description: "Numeric management view",
  version: 1,
  badge: "personal",
  owned_by_caller: true,
  owner_display_name: "Maker",
  owner_user_id: ID,
  visibility: "private",
  visibility_role: null,
  source_pack: null,
  access: "granted",
  message: "",
  catalogue_version: "v1",
  as_of: DAY,
  created_at: `${DAY}T00:00:00Z`,
  updated_at: `${DAY}T00:00:00Z`,
  widgets: [
    {
      id: "loans",
      title: "Saved loan view",
      kind: "table",
      access: "granted",
      query: {
        ...queryWire,
        filters: [{ member: "branch.code", op: "eq", values: ["B001"] }],
      },
      layout: { i: "loans", x: 0, y: 0, w: 6, h: 4 },
    },
  ],
};
const protectedKey = biDashboardKey(scope, BANK, PROTECTED_ID, DAY);
const numericKey = biDashboardKey(scope, BANK, ID, DAY);
const catalogueKey = biCatalogueKey(scope, BANK);
const disposals: (() => void)[] = [];

beforeEach(() => {
  setAccessToken("synthetic-session");
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  clearProtectedData();
  setAccessToken(null);
  vi.unstubAllGlobals();
});

function lockedResponse(reason: string) {
  return jsonWithNotice(
    {
      bank_id: BANK,
      profile: null,
      related_parties: [],
      name_history: [],
      outlets: [],
      products: [],
      licenses: [],
    },
    reason,
  );
}
function jsonWithNotice(body: unknown, reason: string) {
  return Response.json(body, {
    headers: {
      "X-Aequoros-Protected-Data": JSON.stringify({ [BANK]: reason }),
    },
  });
}
function ProfileReader() {
  useInstitutionProfile(BANK);
  return <p>Institution screen</p>;
}
function ProtectedTitle() {
  const query = useBiSavedDashboard(BANK, PROTECTED_ID, DAY, false);
  return <p>{query.data?.title}</p>;
}

function setup(numericDashboard = dashboard) {
  const p = providers();
  p.client.setDefaultOptions({
    queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
  });
  const unbind = bindProtectedDataCache(p.client);
  disposals.push(() => {
    unbind();
    p.client.clear();
  });
  p.client.setQueryData(catalogueKey, BiCatalogueReadFromJSON(catalogue));
  p.client.setQueryData(numericKey, BiDashboardReadFromJSON(numericDashboard));
  p.client.setQueryData(
    protectedKey,
    BiDashboardReadFromJSON({
      ...dashboard,
      id: PROTECTED_ID,
      title: "Protected saved title",
      widgets: [
        {
          ...dashboard.widgets[0],
          query: {
            ...queryWire,
            filters: [
              {
                member: "counterparty.name",
                op: "eq",
                values: ["Synthetic private borrower"],
              },
            ],
          },
        },
      ],
    }),
  );
  return p;
}

it.each(
  (["create", "edit", "alert"] as const).flatMap((surface) =>
    (surface === "edit"
      ? ["branch.code", ...aggregateMembers]
      : ["branch.code"]
    ).flatMap((dimension) =>
      (["unavailable", "revoked"] as const).map((reason) => ({
        surface,
        dimension,
        reason,
      })),
    ),
  ),
)(
  "keeps the $surface draft using $dimension when a navigated-away read returns $reason",
  async ({ surface, dimension, reason }) => {
    const numericDashboard = {
      ...dashboard,
      widgets: dashboard.widgets.map((widget) => ({
        ...widget,
        query: { ...widget.query, dimensions: [dimension] },
      })),
    };
    let finishRead: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/institution-profile"))
          return new Promise<Response>((resolve) => {
            finishRead = resolve;
          });
        if (url.endsWith("/catalogue")) return json(catalogue);
        if (url.endsWith("/feature-flags"))
          return json({ bi_alerts_enabled: true });
        if (url.endsWith("/bi/alerts")) return json({ alerts: [] });
        if (url.includes(`/bi/dashboards/${ID}`)) return json(numericDashboard);
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    const p = setup(numericDashboard);
    const otherKey = biDashboardKey(scope, "BK-other", PROTECTED_ID, DAY);
    p.client.setQueryData(otherKey, { title: "Other bank private title" });
    const params = Promise.resolve({ id: ID });
    await params;
    function Screens() {
      const [editor, setEditor] = useState(false);
      return (
        <>
          <ProtectedDataNotice />
          <ProtectedTitle />
          <button onClick={() => setEditor(true)}>Open editor</button>
          {!editor ? (
            <ProfileReader />
          ) : surface === "create" ? (
            <NewDashboardPage />
          ) : surface === "edit" ? (
            <Suspense fallback={null}>
              <EditDashboardPage params={params} />
            </Suspense>
          ) : (
            <BiAlertsPage />
          )}
        </>
      );
    }
    render(<Screens />, { wrapper: p.wrapper });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => {
      await userEvent.click(
        screen.getByRole("button", { name: "Open editor" }),
      );
    });
    if (surface === "alert") {
      await userEvent.click(
        await screen.findByRole("button", { name: "New alert" }),
      );
      await userEvent.selectOptions(
        screen.getByLabelText("Figure to watch"),
        "loans.balance_rc",
      );
      await userEvent.selectOptions(
        screen.getByLabelText("Judged against"),
        "governed_limit",
      );
      await userEvent.type(
        screen.getByLabelText("Why"),
        "Unsaved alert reason",
      );
      await userEvent.type(
        screen.getByLabelText("Tell these people"),
        "synthetic@example.test",
      );
    } else {
      await userEvent.click(
        await screen.findByRole("button", { name: "Add a view" }),
      );
      await userEvent.type(screen.getByLabelText("Heading"), "Unsaved widget");
      await userEvent.click(
        screen.getByRole("checkbox", { name: "Loan balance" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Put it on the canvas" }),
      );
      const width = await screen.findByLabelText(
        "Width of Unsaved widget in columns",
      );
      fireEvent.change(width, { target: { value: "8" } });
      if (surface === "edit")
        await userEvent.type(
          screen.getByLabelText("What changed"),
          "Unsaved change note",
        );
    }
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Unsaved title");
    expect(screen.getByText("Protected saved title")).toBeVisible();
    await act(async () => {
      finishRead?.(lockedResponse(reason));
    });
    await waitFor(() => expect(protectedDataState(BANK)).toBe(reason));
    await waitFor(() =>
      expect(screen.queryByText("Protected saved title")).toBeNull(),
    );
    expect(p.client.getQueryData(protectedKey)).toBeUndefined();
    expect(p.client.getQueryData(otherKey)).toEqual({
      title: "Other bank private title",
    });
    expect(p.client.getQueryData(catalogueKey)).toEqual(
      BiCatalogueReadFromJSON(catalogue),
    );
    expect(p.client.getQueryData(numericKey)).toEqual(
      BiDashboardReadFromJSON(numericDashboard),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("Unsaved title");
    if (surface === "alert") {
      expect(screen.getByLabelText("Judged against")).toHaveValue(
        "governed_limit",
      );
      expect(screen.getByLabelText("Figure to watch")).toHaveValue(
        "loans.balance_rc",
      );
      expect(screen.getByLabelText("Why")).toHaveValue("Unsaved alert reason");
      expect(screen.getByLabelText("Tell these people")).toHaveValue(
        "synthetic@example.test",
      );
    } else {
      expect(
        screen.getByLabelText("Width of Unsaved widget in columns"),
      ).toHaveValue(8);
      if (surface === "edit")
        expect(screen.getByLabelText("What changed")).toHaveValue(
          "Unsaved change note",
        );
    }
    await act(async () => {
      await observeProtectedData(jsonWithNotice({}, "available"));
    });
    expect(screen.getByLabelText("Name")).toHaveValue("Unsaved title");
  },
);

const queryShapes = [
  { axis: "measures", query: (member: string) => ({ measures: [member] }) },
  { axis: "dimensions", query: (member: string) => ({ dimensions: [member] }) },
  {
    axis: "filters",
    query: (member: string) => ({
      filters: [{ member, op: "eq", values: ["Synthetic filter value"] }],
    }),
  },
  {
    axis: "sort",
    query: (member: string) => ({ sort: [{ member, direction: "asc" }] }),
  },
  {
    axis: "pivot",
    query: (member: string) => ({
      pivot: { dimension: member, measures: ["loans.balance_rc"] },
    }),
  },
  {
    axis: "topN",
    query: (member: string) => ({
      top_n: { dimension: member, n: 5, measure: "loans.balance_rc" },
    }),
  },
];

it.each(
  [
    ...aggregateMembers.map((member) => ({ member, identity: false })),
    ...[
      "counterparty.id",
      "counterparty.name",
      "counterparty.source_reference",
      "counterparty.group",
    ].map((member) => ({ member, identity: true })),
  ].flatMap(({ member, identity }) =>
    queryShapes.flatMap(({ axis, query }) =>
      (["unavailable", "revoked"] as const).map((reason) => ({
        member,
        identity,
        axis,
        query: query(member),
        reason,
      })),
    ),
  ),
)(
  "applies the identity policy to $member in $axis on $reason",
  async ({ identity, query, reason }) => {
    const p = setup();
    const definition = BiDashboardReadFromJSON({
      ...dashboard,
      id: PROTECTED_ID,
      widgets: [{ ...dashboard.widgets[0], query: { ...queryWire, ...query } }],
    });
    p.client.setQueryData(protectedKey, definition);
    await observeProtectedData(lockedResponse(reason));
    expect(p.client.getQueryData(protectedKey)).toEqual(
      identity ? undefined : definition,
    );
    expect(p.client.getQueryData(numericKey)).toEqual(
      BiDashboardReadFromJSON(dashboard),
    );
  },
);
