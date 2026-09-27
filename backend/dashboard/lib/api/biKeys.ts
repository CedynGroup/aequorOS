/**
 * Cache identity for every BI read.
 *
 * A BI answer is a function of five things, and the browser cache must key on
 * all five or one principal's rows are served to another: the TENANT and the
 * AUTHORITY (both carried by `QueryAuthorityScope` — the signed-in actor plus
 * the backend's authorization generation), the INSTITUTION, the REPORTING
 * WINDOW, and the QUERY itself. The server re-decides every request, so a leak
 * here never becomes a leak of fresh data — but a cache hit returns rows
 * WITHOUT asking the server anything, so a key that drops a dimension shows one
 * user a figure the server would have refused them.
 *
 * The query dimension is the query's CANONICAL SERIALISATION, not a digest.
 * A 32- or 64-bit digest of an arbitrarily large `BiQuery` can collide, and a
 * collision here is exactly the failure this module exists to prevent: two
 * different questions sharing one cache entry. Canonical JSON cannot collide
 * because it is reversible. TanStack Query hashes the whole key itself for
 * storage, so the length costs nothing.
 *
 * Everything here is pure and dependency-free so `biKeys.test.ts` can prove the
 * scoping properties without a browser, a backend or a QueryClient.
 */

import { scopedQueryKey, type QueryAuthorityScope } from "./queryPolicy";
import type { QueryKey } from "@tanstack/react-query";

/** Key prefixes, one per BI surface. Also the unit of scoped invalidation. */
export const BI_CATALOGUE_PREFIX = "bi-catalogue";
export const BI_QUERY_PREFIX = "bi-query";
export const BI_GRID_PREFIX = "bi-grid";
export const BI_DRILL_PREFIX = "bi-drill";
export const BI_EXPLAIN_PREFIX = "bi-explain";
export const BI_TRUST_PREFIX = "bi-trust";
export const BI_FEATURE_PREFIX = "bi-features";
/**
 * The certified content packs, and one resolved pack.
 *
 * A pack response is not a list of names: every widget it grants carries the
 * query the server resolved for the date, and every widget it refuses carries
 * only geometry. Which widgets those are is decided per principal, so the pack
 * routes are keyed on exactly the same five dimensions as a figure — a key that
 * dropped the actor or the authorization generation would serve one colleague
 * the widget set another was granted.
 */
export const BI_PACKS_PREFIX = "bi-packs";
export const BI_PACK_PREFIX = "bi-pack";
/**
 * The statements the platform will make about a reporting date.
 *
 * Each one states a figure in words, and which figures are read is decided per
 * principal — so a strip is a data read and is keyed like one.
 */
export const BI_INSIGHTS_PREFIX = "bi-insights";
/**
 * Threshold alerts, scheduled reports and their histories.
 *
 * These carry no figure in their LIST forms — an alert row names a threshold and
 * a verdict, a report names a question and a schedule — but they are keyed on
 * exactly the same five dimensions as every other BI read, for the same reason
 * and with no exception: an alert's verdict IS a figure in all but name, a
 * delivery history names the people a report reaches, and both are decided
 * per principal and per institution by a server that re-decides every request.
 * A cache hit returns rows without asking the server anything, so a key that
 * dropped the actor or the authorization generation would show one colleague
 * another's distribution list.
 */
export const BI_ALERTS_PREFIX = "bi-alerts";
export const BI_ALERT_EVENTS_PREFIX = "bi-alert-events";
export const BI_SUBSCRIPTIONS_PREFIX = "bi-subscriptions";
export const BI_DELIVERIES_PREFIX = "bi-deliveries";

/** Every notification prefix, as the unit of scoped invalidation after a change. */
export const BI_NOTIFICATION_PREFIXES: readonly string[] = [
  BI_ALERTS_PREFIX,
  BI_ALERT_EVENTS_PREFIX,
  BI_SUBSCRIPTIONS_PREFIX,
  BI_DELIVERIES_PREFIX,
];

export const BI_QUERY_PREFIXES: readonly string[] = [
  BI_CATALOGUE_PREFIX,
  BI_QUERY_PREFIX,
  BI_GRID_PREFIX,
  BI_DRILL_PREFIX,
  BI_EXPLAIN_PREFIX,
  BI_TRUST_PREFIX,
  BI_PACKS_PREFIX,
  BI_PACK_PREFIX,
  BI_INSIGHTS_PREFIX,
];

/** The reporting window as one comparable token, for a key and for a label. */
export type BiWindow = Readonly<{
  /** A single business date, ISO `YYYY-MM-DD`. */
  asOf?: string | null;
  /** An inclusive window, ISO `YYYY-MM-DD` at both ends. */
  start?: string | null;
  end?: string | null;
  /** The prior date a comparison is taken against. */
  compareTo?: string | null;
}>;

/**
 * A date as the wire spells it, whatever the caller holds.
 *
 * The generated client parses `format: date` fields into `Date`, and a `Date`
 * built from a local-midnight string moves a day when it is read back in UTC.
 * Everything in a cache key is therefore normalised to the calendar date the
 * server was asked about, never to an instant.
 */
export function isoDay(value: Date | string | null | undefined): string | null {
  if (value == null) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length >= 10 ? trimmed.slice(0, 10) : trimmed || null;
  }
  if (Number.isNaN(value.getTime())) return null;
  return value.toISOString().slice(0, 10);
}

/** Parse an ISO day into the UTC-midnight `Date` the generated client sends. */
export function utcDay(iso: string): Date {
  return new Date(`${iso}T00:00:00.000Z`);
}

/**
 * The window dimension of a key: one date, or `start..end`. A window with
 * neither is `"unset"` rather than empty, so it can never collide with a key
 * that simply had the dimension omitted.
 */
export function biWindowKey(window: BiWindow): string {
  const asOf = isoDay(window.asOf);
  const start = isoDay(window.start);
  const end = isoDay(window.end);
  const compareTo = isoDay(window.compareTo);
  const base = asOf ?? (start && end ? `${start}..${end}` : "unset");
  return compareTo ? `${base}~${compareTo}` : base;
}

/**
 * A canonical, reversible serialisation: object keys sorted, arrays in order,
 * `undefined` dropped, `Date` reduced to its calendar day.
 *
 * Two requests that differ in nothing but property order share a cache entry
 * (correct — they are the same question); two that differ in any value do not
 * (required — they are different questions, and one of them may be refused).
 */
export function canonicalJson(value: unknown): string {
  if (value === null) return "null";
  if (value instanceof Date) return JSON.stringify(isoDay(value));
  const kind = typeof value;
  if (kind === "undefined" || kind === "function") return "null";
  if (kind === "number") {
    return Number.isFinite(value as number) ? JSON.stringify(value) : "null";
  }
  if (kind === "boolean" || kind === "string") return JSON.stringify(value);
  if (kind === "bigint") return JSON.stringify((value as bigint).toString());
  if (Array.isArray(value)) {
    return `[${value.map((entry) => canonicalJson(entry)).join(",")}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, entry]) => entry !== undefined)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry)}`);
  return `{${entries.join(",")}}`;
}

/**
 * The query dimension of a key. Named a fingerprint rather than a hash because
 * it is not one: see the module docstring for why a digest is the wrong tool.
 */
export function biQueryFingerprint(query: unknown): string {
  return canonicalJson(query);
}

/** The window a `BiQuery`-shaped value asks about. */
export function biWindowOf(query: unknown): BiWindow {
  const time = (query as { time?: unknown } | null | undefined)?.time as
    | {
        asOf?: Date | string | null;
        compareTo?: Date | string | null;
        range?: { start?: Date | string | null; end?: Date | string | null };
      }
    | undefined;
  return {
    asOf: isoDay(time?.asOf),
    start: isoDay(time?.range?.start),
    end: isoDay(time?.range?.end),
    compareTo: isoDay(time?.compareTo),
  };
}

function bankDimension(bankId: string | null | undefined): string {
  // `null` is a distinct dimension value, not an absent one: a key built before
  // an institution is selected must never match one built after.
  return bankId ?? "bank:pending";
}

/** `GET …/bi/catalogue` — what this principal may ask, for this institution. */
export function biCatalogueKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
): QueryKey {
  return scopedQueryKey(BI_CATALOGUE_PREFIX, scope, bankDimension(bankId));
}

/** `GET …/bi/trust` — the reconciliation verdict for one (institution, date). */
export function biTrustKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  asOf: string | null | undefined,
): QueryKey {
  return scopedQueryKey(
    BI_TRUST_PREFIX,
    scope,
    bankDimension(bankId),
    isoDay(asOf) ?? "unset",
  );
}

/** `GET /feature-flags` — whether this deployment serves BI at all. */
export function biFeatureKey(scope: QueryAuthorityScope): QueryKey {
  return scopedQueryKey(BI_FEATURE_PREFIX, scope);
}

/**
 * One answered question. The shape the spec fixes:
 * `scopedQueryKey("bi-query", scope, bankId, asOf, queryFingerprint)`.
 */
export function biQueryKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  query: unknown,
): QueryKey {
  return scopedQueryKey(
    BI_QUERY_PREFIX,
    scope,
    bankDimension(bankId),
    biWindowKey(biWindowOf(query)),
    biQueryFingerprint(query),
  );
}

/** One page of the grid. The page bounds are part of the question. */
export function biGridKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  request: unknown,
): QueryKey {
  const query = (request as { query?: unknown } | null | undefined)?.query;
  return scopedQueryKey(
    BI_GRID_PREFIX,
    scope,
    bankDimension(bankId),
    biWindowKey(biWindowOf(query)),
    biQueryFingerprint(request),
  );
}

/** One page of records behind a figure. */
export function biDrillKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  request: unknown,
): QueryKey {
  const query = (request as { query?: unknown } | null | undefined)?.query;
  return scopedQueryKey(
    BI_DRILL_PREFIX,
    scope,
    bankDimension(bankId),
    biWindowKey(biWindowOf(query)),
    biQueryFingerprint(request),
  );
}

/**
 * Where one figure came from. Provenance is a property of a figure IN CONTEXT,
 * so the measure and the query it was read in are both dimensions of the key.
 */
export function biExplainKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  measure: string,
  query: unknown,
): QueryKey {
  return scopedQueryKey(
    BI_EXPLAIN_PREFIX,
    scope,
    bankDimension(bankId),
    biWindowKey(biWindowOf(query)),
    measure,
    biQueryFingerprint(query),
  );
}

/** `GET …/bi/packs` — every certified dashboard this reader may open, for one date. */
export function biPacksKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  asOf: string | null | undefined,
): QueryKey {
  return scopedQueryKey(
    BI_PACKS_PREFIX,
    scope,
    bankDimension(bankId),
    isoDay(asOf) ?? "unset",
  );
}

/** `GET …/bi/packs/{pack}` — one certified dashboard, resolved for one date. */
export function biPackKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  pack: string,
  asOf: string | null | undefined,
): QueryKey {
  return scopedQueryKey(
    BI_PACK_PREFIX,
    scope,
    bankDimension(bankId),
    pack,
    isoDay(asOf) ?? "unset",
  );
}

/**
 * `GET …/bi/insights` — the statements for one (institution, date), measured
 * against a prior date the reader may or may not have named. The comparison is
 * part of the question, so it is part of the key.
 */
export function biInsightsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  asOf: string | null | undefined,
  compareTo?: string | null,
): QueryKey {
  return scopedQueryKey(
    BI_INSIGHTS_PREFIX,
    scope,
    bankDimension(bankId),
    biWindowKey({ asOf, compareTo }),
  );
}

/** `GET …/bi/alerts` — every threshold alert this identity owns or is told about. */
export function biAlertsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
): QueryKey {
  return scopedQueryKey(BI_ALERTS_PREFIX, scope, bankDimension(bankId));
}

/** `GET …/bi/alerts/{id}/events` — one alert's recorded verdicts. */
export function biAlertEventsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  alertId: string,
): QueryKey {
  return scopedQueryKey(
    BI_ALERT_EVENTS_PREFIX,
    scope,
    bankDimension(bankId),
    alertId,
  );
}

/** `GET …/bi/subscriptions` — every scheduled report this identity owns or receives. */
export function biSubscriptionsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
): QueryKey {
  return scopedQueryKey(BI_SUBSCRIPTIONS_PREFIX, scope, bankDimension(bankId));
}

/** `GET …/bi/subscriptions/{id}/deliveries` — who was sent what, and who was not. */
export function biDeliveriesKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  subscriptionId: string,
): QueryKey {
  return scopedQueryKey(
    BI_DELIVERIES_PREFIX,
    scope,
    bankDimension(bankId),
    subscriptionId,
  );
}
