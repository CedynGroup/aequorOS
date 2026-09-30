/**
 * Applying a behavioral model must refresh exactly the surfaces it changes —
 * scoped to the signed-in tenant/authority/bank. The previous list named
 * prefixes no query key ever used (`liquidity`, `ftp`, `irr`, `forecasting`),
 * so the module dashboards kept serving pre-assumption figures until a
 * window-focus refetch happened to land.
 *
 * Loads `hooks.ts` the way `queryAuthorityBoundary.test.tsx` does: the
 * generated client and NextAuth are stubbed at the module loader, everything
 * else is the real code. Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { queryAuthorityScope, scopedQueryKey } from "./queryPolicy";

const BANK_ID = "BK-SAMP0001";
const scope = queryAuthorityScope("OR-DEM00001", "analyst@aequoros.example", 7);
const otherTenant = queryAuthorityScope(
  "OR-OTHER001",
  "analyst@aequoros.example",
  7,
);

const CONVERGENCE_TIMEOUT_MS = Number(
  process.env.QUERY_TEST_TIMEOUT_MS ?? 15_000,
);

async function waitFor(check: () => boolean, message: string): Promise<void> {
  const deadline = Date.now() + CONVERGENCE_TIMEOUT_MS;
  while (!check()) {
    if (Date.now() >= deadline) {
      throw new Error(`${message} (after ${CONVERGENCE_TIMEOUT_MS}ms)`);
    }
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

async function main(): Promise<void> {
  (globalThis as { window?: object }).window = {};
  const { default: NodeModule } = await import("node:module");
  const moduleWithLoader = NodeModule as typeof NodeModule & {
    _load: (request: string, parent: unknown, isMain: boolean) => unknown;
  };
  const originalLoad = moduleWithLoader._load;
  class ApiStub {}
  class ConfigurationStub {
    constructor(_options: unknown) {}
  }
  class ResponseErrorStub extends Error {
    response = new Response("{}");
  }
  const generatedApi = new Proxy(
    {
      Configuration: ConfigurationStub,
      ResponseError: ResponseErrorStub,
    } as Record<string, unknown>,
    { get: (target, property: string) => target[property] ?? ApiStub },
  );
  moduleWithLoader._load = (request, parent, isMain) => {
    if (request === "@aequoros/risk-service-api") return generatedApi;
    if (request === "next-auth/react") {
      return {
        getSession: async () => null,
        useSession: () => ({ data: null, status: "loading" }),
      };
    }
    return originalLoad(request, parent, isMain);
  };
  const { QueryClient, QueryObserver, focusManager } =
    await import("@tanstack/react-query");
  focusManager.setFocused(true);
  const { behavioralApplyInvalidationPrefixes, invalidateBehavioralApply } =
    await import("./hooks");
  moduleWithLoader._load = originalLoad;

  // The exact set: the model's own state, the four consuming engines' detail
  // reads, the cash-flow forecast, and the live summary + snapshot ladders.
  assert.deepEqual(
    [...behavioralApplyInvalidationPrefixes].sort(),
    [
      "behavioral-model",
      "cashflow-forecast",
      "forecast-runs",
      "irr-dashboard",
      "liq-dashboard",
      "live-snapshots",
      "live-summary",
      "ftp-dashboard",
    ].sort(),
  );
  assert.equal(
    new Set(behavioralApplyInvalidationPrefixes).size,
    behavioralApplyInvalidationPrefixes.length,
  );
  for (const retired of ["liquidity", "ftp", "irr", "forecasting"]) {
    assert.ok(
      !behavioralApplyInvalidationPrefixes.includes(retired),
      `${retired} is not a query-key prefix`,
    );
  }

  // Observers on the real key shapes each surface uses today.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const counts = new Map<string, number>();
  const observers: Array<{ unsubscribe: () => void; destroy: () => void }> = [];
  const observe = (name: string, queryKey: readonly unknown[]) => {
    const observer = new QueryObserver(client, {
      queryKey,
      queryFn: async () => {
        const count = (counts.get(name) ?? 0) + 1;
        counts.set(name, count);
        return count;
      },
    });
    const unsubscribe = observer.subscribe(() => undefined);
    observers.push({ unsubscribe, destroy: () => observer.destroy() });
  };
  const current = (prefix: string) =>
    scopedQueryKey(prefix, scope, BANK_ID, "current", null);

  const refreshed = {
    "behavioral-model": ["behavioral-model", BANK_ID, "nmd-duration"],
    "liq-dashboard": current("liq-dashboard"),
    "ftp-dashboard": current("ftp-dashboard"),
    "irr-dashboard": current("irr-dashboard"),
    "forecast-runs": ["forecast-runs", BANK_ID, 25, 0],
    "cashflow-forecast": scopedQueryKey(
      "cashflow-forecast",
      scope,
      BANK_ID,
      90,
      "behavioral",
      "baseline",
    ),
    "live-summary": scopedQueryKey("live-summary", scope, BANK_ID),
    "live-snapshots": scopedQueryKey(
      "live-snapshots",
      scope,
      BANK_ID,
      "liquidity",
      45,
    ),
  };
  const untouched = {
    "cap-dashboard": current("cap-dashboard"),
    "fx-dashboard": current("fx-dashboard"),
    "behavioral-liquidity": ["behavioral-liquidity", BANK_ID],
    "forecast-scenarios": ["forecast-scenarios", BANK_ID],
    "other-tenant-liq": scopedQueryKey(
      "liq-dashboard",
      otherTenant,
      BANK_ID,
      "current",
      null,
    ),
    "other-bank-liq": scopedQueryKey(
      "liq-dashboard",
      scope,
      "BK-OTHER001",
      "current",
      null,
    ),
  };
  for (const [name, key] of Object.entries(refreshed)) observe(name, key);
  for (const [name, key] of Object.entries(untouched)) observe(name, key);
  const total = Object.keys(refreshed).length + Object.keys(untouched).length;
  await waitFor(
    () => [...counts.values()].filter((count) => count === 1).length === total,
    "fixture queries did not load",
  );

  await invalidateBehavioralApply(client, scope, BANK_ID);
  await waitFor(
    () => Object.keys(refreshed).every((name) => counts.get(name) === 2),
    "applying a behavioral model did not refresh every affected surface",
  );
  // Give any stray invalidation a chance to surface before asserting silence.
  await new Promise((resolve) => setTimeout(resolve, 20));
  for (const name of Object.keys(refreshed)) {
    assert.equal(counts.get(name), 2, `${name} refreshed exactly once`);
  }
  for (const name of Object.keys(untouched)) {
    assert.equal(counts.get(name), 1, `${name} must not be refreshed`);
  }

  for (const observer of observers) {
    observer.unsubscribe();
    observer.destroy();
  }
  client.clear();
  console.log(
    `behavioralInvalidation.test.ts: ${Object.keys(refreshed).length} scoped surfaces refreshed, ${Object.keys(untouched).length} untouched.`,
  );
}

void main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
