import type { Query, QueryClient, QueryKey } from "@tanstack/react-query";
import type { BiDashboardRead } from "@aequoros/risk-service-api";
import { BI_CATALOGUE_PREFIX, BI_DASHBOARD_PREFIX } from "./biKeys";

/** Bank-scoped disclosure notices; an unrelated numeric response cannot clear a lock. */
export type ProtectedDataState =
  "available" | "revoked" | "unavailable" | "support";
export const protectedDataMessages = {
  revoked:
    "Your bank has turned off aequorOS access to protected data. Contact your bank administrator.",
  unavailable: "Can't reach your bank's key service. Retrying.",
  support: "Protected data is hidden during staff support access.",
} as const;

const notices = new Map<string, ProtectedDataState>();
const listeners = new Set<() => void>();
const locks = new Map<string, number>();
let revision = 0;
let authorityRevision = 0;
export function subscribeProtectedData(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
export function protectedDataState(bankId: string): ProtectedDataState {
  return notices.get(bankId) ?? "available";
}
export function clearProtectedData() {
  notices.clear();
  locks.clear();
  authorityRevision = ++revision;
  listeners.forEach((listener) => listener());
}
export async function protectedDataFetch(
  ...args: Parameters<typeof fetch>
): Promise<Response> {
  const started = revision;
  const response = await fetch(...args);
  const input = args[0];
  const refused = await observeProtectedData(
    response,
    input instanceof Request ? input.url : String(input),
    started,
  );
  if (refused) {
    return Response.json(
      {
        error: {
          code:
            refused === "revoked"
              ? "bank_key_revoked"
              : refused === "support"
                ? "bank_protected_support"
                : "bank_key_unavailable",
          message: protectedDataMessages[refused],
        },
      },
      { status: 503 },
    );
  }
  return response;
}

export async function observeProtectedData(
  response: Response,
  url?: string,
  started = revision,
): Promise<Exclude<ProtectedDataState, "available"> | undefined> {
  const raw = response.headers.get("X-Aequoros-Protected-Data");
  try {
    let payload: unknown;
    if (raw) {
      payload = JSON.parse(raw);
    } else {
      if (response.ok) return;
      const bank = /\/banks\/([^/?#]+)/.exec(url ?? response.url)?.[1];
      if (!bank) return;
      const body: unknown = await response.clone().json();
      if (!body || typeof body !== "object" || !("error" in body)) return;
      const error = body.error;
      if (!error || typeof error !== "object" || !("code" in error)) return;
      const state =
        error.code === "bank_key_revoked"
          ? "revoked"
          : error.code === "bank_key_unavailable"
            ? "unavailable"
            : error.code === "bank_protected_support"
              ? "support"
              : null;
      if (!state) return;
      payload = { [decodeURIComponent(bank)]: state };
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload))
      return;
    if (started < authorityRevision) return "unavailable";
    let refused: Exclude<ProtectedDataState, "available"> | undefined;
    let changed = false;
    for (const [bank, state] of Object.entries(payload)) {
      if (
        !["available", "revoked", "unavailable", "support"].includes(
          state as string,
        )
      )
        continue;
      if (state === "available" && started < (locks.get(bank) ?? 0)) {
        const current = protectedDataState(bank);
        refused = current === "available" ? "unavailable" : current;
        continue;
      }
      if (state !== "available") locks.set(bank, ++revision);
      if (notices.get(bank) !== state) {
        notices.set(bank, state as ProtectedDataState);
        changed = true;
      }
    }
    if (changed) listeners.forEach((listener) => listener());
    return refused;
  } catch {
    /* A malformed notice must not interrupt the original response. */
  }
}

export function isLockedBankQuery(queryKey: QueryKey): boolean {
  return queryKey.some(
    (part) =>
      typeof part === "string" && protectedDataState(part) !== "available",
  );
}

const protectedBiIdentityMembers = new Set([
  "counterparty.id",
  "counterparty.name",
  "counterparty.source_reference",
  "counterparty.group",
]);

function isUnencryptedBiDefinition(query: Query): boolean {
  if (query.queryKey[0] === BI_CATALOGUE_PREFIX) return true;
  if (query.queryKey[0] !== BI_DASHBOARD_PREFIX) return false;
  const dashboard = query.state.data as BiDashboardRead | undefined;
  if (!dashboard || !Array.isArray(dashboard.widgets)) return false;
  return dashboard.widgets.every((widget) => {
    const question = widget.query;
    if (!question) return true;
    return ![
      ...question.measures,
      ...(question.dimensions ?? []),
      ...(question.filters ?? []).map((filter) => filter.member),
      ...(question.sort ?? []).map((sort) => sort.member),
      ...(question.pivot ? [question.pivot.dimension] : []),
      ...(question.topN ? [question.topN.dimension] : []),
    ].some((member) => protectedBiIdentityMembers.has(member));
  });
}

export function bindProtectedDataCache(client: QueryClient): () => void {
  const shouldEvict = (query: Query) =>
    isLockedBankQuery(query.queryKey) && !isUnencryptedBiDefinition(query);
  return subscribeProtectedData(() => {
    void client.cancelQueries({ predicate: shouldEvict }, { revert: false });
    for (const query of client.getQueryCache().getAll()) {
      if (!shouldEvict(query)) continue;
      query.setState({
        data: undefined,
        dataUpdatedAt: 0,
        isInvalidated: true,
        status: "pending",
        error: null,
      });
    }
    void client.refetchQueries({
      type: "active",
      predicate: (query) => shouldEvict(query) && query.state.isInvalidated,
    });
    void client.invalidateQueries(
      {
        predicate: (query) =>
          query.queryKey.some(
            (part) =>
              typeof part === "string" && notices.get(part) === "available",
          ) &&
          (containsLockedData(query.state.data) ||
            isProtectedDataError(query.state.error)),
        refetchType: "active",
      },
      { cancelRefetch: false },
    );
  });
}

export function isProtectedDataError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const record = error as Record<string, unknown>;
  return [
    "bank_key_revoked",
    "bank_key_unavailable",
    "bank_protected_support",
  ].includes(String(record.errorCode ?? record.code ?? ""));
}

export function containsLockedData(value: unknown): boolean {
  if (value === "🔒 Protected data") return true;
  if (!value || typeof value !== "object") return false;
  if ("_aequoros_protected_data" in value) return true;
  return Object.values(value).some(containsLockedData);
}
