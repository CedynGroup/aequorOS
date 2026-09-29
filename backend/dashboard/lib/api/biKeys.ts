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

/**
 * Saved dashboards: the list, one resolved document, its history, its shares.
 *
 * A saved dashboard is read per principal exactly as a certified pack is — the
 * LIST is the set of documents this identity may open (its own, plus what
 * reaches it by name, by role or organization-wide), and OPENING one authorizes
 * every widget for the reader rather than for the owner. So both are keyed on
 * the same five dimensions as a figure: two colleagues who may open the same
 * document are served different widget sets, and a key that dropped the actor or
 * the authorization generation would hand one of them the other's.
 *
 * The history and the share list carry no figure, and are keyed identically for
 * the reason the notification keys are: the share list names the PEOPLE a
 * document reaches and is the owner's alone, so a cache hit that crossed
 * principals would disclose it without asking the server anything.
 */
export const BI_DASHBOARDS_PREFIX = "bi-dashboards";
export const BI_DASHBOARD_PREFIX = "bi-dashboard";
export const BI_DASHBOARD_VERSIONS_PREFIX = "bi-dashboard-versions";
export const BI_DASHBOARD_SHARES_PREFIX = "bi-dashboard-shares";

/** Every saved-dashboard prefix, as the unit of scoped invalidation after a save. */
export const BI_SAVED_DASHBOARD_PREFIXES: readonly string[] = [
  BI_DASHBOARDS_PREFIX,
  BI_DASHBOARD_PREFIX,
  BI_DASHBOARD_VERSIONS_PREFIX,
  BI_DASHBOARD_SHARES_PREFIX,
];

/** `GET …/bi/dashboards` — every saved dashboard this identity may open. */
export function biDashboardsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
): QueryKey {
  return scopedQueryKey(BI_DASHBOARDS_PREFIX, scope, bankDimension(bankId));
}

/** `GET …/bi/dashboards/{id}` — one saved dashboard, resolved for one date. */
export function biDashboardKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  dashboardId: string,
  asOf: string | null | undefined,
): QueryKey {
  return scopedQueryKey(
    BI_DASHBOARD_PREFIX,
    scope,
    bankDimension(bankId),
    dashboardId,
    isoDay(asOf) ?? "unset",
  );
}

/** `GET …/bi/dashboards/{id}/versions` — who changed it, when, and what they said. */
export function biDashboardVersionsKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  dashboardId: string,
): QueryKey {
  return scopedQueryKey(
    BI_DASHBOARD_VERSIONS_PREFIX,
    scope,
    bankDimension(bankId),
    dashboardId,
  );
}

/** `GET …/bi/dashboards/{id}/shares` — who it reaches. Owner only. */
export function biDashboardSharesKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  dashboardId: string,
): QueryKey {
  return scopedQueryKey(
    BI_DASHBOARD_SHARES_PREFIX,
    scope,
    bankDimension(bankId),
    dashboardId,
  );
}

/**
 * Calculated measures: the list, and one measure.
 *
 * A measure row carries no figure — it is a FORMULA plus catalogue member ids —
 * and it is keyed on exactly the same five dimensions as a figure anyway, with no
 * exception. Two reasons, and the first is the one that matters:
 *
 * * THE LIST IS DECIDED PER PRINCIPAL. `content.readable_measures` serves this
 *   identity's own drafts, plus the institution's proposals and certified
 *   measures, and then drops every one whose figures this identity's access does
 *   not cover — a refused measure is ABSENT, not restricted, because its label is
 *   authored text that can describe the very figure it was refused. So two
 *   colleagues asking the same question get different lists, and a cache hit
 *   answers without asking the server anything.
 * * A FORMULA NAMES FIGURES. `expression` and `referencedMembers` are catalogue
 *   member ids, and which ids an identity may be shown is the same decision the
 *   query path makes.
 *
 * There is no reporting window: a formula is not read at a date. The measure is
 * answered at a date when it is used in a query, and that read is keyed by
 * `biQueryKey` like any other question.
 */
export const BI_MEASURES_PREFIX = "bi-measures";
export const BI_MEASURE_PREFIX = "bi-measure";

/** Every calculated-measure prefix, as the unit of scoped invalidation. */
export const BI_MEASURE_PREFIXES: readonly string[] = [
  BI_MEASURES_PREFIX,
  BI_MEASURE_PREFIX,
];

/** `GET …/bi/measures` — every calculated measure this identity may read. */
export function biMeasuresKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
): QueryKey {
  return scopedQueryKey(BI_MEASURES_PREFIX, scope, bankDimension(bankId));
}

/** `GET …/bi/measures/{id}` — one calculated measure, if this identity may read it. */
export function biMeasureKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  measureId: string,
): QueryKey {
  return scopedQueryKey(
    BI_MEASURE_PREFIX,
    scope,
    bankDimension(bankId),
    measureId,
  );
}

/**
 * One natural-language question, and whatever the platform proposes for it.
 *
 * A QUESTION IS A CACHE DIMENSION. The proposal is a `BiQuery` the model wrote
 * over the members THIS reader may see, judged for THIS institution and THIS
 * reporting date — so it is keyed on exactly the five dimensions every other BI
 * read is, with the question itself as the fifth. Two consequences that are the
 * reason this is not keyed on the question id alone:
 *
 * * the id is a queue row and the route 404s for anybody but the principal who
 *   asked, so a key that dropped the actor or the authorization generation would
 *   let a cache hit answer a colleague WITHOUT asking the server anything — the
 *   one leak `./biKeys` exists to prevent;
 * * the same words asked twice are two questions with two ids, and the proposals
 *   may differ (the catalogue, the grants or the date may have moved between
 *   them), so the words are part of the key and never a substitute for the id.
 *
 * The question is carried as its canonical serialisation, not a digest, for the
 * reason the module docstring gives: a digest can collide and canonical JSON
 * cannot.
 */
export const BI_ASK_PREFIX = "bi-ask";

/** The question dimension of a key: the reader's own words, canonically. */
export function askQuestionFingerprint(
  question: string | null | undefined,
): string {
  return canonicalJson((question ?? "").trim());
}

/** `GET …/bi/ask/{question_id}` — one of your own questions. */
export function biAskKey(
  scope: QueryAuthorityScope,
  bankId: string | null | undefined,
  questionId: string | null | undefined,
  question: string | null | undefined,
  asOf: string | null | undefined,
): QueryKey {
  return scopedQueryKey(
    BI_ASK_PREFIX,
    scope,
    bankDimension(bankId),
    isoDay(asOf) ?? "unset",
    askQuestionFingerprint(question),
    // A distinct dimension value, not an absent one: a key built before the
    // question had an id must never match one built after.
    questionId ?? "question:pending",
  );
}
