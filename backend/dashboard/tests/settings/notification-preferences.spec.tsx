// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import NotificationPreferencesPage from "@/app/(app)/settings/notifications/page";
import NotificationDeliveriesPage from "@/app/(app)/settings/notifications/deliveries/page";
import { apiBaseUrl } from "@/lib/api/client";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { setAccessToken } from "@/lib/api/token";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/settings/notifications",
  useSearchParams: () => new URLSearchParams(""),
}));

const server = setupServer();

function preferences(choice: string | null) {
  return {
    email_configured: true,
    email_enabled: true,
    digest_hour: 7,
    time_zone: "Africa/Accra",
    categories: [
      {
        key: "filing",
        label: "Return review and filing",
        events: [
          {
            key: "filing.review_requested",
            label: "A return is waiting for your review",
            description: "A return reached a review stage you can act on.",
            default_email: "immediate",
            email_mode: choice ?? "immediate",
            choice,
            locked_by: null,
          },
        ],
      },
      {
        key: "regulator",
        label: "Regulator responses",
        events: [
          {
            key: "regulator.declined",
            label: "The regulator declined a return",
            description: "The regulator refused a filed return outright.",
            default_email: "immediate",
            email_mode: "immediate",
            choice: null,
            locked_by: "platform",
          },
        ],
      },
    ],
  };
}

let client: QueryClient;
let saved: unknown[];
let adminStatus: number;

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
  saved = [];
  adminStatus = 403;
  server.use(
    http.get(`${apiBaseUrl}/notifications/preferences`, () =>
      HttpResponse.json(preferences(null)),
    ),
    http.put(`${apiBaseUrl}/notifications/preferences`, async ({ request }) => {
      const body = (await request.json()) as { choices: Record<string, string | null> };
      saved.push(body);
      return HttpResponse.json(preferences(body.choices["filing.review_requested"] ?? null));
    }),
    http.get(`${apiBaseUrl}/notifications/settings`, () =>
      adminStatus === 403
        ? HttpResponse.json({ detail: "Forbidden" }, { status: 403 })
        : HttpResponse.json({
            email_configured: true,
            email_enabled: true,
            time_zone: "UTC",
            digest_hour: 7,
            reminders_enabled: true,
            reminder_after_hours: 24,
            escalate_after_hours: 48,
            policies: [],
          }),
    ),
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

it("shows each notification with its plain-language choice and saves a change", async () => {
  render(view(<NotificationPreferencesPage />));
  const group = await screen.findByRole("radiogroup", {
    name: "Email for “A return is waiting for your review”",
  });
  expect(screen.getByText(/Daily summaries arrive at 07:00 \(Africa\/Accra\)/)).toBeVisible();
  expect(screen.getByText("Organisation default: email right away.")).toBeVisible();

  fireEvent.click(within(group).getByRole("radio", { name: "Daily summary" }));
  await waitFor(() =>
    expect(saved).toEqual([{ choices: { "filing.review_requested": "digest" } }]),
  );
  await waitFor(() =>
    expect(within(group).getByRole("radio", { name: "Daily summary" })).toHaveAttribute(
      "aria-checked",
      "true",
    ),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Use the organisation default" }));
  await waitFor(() =>
    expect(saved.at(-1)).toEqual({ choices: { "filing.review_requested": null } }),
  );
});

it("locks what the platform always emails", async () => {
  render(view(<NotificationPreferencesPage />));
  const group = await screen.findByRole("radiogroup", {
    name: "Email for “The regulator declined a return”",
  });
  for (const radio of within(group).getAllByRole("radio")) {
    expect(radio).toBeDisabled();
  }
  expect(screen.getByText(/Always emailed/)).toBeVisible();
});

it("offers administrator pages only to administrators", async () => {
  render(view(<NotificationPreferencesPage />));
  await screen.findByText("Return review and filing");
  expect(screen.queryByRole("link", { name: "Delivery log" })).toBeNull();

  cleanup();
  client.clear();
  adminStatus = 200;
  render(view(<NotificationPreferencesPage />));
  expect(await screen.findByRole("link", { name: "Delivery log" })).toHaveAttribute(
    "href",
    "/settings/notifications/deliveries",
  );
});

it("the delivery log explains held-back email and resends it", async () => {
  adminStatus = 200;
  const retried: string[] = [];
  const delivery = {
    id: "dlv-1",
    event_key: "filing.review_requested",
    event_label: "A return is waiting for your review",
    title: null,
    recipient_user_id: "u-1",
    recipient_name: "Kofi Boateng",
    address: "kofi@example.test",
    mode: "immediate",
    status: "suppressed",
    attempts: 0,
    last_error: "not_on_allowlist",
    next_attempt_at: "2026-11-23T09:00:00Z",
    sent_at: null,
    created_at: "2026-11-23T09:00:00Z",
  };
  server.use(
    http.get(`${apiBaseUrl}/notifications/deliveries`, () =>
      HttpResponse.json({
        deliveries: [delivery],
        total: 1,
        counts: { suppressed: 1, sent: 4 },
        limit: 25,
        offset: 0,
        has_more: false,
      }),
    ),
    http.post(`${apiBaseUrl}/notifications/deliveries/:id/retry`, ({ params }) => {
      retried.push(String(params.id));
      return HttpResponse.json({ ...delivery, status: "pending", last_error: null });
    }),
  );
  render(view(<NotificationDeliveriesPage />));
  expect(await screen.findByText("Blocked by the test-mode allowlist")).toBeVisible();
  expect(screen.getByText(/Hidden — you can’t see this notice/)).toBeVisible();
  expect(screen.getByRole("button", { name: /All \(5\)/ })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Send again" }));
  await waitFor(() => expect(retried).toEqual(["dlv-1"]));
});
