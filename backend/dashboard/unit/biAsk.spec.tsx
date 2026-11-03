// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  BiAskReadFromJSON,
  BiQueryFromJSON,
  BiQueryToJSON,
} from "@aequoros/risk-service-api";
import {
  askQuestion,
  fetchQuestion,
  runQuestion,
  useBiAsk,
} from "../lib/api/askTransport";
import { ASK_POLL_MS, parseAskProposal } from "../lib/api/ask";
import { useBiAvailability } from "../lib/api/bi";
import { setAccessToken } from "../lib/api/token";
import AskPage from "../app/(app)/explore/ask/page";
import ExploreLayout from "../app/(app)/explore/layout";
import CommandPalette from "../components/shell/CommandPalette";
import AskAnswer from "../features/bi/components/AskAnswer";
import { providers, json, queryWire, result, columns } from "./biTestSupport";

const shell = vi.hoisted(() => ({
  nlqEnabled: true as boolean | undefined,
  push: vi.fn(),
}));
vi.mock("next-auth/react", () => ({
  getSession: async () => ({ accessToken: "session-token" }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: shell.push }),
  usePathname: () => "/explore",
}));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-test" },
    period: { periodEnd: new Date("2026-09-30") },
    isLoading: false,
  }),
  useModuleScope: () => ({
    modules: new Set(["bi", "command_center"]),
    organizationModules: new Set(),
    hasInstitutionAuthority: true,
    institutionClass: "bank",
    isResolved: true,
    biEnabled: true,
    nlqEnabled: shell.nlqEnabled,
  }),
}));
beforeEach(() => {
  vi.stubGlobal("React", React);
  setAccessToken(null);
  shell.nlqEnabled = true;
  shell.push.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setAccessToken(null);
});

const opaqueQuery = {
  ...queryWire,
  top_n: { dimension: "branch.code", n: 10, other: true },
  segment_scope: { kind: "branch", values: ["B001"] },
};
const proposalWire = (state = "proposed") => ({
  question_id: "6f2a1c90-1d3e-4a5b-8c6d-9e0f1a2b3c4d",
  state,
  message: "Check this question before it reads your figures.",
  question: "Loan balance by branch",
  as_of: "2026-09-30",
  catalogue_version: "v1",
  figures_offered: 40,
  query: state === "proposed" ? opaqueQuery : null,
  reading:
    state === "proposed"
      ? {
          sentence: "Loan balance grouped by Branch",
          clauses: [
            { kind: "figure", text: "Loan balance" },
            { kind: "grouping", text: "grouped by Branch" },
            { kind: "future_kind", text: "Restated at closing rates" },
          ],
          as_of: "2026-09-30",
          range_start: null,
          range_end: null,
          compare_to: null,
          member_ids: queryWire.measures,
        }
      : null,
  suggestions: [],
});

it("the real Ask transport preserves the opaque proposal in both directions despite generated rewrites", async () => {
  const parsed = BiQueryFromJSON(opaqueQuery);
  expect(parsed.topN).toBeTruthy();
  expect(BiQueryToJSON(parsed)).not.toHaveProperty("segment_scope");
  expect(BiAskReadFromJSON(proposalWire()).query).toHaveProperty("topN");
  const wireResult = {
    columns,
    rows: [["B001", "123.45"]],
    as_of: "2026-09-30",
    catalogue_version: "v1",
    dimension_count: 1,
    truncated: false,
  };
  const fetch = vi.fn(async (url: string) =>
    json(url.endsWith("/run") ? wireResult : proposalWire()),
  );
  vi.stubGlobal("fetch", fetch);
  const asked = await askQuestion(
    "BK/test",
    "Loan balance by branch",
    "2026-09-30",
  );
  const polled = await fetchQuestion("BK/test", asked.questionId);
  expect(asked.proposedQuery).toEqual(opaqueQuery);
  expect(polled.proposedQuery).toEqual(opaqueQuery);
  const answered = await runQuestion("BK/test", polled);
  expect(answered.rows).toEqual(wireResult.rows);
  expect(answered.columns[1].memberId).toBe("loans.balance_rc");
  expect(answered.catalogueVersion).toBe("v1");
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  expect(calls.map(([url]) => new URL(url).pathname)).toEqual([
    "/api/v1/banks/BK%2Ftest/bi/ask",
    `/api/v1/banks/BK%2Ftest/bi/ask/${asked.questionId}`,
    `/api/v1/banks/BK%2Ftest/bi/ask/${asked.questionId}/run`,
  ]);
  expect(JSON.parse(String(calls[0][1].body))).toEqual({
    question: "Loan balance by branch",
    as_of: "2026-09-30",
  });
  expect(JSON.parse(String(calls[2][1].body))).toEqual({ query: opaqueQuery });
  for (const [, init] of calls)
    expect(init).toMatchObject({
      cache: "no-store",
      headers: { Authorization: "Bearer session-token" },
    });
});

it.each(["proposed", "refused", "stopped"])(
  "real hook polling runs while translating and stops at %s",
  async (terminal) => {
    vi.useFakeTimers();
    let polls = 0;
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) =>
      json(
        init.method === "POST"
          ? proposalWire("translating")
          : proposalWire(++polls < 2 ? "translating" : terminal),
      ),
    );
    const p = providers();
    const hook = renderHook(() => useBiAsk("BK-test"), p);
    await act(async () => {
      await hook.result.current.ask.mutateAsync({
        question: "Loan balance by branch",
        asOf: "2026-09-30",
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(polls).toBe(1);
    expect(hook.result.current.proposal?.state).toBe("translating");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ASK_POLL_MS + 100);
    });
    expect(polls).toBe(2);
    expect(hook.result.current.proposal?.state).toBe(terminal);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ASK_POLL_MS * 3);
    });
    expect(polls).toBe(2);
    p.client.clear();
  },
);

it.each([true, false, undefined])(
  "the real flag reader decodes NLQ as %s and remains unresolved while loading",
  async (enabled) => {
    let complete: (value: Response) => void = () => {};
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const p = providers();
    const hook = renderHook(() => useBiAvailability(), p);
    expect(hook.result.current.nlqEnabled).toBeUndefined();
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await act(async () =>
      complete(
        json({
          bi_enabled: true,
          ...(enabled === undefined ? {} : { bi_nlq_enabled: enabled }),
        }),
      ),
    );
    await waitFor(() => expect(hook.result.current.isLoading).toBe(false));
    expect(hook.result.current.nlqEnabled).toBe(enabled);
    expect(hook.result.current.biEnabled).toBe(true);
    expect(
      new URL((fetch.mock.calls[0] as unknown as [string])[0]).pathname,
    ).toBe("/api/v1/feature-flags");
    p.client.clear();
  },
);

it("a failed flag read fails closed", async () => {
  vi.stubGlobal("fetch", async () => json({ message: "Unavailable" }, 503));
  const p = providers();
  const hook = renderHook(() => useBiAvailability(), p);
  await waitFor(() => expect(hook.result.current.isLoading).toBe(false));
  expect(hook.result.current).toMatchObject({
    biEnabled: false,
    nlqEnabled: false,
  });
  p.client.clear();
});

it("the tab and command palette reach Ask only when NLQ is enabled", () => {
  const onClose = vi.fn();
  const view = render(
    <>
      <ExploreLayout>Workspace</ExploreLayout>
      <CommandPalette open onClose={onClose} />
    </>,
  );
  const links = screen.getAllByRole("link", { name: "Ask a question" });
  expect(links).toHaveLength(2);
  for (const link of links)
    expect(link.getAttribute("href")).toBe("/explore/ask");
  fireEvent.click(links[1]);
  expect(onClose).toHaveBeenCalled();
  shell.nlqEnabled = undefined;
  view.rerender(
    <>
      <ExploreLayout>Workspace</ExploreLayout>
      <CommandPalette open onClose={onClose} />
    </>,
  );
  expect(screen.queryByRole("link", { name: "Ask a question" })).toBeNull();
});

it("Ask renders the server proposal, confirms the identical query and then draws the answer", async () => {
  const fetch = vi.fn(async (url: string) =>
    json(
      url.endsWith("/run")
        ? { columns, rows: [["B001", "0"]], truncated: false }
        : proposalWire(),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const p = providers();
  render(<AskPage />, p);
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
    target: { value: "Loan balance by branch" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Work out my question" }));
  await screen.findByText(proposalWire().message);
  expect(screen.getByText("Restated at closing rates")).toBeTruthy();
  expect(screen.getByText("Also")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/run"))).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Yes, run this question" }),
  );
  await screen.findByRole("table");
  expect(screen.getByText("B001")).toBeTruthy();
  const call = (fetch.mock.calls as unknown as [string, RequestInit][]).find(
    ([url]) => url.endsWith("/run"),
  )!;
  expect(JSON.parse(String(call[1].body))).toEqual({ query: opaqueQuery });
  p.client.clear();
});

it.each(["ask", "poll", "run"])(
  "a %s 404 is rendered according to the request that failed",
  async (stage) => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      if (
        (stage === "ask" && init.method === "POST" && !url.endsWith("/run")) ||
        (stage === "poll" && init.method === "GET") ||
        (stage === "run" && url.endsWith("/run"))
      )
        return json(
          {
            error: {
              code: "not_found",
              details: {
                error_code: "bi_question_not_found",
                message: "Question not found.",
              },
            },
          },
          404,
        );
      return json(proposalWire(stage === "poll" ? "translating" : "proposed"));
    });
    const p = providers();
    render(<AskPage />, p);
    fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
      target: { value: "Loan balance by branch" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Work out my question" }),
    );
    if (stage === "run")
      fireEvent.click(
        await screen.findByRole("button", { name: "Yes, run this question" }),
      );
    await screen.findByText(
      stage === "ask"
        ? "Business intelligence is not available here"
        : "Question not found.",
    );
    if (stage !== "ask")
      expect(
        screen.queryByText("Business intelligence is not available here"),
      ).toBeNull();
    expect(document.body.textContent).not.toContain("bi_question_not_found");
    p.client.clear();
  },
);

it.each(["refused", "stopped"])(
  "a %s question renders platform copy and labelled suggestions without wire tokens",
  async (state) => {
    vi.stubGlobal("fetch", async () =>
      json({
        ...proposalWire(state),
        message: "The platform could not recognize that figure.",
        reason_code: "SECRET_REASON",
        refusal_code: "SECRET_REFUSAL",
        suggestions: [
          { member_id: "SECRET_ID", label: "Loan balance" },
          { member_id: "UNLABELLED_ID" },
        ],
      }),
    );
    const p = providers();
    const view = render(<AskPage />, p);
    fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
      target: { value: "Loan balance by branch" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Work out my question" }),
    );
    await screen.findByText("The platform could not recognize that figure.");
    expect(screen.getByText("Loan balance")).toBeTruthy();
    expect(view.container.textContent).not.toMatch(
      /SECRET_REASON|SECRET_REFUSAL|SECRET_ID|UNLABELLED_ID/,
    );
    expect(
      screen.queryByRole("button", { name: "Yes, run this question" }),
    ).toBeNull();
    p.client.clear();
  },
);

it.each([{ rows: [] }, { rows: [["B001", null]] }])(
  "an empty Ask answer states its figures and date before any table (%j)",
  ({ rows }) => {
    render(
      <AskAnswer
        proposal={parseAskProposal(proposalWire())}
        result={result(rows)}
      />,
    );
    expect(
      screen.getByText(/Loan balance — nothing has been measured for/)
        .textContent,
    ).toMatch(/30 Sep 2026|Sep 30, 2026|30 Sept 2026/);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  },
);
