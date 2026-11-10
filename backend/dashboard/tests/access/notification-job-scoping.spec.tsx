// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import UnifiedBell from "@/components/shell/UnifiedBell";
import { useNotifications, useMarkNotificationRead, useRefreshBankData } from "@/lib/api/hooks";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";
import { apiBaseUrl } from "@/lib/api/client";
import { setAccessToken } from "@/lib/api/token";

vi.mock("@/lib/api/regulatoryRules", () => ({
  useInForceAlerts: () => ({
    data: { items: [], total: 0 },
    isLoading: false,
    error: null,
  }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SYNTH001" } }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/features/live/components/moduleDisplay", () => ({
  LIVE_MODULE_HREFS: { capital: "/capital" },
  LIVE_MODULE_LABELS: { capital: "Capital" },
}));

const server = setupServer();

const VISIBLE_FEED = {
  notifications: [
    {
      id: "notif-visible-1",
      type: "reporting.deadline.due_soon",
      entity_type: "bank",
      entity_id: "BK-SYNTH001",
      recipient_user_id: "user-synthetic",
      title: "Deadline approaching",
      body: "Synthetic deadline body",
      severity: "warning",
      created_at: "2027-01-01T00:00:00Z",
      read_at: null,
    },
    {
      id: "notif-visible-2",
      type: "reporting.package.approved",
      entity_type: "regulatory_package",
      entity_id: "pkg-1",
      recipient_user_id: null,
      title: "Package approved",
      body: "Synthetic approval body",
      severity: "info",
      created_at: "2027-01-02T00:00:00Z",
      read_at: null,
    },
  ],
  unread_count: 2,
  total: 2,
  has_more: false,
  offset: 0,
  limit: 50,
};

let client: QueryClient;
let notificationFetches: number;
let jobFetches: number;
let markReadDenies: boolean;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  setAccessToken(null);
});
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  setAccessToken("synthetic-token");
  notificationFetches = 0;
  jobFetches = 0;
  markReadDenies = true;
  server.use(
    http.get(`${apiBaseUrl}/notifications`, () => {
      notificationFetches += 1;
      return HttpResponse.json(VISIBLE_FEED);
    }),
    http.post(`${apiBaseUrl}/notifications/:id/read`, () => {
      // The scoped read path answers a hidden row with the same opaque 404 as
      // an unknown id.
      if (!markReadDenies) return HttpResponse.json({}, { status: 200 });
      return HttpResponse.json(
        { detail: "Notification not found." },
        { status: 404 },
      );
    }),
    http.post(`${apiBaseUrl}/banks/BK-SYNTH001/refresh`, () =>
      HttpResponse.json({ job_id: "synthetic-job" }),
    ),
    http.get(`${apiBaseUrl}/jobs/:jobId`, () => {
      jobFetches += 1;
      // The scoped job read answers a hidden row with the same opaque 404 as
      // an unknown id.
      return HttpResponse.json({ detail: "Job not found." }, { status: 404 });
    }),
  );
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

it("the bell badge and inbox rows mirror the scoped API feed exactly", async () => {
  render(view(<UnifiedBell />));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Notifications \(2\)/ })).toBeVisible(),
  );
  fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
  const inbox = await screen.findByRole("dialog", { name: "Notifications" });
  await waitFor(() =>
    expect(screen.getByText("Deadline approaching")).toBeVisible(),
  );
  // Exactly the rows the scoped API returned — no cached or hidden ids leak in.
  expect(withinRows(inbox)).toEqual([
    "Deadline approaching",
    "Package approved",
  ]);
});

function withinRows(dialog: HTMLElement): string[] {
  return Array.from(dialog.querySelectorAll("ul li span")).flatMap((node) => {
    const text = node.textContent ?? "";
    return ["Deadline approaching", "Package approved"].includes(text)
      ? [text]
      : [];
  });
}

it("a mark-read denial for a since-hidden row refetches the scoped feed", async () => {
  function Harness() {
    const feed = useNotifications();
    const markRead = useMarkNotificationRead();
    return (
      <button
        type="button"
        onClick={() => markRead.mutate("notif-visible-1")}
      >
        {`fetches:${notificationFetches} rows:${feed.data?.notifications.length ?? 0}`}
      </button>
    );
  }
  render(view(<Harness />));
  await waitFor(() => expect(notificationFetches).toBe(1));
  fireEvent.click(screen.getByRole("button"));
  // The opaque 404 is not retried, and the denial invalidates the feed so the
  // since-hidden row drops out instead of lingering in the open drawer.
  await waitFor(() => expect(notificationFetches).toBeGreaterThanOrEqual(2));
  expect(await screen.findByText(/^fetches:/)).toHaveTextContent("rows:2");
});

it("job polling stops cleanly when the scoped job read denies", async () => {
  function Harness() {
    const refresh = useRefreshBankData("BK-SYNTH001");
    const message =
      refresh.error instanceof Error ? refresh.error.message : "";
    return (
      <button type="button" onClick={() => refresh.mutate({ asOfDate: "2027-01-31", reason: "synthetic" })}>
        {refresh.isPending ? "polling" : message || "idle"}
      </button>
    );
  }
  render(view(<Harness />));
  fireEvent.click(screen.getByRole("button", { name: "idle" }));
  await waitFor(() =>
    expect(
      screen.getByText("This background job is no longer available for your account."),
    ).toBeVisible(),
  );
  // One denial ends the poll: no retry loop against an authorization decision.
  expect(jobFetches).toBe(1);
});
