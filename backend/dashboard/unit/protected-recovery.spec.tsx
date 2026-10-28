// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import ProtectedDataNotice from "../components/ui/ProtectedDataNotice";
import {
  bindProtectedDataCache,
  clearProtectedData,
  containsLockedData,
  observeProtectedData,
  protectedDataFetch,
  protectedDataMessages,
  protectedDataState,
} from "../lib/api/protectedData";
import { setAccessToken } from "../lib/api/token";
import { institutionProfileApi } from "../lib/api/client";

const BANK = "BK-SAMP0001";
const disposals: (() => void)[] = [];
const context = vi.hoisted(() => ({
  bank: { id: "BK-SAMP0001" } as { id: string } | undefined,
}));

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => context,
}));

beforeEach(() => {
  context.bank = { id: BANK };
  setAccessToken("synthetic-session");
});

afterEach(() => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  clearProtectedData();
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function NumericScreen() {
  const query = useQuery({
    queryKey: ["live-summary", BANK],
    queryFn: async () => {
      const response = await protectedDataFetch(
        `/api/v1/banks/${BANK}/live-summary`,
      );
      return (await response.json()) as { ratio: number };
    },
  });
  return <p>Ratio {query.data?.ratio}</p>;
}

it.each(
  (["revoked", "unavailable"] as const).flatMap((state) =>
    (["manual", "automatic"] as const).map((retry) => ({ state, retry })),
  ),
)(
  "recovers a numeric-only screen from $state using $retry retry",
  async ({ state, retry }) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    setAccessToken("synthetic-session");
    let restored = false;
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        requests.push(url);
        if (url.endsWith("/live-summary"))
          return Response.json({ ratio: 1.25 });
        if (url.endsWith(`/banks/${BANK}`)) {
          return Response.json(
            { id: BANK },
            {
              headers: {
                "X-Aequoros-Protected-Data": JSON.stringify({
                  [BANK]: restored ? "available" : state,
                }),
              },
            },
          );
        }
        throw new Error(`Unexpected recovery request: ${url}`);
      }),
    );
    await observeProtectedData(
      new Response(null, {
        headers: {
          "X-Aequoros-Protected-Data": JSON.stringify({ [BANK]: state }),
        },
      }),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const unsubscribe = bindProtectedDataCache(client);
    disposals.push(() => {
      unsubscribe();
      client.clear();
    });
    client.setQueryData(["institution-profile", BANK], {
      fullName: "🔒 Protected data",
    });
    render(
      <QueryClientProvider client={client}>
        <ProtectedDataNotice />
        <NumericScreen />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Ratio 1.25")).toBeVisible();
    expect(screen.getByText(protectedDataMessages[state])).toBeVisible();
    expect(protectedDataState(BANK)).toBe(state);
    restored = true;
    if (retry === "manual") {
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    } else {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
    }
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    expect(screen.queryByText(protectedDataMessages[state])).toBeNull();
    expect(screen.getByText("Ratio 1.25")).toBeVisible();
    expect(
      requests.filter((url) => url.endsWith(`/banks/${BANK}`)),
    ).toHaveLength(1);
    expect(requests.some((url) => url.endsWith("/institution-profile"))).toBe(
      false,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(
      requests.filter((url) => url.endsWith(`/banks/${BANK}`)),
    ).toHaveLength(1);
  },
);

function disclosure(state: string, bank = BANK) {
  return Response.json(
    { id: bank },
    {
      headers: {
        "X-Aequoros-Protected-Data": JSON.stringify({ [bank]: state }),
      },
    },
  );
}

function mountNotice(children?: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const unsubscribe = bindProtectedDataCache(client);
  disposals.push(() => {
    unsubscribe();
    client.clear();
  });
  return {
    ...render(
      <QueryClientProvider client={client}>
        <ProtectedDataNotice />
        {children}
      </QueryClientProvider>,
    ),
    client,
  };
}

it.each(["revoked", "unavailable"] as const)(
  "retains %s after a failed probe and retries until readiness returns",
  async (state) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error("Synthetic network failure"))
      .mockResolvedValueOnce(disclosure(state))
      .mockResolvedValueOnce(disclosure("available"));
    vi.stubGlobal("fetch", fetch);
    await observeProtectedData(disclosure(state));
    mountNotice();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(protectedDataState(BANK)).toBe(state);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(screen.getByText(protectedDataMessages[state])).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetch).toHaveBeenCalledTimes(3);
  },
);

it.each(["manual", "automatic"] as const)(
  "uses the active bank-scoped protected query for %s retry without a fallback",
  async (retry) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await observeProtectedData(disclosure("revoked"));
    let restored = false;
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toMatch(
        new RegExp(`/banks/${BANK}/institution-profile$`),
      );
      return Response.json(
        { name: restored ? "Recovered owner" : "🔒 Protected data" },
        {
          headers: {
            "X-Aequoros-Protected-Data": JSON.stringify({
              [BANK]: restored ? "available" : "revoked",
            }),
          },
        },
      );
    });
    vi.stubGlobal("fetch", fetch);
    function Profile() {
      const query = useQuery({
        queryKey: ["institution-profile", "tenant", "authority", BANK],
        queryFn: async () =>
          (await (
            await institutionProfileApi.getInstitutionProfileRaw({
              bankId: BANK,
            })
          ).raw.json()) as { name: string },
      });
      return <p>{query.data?.name}</p>;
    }
    mountNotice(<Profile />);
    expect(await screen.findByText("🔒 Protected data")).toBeVisible();
    restored = true;
    if (retry === "manual")
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    else
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
    expect(await screen.findByText("Recovered owner")).toBeVisible();
    expect(protectedDataState(BANK)).toBe("available");
    expect(fetch).toHaveBeenCalledTimes(2);
  },
);

it.each(["available", "support", "no-bank"] as const)(
  "does not probe with %s disclosure context",
  async (state) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await observeProtectedData(
      disclosure(state === "no-bank" ? "revoked" : state),
    );
    if (state === "no-bank") context.bank = undefined;
    mountNotice();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetch).not.toHaveBeenCalled();
  },
);

it.each(["disabled", "static"] as const)(
  "falls back when the current protected query is %s and leaves another bank alone",
  async (mode) => {
    await observeProtectedData(disclosure("revoked"));
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toMatch(new RegExp(`/banks/${BANK}$`));
      return disclosure("available");
    });
    vi.stubGlobal("fetch", fetch);
    const otherRead = vi.fn(async () => "🔒 Protected data");
    const currentRead = vi.fn(async () => "🔒 Protected data");
    function Profiles() {
      useQuery({
        queryKey: ["institution-profile", BANK],
        queryFn: currentRead,
        initialData: "🔒 Protected data",
        enabled: mode !== "disabled",
        staleTime: mode === "static" ? "static" : Infinity,
      });
      useQuery({
        queryKey: ["institution-profile", "BK-SAMP0002"],
        queryFn: otherRead,
        initialData: "🔒 Protected data",
        staleTime: Infinity,
      });
      return null;
    }
    mountNotice(<Profiles />);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(protectedDataState(BANK)).toBe("available"));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(currentRead).not.toHaveBeenCalled();
    expect(otherRead).not.toHaveBeenCalled();
    expect(protectedDataState("BK-SAMP0002")).toBe("available");
  },
);

it("bounds slow probes, cancels a departed bank, and retries only the current bank", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const other = "BK-SAMP0002";
  await observeProtectedData(disclosure("revoked"));
  await observeProtectedData(disclosure("unavailable", other));
  let signal: AbortSignal | undefined;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith(`/banks/${BANK}`)) {
      signal = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      });
    }
    expect(String(input)).toMatch(new RegExp(`/banks/${other}$`));
    return disclosure("available", other);
  });
  vi.stubGlobal("fetch", fetch);
  const view = mountNotice();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  context.bank = { id: other };
  view.rerender(
    <QueryClientProvider client={view.client}>
      <ProtectedDataNotice />
    </QueryClientProvider>,
  );
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  await waitFor(() => expect(protectedDataState(other)).toBe("available"));
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(protectedDataState(BANK)).toBe("revoked");
});

it.each(["available", "support"] as const)(
  "cancels a pending fallback when disclosure becomes %s",
  async (state) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await observeProtectedData(disclosure("unavailable"));
    let signal: AbortSignal | undefined;
    const fetch = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        signal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      },
    );
    vi.stubGlobal("fetch", fetch);
    mountNotice();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await act(async () => {
      await observeProtectedData(disclosure(state));
    });
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

type BiRecoveryResponse = Readonly<{
  name: string;
  prefix: string;
  locked: unknown;
  recovered: unknown;
}>;

function biRecoveryResponses(message: string): BiRecoveryResponse[] {
  const rows = (parsed: boolean, locked: boolean) => [
    {
      id: "numeric",
      name: "Numeric sibling",
      [parsed ? "lockedReason" : "locked_reason"]: null,
    },
    {
      id: "identity",
      name: "Protected item",
      [parsed ? "lockedReason" : "locked_reason"]: locked ? message : null,
    },
  ];
  const responses: BiRecoveryResponse[] = [];
  for (const parsed of [false, true]) {
    for (const [collection, prefix] of [
      ["alerts", "bi-alerts"],
      ["subscriptions", "bi-subscriptions"],
      ["dashboards", "bi-dashboards"],
      ["versions", "bi-dashboard-versions"],
    ]) {
      const body = (locked: boolean) => {
        const items = rows(parsed, locked).map((row, index) => {
          if (collection === "dashboards") {
            const { name, ...metadata } = row;
            return { ...metadata, title: name };
          }
          if (collection === "versions")
            return {
              version: index + 1,
              title: row.name,
              [parsed ? "lockedReason" : "locked_reason"]:
                row[parsed ? "lockedReason" : "locked_reason"],
            };
          return collection === "subscriptions"
            ? {
                ...row,
                query:
                  row.id === "identity" && locked
                    ? null
                    : {
                        measures: ["loans.balance_rc"],
                        time: { as_of: "2026-08-31" },
                        filters: [],
                      },
              }
            : row;
        });
        return parsed &&
          (collection === "alerts" || collection === "subscriptions")
          ? items
          : { [collection]: items };
      };
      responses.push({
        name: `${parsed ? "parsed" : "raw"}-${collection}`,
        prefix,
        locked: body(true),
        recovered: body(false),
      });
    }
    responses.push({
      name: `${parsed ? "parsed" : "raw"}-detail`,
      prefix: "bi-dashboard",
      locked: { id: "identity", access: "restricted", message, widgets: [] },
      recovered: {
        id: "identity",
        access: "granted",
        message: "Ready",
        widgets: [{ id: "numeric", query: { measures: ["loans.balance_rc"] } }],
      },
    });
  }
  return responses;
}

function BiRecoveryRead({ response }: { response: BiRecoveryResponse }) {
  const query = useQuery({
    queryKey: [response.prefix, "tenant", "authority", BANK, response.name],
    staleTime: Infinity,
    queryFn: async () => {
      const reply = await protectedDataFetch(
        `/api/v1/banks/${BANK}/bi/${response.name}`,
      );
      return (await reply.json()) as unknown;
    },
  });
  const locked = JSON.stringify(query.data) === JSON.stringify(response.locked);
  return (
    <section aria-label={response.name}>
      <button disabled={locked}>Change {response.name}</button>
      <output>{JSON.stringify(query.data)}</output>
    </section>
  );
}

it.each(
  (["revoked", "unavailable"] as const).flatMap((state) =>
    (["manual", "automatic"] as const).map((retry) => ({ state, retry })),
  ),
)(
  "recovers successful locked BI collections and details after $state with $retry retry",
  async ({ state, retry }) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const responses = biRecoveryResponses(protectedDataMessages[state]);
    let restored = false;
    const reads = new Map<string, number>();
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      reads.set(url, (reads.get(url) ?? 0) + 1);
      if (url.endsWith("/live-summary")) return Response.json({ ratio: 1.25 });
      if (url.endsWith(`/banks/${BANK}`))
        return disclosure(restored ? "available" : state);
      const response = responses.find((candidate) =>
        url.endsWith(`/bi/${candidate.name}`),
      );
      if (!response) throw new Error(`Unexpected recovery request: ${url}`);
      return Response.json(restored ? response.recovered : response.locked, {
        headers: {
          "X-Aequoros-Protected-Data": JSON.stringify({
            [BANK]: restored ? "available" : state,
          }),
        },
      });
    });
    vi.stubGlobal("fetch", fetch);
    await observeProtectedData(disclosure(state));
    const view = mountNotice(
      <>
        <NumericScreen />
        {responses.map((response) => (
          <BiRecoveryRead key={response.name} response={response} />
        ))}
      </>,
    );
    const otherKey = ["bi-alerts", "tenant", "authority", "BK-SAMP0002"];
    view.client.setQueryData(otherKey, responses[0].locked);
    await waitFor(() => {
      for (const response of responses)
        expect(
          screen.getByRole("button", { name: `Change ${response.name}` }),
        ).toBeDisabled();
    });
    expect(screen.getByText("Ratio 1.25")).toBeVisible();
    restored = true;
    if (retry === "manual")
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    else
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
    await waitFor(() => {
      for (const response of responses) {
        expect(
          view.client.getQueryData([
            response.prefix,
            "tenant",
            "authority",
            BANK,
            response.name,
          ]),
        ).toEqual(response.recovered);
        expect(
          screen.getByRole("button", { name: `Change ${response.name}` }),
        ).toBeEnabled();
      }
    });
    expect(protectedDataState(BANK)).toBe("available");
    expect(screen.queryByText(protectedDataMessages[state])).toBeNull();
    expect(screen.getByText("Ratio 1.25")).toBeVisible();
    expect(view.client.getQueryData(otherKey)).toEqual(responses[0].locked);
    expect(view.client.getQueryState(otherKey)?.isInvalidated).toBe(false);
    expect(
      [...reads.entries()]
        .filter(([url]) => url.endsWith("/live-summary"))
        .map(([, count]) => count),
    ).toEqual([1]);
    for (const response of responses)
      expect(
        [...reads.entries()].find(([url]) =>
          url.endsWith(`/bi/${response.name}`),
        )?.[1],
      ).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    for (const response of responses)
      expect(
        [...reads.entries()].find(([url]) =>
          url.endsWith(`/bi/${response.name}`),
        )?.[1],
      ).toBe(2);
  },
);

it.each(["support", "no-bank"] as const)(
  "does not probe cached locked BI data in %s context",
  async (state) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await observeProtectedData(
      disclosure(state === "support" ? "support" : "revoked"),
    );
    if (state === "no-bank") context.bank = undefined;
    const locked = biRecoveryResponses(protectedDataMessages.revoked)[0].locked;
    function CachedBi() {
      useQuery({
        queryKey: ["bi-alerts", "tenant", "authority", BANK],
        queryFn: fetch,
        initialData: locked,
        staleTime: Infinity,
      });
      return null;
    }
    mountNotice(<CachedBi />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it.each(["revoked", "unavailable"] as const)(
  "discards a late BI recovery response from the previous authority after %s",
  async (state) => {
    await observeProtectedData(disclosure(state));
    let finish: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );
    const pending = protectedDataFetch(`/api/v1/banks/${BANK}/bi/alerts`);
    clearProtectedData();
    await observeProtectedData(disclosure("support"));
    finish?.(
      Response.json(
        {
          alerts: [
            {
              id: "identity",
              locked_reason: null,
              filters: [
                {
                  member: "counterparty.name",
                  values: ["Previous authority identity"],
                },
              ],
            },
          ],
        },
        {
          headers: {
            "X-Aequoros-Protected-Data": JSON.stringify({
              [BANK]: "available",
            }),
          },
        },
      ),
    );
    const response = await pending;
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: {
        code: "bank_key_unavailable",
        message: protectedDataMessages.unavailable,
      },
    });
    expect(protectedDataState(BANK)).toBe("support");
  },
);

it.each([
  { alerts: [{ locked_reason: null }] },
  [{ lockedReason: null }],
  { dashboards: [{ lockedReason: "" }] },
  {
    access: "restricted",
    message: "Your permissions do not cover this dashboard.",
  },
  { access: "granted", message: protectedDataMessages.unavailable },
])("does not retry ordinary numeric or authorization content: %j", (data) => {
  expect(containsLockedData(data)).toBe(false);
});
