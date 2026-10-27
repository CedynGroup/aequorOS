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
