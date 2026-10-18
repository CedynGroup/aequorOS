// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
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
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fc from "fast-check";
import { CfpContentInputFromJSON } from "@aequoros/risk-service-api";
import { cfpContentToWire } from "../lib/api/cfp";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import CfpLifecycle from "../components/liquidity/CfpLifecycle";
import ContingencyFundingPlan from "../app/(app)/liquidity/cfp/page";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import { apiBaseUrl } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import { runBackend } from "./backend";

const context = vi.hoisted(() => ({
  actor: "maker",
  permissions: ["create", "edit", "approve"],
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-SAMP0001" },
    period: { id: "period", periodEnd: new Date("2026-03-31") },
    authorityPending: false,
    institutionCapabilities: context.permissions.map((permission) => ({
      module: "liq",
      sensitivity: "confidential",
      dataScope: { kind: "all", branches: [], regions: [] },
      permission,
      requiresContextualAuthorization: permission === "approve",
    })),
  }),
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ profile: { userId: context.actor } }),
}));
const server = setupServer();
let client: QueryClient;
const url = `${apiBaseUrl}/banks/BK-SAMP0001/liquidity/cfp`;
const plan = {
  id: "plan",
  bank_id: "BK-SAMP0001",
  version: 1,
  revision: "revision-1",
  status: "draft",
  prepared_by: "maker",
  content: {},
  created_at: "2026-03-31T00:00:00Z",
  updated_at: "2026-03-31T00:00:00Z",
  active: false,
  approval_overdue: false,
  approval_expires_at: "2027-03-31",
};
const authoredContent = {
  ewi_triggers: [{ indicator_code: "LCR", trigger_condition: "Below trigger" }],
  funding_options: [
    {
      horizon: "intraday",
      source: "Committed facility",
      estimated_capacity: "100 million",
      lead_time: "Same day",
    },
  ],
  action_plans: [
    { side: "asset", action: "Sell liquid assets", owner: "Treasury" },
  ],
  alternative_sources: [
    { source: "Standby facility", conditions: "Board consent" },
  ],
  escalation_procedures: [
    {
      priority: 1,
      stage: "Watch",
      trigger: "LCR breach",
      actions: "Convene committee",
      owner: "CRO",
    },
  ],
  key_relationships: [
    {
      institution: "Clearing bank",
      role: "Settlement",
      contact_name: "Duty officer",
    },
  ],
  communication_plans: [
    {
      audience: "regulator",
      channel: "Internal record",
      owner: "Compliance",
      message_outline: "Activation evidence",
    },
  ],
  behavioral_liquidity_scenarios: [
    {
      name: "Deposit stress",
      activation_horizon: "intraday",
      linked_action: "Sell liquid assets",
      deposit_runoff_uplift_pct: 0.5,
      funding_cost_uplift_bps: 12.5,
    },
  ],
};
const editableSections = [
  { title: "EWI triggers", field: "trigger Condition" },
  { title: "Funding options", field: "source" },
  { title: "Action plans", field: "action" },
  { title: "Alternative sources", field: "source" },
  { title: "Escalation procedures", field: "stage" },
  { title: "Key relationships", field: "institution" },
  { title: "Communication plans", field: "channel" },
  { title: "Behavioral liquidity scenarios", field: "name" },
];
function mount(content = <CfpLifecycle />) {
  return render(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("org", "fixture", 1)}
      >
        {content}
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  context.actor = "maker";
  context.permissions = ["create", "edit", "approve"];
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setAccessToken("fixture");
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});

it("retries a failed plan read before allowing a draft to be authored", async () => {
  server.use(
    http.get(url, () => HttpResponse.error(), { once: true }),
    http.get(url, () => HttpResponse.json({ current: null, approved: null })),
  );
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: "Retry" }));
  expect(await screen.findByText(/No draft or approved plan/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Add funding options" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Retry" }),
  ).not.toBeInTheDocument();
});

it("refuses an implicit draft submission with whitespace evidence, then saves with a reason", async () => {
  const requests: unknown[] = [];
  let current: typeof plan | null = null;
  server.use(
    http.get(url, () => HttpResponse.json({ current, approved: null })),
    http.put(url, async ({ request }) => {
      requests.push(await request.json());
      current = plan;
      return HttpResponse.json(current);
    }),
  );
  const user = userEvent.setup();
  mount();
  const reason = await screen.findByLabelText("Reason / evidence");
  await user.type(reason, "   ");
  // A form submission must enforce the same reason rule as the disabled button.
  fireEvent.submit(reason.closest("form")!);
  expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  await user.clear(reason);
  await user.type(reason, "Treasury evidence");
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText(/Current plan: v1 · draft/);
  expect(requests).toEqual([
    expect.objectContaining({ reason: "Treasury evidence" }),
  ]);
});

it("retries the event log independently and shows event-time snapshots when live indicators are unavailable", async () => {
  const event = {
    cfp_id: "plan",
    cfp_version: 1,
    created_at: "2026-03-31T12:00:00Z",
    approval_overdue: false,
  };
  server.use(
    http.get(url, () => HttpResponse.json({ current: plan, approved: null })),
    http.get(`${url}/events`, () => HttpResponse.error(), { once: true }),
    http.get(`${url}/events`, () =>
      HttpResponse.json({
        events: [
          {
            ...event,
            id: "activation",
            event_type: "activated",
            reason: "Activate committed facility",
            regulator_notification_id: "internal-record",
            ewi_snapshot: [
              {
                code: "LCR",
                name: "Liquidity coverage",
                metric_basis: "lcr_pct",
                direction: "below",
                unit: "percent",
                status: "action",
                value: "92.5",
              },
            ],
          },
          {
            ...event,
            id: "stand-down",
            event_type: "de_escalated",
            reason: "Liquidity restored",
            approval_overdue: true,
            ewi_snapshot: [
              {
                code: "LCR",
                name: "Liquidity coverage",
                metric_basis: "lcr_pct",
                direction: "below",
                unit: "percent",
                status: "normal",
                value: "125",
              },
            ],
          },
        ],
      }),
    ),
    http.get(`${apiBaseUrl}/banks/BK-SAMP0001/liquidity/ewis`, () =>
      HttpResponse.json({
        available: false,
        error_code: "liquidity_unavailable",
        reason: "No live liquidity facts",
      }),
    ),
  );
  const user = userEvent.setup();
  mount(<ContingencyFundingPlan />);
  expect(
    screen.getByRole("heading", {
      name: "Contingency Funding Plan",
      level: 1,
    }),
  ).toBeVisible();
  await user.click(await screen.findByRole("button", { name: "Retry" }));
  const activation = (
    await screen.findByText("Activate committed facility")
  ).closest("li")!;
  const standDown = screen.getByText("Liquidity restored").closest("li")!;
  expect(within(activation).getByText("Activated")).toBeVisible();
  expect(
    within(activation).getByText(/internal regulator-notification recorded/),
  ).toBeVisible();
  expect(within(standDown).getByText("De-escalated")).toBeVisible();
  expect(
    within(standDown).getByText(/Board approval was overdue at event time/),
  ).toBeVisible();
  for (const row of [activation, standDown]) {
    await user.click(within(row).getByText("Indicator snapshot at event time"));
  }
  expect(
    within(activation).getByText("Liquidity coverage: 92.50% · Action"),
  ).toBeVisible();
  expect(
    within(standDown).getByText("Liquidity coverage: 125.00% · Normal"),
  ).toBeVisible();
  expect(await screen.findByText("No live liquidity facts")).toBeVisible();
  expect(screen.getByText(/does not transmit them externally/)).toBeVisible();
});

it("saves every authored collection against the closed backend schema, allowing fractional overlays but integral priorities", async () => {
  let current: typeof plan | null = null;
  server.use(
    http.get(url, () => HttpResponse.json({ current, approved: null })),
    http.put(url, async ({ request }) => {
      const body = (await request.json()) as {
        content: typeof authoredContent;
        reason: string;
      };
      expect(
        runBackend<boolean>(
          `
from app.schemas.liquidity_cfp import CfpPut
CfpPut.model_validate(payload)
print(json.dumps(True))
`,
          body,
        ),
      ).toBe(true);
      expect(body).toEqual({
        content: authoredContent,
        reason: "Treasury evidence",
      });
      current = { ...plan, content: body.content };
      return HttpResponse.json(current);
    }),
  );
  const user = userEvent.setup();
  const view = mount();
  await screen.findByRole("button", { name: "Add funding options" });
  const inputs: Record<string, Record<string, string>> = {
    "EWI triggers": {
      "indicator Code": "LCR",
      "trigger Condition": "Below trigger",
    },
    "Funding options": {
      source: "Committed facility",
      "estimated Capacity": "100 million",
      "lead Time": "Same day",
    },
    "Action plans": { action: "Sell liquid assets", owner: "Treasury" },
    "Alternative sources": {
      source: "Standby facility",
      conditions: "Board consent",
    },
    "Escalation procedures": {
      stage: "Watch",
      trigger: "LCR breach",
      actions: "Convene committee",
      owner: "CRO",
    },
    "Key relationships": {
      institution: "Clearing bank",
      role: "Settlement",
      "contact Name": "Duty officer",
    },
    "Communication plans": {
      channel: "Internal record",
      owner: "Compliance",
      "message Outline": "Activation evidence",
    },
    "Behavioral liquidity scenarios": {
      name: "Deposit stress",
      "linked Action": "Sell liquid assets",
      "deposit Runoff Uplift Pct": "0.5",
      "funding Cost Uplift Bps": "12.5",
    },
  };
  for (const [title, fields] of Object.entries(inputs)) {
    await user.click(
      screen.getByRole("button", { name: `Add ${title.toLowerCase()}` }),
    );
    const section = within(screen.getByRole("group", { name: title }));
    for (const [field, value] of Object.entries(fields)) {
      const input = section.getByLabelText(field, { exact: false });
      await user.clear(input);
      await user.type(input, value);
    }
  }
  await user.selectOptions(
    within(
      screen.getByRole("group", { name: "Communication plans" }),
    ).getByLabelText("audience"),
    "regulator",
  );
  await user.type(
    screen.getByLabelText("Reason / evidence"),
    "Treasury evidence",
  );
  const priority = screen.getByLabelText("priority");
  await user.clear(priority);
  await user.type(priority, "1.5");
  expect(priority).toBeInvalid();
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(current).toBeNull();
  await user.clear(priority);
  await user.type(priority, "1");
  expect(priority.closest("form")).toBeValid();
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText(/Current plan: v1 · draft/);
  expect(screen.getByDisplayValue("Committed facility")).toBeVisible();
  expect(screen.getByLabelText("deposit Runoff Uplift Pct")).toHaveValue(0.5);
  expect(screen.getByLabelText("funding Cost Uplift Bps")).toHaveValue(12.5);
  view.unmount();
  client.clear();
  mount();
  expect(await screen.findByDisplayValue("Committed facility")).toBeVisible();
  expect(screen.getByLabelText("funding Cost Uplift Bps")).toHaveValue(12.5);
}, 30_000);

it.each(editableSections)(
  "refuses approval for unsaved edits, additions and removals in $title",
  async ({ title, field }) => {
    context.actor = "checker";
    const current = { ...plan, content: authoredContent };
    let approvalRecorded = false;
    server.use(
      http.get(url, () => HttpResponse.json({ current, approved: null })),
      http.post(`${url}/approve`, () => {
        approvalRecorded = true;
        return HttpResponse.json(current);
      }),
    );
    const user = userEvent.setup();
    mount();
    await user.type(
      await screen.findByLabelText("Reason / evidence"),
      "Reviewed",
    );
    await user.type(
      screen.getByLabelText("Board approval reference"),
      "Board 42",
    );
    const approval = () =>
      screen.getByRole("button", { name: "Record Board approval" });
    expect(approval()).toBeEnabled();
    const section = within(screen.getByRole("group", { name: title }));
    const input = section.getByLabelText(field);
    const original = (input as HTMLInputElement).value;
    await user.clear(input);
    await user.type(input, "Changed content");
    expect(approval()).toBeDisabled();
    await user.hover(approval().parentElement!);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Save your content changes before Board approval",
    );
    await user.click(approval());
    expect(approvalRecorded).toBe(false);
    await user.clear(input);
    await user.type(input, original);
    expect(approval()).toBeEnabled();
    await user.click(
      section.getByRole("button", { name: `Add ${title.toLowerCase()}` }),
    );
    expect(approval()).toBeDisabled();
    await user.click(
      section.getByRole("button", { name: `Remove ${title.toLowerCase()} 2` }),
    );
    expect(approval()).toBeEnabled();
    await user.click(
      section.getByRole("button", { name: `Remove ${title.toLowerCase()} 1` }),
    );
    expect(approval()).toBeDisabled();
    expect(approvalRecorded).toBe(false);
  },
);

it("requires an independent checker after saving displayed edits", async () => {
  context.actor = "checker";
  let current = { ...plan, content: authoredContent };
  server.use(
    http.get(url, () => HttpResponse.json({ current, approved: null })),
    http.put(url, async ({ request }) => {
      const body = (await request.json()) as {
        content: typeof authoredContent;
      };
      expect(
        runBackend<boolean>(
          `
from app.schemas.liquidity_cfp import CfpPut
CfpPut.model_validate(payload)
print(json.dumps(True))
`,
          body,
        ),
      ).toBe(true);
      expect(body.content.funding_options[0].source).toBe(
        "Replacement facility",
      );
      current = {
        ...current,
        content: body.content,
        prepared_by: context.actor,
        updated_at: "2026-04-01T00:00:00Z",
        revision: "revision-2",
      };
      return HttpResponse.json(current);
    }),
  );
  const user = userEvent.setup();
  mount();
  const source = await screen.findByDisplayValue("Committed facility");
  await user.clear(source);
  await user.type(source, "Replacement facility");
  await user.type(
    screen.getByLabelText("Reason / evidence"),
    "Treasury evidence",
  );
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Reason / evidence")).toHaveValue(""),
  );
  expect(screen.getByDisplayValue("Replacement facility")).toBeVisible();
  await user.type(
    screen.getByLabelText("Reason / evidence"),
    "Saved for independent review",
  );
  await user.type(
    screen.getByLabelText("Board approval reference"),
    "Board 42",
  );
  const approval = screen.getByRole("button", {
    name: "Record Board approval",
  });
  expect(approval).toBeDisabled();
  await user.hover(approval.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "The preparer cannot approve their own plan",
  );
}, 15_000);

it("preserves loaded and edited funding descriptions through the closed wire contract", () => {
  const requests: {
    content: ReturnType<typeof cfpContentToWire>;
    reason: string;
  }[] = [];
  const sources: string[] = [];
  fc.assert(
    fc.property(fc.string({ minLength: 1, maxLength: 60 }), (source) => {
      const content = CfpContentInputFromJSON(authoredContent);
      const wire = cfpContentToWire({
        ...content,
        fundingOptions: [{ ...content.fundingOptions![0], source }],
      });
      expect(wire.funding_options).toEqual([
        { ...authoredContent.funding_options[0], source },
      ]);
      requests.push({ content: wire, reason: "Treasury evidence" });
      sources.push(source);
    }),
    { numRuns: 30 },
  );
  expect(
    runBackend<string[]>(
      `
from app.schemas.liquidity_cfp import CfpPut
print(json.dumps([CfpPut.model_validate(item).content.funding_options[0].source
    for item in payload]))
`,
      requests,
    ),
  ).toEqual(sources);
}, 15_000);

it("blocks maker approval, then an independent approver can approve, activate and stand down", async () => {
  let current = { ...plan };
  let active = false;
  server.use(
    http.get(url, () =>
      HttpResponse.json({
        current: { ...current, active },
        approved: current.status === "approved" ? { ...current, active } : null,
      }),
    ),
    http.post(`${url}/approve`, async ({ request }) => {
      expect(await request.json()).toEqual({
        expected_revision: "revision-1",
        approval_reference: "Board 42",
        reason: "Reviewed",
      });
      current = {
        ...current,
        status: "approved",
        updated_at: "2026-04-01T00:00:00Z",
        revision: "revision-2",
      };
      return HttpResponse.json(current);
    }),
    ...["activate", "de-escalate"].map((action) =>
      http.post(`${url}/${action}`, async ({ request }) => {
        expect(await request.json()).toEqual({
          reporting_period_id: "period",
          reason: "Evidence",
        });
        active = action === "activate";
        return HttpResponse.json({
          id: action,
          cfp_id: "plan",
          cfp_version: 1,
          event_type: active ? "activated" : "de_escalated",
          reason: "Evidence",
          ewi_snapshot: [],
          approval_overdue: false,
          created_at: "2026-04-01T00:00:00Z",
        });
      }),
    ),
  );
  const user = userEvent.setup();
  const view = mount();
  await user.type(
    await screen.findByLabelText("Reason / evidence"),
    "Reviewed",
  );
  await user.type(
    screen.getByLabelText("Board approval reference"),
    "Board 42",
  );
  expect(
    screen.getByRole("button", { name: "Record Board approval" }),
  ).toBeDisabled();
  context.actor = "checker";
  view.rerender(
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("org", "fixture", 1)}
      >
        <CfpLifecycle />
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>,
  );
  await user.click(
    screen.getByRole("button", { name: "Record Board approval" }),
  );
  await screen.findByText(/Current plan: v1 · approved/);
  await user.type(screen.getByLabelText("Reason / evidence"), "Evidence");
  await user.click(screen.getByRole("button", { name: "Activate CFP" }));
  await waitFor(() => expect(active).toBe(true));
  await screen.findByText(
    "CFP action recorded. The plan and event log have been refreshed.",
  );
  expect(screen.getByRole("button", { name: "Stand down CFP" })).toBeDisabled();
  await user.clear(screen.getByLabelText("Reason / evidence"));
  await user.type(screen.getByLabelText("Reason / evidence"), "Evidence");
  await user.click(screen.getByRole("button", { name: "Stand down CFP" }));
  await waitFor(() => expect(active).toBe(false));
});

it("refuses cross-session approval until the checker reloads and reviews the latest revision", async () => {
  context.actor = "checker";
  const latest = {
    ...plan,
    revision: "revision-2",
    updated_at: "2026-03-31T00:00:00.000001Z",
    content: {
      ...authoredContent,
      funding_options: [
        {
          ...authoredContent.funding_options[0],
          source: "Replacement facility",
        },
      ],
    },
  };
  let current = { ...plan, content: authoredContent };
  let approved = false;
  const requests: unknown[] = [];
  server.use(
    http.get(url, () =>
      HttpResponse.json({ current, approved: approved ? current : null }),
    ),
    http.post(`${url}/approve`, async ({ request }) => {
      const body = (await request.json()) as { expected_revision: string };
      requests.push(body);
      if (body.expected_revision !== current.revision)
        return HttpResponse.json(
          {
            error: {
              code: "conflict",
              message: "Conflict",
              details: {
                error_code: "stale_cfp_revision",
                message:
                  "The plan changed since you loaded it. Reload the latest plan and review it before approving.",
              },
            },
          },
          { status: 409 },
        );
      approved = true;
      current = { ...current, status: "approved", revision: "revision-3" };
      return HttpResponse.json(current);
    }),
  );
  const user = userEvent.setup();
  mount();
  expect(await screen.findByDisplayValue("Committed facility")).toBeVisible();
  current = latest;
  await user.type(screen.getByLabelText("Reason / evidence"), "Reviewed");
  await user.type(
    screen.getByLabelText("Board approval reference"),
    "Board 42",
  );
  await user.click(
    screen.getByRole("button", { name: "Record Board approval" }),
  );
  expect(
    await screen.findByText(/The plan changed since you loaded it/),
  ).toBeVisible();
  expect(approved).toBe(false);
  expect(requests).toEqual([
    {
      expected_revision: "revision-1",
      approval_reference: "Board 42",
      reason: "Reviewed",
    },
  ]);
  expect(
    screen.getByRole("button", { name: "Record Board approval" }),
  ).toBeDisabled();
  expect(screen.getByDisplayValue("Committed facility")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Reload latest plan" }));
  expect(await screen.findByDisplayValue("Replacement facility")).toBeVisible();
  expect(screen.getByLabelText("Reason / evidence")).toHaveValue("");
  expect(screen.getByLabelText("Board approval reference")).toHaveValue("");
  await user.type(
    screen.getByLabelText("Reason / evidence"),
    "Reviewed latest",
  );
  await user.type(
    screen.getByLabelText("Board approval reference"),
    "Board 43",
  );
  await user.click(
    screen.getByRole("button", { name: "Record Board approval" }),
  );
  await screen.findByText(/Current plan: v1 · approved/);
  expect(approved).toBe(true);
  expect(requests[1]).toEqual({
    expected_revision: "revision-2",
    approval_reference: "Board 43",
    reason: "Reviewed latest",
  });
  expect(screen.getByDisplayValue("Replacement facility")).toBeVisible();
});

it("keeps readers' mutation controls disabled with scoped reasons", async () => {
  context.permissions = ["view"];
  server.use(
    http.get(url, () => HttpResponse.json({ current: plan, approved: null })),
  );
  mount();
  const save = await screen.findByRole("button", { name: "Save draft" });
  expect(save).toBeDisabled();
  const user = userEvent.setup();
  await user.hover(save.parentElement!);
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "Liquidity · Confidential · Create",
  );
  expect(
    screen.getByRole("button", { name: "Record Board approval" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: "Activate CFP" })).toBeDisabled();
});
