// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import InstitutionProfilePage from "../app/(app)/institution/page";
import RelatedPartiesPage from "../app/(app)/institution/parties/page";
import NameHistoryPage from "../app/(app)/institution/history/page";
import OutletsPage from "../app/(app)/institution/outlets/page";
import ProductsLicencesPage from "../app/(app)/institution/products/page";
import ProtectedDataNotice from "../components/ui/ProtectedDataNotice";
import { createAuthorityQueryClient } from "../lib/api/QueryAuthorityBoundary";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { useBiAlerts, useDeactivateBiAlert } from "../lib/api/bi";
import { institutionProfileApi } from "../lib/api/client";
import { setAccessToken } from "../lib/api/token";
import {
  bindProtectedDataCache,
  clearProtectedData,
  observeProtectedData,
  protectedDataMessages,
  protectedDataState,
} from "../lib/api/protectedData";

const BANK = "BK-SAMP0001";
const PARTY = "00000000-0000-4000-8000-000000000017";
const profileKey = ["institution-profile", BANK];
const disposals: (() => void)[] = [];

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SAMP0001", currency: "GHS" } }),
}));

afterEach(() => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  clearProtectedData();
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function party(name = "Synthetic owner") {
  return {
    id: PARTY,
    bank_id: BANK,
    full_name: name,
    party_type: "individual",
    status: "active",
    contact: {},
    roles: [],
    shareholdings: [],
    regulated_elsewhere: false,
    regulated_jurisdiction: null,
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
  };
}

function profileResponse(state = "available", name = "Synthetic owner") {
  return new Response(
    JSON.stringify({
      bank_id: BANK,
      profile: null,
      related_parties: [
        party(state === "available" ? name : "🔒 Protected data"),
      ],
      name_history: [],
      outlets: [],
      licenses: [],
      products: [],
    }),
    {
      headers: {
        "Content-Type": "application/json",
        "X-Aequoros-Protected-Data": JSON.stringify({ [BANK]: state }),
      },
    },
  );
}

function refusal(state: "revoked" | "unavailable") {
  return new Response(
    JSON.stringify({
      error: {
        code: state === "revoked" ? "bank_key_revoked" : "bank_key_unavailable",
        message: protectedDataMessages[state],
      },
    }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  );
}

function mount(children: ReactNode) {
  setAccessToken("synthetic-session");
  const client = createAuthorityQueryClient();
  const unsubscribe = bindProtectedDataCache(client);
  disposals.push(() => {
    unsubscribe();
    client.clear();
  });
  render(
    <QueryAuthorityScopeProvider
      scope={{ tenantId: "tenant", authorityId: "actor" }}
    >
      <QueryClientProvider client={client}>
        <ProtectedDataNotice />
        {children}
      </QueryClientProvider>
    </QueryAuthorityScopeProvider>,
  );
  return client;
}

it.each(["manual", "automatic"] as const)(
  "recovers an empty register after Add party fails using %s retry without losing its draft",
  async (retry) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let available = true;
    let created = false;
    let reads = 0;
    const draft = "Synthetic new owner";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          if (!available) return refusal("unavailable");
          expect(JSON.parse(String(init.body))).toMatchObject({
            full_name: draft,
          });
          created = true;
          return Response.json(party(draft));
        }
        reads++;
        if (created) return profileResponse("available", draft);
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (reads > 2) {
          headers["X-Aequoros-Protected-Data"] = JSON.stringify({
            [BANK]: available ? "available" : "unavailable",
          });
        }
        return new Response(
          JSON.stringify({
            bank_id: BANK,
            profile: null,
            related_parties: [],
            name_history: [],
            outlets: [],
            licenses: [],
            products: [],
          }),
          { headers },
        );
      }),
    );
    const client = mount(<RelatedPartiesPage />);
    await userEvent.click(
      screen.getByRole("button", { name: "Add related party" }),
    );
    await userEvent.type(screen.getByLabelText(/Full name/), draft);
    await userEvent.type(
      screen.getByLabelText(/Reason for change/),
      "Synthetic new party",
    );
    available = false;
    await userEvent.click(screen.getByRole("button", { name: "Add party" }));
    await waitFor(() => expect(reads).toBe(2));
    expect(protectedDataState(BANK)).toBe("unavailable");
    expect(screen.getByLabelText(/Full name/)).toHaveValue(draft);
    expect(client.getQueryState(profileKey)?.isInvalidated).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await waitFor(() => expect(reads).toBe(3));
    expect(protectedDataState(BANK)).toBe("unavailable");
    expect(
      screen.getAllByText(protectedDataMessages.unavailable).length,
    ).toBeGreaterThan(0);
    available = true;
    if (retry === "manual") {
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    } else {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
    }
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    expect(reads).toBe(4);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByLabelText(/Full name/)).toHaveValue(draft);
    await userEvent.click(screen.getByRole("button", { name: "Add party" }));
    await waitFor(() =>
      expect(screen.queryByLabelText(/Full name/)).toBeNull(),
    );
    expect(await screen.findByText(draft)).toBeVisible();
  },
);

it.each(["party", "shareholding"] as const)(
  "preserves a conditional existing-party %s editor through cache purge and recovery",
  async (editor) => {
    let state = "available";
    let holdRead = false;
    let finishRead: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        holdRead
          ? new Promise<Response>((resolve) => {
              finishRead = resolve;
            })
          : profileResponse(state),
      ),
    );
    const client = mount(<RelatedPartiesPage />);
    await userEvent.click(await screen.findByText("Synthetic owner"));
    await userEvent.click(
      screen.getByRole("button", {
        name: editor === "party" ? "Edit" : "Add shareholding",
      }),
    );
    const label = editor === "party" ? /Full name/ : /Share type/;
    await userEvent.clear(screen.getByLabelText(label));
    await userEvent.type(screen.getByLabelText(label), "Unsaved draft");
    if (editor === "shareholding") {
      await userEvent.selectOptions(
        screen.getByLabelText(/Ultimate beneficial owner/),
        PARTY,
      );
    }
    holdRead = true;
    state = "revoked";
    await act(async () => {
      await observeProtectedData(profileResponse(state));
    });
    expect(client.getQueryData(profileKey)).toBeUndefined();
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    expect(screen.getByLabelText(label)).toBeVisible();
    expect(screen.getByLabelText(label)).toHaveValue("Unsaved draft");
    expect(screen.getByText(protectedDataMessages.revoked)).toBeVisible();
    await act(async () => {
      finishRead?.(profileResponse(state));
    });
    await waitFor(() => expect(screen.getByLabelText(label)).toBeVisible());
    expect(screen.getByLabelText(label)).toHaveValue("Unsaved draft");
    expect(screen.queryByText("Synthetic owner")).toBeNull();
    if (editor === "shareholding") {
      expect(screen.getByLabelText(/Ultimate beneficial owner/)).toHaveValue(
        PARTY,
      );
    }
    holdRead = false;
    state = "available";
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    expect(screen.getByLabelText(label)).toHaveValue("Unsaved draft");
    if (editor === "shareholding") {
      expect(screen.getByLabelText(/Ultimate beneficial owner/)).toHaveValue(
        PARTY,
      );
    }
  },
);

it.each(["revoked", "unavailable"] as const)(
  "learns %s from a generated-client mutation refusal and keeps the party draft",
  async (reason) => {
    let state = "available";
    let name = "Synthetic owner";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "PUT") {
          if (state !== "available") return refusal(reason);
          const payload = JSON.parse(String(init.body)) as {
            full_name: string;
          };
          name = payload.full_name;
          return new Response(JSON.stringify(party(name)), {
            headers: { "Content-Type": "application/json" },
          });
        }
        return profileResponse(state, name);
      }),
    );
    const client = mount(<RelatedPartiesPage />);
    client.setQueryData(["de-batch", BANK, "other"], {
      name: "Cached private owner",
    });
    await userEvent.click(await screen.findByText("Synthetic owner"));
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getByLabelText(/Full name/));
    await userEvent.type(
      screen.getByLabelText(/Full name/),
      "Unsaved new name",
    );
    await userEvent.type(
      screen.getByLabelText(/Reason for change/),
      "Synthetic edit",
    );
    state = reason;
    await userEvent.click(screen.getByRole("button", { name: "Save party" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe(reason));
    await waitFor(() =>
      expect(screen.getByLabelText(/Full name/)).toBeVisible(),
    );
    expect(client.getQueryData(["de-batch", BANK, "other"])).toBeUndefined();
    expect(screen.getByLabelText(/Full name/)).toHaveValue("Unsaved new name");
    expect(
      screen.getAllByText(protectedDataMessages[reason]).length,
    ).toBeGreaterThan(0);
    state = "available";
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    await userEvent.click(screen.getByRole("button", { name: "Save party" }));
    await waitFor(() =>
      expect(screen.queryByLabelText(/Full name/)).toBeNull(),
    );
    expect(screen.getAllByText("Unsaved new name").length).toBeGreaterThan(0);
  },
);

function StopAlert() {
  const mutation = useDeactivateBiAlert(BANK);
  return (
    <button
      onClick={() =>
        mutation.mutate({ alertId: PARTY, reason: "Synthetic edit" })
      }
    >
      Stop alert
    </button>
  );
}

it.each(["revoked", "unavailable"] as const)(
  "learns %s from the BI notification client and purges sibling plaintext",
  async (reason) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => refusal(reason)),
    );
    const client = mount(<StopAlert />);
    client.setQueryData(profileKey, { name: "Cached private owner" });
    await userEvent.click(screen.getByRole("button", { name: "Stop alert" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe(reason));
    expect(client.getQueryData(profileKey)).toBeUndefined();
    expect(screen.getByText(protectedDataMessages[reason])).toBeVisible();
  },
);

function AlertRead() {
  const query = useBiAlerts(BANK);
  return <span>{query.data ? "Alerts loaded" : "Alerts loading"}</span>;
}

it.each(["generated", "notifications"] as const)(
  "rejects delayed availability from the %s client after a newer bank lock",
  async (clientKind) => {
    let state = "available";
    let delayNext = false;
    let finishRead: ((response: Response) => void) | undefined;
    const responseFor = (notifications: boolean, reason: string) =>
      notifications
        ? new Response(JSON.stringify({ alerts: [] }), {
            headers: {
              "Content-Type": "application/json",
              "X-Aequoros-Protected-Data": JSON.stringify({ [BANK]: reason }),
            },
          })
        : profileResponse(reason);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const notifications = String(input).endsWith("/bi/alerts");
        if (delayNext && notifications === (clientKind === "notifications")) {
          delayNext = false;
          return new Promise<Response>((resolve) => {
            finishRead = resolve;
          });
        }
        return responseFor(notifications, state);
      }),
    );
    const client = mount(
      <>
        <RelatedPartiesPage />
        <AlertRead />
      </>,
    );
    await screen.findByText("Synthetic owner");
    await screen.findByText("Alerts loaded");
    delayNext = true;
    const pending = client.refetchQueries({
      predicate: (query) =>
        clientKind === "generated"
          ? query.queryKey.includes("institution-profile")
          : !query.queryKey.includes("institution-profile"),
    });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    state = "revoked";
    await act(async () => {
      await institutionProfileApi.getInstitutionProfile({ bankId: BANK });
    });
    await waitFor(() => expect(protectedDataState(BANK)).toBe("revoked"));
    await act(async () => {
      finishRead?.(responseFor(clientKind === "notifications", "available"));
      await pending;
    });
    expect(protectedDataState(BANK)).toBe("revoked");
    expect(screen.getByText(protectedDataMessages.revoked)).toBeVisible();
    expect(screen.queryByText("Synthetic owner")).toBeNull();
    state = "available";
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    expect(await screen.findByText("Synthetic owner")).toBeVisible();
  },
);

it.each([
  {
    kind: "history",
    Page: NameHistoryPage,
    name: "Synthetic prior name",
    label: /Previous name/,
  },
  {
    kind: "outlet",
    Page: OutletsPage,
    name: "Synthetic outlet",
    label: /^Name/,
  },
  {
    kind: "product",
    Page: ProductsLicencesPage,
    name: "Synthetic product",
    label: /^Name/,
  },
  {
    kind: "licence",
    Page: ProductsLicencesPage,
    name: "Synthetic licence",
    label: /Licence name/,
  },
])(
  "keeps the existing $kind editor visible while read-only rows are purged",
  async ({ Page, name, label }) => {
    let locked = false;
    let finishRead: ((response: Response) => void) | undefined;
    const common = {
      id: PARTY,
      bank_id: BANK,
      created_at: "2026-06-30T00:00:00Z",
      updated_at: "2026-06-30T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (locked)
          return new Promise<Response>((resolve) => {
            finishRead = resolve;
          });
        return new Response(
          JSON.stringify({
            bank_id: BANK,
            profile: null,
            related_parties: [],
            name_history: [
              {
                ...common,
                previous_name: "Synthetic prior name",
                changed_on: null,
                change_reason: null,
              },
            ],
            outlets: [
              {
                ...common,
                name: "Synthetic outlet",
                outlet_type: "branch",
                status: "active",
                address: {},
                outlet_number: null,
                opened_on: null,
                closed_on: null,
                relocated_from: null,
              },
            ],
            products: [
              {
                ...common,
                name: "Synthetic product",
                product_type: "lending",
                status: "approved",
                approval_reference: null,
              },
            ],
            licenses: [
              {
                ...common,
                license_name: "Synthetic licence",
                status: "active",
                license_class: null,
                issued_on: null,
              },
            ],
          }),
          { headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const client = mount(<Page />);
    const item = await screen.findByText(name);
    const row = item.closest("tr") ?? item.closest("li");
    expect(row).not.toBeNull();
    await userEvent.click(
      within(row as HTMLElement).getByRole("button", {
        name: "Edit",
      }),
    );
    await userEvent.clear(screen.getByLabelText(label));
    await userEvent.type(screen.getByLabelText(label), "Unsaved draft");
    locked = true;
    await act(async () => {
      await observeProtectedData(profileResponse("revoked"));
    });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    expect(client.getQueryData(profileKey)).toBeUndefined();
    expect(screen.getByLabelText(label)).toBeVisible();
    expect(screen.getByLabelText(label)).toHaveValue("Unsaved draft");
    expect(screen.getByText(protectedDataMessages.revoked)).toBeVisible();
    await act(async () => {
      finishRead?.(profileResponse("revoked"));
    });
    await waitFor(() => expect(client.getQueryData(profileKey)).toBeDefined());
    expect(screen.getByLabelText(label)).toBeVisible();
    expect(screen.getByLabelText(label)).toHaveValue("Unsaved draft");
  },
);

it("keeps the corporate-profile draft visible during a locked refetch", async () => {
  let locked = false;
  let finishRead: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).includes("/institution-profile")) {
        return new Response(JSON.stringify({ anchors: [] }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      if (locked)
        return new Promise<Response>((resolve) => {
          finishRead = resolve;
        });
      return new Response(
        JSON.stringify({
          bank_id: BANK,
          related_parties: [],
          name_history: [],
          outlets: [],
          products: [],
          licenses: [],
          profile: {
            bank_id: BANK,
            institution_type: "bank",
            legal_entity_structure: "limited_liability",
            tin: "Synthetic tax ID",
            warnings: [],
            traded_on_exchange: false,
            created_at: "2026-06-30T00:00:00Z",
            updated_at: "2026-06-30T00:00:00Z",
          },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  mount(<InstitutionProfilePage />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit profile" }),
  );
  await userEvent.clear(screen.getByLabelText("TIN"));
  await userEvent.type(screen.getByLabelText("TIN"), "Unsaved draft");
  locked = true;
  await act(async () => {
    await observeProtectedData(profileResponse("revoked"));
  });
  await waitFor(() => expect(finishRead).toBeTypeOf("function"));
  expect(screen.getByLabelText("TIN")).toBeVisible();
  expect(screen.getByLabelText("TIN")).toHaveValue("Unsaved draft");
  expect(screen.getByText(protectedDataMessages.revoked)).toBeVisible();
  await act(async () => {
    finishRead?.(profileResponse("revoked"));
  });
  expect(screen.getByLabelText("TIN")).toHaveValue("Unsaved draft");
});
