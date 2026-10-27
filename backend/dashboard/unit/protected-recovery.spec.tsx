// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it, vi } from "vitest";
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

const BANK = "BK-SAMP0001";
const disposals: (() => void)[] = [];

vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: "BK-SAMP0001" } }),
}));

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
