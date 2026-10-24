// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
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
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import ReturnsPage from "@/app/(app)/submissions/returns/page";
import CalendarPage from "@/app/(app)/submissions/calendar/page";
import RegulatoryRuleStatus from "@/components/submissions/RegulatoryRuleStatus";
import { useNotifications, useReturnTemplates } from "@/lib/api/hooks";
import { useBankRules } from "@/lib/api/regulatoryRules";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";
import { apiBaseUrl } from "@/lib/api/client";
import { setAccessToken } from "@/lib/api/token";

vi.mock("next/font/local", () => ({
  default: () => ({
    className: "synthetic-font",
    style: { fontFamily: "synthetic-font" },
  }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/submissions/returns",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ effectiveAuthority: undefined, isLoading: false }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));
vi.mock("@/components/shell/BankContext", () => {
  const scope = {
    modules: new Set(["regulatory_reporting", "irrbb"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    irrbbAggregatedView: true,
  };
  return {
    useBankContext: () => ({ bank: { id: "BK-SYNTH001" }, moduleScope: scope }),
    useModuleScope: () => scope,
  };
});
vi.mock("@/lib/api/hooks", async (importOriginal) => {
  const hooks = await importOriginal<typeof import("@/lib/api/hooks")>();
  const query = (data: unknown) => ({
    data,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  return {
    ...hooks,
    useReturnAnchors: () => query({ anchors: [] }),
    useRegulatoryPackages: () => query({ packages: [] }),
    useRegulatoryPackage: () => query(undefined),
    useGenerateRegulatoryPackage: () => ({ isPending: false, error: null }),
    useValidateRegulatoryPackage: () => ({ isPending: false, error: null }),
  };
});
const server = setupServer();
let client: QueryClient;
let fail = false;
let irrStatus = "exposure_draft";
let noticeId: string | null = null;
const calls = new Map<string, number>();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  vi.setSystemTime(new Date("2026-12-31T23:59:30Z"));
  fail = false;
  irrStatus = "exposure_draft";
  noticeId = null;
  calls.clear();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  setAccessToken("synthetic-token");
  server.use(
    http.get(`${apiBaseUrl}/regulatory-reporting/templates`, ({ request }) => {
      const bankId = new URL(request.url).searchParams.get("bank_id") ?? "all";
      calls.set(bankId, (calls.get(bankId) ?? 0) + 1);
      if (fail)
        return HttpResponse.json(
          { detail: "Synthetic metadata failure" },
          { status: 503 },
        );
      return HttpResponse.json({
        templates: [
          {
            code: "LE-MONTHLY",
            instrument_status:
              Date.now() >= Date.parse("2027-01-01T00:00:00Z")
                ? "in_force"
                : "final_not_in_force",
            title: "Synthetic large exposure",
            family: "large_exposures",
            effective_from: "2027-01-01",
          },
          {
            code: "LCR-NSFR",
            instrument_status: "unpublished",
            title: "Synthetic reference",
            family: "liquidity",
          },
          {
            code: "IRRBB-PILOT",
            instrument_status: irrStatus,
            title: "Synthetic IRR",
            family: "irr",
          },
        ].map((row) => ({
          ...row,
          fidelity: "PARTIAL",
          regulator: "Synthetic",
          generator: "synthetic",
          template_id: row.code,
          default_channel: "manual",
          supports_working_copy: false,
          frequency: "monthly",
          directive_citation: "Synthetic source",
          calculations: [],
        })),
      });
    }),
    http.get(`${apiBaseUrl}/notifications`, () =>
      HttpResponse.json({
        notifications: noticeId
          ? [
              {
                id: noticeId,
                type: "reporting.instrument.changed:IRRBB-PILOT:",
                entity_type: "bank",
                entity_id: "BK-SYNTH001",
                title: "Synthetic status change",
                body: "Synthetic",
                severity: "info",
                created_at: "2027-01-01T00:00:00Z",
                read_at: null,
              },
            ]
          : [],
        unread_count: noticeId ? 1 : 0,
        total: noticeId ? 1 : 0,
        has_more: false,
        offset: 0,
        limit: 50,
      }),
    ),
  );
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});
function view(children: React.ReactNode) {
  return (
    <QueryClientProvider client={client}>
      <QueryAuthorityScopeProvider
        scope={queryAuthorityScope("OR-SYNTH001", "synthetic@example.test", 1)}
      >
        {children}
      </QueryAuthorityScopeProvider>
    </QueryClientProvider>
  );
}
function Assessments() {
  const rules = useBankRules("BK-SYNTH001");
  return (
    <p>
      Rule assessment:{" "}
      {rules.error
        ? "error"
        : rules.isLoading
          ? "loading"
          : String(rules.isInForce("IRRBB-PILOT"))}
    </p>
  );
}
function Feed() {
  const feed = useNotifications();
  const [refreshes, setRefreshes] = useState(0);
  useReturnTemplates("BK-SYNTH002");
  return (
    <>
      <button
        disabled={feed.isFetching}
        onClick={() =>
          void feed.refetch().then(() => setRefreshes((count) => count + 1))
        }
      >
        Refresh notices
      </button>
      <p>Refreshes: {refreshes}</p>
      <p>Notices: {feed.data?.total ?? "loading"}</p>
    </>
  );
}
it.each(["poll", "notification"])(
  "refreshes mounted calendar rows and counts across commencement via %s",
  async (trigger) => {
    server.use(
      http.get(`${apiBaseUrl}/banks/:bankId/reporting-obligations`, () => {
        const commenced = Date.now() >= Date.parse("2027-01-01T00:00:00Z");
        return HttpResponse.json({
          bank_id: "BK-SYNTH001",
          as_of: new Date().toISOString(),
          horizon_months: 3,
          lookback_months: 3,
          limit: 25,
          offset: 0,
          has_more: false,
          total: commenced ? 1 : 0,
          summary: {
            overdue: 0,
            due_soon: 0,
            on_track: commenced ? 1 : 0,
            pending_reupload: 0,
          },
          obligations: commenced
            ? [
                {
                  return_code: "LE-MONTHLY",
                  title: "Synthetic large exposure",
                  return_family: "large_exposures",
                  frequency: "monthly",
                  instrument_status: "in_force",
                  effective_from: "2027-01-01",
                  reporting_date: "2027-01-31",
                  due_date: "2027-02-09",
                  rag: "on_track",
                  data_status: "awaiting_data",
                  package_id: null,
                  package_status: null,
                  package_version: null,
                  fidelity: "PARTIAL",
                  default_channel: "manual",
                  annexes: [],
                },
              ]
            : [],
        });
      }),
    );
    render(
      view(
        <>
          <section aria-label="Upcoming">
            <InstrumentsRegister upcoming />
          </section>
          <section aria-label="Returns">
            <ReturnsPage />
          </section>
          <section aria-label="Calendar">
            <CalendarPage />
          </section>
          {trigger === "notification" && <Feed />}
        </>,
      ),
    );
    const upcoming = within(screen.getByRole("region", { name: "Upcoming" }));
    const returns = within(screen.getByRole("region", { name: "Returns" }));
    const calendar = within(screen.getByRole("region", { name: "Calendar" }));
    await upcoming.findByRole("heading", { name: "Synthetic large exposure" });
    await calendar.findByText("No obligations in this horizon");
    expect(returns.queryByRole("option", { name: /LE-MONTHLY/ })).toBeNull();
    const onTrack = () =>
      within(calendar.getByText("On track", { selector: "p" }).parentElement!);
    expect(onTrack().getByText("0")).toBeVisible();
    if (trigger === "poll") {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
    } else {
      await screen.findByText("Notices: 0");
      vi.setSystemTime(new Date("2027-01-01T00:00:30Z"));
      irrStatus = "in_force";
      noticeId = "commencement";
      fireEvent.click(screen.getByRole("button", { name: "Refresh notices" }));
      await screen.findByText("Refreshes: 1");
    }
    await returns.findByRole("option", { name: /LE-MONTHLY/ });
    await calendar.findByText("LE-MONTHLY");
    expect(onTrack().getByText("1")).toBeVisible();
    expect(calendar.getByText("1–1 of 1")).toBeVisible();
    expect(
      upcoming.queryByRole("heading", { name: "Synthetic large exposure" }),
    ).toBeNull();
    expect(calls.get("BK-SYNTH001")).toBeGreaterThanOrEqual(2);
  },
);

it("refreshes the affected bank on new status notices without losing metadata errors", async () => {
  render(
    view(
      <>
        <RegulatoryRuleStatus code="IRRBB-PILOT" />
        <Assessments />
        <Feed />
      </>,
    ),
  );
  await screen.findByText("Rule assessment: false");
  await screen.findByText("Notices: 0");
  await waitFor(() => expect(calls.get("BK-SYNTH002")).toBe(1));
  irrStatus = "in_force";
  noticeId = "change-1";
  fireEvent.click(screen.getByRole("button", { name: "Refresh notices" }));
  await screen.findByText("Rule assessment: true");
  await screen.findByText("Refreshes: 1");
  expect(screen.getByText("In force")).toBeVisible();
  expect(calls.get("BK-SYNTH002")).toBe(1);
  const afterChange = calls.get("BK-SYNTH001");
  fireEvent.click(screen.getByRole("button", { name: "Refresh notices" }));
  await screen.findByText("Refreshes: 2");
  expect(calls.get("BK-SYNTH001")).toBe(afterChange);
  fail = true;
  noticeId = "change-2";
  fireEvent.click(screen.getByRole("button", { name: "Refresh notices" }));
  await screen.findByText("Rule assessment: error");
  expect(screen.queryByText("Rule assessment: true")).toBeNull();
  fail = false;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  await screen.findByText("Rule assessment: true");
});

it("refreshes mounted rule assessments on window focus even while the cache is fresh", async () => {
  render(view(<Assessments />));
  await screen.findByText("Rule assessment: false");
  focusManager.setFocused(false);
  irrStatus = "in_force";
  await act(async () => {
    focusManager.setFocused(true);
  });
  await screen.findByText("Rule assessment: true");
});
