"use client";

/**
 * The dashboard's client for the six BI read routes.
 *
 * `GET …/bi/catalogue` says what this principal may ask; `POST …/bi/query`
 * answers one question; `…/bi/grid` and `…/bi/drill` page an answer;
 * `…/bi/explain` says where a figure came from; `GET …/bi/trust` says whether
 * the figures reconcile to what the platform already files. Nothing is decided
 * here: every route re-evaluates the caller's bindings against every member the
 * submitted query touches, so the browser's job is to ask honestly, to key the
 * answer so it cannot be reused by anyone else, and to render a refusal as a
 * refusal.
 *
 * Cache identity lives in `./biKeys`, which is pure and separately proved. The
 * one rule to keep in mind when adding a hook here: a BI cache key must carry
 * the tenant, the actor, the authorization generation, the institution, the
 * reporting window and the query — see that module for why.
 *
 * `BI_ENABLED` defaults off in every deployment, and with the flag off each
 * route answers 404 exactly as an unmounted path would. `useBiAvailability`
 * asks `GET /feature-flags` so navigation can decline to offer a door that is
 * walled up, and `isBiUnavailable` recognises the 404 for a surface that is
 * opened anyway.
 */

import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BiApi,
  BiQueryFromJSON,
  BiQueryToJSON,
  FeatureFlagsApi,
  type BiCatalogueRead,
  type BiExplainRead,
  type BiFilter,
  type BiGridPageRead,
  type BiPagedQueryRequest,
  type BiQuery,
  type BiQueryResult,
  type BiTrustRead,
} from "@aequoros/risk-service-api";
import {
  ApiError,
  apiBaseUrl,
  apiCall,
  configuration,
  isApiError,
} from "./client";
import { useQueryAuthorityScope } from "./useQueryScope";
import type {
  AlertDirection,
  ArtifactFormat,
  Cadence,
  DeliveryStatus,
  ThresholdBasis,
} from "@/components/bi/notifications";
import {
  BI_ALERT_EVENTS_PREFIX,
  BI_DELIVERIES_PREFIX,
  biAlertEventsKey,
  biAlertsKey,
  biCatalogueKey,
  biDeliveriesKey,
  biDrillKey,
  biExplainKey,
  biFeatureKey,
  biGridKey,
  biQueryKey,
  biSubscriptionsKey,
  biTrustKey,
  isoDay,
  utcDay,
} from "./biKeys";

const biApi = new BiApi(configuration);
const featureFlagsApi = new FeatureFlagsApi(configuration);

/**
 * The server refused at least one member of the submitted query.
 *
 * There is no partial answer: one denied member denies the whole query, and the
 * response carries no rows. The 403 body names the members, and the UI
 * deliberately does not show them — see `components/bi/RestrictedWidget.tsx`.
 */
export function isBiAccessDenied(error: unknown): boolean {
  if (!isApiError(error)) return false;
  return error.status === 403;
}

/**
 * BI is not served here. Either the deployment has the feature off, or the
 * institution belongs to another tenant — both answer 404, which is the point:
 * a feature that is off is not "forbidden", it is not there.
 */
export function isBiUnavailable(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

/**
 * Whether this deployment serves BI at all.
 *
 * `undefined` means "not yet known" and is distinct from `false`: navigation
 * hides BI until the answer arrives, so no link flashes, while the route guard
 * only refuses once the answer is a definite no.
 */
export function useBiAvailability(enabled = true): {
  biEnabled: boolean | undefined;
  isLoading: boolean;
} {
  const scope = useQueryAuthorityScope();
  const query = useQuery({
    queryKey: biFeatureKey(scope),
    queryFn: () => apiCall(() => featureFlagsApi.readFeatureFlags()),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return {
    // A failed flag read is not an entitlement. Fail closed.
    biEnabled: query.isError ? false : query.data?.biEnabled,
    isLoading: query.isPending,
  };
}

/** Every measure, dimension and drill path THIS caller may query. */
export function useBiCatalogue(bankId: string | undefined, enabled = true) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiCatalogueRead>({
    queryKey: biCatalogueKey(scope, bankId),
    queryFn: () => apiCall(() => biApi.getBiCatalogue({ bankId: bankId! })),
    enabled: enabled && Boolean(bankId),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** One answered question, under the interactive row cap. */
export function useBiQuery(
  bankId: string | undefined,
  query: BiQuery | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiQueryResult>({
    queryKey: biQueryKey(scope, bankId, query),
    queryFn: () =>
      apiCall(() => biApi.runBiQuery({ bankId: bankId!, biQuery: query! })),
    enabled: enabled && Boolean(bankId) && query !== null,
    retry: false,
  });
}

/** One page of grouped, pivoted or plain rows. */
export function useBiGrid(
  bankId: string | undefined,
  request: BiPagedQueryRequest | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiGridPageRead>({
    queryKey: biGridKey(scope, bankId, request),
    queryFn: () =>
      apiCall(() =>
        biApi.runBiGridQuery({
          bankId: bankId!,
          biPagedQueryRequest: request!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && request !== null,
    retry: false,
  });
}

/**
 * Fetch one grid page on demand, through the same cache identity `useBiGrid`
 * uses.
 *
 * AG Grid's Infinite Row Model asks for a block of rows from a callback, which
 * is not a place a hook can be called — so paging needs an imperative fetch.
 * It goes through `QueryClient.fetchQuery` rather than straight to the API so
 * that a page already in the cache is not asked for twice, and — the reason
 * this lives here and not in a component — so the key is built by `biGridKey`
 * like every other BI read. A page fetched under a hand-rolled key would be
 * cached without the tenant, the actor or the authorization generation in it,
 * which is exactly the leak `./biKeys` exists to prevent.
 *
 * Omitting `endRow` asks the server for one page at ITS cap, which is how the
 * grid learns the page size instead of choosing one.
 */
export function useBiGridPages(
  bankId: string | undefined,
): (request: BiPagedQueryRequest) => Promise<BiGridPageRead> {
  const scope = useQueryAuthorityScope();
  const client = useQueryClient();
  return useCallback(
    (request: BiPagedQueryRequest) =>
      client.fetchQuery<BiGridPageRead>({
        queryKey: biGridKey(scope, bankId, request),
        queryFn: () =>
          apiCall(() =>
            biApi.runBiGridQuery({
              bankId: bankId!,
              biPagedQueryRequest: request,
            }),
          ),
        retry: false,
      }),
    [bankId, client, scope],
  );
}

/** The records behind a figure: one capped page at the record grain. */
export function useBiDrill(
  bankId: string | undefined,
  request: BiPagedQueryRequest | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiGridPageRead>({
    queryKey: biDrillKey(scope, bankId, request),
    queryFn: () =>
      apiCall(() =>
        biApi.runBiDrillQuery({
          bankId: bankId!,
          biPagedQueryRequest: request!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && request !== null,
    retry: false,
  });
}

/** Where one figure of one query came from — definition, source, checks. */
export function useBiExplain(
  bankId: string | undefined,
  measure: string | null,
  query: BiQuery | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiExplainRead>({
    queryKey: biExplainKey(scope, bankId, measure ?? "unset", query),
    queryFn: () =>
      apiCall(() =>
        biApi.explainBiMeasure({
          bankId: bankId!,
          biExplainRequest: { query: query!, measure: measure! },
        }),
      ),
    enabled: enabled && Boolean(bankId) && measure !== null && query !== null,
    retry: false,
  });
}

/** Every reconciliation check for one (institution, date), with its evidence. */
export function useBiTrust(
  bankId: string | undefined,
  asOf: string | null | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  return useQuery<BiTrustRead>({
    queryKey: biTrustKey(scope, bankId, day),
    queryFn: () =>
      apiCall(() => biApi.getBiTrust({ bankId: bankId!, asOf: utcDay(day!) })),
    enabled: enabled && Boolean(bankId) && day !== null,
    retry: false,
  });
}

export {
  biAlertEventsKey,
  biAlertsKey,
  biCatalogueKey,
  biDeliveriesKey,
  biDrillKey,
  biExplainKey,
  biGridKey,
  biQueryKey,
  biSubscriptionsKey,
  biTrustKey,
  isoDay,
  utcDay,
} from "./biKeys";

// ---------------------------------------------------------------------------
// Threshold alerts and scheduled reports
// ---------------------------------------------------------------------------

/**
 * THE GENERATED CLIENT DOES NOT CARRY THESE OPERATIONS YET.
 *
 * `/banks/{bankId}/bi/alerts` and `/banks/{bankId}/bi/subscriptions` landed after
 * the last `mise run risk-service:openapi-client`, so — like the market-data
 * source-selection surface before them (`lib/api/marketDataSources.ts`) — they are
 * called directly here against the wire contract, which is frozen by
 * `app/schemas/bi_notifications.py`. The transport reuses `client.ts`'s bearer
 * resolution and its `ApiError` envelope, so a refusal surfaces identically to
 * every generated-client call, and the types below are the same shape the
 * generated models will have. When the client is regenerated the swap is
 * `biNotificationsFetch` → `biApi.<operation>` in the seven callers below and
 * nothing else; the hooks, the cache keys and the components need no change.
 *
 * The wire is snake_case and the app is camelCase, so each read is parsed rather
 * than cast: a payload the server changed shape on becomes a missing field the
 * parser answers for, not a silent `undefined` in a table cell.
 */

/** Mirrors `BiNotificationRecipient`. */
export type BiNotificationRecipient = Readonly<{
  userId: string;
  email: string;
  displayName: string | null;
  isActive: boolean;
}>;

/** Mirrors `BiAlertRead`. */
export type BiAlertRead = Readonly<{
  id: string;
  bankId: string;
  name: string;
  measureId: string;
  measureLabel: string;
  filters: readonly BiFilter[];
  direction: AlertDirection;
  thresholdBasis: ThresholdBasis;
  threshold: string | null;
  ownerUserId: string;
  ownerDisplayName: string | null;
  ownedByCaller: boolean;
  notifyUserIds: readonly string[];
  recipients: readonly BiNotificationRecipient[];
  isActive: boolean;
  latestState: "breached" | "cleared" | "not_evaluated" | null;
  latestAsOf: string | null;
  latestDetail: string;
}>;

/** Mirrors `BiAlertEventRead`. */
export type BiAlertEventRead = Readonly<{
  id: string;
  asOfDate: string;
  state: "breached" | "cleared" | "not_evaluated";
  observedValue: string | null;
  thresholdValue: string | null;
  thresholdBasis: ThresholdBasis;
  limitSource: string | null;
  reason: string | null;
  detail: string;
  evaluatedAt: string;
}>;

/** Mirrors `BiSubscriptionRead`. */
export type BiSubscriptionRead = Readonly<{
  id: string;
  bankId: string;
  name: string;
  query: BiQuery;
  artifactFormat: ArtifactFormat;
  cadence: Cadence;
  hour: number | null;
  minute: number | null;
  dayOfWeek: number | null;
  dayOfMonth: number | null;
  timeZone: string;
  ownerUserId: string;
  ownerDisplayName: string | null;
  ownedByCaller: boolean;
  recipientUserIds: readonly string[];
  recipients: readonly BiNotificationRecipient[];
  isActive: boolean;
  disclosureClass: "summary" | "record_level";
  deliveryNote: string;
}>;

/** Mirrors `BiSubscriptionDeliveryRead`. */
export type BiSubscriptionDeliveryRead = Readonly<{
  id: string;
  recipientUserId: string;
  recipientEmail: string | null;
  recipientDisplayName: string | null;
  scheduledFor: string;
  trigger: "schedule" | "new_data";
  status: DeliveryStatus;
  asOfDate: string | null;
  disclosureClass: "summary" | "record_level" | null;
  deliveryMode: "attachment" | "link" | null;
  artifactFormat: ArtifactFormat | null;
  artifactSizeBytes: number | null;
  rowCount: number | null;
  detail: string;
  sentAt: string | null;
}>;

/** Mirrors `BiAlertUpsert`. */
export type BiAlertUpsert = Readonly<{
  name: string;
  measureId: string;
  filters?: readonly BiFilter[];
  direction: AlertDirection;
  thresholdBasis: ThresholdBasis;
  threshold: string | null;
  notifyEmails: readonly string[];
  isActive: boolean;
  reason: string;
}>;

/** Mirrors `BiSubscriptionUpsert`. */
export type BiSubscriptionUpsert = Readonly<{
  name: string;
  query: BiQuery;
  artifactFormat: ArtifactFormat;
  cadence: Cadence;
  hour: number | null;
  minute: number | null;
  dayOfWeek: number | null;
  dayOfMonth: number | null;
  recipientEmails: readonly string[];
  isActive: boolean;
  reason: string;
}>;

/** The addresses a refusal names back, so the form can mark them. */
export function unknownRecipients(error: unknown): string[] {
  if (!isApiError(error)) return [];
  const details = error.details as { unknown_emails?: unknown } | null;
  const values = details?.unknown_emails;
  return Array.isArray(values)
    ? values.filter((v): v is string => typeof v === "string")
    : [];
}

/** The server's own domain code for a refusal, for a form that must act on one. */
export function biErrorCode(error: unknown): string | null {
  return isApiError(error) ? error.errorCode : null;
}

function field(value: unknown, name: string): unknown {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)[name]
    : undefined;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function optNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown): boolean {
  return value === true;
}

/** A decimal as the wire carries it: a string, never a float. */
function optDecimal(value: unknown): string | null {
  if (typeof value === "string") return value;
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function parseRecipient(value: unknown): BiNotificationRecipient {
  return {
    userId: str(field(value, "user_id")),
    email: str(field(value, "email")),
    displayName: optStr(field(value, "display_name")),
    isActive: bool(field(value, "is_active")),
  };
}

function parseAlert(value: unknown): BiAlertRead {
  const state = str(field(value, "latest_state"));
  return {
    id: str(field(value, "id")),
    bankId: str(field(value, "bank_id")),
    name: str(field(value, "name")),
    measureId: str(field(value, "measure_id")),
    measureLabel: str(field(value, "measure_label")),
    filters: list(field(value, "filters")) as readonly BiFilter[],
    direction: field(value, "direction") === "below" ? "below" : "above",
    thresholdBasis:
      field(value, "threshold_basis") === "governed_limit"
        ? "governed_limit"
        : "stated",
    threshold: optDecimal(field(value, "threshold")),
    ownerUserId: str(field(value, "owner_user_id")),
    ownerDisplayName: optStr(field(value, "owner_display_name")),
    ownedByCaller: bool(field(value, "owned_by_caller")),
    notifyUserIds: list(field(value, "notify_user_ids")).map((entry) =>
      str(entry),
    ),
    recipients: list(field(value, "recipients")).map(parseRecipient),
    isActive: bool(field(value, "is_active")),
    latestState:
      state === "breached" || state === "cleared" || state === "not_evaluated"
        ? state
        : null,
    latestAsOf: optStr(field(value, "latest_as_of")),
    latestDetail: str(field(value, "latest_detail")),
  };
}

function parseAlertEvent(value: unknown): BiAlertEventRead {
  const state = str(field(value, "state"));
  return {
    id: str(field(value, "id")),
    asOfDate: str(field(value, "as_of_date")),
    state:
      state === "cleared"
        ? "cleared"
        : state === "breached"
          ? "breached"
          : "not_evaluated",
    observedValue: optDecimal(field(value, "observed_value")),
    thresholdValue: optDecimal(field(value, "threshold_value")),
    thresholdBasis:
      field(value, "threshold_basis") === "governed_limit"
        ? "governed_limit"
        : "stated",
    limitSource: optStr(field(value, "limit_source")),
    reason: optStr(field(value, "reason")),
    detail: str(field(value, "detail")),
    evaluatedAt: str(field(value, "evaluated_at")),
  };
}

function parseCadence(value: unknown): Cadence {
  return value === "daily" ||
    value === "weekly" ||
    value === "monthly" ||
    value === "on_new_data"
    ? value
    : "on_new_data";
}

function parseFormat(value: unknown): ArtifactFormat {
  return value === "xlsx" || value === "pdf" ? value : "csv";
}

function parseSubscription(value: unknown): BiSubscriptionRead {
  return {
    id: str(field(value, "id")),
    bankId: str(field(value, "bank_id")),
    name: str(field(value, "name")),
    query: BiQueryFromJSON(field(value, "query")),
    artifactFormat: parseFormat(field(value, "artifact_format")),
    cadence: parseCadence(field(value, "cadence")),
    hour: optNum(field(value, "hour")),
    minute: optNum(field(value, "minute")),
    dayOfWeek: optNum(field(value, "day_of_week")),
    dayOfMonth: optNum(field(value, "day_of_month")),
    timeZone: str(field(value, "time_zone")),
    ownerUserId: str(field(value, "owner_user_id")),
    ownerDisplayName: optStr(field(value, "owner_display_name")),
    ownedByCaller: bool(field(value, "owned_by_caller")),
    recipientUserIds: list(field(value, "recipient_user_ids")).map((entry) =>
      str(entry),
    ),
    recipients: list(field(value, "recipients")).map(parseRecipient),
    isActive: bool(field(value, "is_active")),
    disclosureClass:
      field(value, "disclosure_class") === "record_level"
        ? "record_level"
        : "summary",
    deliveryNote: str(field(value, "delivery_note")),
  };
}

function parseDelivery(value: unknown): BiSubscriptionDeliveryRead {
  const status = str(field(value, "status"));
  const mode = field(value, "delivery_mode");
  return {
    id: str(field(value, "id")),
    recipientUserId: str(field(value, "recipient_user_id")),
    recipientEmail: optStr(field(value, "recipient_email")),
    recipientDisplayName: optStr(field(value, "recipient_display_name")),
    scheduledFor: str(field(value, "scheduled_for")),
    trigger: field(value, "trigger") === "new_data" ? "new_data" : "schedule",
    status: (
      ["pending", "sent", "denied", "no_data", "failed"] as const
    ).includes(status as DeliveryStatus)
      ? (status as DeliveryStatus)
      : "pending",
    asOfDate: optStr(field(value, "as_of_date")),
    disclosureClass:
      field(value, "disclosure_class") === "record_level"
        ? "record_level"
        : field(value, "disclosure_class") === "summary"
          ? "summary"
          : null,
    deliveryMode:
      mode === "attachment" ? "attachment" : mode === "link" ? "link" : null,
    artifactFormat:
      field(value, "artifact_format") == null
        ? null
        : parseFormat(field(value, "artifact_format")),
    artifactSizeBytes: optNum(field(value, "artifact_size_bytes")),
    rowCount: optNum(field(value, "row_count")),
    detail: str(field(value, "detail")),
    sentAt: optStr(field(value, "sent_at")),
  };
}

function alertPayload(body: BiAlertUpsert): Record<string, unknown> {
  return {
    name: body.name,
    measure_id: body.measureId,
    filters: body.filters ?? [],
    direction: body.direction,
    threshold_basis: body.thresholdBasis,
    threshold: body.threshold,
    notify_emails: body.notifyEmails,
    is_active: body.isActive,
    reason: body.reason,
  };
}

function subscriptionPayload(
  body: BiSubscriptionUpsert,
): Record<string, unknown> {
  return {
    name: body.name,
    // Serialised by the GENERATED converter, not by hand: a `BiQuery` is
    // camelCase in the browser and snake_case on the wire (`top_n`), and one
    // hand-written mapping that missed a field would send a question the
    // compiler answers differently from the one the reader built.
    query: BiQueryToJSON(body.query),
    artifact_format: body.artifactFormat,
    cadence: body.cadence,
    hour: body.hour,
    minute: body.minute,
    day_of_week: body.dayOfWeek,
    day_of_month: body.dayOfMonth,
    recipient_emails: body.recipientEmails,
    is_active: body.isActive,
    reason: body.reason,
  };
}

/**
 * One request to the notification routes.
 *
 * The bearer comes from the SAME resolver every generated call uses
 * (`configuration.accessToken`, which prefers the warm cached token and falls
 * back to the NextAuth session), so an act-as-examiner hand-off, a silent
 * refresh and a cold first load all behave here exactly as they do everywhere
 * else. Failures are shaped into `client.ts`'s `ApiError` through `apiCall`, so
 * `isBiUnavailable`, `isBiAccessDenied` and `biErrorCode` work unchanged.
 */
async function biNotificationsFetch(
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<unknown> {
  const resolve = configuration.accessToken;
  const token = resolve ? await resolve("bearerAuth", []) : "";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: init?.method ?? "GET",
    headers,
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (response.status === 204) return null;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = field(body, "error") ?? body;
    const details =
      field(envelope, "details") ?? field(envelope, "detail") ?? null;
    throw new ApiError({
      message:
        optStr(field(details, "message")) ??
        optStr(field(envelope, "message")) ??
        `Request failed (${response.status}).`,
      status: response.status,
      code: optStr(field(envelope, "code")),
      errorCode: optStr(field(details, "error_code")),
      details,
    });
  }
  return body;
}

/** Every threshold alert of this institution this identity owns or is told about. */
export function useBiAlerts(bankId: string | undefined, enabled = true) {
  const scope = useQueryAuthorityScope();
  return useQuery<readonly BiAlertRead[]>({
    queryKey: biAlertsKey(scope, bankId),
    queryFn: async () =>
      list(
        field(
          await apiCall(() =>
            biNotificationsFetch(`/banks/${bankId!}/bi/alerts`),
          ),
          "alerts",
        ),
      ).map(parseAlert),
    enabled: enabled && Boolean(bankId),
    retry: false,
  });
}

/**
 * One alert's recorded verdicts.
 *
 * Every row states the figure it was judged on, so the server refuses this read
 * unless the caller's own bindings cover the alert's figure — including when the
 * caller owns it. A 403 here is therefore a normal state of the page, not an
 * error: the alert row's own sentence already says so.
 */
export function useBiAlertEvents(
  bankId: string | undefined,
  alertId: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<readonly BiAlertEventRead[]>({
    queryKey: biAlertEventsKey(scope, bankId, alertId ?? "unset"),
    queryFn: async () =>
      list(
        field(
          await apiCall(() =>
            biNotificationsFetch(
              `/banks/${bankId!}/bi/alerts/${alertId!}/events`,
            ),
          ),
          "events",
        ),
      ).map(parseAlertEvent),
    enabled: enabled && Boolean(bankId) && alertId !== null,
    retry: false,
  });
}

/** Every scheduled report of this institution this identity owns or receives. */
export function useBiSubscriptions(bankId: string | undefined, enabled = true) {
  const scope = useQueryAuthorityScope();
  return useQuery<readonly BiSubscriptionRead[]>({
    queryKey: biSubscriptionsKey(scope, bankId),
    queryFn: async () =>
      list(
        field(
          await apiCall(() =>
            biNotificationsFetch(`/banks/${bankId!}/bi/subscriptions`),
          ),
          "subscriptions",
        ),
      ).map(parseSubscription),
    enabled: enabled && Boolean(bankId),
    retry: false,
  });
}

/** What each recipient of one report was sent, and which of them was not. Owner only. */
export function useBiSubscriptionDeliveries(
  bankId: string | undefined,
  subscriptionId: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<readonly BiSubscriptionDeliveryRead[]>({
    queryKey: biDeliveriesKey(scope, bankId, subscriptionId ?? "unset"),
    queryFn: async () =>
      list(
        field(
          await apiCall(() =>
            biNotificationsFetch(
              `/banks/${bankId!}/bi/subscriptions/${subscriptionId!}/deliveries`,
            ),
          ),
          "deliveries",
        ),
      ).map(parseDelivery),
    enabled: enabled && Boolean(bankId) && subscriptionId !== null,
    retry: false,
  });
}

/**
 * Invalidate every notification read for this institution after a change.
 *
 * Scoped to the notification prefixes: a saved alert changes no figure, so the
 * query, grid and drill caches are left alone — invalidating them would make
 * every open panel re-ask the server for answers that cannot have moved.
 */
function useInvalidateNotifications(
  bankId: string | undefined,
): () => Promise<void> {
  const client = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useCallback(async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: biAlertsKey(scope, bankId) }),
      client.invalidateQueries({ queryKey: biSubscriptionsKey(scope, bankId) }),
      client.invalidateQueries({ queryKey: [BI_ALERT_EVENTS_PREFIX] }),
      client.invalidateQueries({ queryKey: [BI_DELIVERIES_PREFIX] }),
    ]);
  }, [bankId, client, scope]);
}

/** Save a new threshold alert. The caller becomes its owner. */
export function useCreateBiAlert(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<BiAlertRead, unknown, BiAlertUpsert>({
    mutationFn: async (body) =>
      parseAlert(
        await apiCall(() =>
          biNotificationsFetch(`/banks/${bankId!}/bi/alerts`, {
            method: "POST",
            body: alertPayload(body),
          }),
        ),
      ),
    onSuccess: invalidate,
  });
}

/** Replace an alert's definition. Owner only — the server decides, not this hook. */
export function useUpdateBiAlert(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<
    BiAlertRead,
    unknown,
    { alertId: string; body: BiAlertUpsert }
  >({
    mutationFn: async ({ alertId, body }) =>
      parseAlert(
        await apiCall(() =>
          biNotificationsFetch(`/banks/${bankId!}/bi/alerts/${alertId}`, {
            method: "PUT",
            body: alertPayload(body),
          }),
        ),
      ),
    onSuccess: invalidate,
  });
}

/** Stop judging a figure, keeping the alert and its history. */
export function useDeactivateBiAlert(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<BiAlertRead, unknown, { alertId: string; reason: string }>(
    {
      mutationFn: async ({ alertId, reason }) =>
        parseAlert(
          await apiCall(() =>
            biNotificationsFetch(
              `/banks/${bankId!}/bi/alerts/${alertId}/deactivation`,
              {
                method: "POST",
                body: { reason },
              },
            ),
          ),
        ),
      onSuccess: invalidate,
    },
  );
}

/** Delete an alert and every verdict it recorded. */
export function useDeleteBiAlert(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<void, unknown, string>({
    mutationFn: async (alertId) => {
      await apiCall(() =>
        biNotificationsFetch(`/banks/${bankId!}/bi/alerts/${alertId}`, {
          method: "DELETE",
        }),
      );
    },
    onSuccess: invalidate,
  });
}

/** Save a new scheduled report. The caller becomes its owner. */
export function useCreateBiSubscription(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<BiSubscriptionRead, unknown, BiSubscriptionUpsert>({
    mutationFn: async (body) =>
      parseSubscription(
        await apiCall(() =>
          biNotificationsFetch(`/banks/${bankId!}/bi/subscriptions`, {
            method: "POST",
            body: subscriptionPayload(body),
          }),
        ),
      ),
    onSuccess: invalidate,
  });
}

/** Replace a scheduled report's definition. */
export function useUpdateBiSubscription(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<
    BiSubscriptionRead,
    unknown,
    { subscriptionId: string; body: BiSubscriptionUpsert }
  >({
    mutationFn: async ({ subscriptionId, body }) =>
      parseSubscription(
        await apiCall(() =>
          biNotificationsFetch(
            `/banks/${bankId!}/bi/subscriptions/${subscriptionId}`,
            {
              method: "PUT",
              body: subscriptionPayload(body),
            },
          ),
        ),
      ),
    onSuccess: invalidate,
  });
}

/** Stop sending a report, keeping it and its delivery history. */
export function useDeactivateBiSubscription(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<
    BiSubscriptionRead,
    unknown,
    { subscriptionId: string; reason: string }
  >({
    mutationFn: async ({ subscriptionId, reason }) =>
      parseSubscription(
        await apiCall(() =>
          biNotificationsFetch(
            `/banks/${bankId!}/bi/subscriptions/${subscriptionId}/deactivation`,
            { method: "POST", body: { reason } },
          ),
        ),
      ),
    onSuccess: invalidate,
  });
}

/** Delete a scheduled report and its delivery history. */
export function useDeleteBiSubscription(bankId: string | undefined) {
  const invalidate = useInvalidateNotifications(bankId);
  return useMutation<void, unknown, string>({
    mutationFn: async (subscriptionId) => {
      await apiCall(() =>
        biNotificationsFetch(
          `/banks/${bankId!}/bi/subscriptions/${subscriptionId}`,
          {
            method: "DELETE",
          },
        ),
      );
    },
    onSuccess: invalidate,
  });
}
