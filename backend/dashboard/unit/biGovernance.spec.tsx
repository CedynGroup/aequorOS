// @vitest-environment jsdom
import React from "react";
import { createHash } from "node:crypto";
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
  BiMeasureReadFromJSON,
  BiMeasureValidationReadFromJSON,
} from "@aequoros/risk-service-api";
import MeasureReview from "../components/bi/MeasureReview";
import ExpressionEditor from "../components/bi/ExpressionEditor";
import SharePanel, {
  SHARING_GRANTS_NOTHING,
} from "../components/bi/SharePanel";
import SavedDashboardScreen from "../components/bi/SavedDashboardScreen";
import MeasuresPage from "../app/(app)/explore/measures/page";
import SubscriptionsPage from "../app/(app)/explore/subscriptions/page";
import EditDashboardPage from "../app/(app)/dashboards/[id]/edit/page";
import ExplainDrawer from "../components/bi/ExplainDrawer";
import {
  designationLabel,
  aggregationLabel,
  componentRoleLabel,
  moduleLabel,
  engineTierLabel,
  regimeLabel,
} from "../components/bi/labels";
import { providers, json, query, queryWire, catalogueWire } from "./biTestSupport";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  notFound: vi.fn(() => {
    throw new Error("Not found");
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push }),
  notFound: navigation.notFound,
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
beforeEach(() => {
  vi.stubGlobal("React", React);
  navigation.push.mockClear();
  navigation.notFound.mockClear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each([
  { enabled: true, active: true, label: "Sending" },
  { enabled: false, active: true, label: "Not sending here" },
  { enabled: undefined, active: true, label: "Scheduled" },
  { enabled: true, active: false, label: "Stopped" },
  { enabled: false, active: false, label: "Stopped" },
  { enabled: undefined, active: false, label: "Stopped" },
])(
  "a report with active=$active and delivery=$enabled renders $label",
  async ({ enabled, active, label }) => {
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("/feature-flags"))
        return json({ bi_subscriptions_enabled: enabled });
      if (url.endsWith("/catalogue")) return json(catalogueWire);
      if (url.endsWith("/subscriptions"))
        return json({
          subscriptions: [{
            id: "report",
            bank_id: "BK-test",
            name: "Weekly report",
            query: queryWire,
            artifact_format: "xlsx",
            cadence: "weekly",
            hour: 8,
            minute: 30,
            day_of_week: 2,
            time_zone: "UTC",
            is_active: active,
            owned_by_caller: false,
            recipients: [],
            recipient_user_ids: [],
            disclosure_class: "summary",
            delivery_note: "Prepared under each recipient's access.",
          }],
        });
      throw new Error(`Unexpected request: ${url}`);
    });
    render(<SubscriptionsPage />, { wrapper: providers().wrapper });
    await screen.findByText("Weekly report");
    await screen.findByText(label, { exact: true });
    for (const other of ["Sending", "Not sending here", "Scheduled", "Stopped"]) {
      if (other !== label)
        expect(screen.queryByText(other, { exact: true })).toBeNull();
    }
  },
);
const id = "00000000-0000-4000-8000-000000000001";
const measureWire = (owned = true, awaiting = false, state = "proposed") => ({
  id,
  label: "Bank loan ratio",
  measure_key: "bank.loan_ratio",
  description: "Ratio for management",
  expression: "SAFE_DIV([m:loans.balance_rc], 100)",
  expression_digest: "a".repeat(64),
  referenced_member_ids: ["loans.balance_rc"],
  referenced_member_labels: ["Loan balance"],
  value_type: "pct",
  favourable_direction: "higher_better",
  state,
  owned_by_caller: owned,
  awaiting_caller_decision: awaiting,
  owner_display_name: "Maker",
  approved_expression:
    state === "bank_certified" ? "SAFE_DIV([m:loans.balance_rc], 100)" : null,
  proposed_at: "2026-09-29T10:00:00Z",
  proposal_reason: "Management monitoring",
  approved_at: null,
});

it.each([false, true])(
  "the decision controls rely on awaiting_caller_decision=%s",
  (awaiting) => {
    const onDecide = vi.fn();
    render(
      <MeasureReview
        measure={BiMeasureReadFromJSON(measureWire(false, awaiting))}
        proposing={false}
        deciding={false}
        refusal={null}
        sodFindings={[]}
        sodRemedy={null}
        onPropose={vi.fn()}
        onDecide={onDecide}
      />,
    );
    const button = screen.queryByRole("button", {
      name: "Certify for the institution",
    });
    if (!awaiting) {
      expect(button).toBeNull();
      return;
    }
    expect(screen.getByText(measureWire().expression)).toBeTruthy();
    expect(screen.getByText("It reads Loan balance.")).toBeTruthy();
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(
      screen.getByRole("textbox", { name: "What you concluded" }),
      { target: { value: "  Checked numerator and denominator  " } },
    );
    fireEvent.click(button!);
    expect(onDecide).toHaveBeenCalledWith(
      "approve",
      "Checked numerator and denominator",
    );
    fireEvent.click(screen.getByRole("button", { name: "Send it back" }));
    expect(onDecide).toHaveBeenCalledWith(
      "reject",
      "Checked numerator and denominator",
    );
  },
);

it("the proposer gets a reason instead of a certification control, and refusals retain figure names", () => {
  render(
    <MeasureReview
      measure={BiMeasureReadFromJSON(measureWire())}
      proposing={false}
      deciding={false}
      refusal="Your access does not cover Loan balance."
      sodFindings={[]}
      sodRemedy={null}
      onPropose={vi.fn()}
      onDecide={vi.fn()}
    />,
  );
  expect(screen.getByRole("status").textContent).toMatch(
    /You proposed this formula, so you cannot certify it/,
  );
  expect(
    screen.queryByRole("button", { name: "Certify for the institution" }),
  ).toBeNull();
  expect(screen.getByRole("alert").textContent).toBe(
    "Your access does not cover Loan balance.",
  );
});

it("the expression editor renders only the server verdict for the exact text checked", () => {
  const expression = "a future server-language expression";
  const valid = BiMeasureValidationReadFromJSON({
    valid: true,
    message: "Accepted by the server.",
    referenced_member_labels: ["Loan balance"],
  });
  const onCheck = vi.fn();
  const onChange = vi.fn();
  const props = {
    value: expression,
    figures: [],
    verdict: { expression, read: valid },
    checking: false,
    checkError: null,
    onCheck,
    onChange,
  };
  const view = render(<ExpressionEditor {...props} />);
  expect(screen.getByText(valid.message)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Check this formula" }));
  expect(onCheck).toHaveBeenCalledOnce();
  const area = screen.getByRole("textbox", { name: "The formula" });
  fireEvent.change(area, { target: { value: "[m:ungranted.figure]" } });
  expect(onChange).toHaveBeenCalledWith("[m:ungranted.figure]");
  view.rerender(<ExpressionEditor {...props} value="changed" />);
  expect(screen.queryByText(valid.message)).toBeNull();
  expect(screen.getByText(/changed since it was last checked/)).toBeTruthy();
  view.rerender(
    <ExpressionEditor
      {...props}
      verdict={{
        expression,
        read: BiMeasureValidationReadFromJSON({
          valid: false,
          message: "A server syntax refusal.",
          position: 3,
        }),
      }}
    />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "A server syntax refusal.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Show me character 3" }));
  expect((area as HTMLTextAreaElement).selectionStart).toBe(2);
});

it("the sharing panel explains per-view access and submits identities from the directory", () => {
  const onSave = vi.fn();
  const view = render(
    <SharePanel
      visibility="users"
      visibilityRole={null}
      shares={[]}
      sharesLoading={false}
      sharesError={null}
      candidates={[
        {
          id,
          displayName: "Colleague",
          email: "colleague@example.com",
          isActive: true,
        },
      ]}
      directoryRefused={false}
      directoryLoading={false}
      editHref="/dashboards/saved/edit"
      saving={false}
      saveError={null}
      onSave={onSave}
    />,
  );
  expect(screen.getByText(SHARING_GRANTS_NOTHING)).toBeTruthy();
  fireEvent.change(
    screen.getByRole("combobox", { name: "Name someone else" }),
    { target: { value: id } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Add to the list" }));
  expect(onSave).toHaveBeenCalledWith([id]);
  view.rerender(
    <SharePanel
      visibility="org"
      visibilityRole={null}
      shares={undefined}
      sharesLoading={false}
      sharesError={null}
      candidates={[]}
      directoryRefused={false}
      directoryLoading={false}
      editHref={null}
      saving={false}
      saveError={null}
      onSave={onSave}
    />,
  );
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.getByText(/not shared by name/)).toBeTruthy();
});

const dashboard = (owned: boolean) => ({
  id,
  title: "Saved by the institution",
  description: "Shared document",
  version: 1,
  badge: "personal",
  owned_by_caller: owned,
  owner_display_name: "Maker",
  visibility: "users",
  visibility_role: null,
  source_pack: null,
  access: "granted",
  message: "Some views are restricted.",
  widgets: [
    {
      id: "locked",
      access: "restricted",
      layout: { i: "locked", x: 0, y: 0, w: 6, h: 4 },
    },
  ],
});
function dashboardTransport(owned: boolean) {
  let deleted = false;
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    if (init?.method === "DELETE") {
      deleted = true;
      return new Response(null, { status: 204 });
    }
    if (path.endsWith("/catalogue"))
      return json({ measures: [], dimensions: [], drills: [] });
    if (path.endsWith("/versions")) return json({ versions: [] });
    if (path.endsWith("/shares")) return json({ shares: [] });
    if (path.endsWith("/users")) return json([]);
    return deleted
      ? json({ error: { message: "Not found" } }, 404)
      : json(dashboard(owned));
  });
}

it.each([true, false])(
  "the saved screen offers ownership controls only when owned_by_caller=%s",
  async (owned) => {
    const fetch = dashboardTransport(owned);
    vi.stubGlobal("fetch", fetch);
    const p = providers();
    render(<SavedDashboardScreen id={id} />, p);
    await screen.findByText("Saved by the institution");
    await screen.findByText("Access restricted");
    expect(screen.getByText("Some views are restricted.")).toBeTruthy();
    expect(Boolean(screen.queryByRole("link", { name: "Arrange" }))).toBe(
      owned,
    );
    expect(Boolean(screen.queryByRole("button", { name: "Delete" }))).toBe(
      owned,
    );
    if (owned) await screen.findByText(SHARING_GRANTS_NOTHING);
    else {
      expect(
        screen.getByText(/Maker saved this dashboard and is the only person/),
      ).toBeTruthy();
      expect(screen.queryByText(SHARING_GRANTS_NOTHING)).toBeNull();
      expect(
        fetch.mock.calls.some(([url]) =>
          new URL(url).pathname.endsWith("/shares"),
        ),
      ).toBe(false);
    }
    p.client.clear();
  },
);

it("deleting the owned dashboard handles the refetched 404 before navigation", async () => {
  vi.stubGlobal("fetch", dashboardTransport(true));
  const p = providers();
  render(<SavedDashboardScreen id={id} />, p);
  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Delete it and its history" }),
  );
  await screen.findByText("Dashboard deleted");
  await waitFor(() =>
    expect(navigation.push).toHaveBeenCalledWith("/dashboards"),
  );
  expect(navigation.notFound).not.toHaveBeenCalled();
  p.client.clear();
});

it("a non-owner deep link to arrangement offers the reason and no editor", async () => {
  vi.stubGlobal("fetch", dashboardTransport(false));
  const p = providers();
  const params = Promise.resolve({ id });
  await params;
  await act(async () => {
    render(
      <React.Suspense fallback={null}>
        <EditDashboardPage params={params} />
      </React.Suspense>,
      p,
    );
  });
  await screen.findByText(
    "Only the person who saved this dashboard can change it",
  );
  expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "Back to the dashboard" })
      .getAttribute("href"),
  ).toBe(`/dashboards/${id}`);
  p.client.clear();
});

it("a certified measure withholds deletion with an on-screen reason and never invents refused measures", async () => {
  vi.stubGlobal("fetch", async (url: string) =>
    json(
      url.endsWith("/catalogue")
        ? { measures: [], dimensions: [] }
        : { measures: [measureWire(true, false, "bank_certified")] },
    ),
  );
  const p = providers();
  const view = render(<MeasuresPage />, p);
  await screen.findByText("Bank loan ratio");
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(
    screen.getByText(
      /This formula was certified for the institution, so one person cannot remove/,
    ),
  ).toBeTruthy();
  expect(view.container.textContent).not.toMatch(
    /Access restricted|withheld measures|SECRET/,
  );
  p.client.clear();
});

it("measure certification uses the exact displayed digest and renders a figure-level server refusal", async () => {
  const wire = measureWire(false, true);
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/decision"))
      return json(
        {
          error: {
            details: {
              error_code: "bi_authorization_denied",
              message: "Your access does not cover Loan balance.",
              refused_members: [
                { label: "Loan balance", member_id: "loans.balance_rc" },
              ],
            },
          },
        },
        403,
      );
    return json(
      url.endsWith("/catalogue")
        ? { measures: [], dimensions: [] }
        : { measures: [wire] },
    );
  });
  vi.stubGlobal("fetch", fetch);
  const p = providers();
  render(<MeasuresPage />, p);
  await screen.findByRole("button", { name: "Certify for the institution" });
  fireEvent.change(
    screen.getByRole("textbox", { name: "What you concluded" }),
    { target: { value: "Checked the formula" } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Certify for the institution" }),
  );
  await screen.findByText("Your access does not cover Loan balance.");
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/decision"))!;
  expect(JSON.parse(String(call[1]!.body))).toEqual({
    decision: "approve",
    expression_digest: createHash("sha256")
      .update(wire.expression, "utf8")
      .digest("hex"),
    reason: "Checked the formula",
  });
  p.client.clear();
});

it("provenance renders readable vocabularies and labels the one raw engine code", async () => {
  const wire = {
    measure: {
      id: "capital.car",
      label: "Capital ratio",
      description: "Filed capital",
      aggregation: "ratio_of_sums",
      advisory_designation: "advisory_internal",
      value_type: "pct",
    },
    catalogue_version: "v1",
    components: [
      { role: "over", member_id: "rwa", label: "Risk-weighted assets" },
    ],
    engine: {
      metric_id: "engine.car_pct",
      module: "liq",
      tier: "live",
      regime: "basel_bank",
    },
  };
  vi.stubGlobal("fetch", async () => json(wire));
  const p = providers();
  const view = render(
    <ExplainDrawer
      bankId="BK-test"
      measure="capital.car"
      query={query}
      onClose={vi.fn()}
    />,
    p,
  );
  await screen.findByText("Capital ratio");
  for (const label of [
    aggregationLabel("ratio_of_sums"),
    designationLabel("advisory_internal"),
    componentRoleLabel("over"),
    moduleLabel("liq"),
    engineTierLabel("live"),
    regimeLabel("basel_bank"),
  ])
    if (label) expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText("Engine metric code")).toBeTruthy();
  expect(screen.getByText("engine.car_pct").tagName).toBe("CODE");
  expect(view.container.textContent).not.toMatch(
    /ratio_of_sums|advisory_internal|basel_bank/,
  );
  p.client.clear();
});
