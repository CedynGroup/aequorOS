// @vitest-environment jsdom
// Regression test for issue #846: the instruments register fetched return
// templates with an unresolved bank id, then swapped through its loading
// skeleton (and remounted, resetting the page scroll) once the bank context
// resolved late. The templates query must wait for the bank, and the
// register must never collapse back to loading after it has rendered.
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import InstrumentsRegister from "@/components/submissions/InstrumentsRegister";
import { QueryAuthorityScopeProvider } from "@/lib/api/useQueryScope";
import { queryAuthorityScope } from "@/lib/api/queryPolicy";
import { apiBaseUrl } from "@/lib/api/client";
import { setAccessToken } from "@/lib/api/token";
import { setActiveJurisdiction } from "@/lib/format";

const bankStore = vi.hoisted(() => {
  type Bank = { id: string; name: string };
  let bank: Bank | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => bank,
    set(value: Bank | null) {
      bank = value;
      for (const listener of listeners) listener();
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});

vi.mock("next/font/local", () => ({
  default: () => ({
    className: "synthetic-font",
    style: { fontFamily: "synthetic-font" },
  }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/submissions/instruments",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@/components/shell/BankContext", async () => {
  const { useSyncExternalStore } = await import("react");
  const scope = {
    modules: new Set(["regulatory_reporting"]),
    organizationModules: new Set(),
    institutionClass: "bank",
    isResolved: true,
    hasInstitutionAuthority: true,
    irrbbAggregatedView: false,
  };
  return {
    useBankContext: () => {
      const bank = useSyncExternalStore(
        bankStore.subscribe,
        bankStore.get,
        bankStore.get,
      );
      return {
        bank: bank
          ? { ...bank, institutionType: null, jurisdiction: null }
          : null,
        banks: bank ? [bank] : undefined,
        moduleScope: scope,
      };
    },
    useModuleScope: () => scope,
  };
});

const server = setupServer();
let client: QueryClient;
// The bank_id query parameter of every return-templates request, in order.
const templateCalls: Array<string | null> = [];

beforeEach(() => {
  templateCalls.length = 0;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  setAccessToken("synthetic-token");
  server.use(
    http.get(
      `${apiBaseUrl}/regulatory-reporting/templates`,
      ({ request }) => {
        templateCalls.push(
          new URL(request.url).searchParams.get("bank_id"),
        );
        return HttpResponse.json({
          templates: [
            {
              code: "LMT",
              instrument_status: "in_force",
              title: "Synthetic liquidity monitoring",
              family: "liquidity",
              fidelity: "PARTIAL",
              regulator: "Synthetic",
              generator: "synthetic",
              template_id: "LMT",
              default_channel: "manual",
              supports_working_copy: false,
              frequency: "monthly",
              directive_citation: "Synthetic source",
              calculations: [],
            },
          ],
        });
      },
    ),
  );
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  cleanup();
  client.clear();
  server.resetHandlers();
  server.close();
  setAccessToken(null);
  setActiveJurisdiction({});
  bankStore.set(null);
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

it("does not request return templates before the bank is resolved", async () => {
  render(view(<InstrumentsRegister />));
  // The bank context resolves only after the first render has committed.
  act(() => bankStore.set({ id: "BK-SYNTH001", name: "Synthetic Bank" }));
  await waitFor(() =>
    expect(document.querySelector("section#LMT")).not.toBeNull(),
  );
  await waitFor(() => expect(templateCalls).toEqual(["BK-SYNTH001"]));
});

it("does not hide the rendered register when the bank resolves after first render", async () => {
  // The register keeps its DOM nodes through QueryBoundary's Activity
  // wrapper, but the unfixed sequence swaps them to "hidden" while the
  // bank-scoped query re-runs — the page collapses and the scroll resets
  // (#846). Watch for the rendered content ever becoming hidden once shown.
  let contentSeen = false;
  let contentHiddenAfterRender = false;
  const hiddenByStyle = (element: Element) => {
    for (let node: Element | null = element; node; node = node.parentElement) {
      if ((node as HTMLElement).style?.display === "none") return true;
    }
    return false;
  };
  const observer = new MutationObserver(() => {
    const section = document.querySelector("section#LMT");
    if (!section) return;
    if (hiddenByStyle(section)) {
      if (contentSeen) contentHiddenAfterRender = true;
    } else {
      contentSeen = true;
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
  });
  try {
    render(view(<InstrumentsRegister />));
    // Give the unfixed sequence a bounded chance to happen: it renders the
    // register from an un-scoped templates response before the bank context
    // resolves. The fixed component holds its skeleton instead, so nothing
    // appears here and the bank simply resolves first.
    try {
      await waitFor(
        () => expect(document.querySelector("section#LMT")).not.toBeNull(),
        { timeout: 300 },
      );
    } catch {
      // Fixed behaviour: no register content before the bank resolves.
    }
    act(() => bankStore.set({ id: "BK-SYNTH001", name: "Synthetic Bank" }));
    await waitFor(() =>
      expect(
        [...document.querySelectorAll("section#LMT")].some(
          (section) => !hiddenByStyle(section),
        ),
      ).toBe(true),
    );
    // Flush any trailing re-render the late resolution could still cause.
    await act(async () => {});
    observer.disconnect();
    expect(contentSeen).toBe(true);
    expect(contentHiddenAfterRender).toBe(false);
  } finally {
    observer.disconnect();
  }
});
