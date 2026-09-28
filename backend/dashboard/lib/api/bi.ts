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
  type BiExportRead,
  type BiFilter,
  type BiGridPageRead,
  type BiInsightsRead,
  type BiPackListRead,
  type BiPackRead,
  type BiPagedQueryRequest,
  type BiQuery,
  type BiQueryResult,
  type BiTrustRead,
  // Saved dashboards: the eight routes are generated operations on this same
  // `BiApi`, so nothing below is hand-rolled.
  type BiDashboardCreateRequest,
  type BiDashboardListRead,
  type BiDashboardRead,
  type BiDashboardShareListRead,
  type BiDashboardSummaryRead,
  type BiDashboardUpdateRequest,
  type BiDashboardVersionListRead,
  // Calculated measures: the eight routes are generated operations on the same
  // `BiApi` too, so no fetch below is hand-rolled either.
  type BiMeasureCreateRequest,
  type BiMeasureDecisionRead,
  type BiMeasureListRead,
  type BiMeasureRead,
  type BiMeasureUpdateRequest,
  type BiMeasureValidationRead,
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
  BI_DASHBOARD_PREFIX,
  BI_DASHBOARD_SHARES_PREFIX,
  BI_DASHBOARD_VERSIONS_PREFIX,
  biCatalogueKey,
  biDashboardKey,
  biDashboardSharesKey,
  biDashboardVersionsKey,
  biDashboardsKey,
  biDeliveriesKey,
  biDrillKey,
  biExplainKey,
  biFeatureKey,
  biGridKey,
  biInsightsKey,
  BI_MEASURE_PREFIX,
  biMeasureKey,
  biMeasuresKey,
  biPackKey,
  biPacksKey,
  biQueryKey,
  biSubscriptionsKey,
  biTrustKey,
  biWindowOf,
  isoDay,
  utcDay,
} from "./biKeys";
import { expressionDigest } from "@/components/bi/expressionDigest";

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

/**
 * Every certified content pack this institution and reader may open.
 *
 * `as_of` is REQUIRED by the route and is not defaulted here either: a pack
 * carries no date of its own, and a client that picked one would be choosing an
 * institution's reporting date on its behalf. Until the page knows the date it
 * asks nothing.
 *
 * A deployment whose licence class has no certified pack set answers 404, which
 * `isBiUnavailable` recognises — the same shape as the feature being off, and
 * for the same reason: the surface is not there.
 */
export function useBiPacks(
  bankId: string | undefined,
  asOf: string | null | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  return useQuery<BiPackListRead>({
    queryKey: biPacksKey(scope, bankId, day),
    queryFn: () =>
      apiCall(() => biApi.listBiPacks({ bankId: bankId!, asOf: utcDay(day!) })),
    enabled: enabled && Boolean(bankId) && day !== null,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * One certified pack, resolved for one reader and one reporting date.
 *
 * The response is the ONLY definition of the pack the browser sees: the widgets,
 * their geometry and the query behind each granted one all arrive resolved and
 * authorized. There is no client-side copy to disagree with it.
 */
export function useBiPack(
  bankId: string | undefined,
  pack: string | null,
  asOf: string | null | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  return useQuery<BiPackRead>({
    queryKey: biPackKey(scope, bankId, pack ?? "unset", day),
    queryFn: () =>
      apiCall(() =>
        biApi.getBiPack({ bankId: bankId!, pack: pack!, asOf: utcDay(day!) }),
      ),
    enabled:
      enabled &&
      Boolean(bankId) &&
      pack !== null &&
      pack !== "" &&
      day !== null,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * What the platform is prepared to SAY about one institution at one reporting
 * date.
 *
 * `measuresRead` rides beside the statements and must be read with them: zero
 * statements over zero measures read is "nothing has been computed", which is a
 * different answer from "nothing stands out". When NOTHING was readable and
 * something was withheld the route answers 403 rather than an empty list, so a
 * refusal here is a normal state of the strip and not a failure.
 *
 * `compareTo` is optional: omitted, the server compares against the prior period
 * on the packs' own end-of-month convention, so a strip and the dashboard beside
 * it cannot measure a movement against different dates.
 */
export function useBiInsights(
  bankId: string | undefined,
  asOf: string | null | undefined,
  compareTo?: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  const prior = isoDay(compareTo);
  return useQuery<BiInsightsRead>({
    queryKey: biInsightsKey(scope, bankId, day, prior),
    queryFn: () =>
      apiCall(() =>
        biApi.getBiInsights({
          bankId: bankId!,
          asOf: utcDay(day!),
          compareTo: prior === null ? undefined : utcDay(prior),
        }),
      ),
    enabled: enabled && Boolean(bankId) && day !== null,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// The governed export
// ---------------------------------------------------------------------------

/**
 * THE ONE WAY FIGURES LEAVE A BI VIEW.
 *
 * `POST …/bi/export` re-makes the same authorization decision the screen was
 * served under, classifies the disclosure from the catalogue members the query
 * touches (never from a flag on the request), audits the release and watermarks
 * the artifact. A chart library's or a grid's own "download data" does none of
 * those, which is why neither is wired to anything.
 *
 * This is a hand-rolled request rather than the generated `runBiExport` for one
 * reason: the operation is declared as returning either the FILE or a queued job,
 * so the generated method is typed `any` and resolves the body through a JSON or
 * TEXT reader. A workbook and a PDF are bytes — read as text they arrive
 * corrupted — and the filename the server chose lives in a response HEADER the
 * generated method discards. So the response is taken whole here: the bearer
 * comes from the SAME resolver every generated call uses, and failures are shaped
 * into `client.ts`'s `ApiError`, so a 403 surfaces identically to every other
 * refusal.
 */
export type BiExportFormat = "csv" | "xlsx" | "pdf";

/** Where a finished export is collected from, and what it is called. */
export type BiExportedFile = Readonly<{ url: string; filename: string }>;

/**
 * What to call the file when the server's own choice cannot be read.
 *
 * The server names every artifact `{institution}-analytics-{window}.{ext}` and
 * sends it on `Content-Disposition`. **A browser cannot read that header across
 * an origin** unless the API lists it in `Access-Control-Expose-Headers`, and it
 * does not — the dashboard and the API are different hosts in every deployment, so
 * the name is invisible here. Falling back to one fixed name would give a bank
 * three identically-named files for three reporting dates, which is how a March
 * book gets discussed as if it were August.
 *
 * So the same two inputs the server uses are used here: the institution, and the
 * window the QUERY asked about. Nothing governed is duplicated — the authority for
 * what is in the file is the provenance block inside it, and a filename is
 * presentation. The header still wins whenever it is readable.
 */
function composedFilename(
  bankId: string,
  query: BiQuery,
  format: BiExportFormat,
): string {
  const window = biWindowOf(query);
  const span =
    window.asOf ??
    (window.start && window.end
      ? `${window.start}-to-${window.end}`
      : "all-dates");
  // The format IS the extension for all three artifacts, as it is on the
  // server (`exports.EXTENSIONS`).
  return `${bankId}-analytics-${span}.${format}`;
}

/** The filename the SERVER chose, from `Content-Disposition`, when readable. */
function headerFilename(header: string | null): string | null {
  const quoted = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header ?? "");
  const name = quoted?.[1]?.trim();
  return name && name.length > 0 ? name : null;
}

/** How long a queued export is waited on before the reader is told to come back. */
const EXPORT_POLL_INTERVAL_MS = 2_000;
const EXPORT_POLL_ATTEMPTS = 45;

async function exportError(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null);
  const envelope = field(body, "error") ?? body;
  const details =
    field(envelope, "details") ?? field(envelope, "detail") ?? null;
  return new ApiError({
    message:
      optStr(field(details, "message")) ??
      optStr(field(envelope, "message")) ??
      `The export could not be produced (${response.status}).`,
    status: response.status,
    code: optStr(field(envelope, "code")),
    errorCode: optStr(field(details, "error_code")),
    details,
  });
}

/**
 * Run one governed export and hand back where the file is and what it is called.
 *
 * Two deliveries, both the server's choice, never the caller's: an answer that
 * fits one request is streamed back and turned into an object URL here; a larger
 * one becomes a job in the `bi` worker lane, and this polls the job's own route
 * until the server mints a short-lived download link. A job that ends `denied` or
 * `failed` raises with the SERVER's own production copy — the reader is told what
 * the platform decided, not what the browser guessed.
 */
export function useBiExport(bankId: string | undefined) {
  return useMutation<
    BiExportedFile,
    unknown,
    { query: BiQuery; format: BiExportFormat }
  >({
    mutationFn: async ({ query, format }) => {
      const resolve = configuration.accessToken;
      const token = resolve ? await resolve("bearerAuth", []) : "";
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Accept: "application/octet-stream, application/json",
      };
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch(`${apiBaseUrl}/banks/${bankId!}/bi/export`, {
        method: "POST",
        headers,
        body: JSON.stringify({ format, query: BiQueryToJSON(query) }),
      });
      if (!response.ok) throw await exportError(response);

      if (response.status !== 202) {
        const blob = await response.blob();
        return {
          url: URL.createObjectURL(blob),
          filename:
            headerFilename(response.headers.get("content-disposition")) ??
            composedFilename(bankId!, query, format),
        };
      }

      // Queued. The job id is the only thing this request served; the file is
      // collected from the job's own route, which mints the link for its owner
      // and for nobody else.
      const queued: unknown = await response.json().catch(() => null);
      const jobId = optStr(field(queued, "job_id"));
      if (jobId === null) {
        throw new ApiError({
          message:
            optStr(field(queued, "message")) ??
            "The export was accepted but the platform did not say where to collect it.",
          status: response.status,
          code: null,
          errorCode: null,
        });
      }
      for (let attempt = 0; attempt < EXPORT_POLL_ATTEMPTS; attempt += 1) {
        await new Promise((done) => setTimeout(done, EXPORT_POLL_INTERVAL_MS));
        const job: BiExportRead = await apiCall(() =>
          biApi.getBiExport({ bankId: bankId!, jobId }),
        );
        if (job.state === "ready" && job.downloadUrl) {
          return {
            url: job.downloadUrl,
            // The queued path answers in JSON, so the server's own filename IS
            // readable here; the composed one is only the fallback.
            filename: job.filename ?? composedFilename(bankId!, query, format),
          };
        }
        if (job.state === "denied" || job.state === "failed") {
          throw new ApiError({
            message: job.message,
            status: job.state === "denied" ? 403 : 500,
            code: null,
            errorCode: `bi_export_${job.state}`,
          });
        }
      }
      throw new ApiError({
        message:
          "This export is still being prepared. Open the Export menu again in a " +
          "few minutes to collect it.",
        status: null,
        code: null,
        errorCode: "bi_export_still_preparing",
      });
    },
  });
}

export {
  biAlertEventsKey,
  biAlertsKey,
  biCatalogueKey,
  biDashboardKey,
  biDashboardSharesKey,
  biDashboardVersionsKey,
  biDashboardsKey,
  biDeliveriesKey,
  biDrillKey,
  biExplainKey,
  biGridKey,
  biInsightsKey,
  biPackKey,
  biPacksKey,
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

// ---------------------------------------------------------------------------
// Saved dashboards
// ---------------------------------------------------------------------------

/**
 * A dashboard a bank saved for itself, as opposed to one the platform certified.
 *
 * SHARING NEVER SHARES DATA, AND THAT IS ENFORCED ON EVERY READ. The list is the
 * set of documents this identity may OPEN — its own, plus whatever reaches it by
 * name, by role or organization-wide — and opening one authorizes every widget
 * for the reader, never for the owner. So a document a broadly-authorized
 * colleague shares resolves to refusal markers for a narrower reader, and the
 * marker carries its place on the canvas and nothing else.
 *
 * ONLY THE OWNER MAY CHANGE ONE. The server decides that (`owned_by_caller` on
 * every read says who), and a dashboard this identity may not reach is 404 —
 * identical to one that does not exist, so a private document cannot be
 * enumerated by id. The hooks below therefore offer no "edit anyway" path: a
 * non-owner's PUT is refused, and the surface does not put the control on screen.
 */
export function useBiSavedDashboards(
  bankId: string | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiDashboardListRead>({
    queryKey: biDashboardsKey(scope, bankId),
    queryFn: () => apiCall(() => biApi.listBiDashboards({ bankId: bankId! })),
    enabled: enabled && Boolean(bankId),
    retry: false,
  });
}

/**
 * One saved dashboard, resolved for this reader and this reporting date.
 *
 * `as_of` is required by the route and is not defaulted here, for the reason the
 * pack hook gives: a saved dashboard carries no date of its own, and a client
 * that picked one would be choosing an institution's reporting date for it.
 */
export function useBiSavedDashboard(
  bankId: string | undefined,
  dashboardId: string | null,
  asOf: string | null | undefined,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  const day = isoDay(asOf);
  return useQuery<BiDashboardRead>({
    queryKey: biDashboardKey(scope, bankId, dashboardId ?? "unset", day),
    queryFn: () =>
      apiCall(() =>
        biApi.getBiDashboard({
          bankId: bankId!,
          dashboardId: dashboardId!,
          asOf: utcDay(day!),
        }),
      ),
    enabled:
      enabled &&
      Boolean(bankId) &&
      dashboardId !== null &&
      dashboardId !== "" &&
      day !== null,
    retry: false,
  });
}

/**
 * A dashboard's history: who changed it, when, and what they said about it.
 *
 * Append-only in the database, so this list only ever grows. It carries no
 * canvas: an old layout would have to be re-authorized widget by widget to be
 * shown safely, and the route deliberately does not try.
 */
export function useBiDashboardVersions(
  bankId: string | undefined,
  dashboardId: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiDashboardVersionListRead>({
    queryKey: biDashboardVersionsKey(scope, bankId, dashboardId ?? "unset"),
    queryFn: () =>
      apiCall(() =>
        biApi.listBiDashboardVersions({
          bankId: bankId!,
          dashboardId: dashboardId!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && dashboardId !== null,
    retry: false,
  });
}

/**
 * Who a dashboard is named to. The owner's alone.
 *
 * Owner-only because the list is a membership disclosure: being able to open a
 * document is not being told who else can. A 403 here is therefore a normal
 * answer for a reader who was shared the document, not a failure.
 */
export function useBiDashboardShares(
  bankId: string | undefined,
  dashboardId: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiDashboardShareListRead>({
    queryKey: biDashboardSharesKey(scope, bankId, dashboardId ?? "unset"),
    queryFn: () =>
      apiCall(() =>
        biApi.listBiDashboardShares({
          bankId: bankId!,
          dashboardId: dashboardId!,
        }),
      ),
    enabled: enabled && Boolean(bankId) && dashboardId !== null,
    retry: false,
  });
}

/**
 * Invalidate every saved-dashboard read for this institution after a change.
 *
 * Scoped to the four saved-dashboard prefixes: saving a canvas changes no
 * figure, so the query, grid and drill caches are left alone — re-asking the
 * server for answers that cannot have moved would make every open tile flicker.
 */
function useInvalidateSavedDashboards(
  bankId: string | undefined,
): () => Promise<void> {
  const client = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useCallback(async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: biDashboardsKey(scope, bankId) }),
      client.invalidateQueries({ queryKey: [BI_DASHBOARD_PREFIX] }),
      client.invalidateQueries({
        queryKey: [BI_DASHBOARD_VERSIONS_PREFIX],
      }),
      client.invalidateQueries({ queryKey: [BI_DASHBOARD_SHARES_PREFIX] }),
    ]);
  }, [bankId, client, scope]);
}

/** Save a new dashboard: an authored canvas, or a copy of a certified pack. */
export function useCreateBiSavedDashboard(bankId: string | undefined) {
  const invalidate = useInvalidateSavedDashboards(bankId);
  return useMutation<BiDashboardSummaryRead, unknown, BiDashboardCreateRequest>(
    {
      mutationFn: (body) =>
        apiCall(() =>
          biApi.createBiDashboard({
            bankId: bankId!,
            biDashboardCreateRequest: body,
          }),
        ),
      onSuccess: invalidate,
    },
  );
}

/**
 * Replace a dashboard's canvas, appending a version. Owner only — the SERVER
 * decides that, not this hook.
 */
export function useUpdateBiSavedDashboard(bankId: string | undefined) {
  const invalidate = useInvalidateSavedDashboards(bankId);
  return useMutation<
    BiDashboardSummaryRead,
    unknown,
    { dashboardId: string; body: BiDashboardUpdateRequest }
  >({
    mutationFn: ({ dashboardId, body }) =>
      apiCall(() =>
        biApi.updateBiDashboard({
          bankId: bankId!,
          dashboardId,
          biDashboardUpdateRequest: body,
        }),
      ),
    onSuccess: invalidate,
  });
}

/** Delete a dashboard, its history and its shares. Owner only. */
export function useDeleteBiSavedDashboard(bankId: string | undefined) {
  const invalidate = useInvalidateSavedDashboards(bankId);
  return useMutation<void, unknown, string>({
    mutationFn: (dashboardId) =>
      apiCall(() => biApi.deleteBiDashboard({ bankId: bankId!, dashboardId })),
    onSuccess: invalidate,
  });
}

/**
 * Replace the complete set of identities a dashboard is named to. Owner only.
 *
 * The whole set rather than one at a time, because that makes revocation an
 * ordinary save: an identity absent from the list loses reachability in the same
 * transaction the rest keep it.
 */
export function useSetBiDashboardShares(bankId: string | undefined) {
  const invalidate = useInvalidateSavedDashboards(bankId);
  return useMutation<
    BiDashboardShareListRead,
    unknown,
    { dashboardId: string; userIds: readonly string[] }
  >({
    mutationFn: ({ dashboardId, userIds }) =>
      apiCall(() =>
        biApi.setBiDashboardShares({
          bankId: bankId!,
          dashboardId,
          biDashboardShareRequest: { userIds: [...userIds] },
        }),
      ),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Calculated measures
// ---------------------------------------------------------------------------

/**
 * A formula a bank wrote for itself, over figures the catalogue already publishes.
 *
 * THE SERVER IS THE ONLY AUTHORITY ON WHAT A FORMULA MEANS, and these hooks are
 * written so that nothing else can creep in. The text goes up; the figures it
 * names, the module and sensitivity pairs it needs, and whether it is well formed
 * all come back from the server's own parse — there is no parser in this client
 * and `useValidateBiMeasureExpression` is how the editor asks
 * (`POST …/bi/measures/validation`, which saves nothing).
 *
 * A MEASURE IS AUTHORIZED AS THE FIGURES ITS TEXT NAMES. So the list contains
 * only measures whose figures this identity's access covers, a measure it does not
 * cover is ABSENT rather than restricted, and one asked for by id answers 404
 * exactly as one that does not exist — naming it would disclose the figure behind
 * it. There is deliberately no "restricted measure" shape to render.
 *
 * PROMOTION IS TWO ROUTES BECAUSE IT IS TWO PEOPLE. The proposal and the decision
 * are separate calls, the database refuses a row whose approver is its proposer,
 * and the decision carries the digest of the formula the checker actually read —
 * so a formula edited under a reviewer cannot be certified by a decision taken
 * against the version they saw.
 */
export function useBiMeasures(bankId: string | undefined, enabled = true) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiMeasureListRead>({
    queryKey: biMeasuresKey(scope, bankId),
    queryFn: () => apiCall(() => biApi.listBiMeasures({ bankId: bankId! })),
    enabled: enabled && Boolean(bankId),
    retry: false,
  });
}

/** One calculated measure. 404 is "no such measure for you", never "forbidden". */
export function useBiMeasure(
  bankId: string | undefined,
  measureId: string | null,
  enabled = true,
) {
  const scope = useQueryAuthorityScope();
  return useQuery<BiMeasureRead>({
    queryKey: biMeasureKey(scope, bankId, measureId ?? "unset"),
    queryFn: () =>
      apiCall(() =>
        biApi.getBiMeasure({ bankId: bankId!, measureId: measureId! }),
      ),
    enabled: enabled && Boolean(bankId) && Boolean(measureId),
    retry: false,
  });
}

/**
 * Invalidate every calculated-measure read for this institution.
 *
 * Scoped to the two measure prefixes. The catalogue is deliberately left alone:
 * `GET …/bi/catalogue` serves the PLATFORM's members and knows nothing about a
 * bank's formulas, so certifying one cannot change its answer. The figure caches
 * are left alone for the same reason a canvas save leaves them alone — no figure
 * moved, and re-asking would make every open tile flicker.
 */
function useInvalidateMeasures(bankId: string | undefined): () => Promise<void> {
  const client = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useCallback(async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: biMeasuresKey(scope, bankId) }),
      client.invalidateQueries({ queryKey: [BI_MEASURE_PREFIX] }),
    ]);
  }, [bankId, client, scope]);
}

/**
 * Ask the server whether a formula is well formed, and what it reads.
 *
 * A mutation rather than a query on purpose: it is a question about text a person
 * is still typing, it saves nothing, and it must not be cached — a cached verdict
 * keyed on the text would answer for a formula whose FIGURES the reader's access
 * has since stopped covering.
 */
export function useValidateBiMeasureExpression(bankId: string | undefined) {
  return useMutation<BiMeasureValidationRead, unknown, string>({
    mutationFn: (expression) =>
      apiCall(() =>
        biApi.validateBiMeasureExpression({
          bankId: bankId!,
          biMeasureValidationRequest: { expression },
        }),
      ),
  });
}

/** Save a new personal formula. The figures it names come from the server's parse. */
export function useCreateBiMeasure(bankId: string | undefined) {
  const invalidate = useInvalidateMeasures(bankId);
  return useMutation<BiMeasureRead, unknown, BiMeasureCreateRequest>({
    mutationFn: (body) =>
      apiCall(() =>
        biApi.createBiMeasure({
          bankId: bankId!,
          biMeasureCreateRequest: body,
        }),
      ),
    onSuccess: invalidate,
  });
}

/**
 * Change a formula. Owner only, and a changed text DROPS any certification —
 * an approver certified the exact wording, not the name.
 */
export function useUpdateBiMeasure(bankId: string | undefined) {
  const invalidate = useInvalidateMeasures(bankId);
  return useMutation<
    BiMeasureRead,
    unknown,
    { measureId: string; body: BiMeasureUpdateRequest }
  >({
    mutationFn: ({ measureId, body }) =>
      apiCall(() =>
        biApi.updateBiMeasure({
          bankId: bankId!,
          measureId,
          biMeasureUpdateRequest: body,
        }),
      ),
    onSuccess: invalidate,
  });
}

/**
 * Delete a formula. Owner only.
 *
 * THE SURFACE OFFERS THIS FOR A DRAFT ONLY, and that is narrower than the route:
 * `DELETE …/bi/measures/{id}` is unconditional in every state, certified
 * included. Certifying a formula takes two identities and deleting it takes one —
 * the maker — and the row's `approved_expression` IS the record of what was
 * certified, so the delete destroys the evidence with it and anything naming the
 * measure loses its definition. `components/bi/measures.ts::measureControls`
 * withholds the control above `personal` and says why. Retiring a certified
 * measure through its own review is the act that belongs there.
 */
export function useDeleteBiMeasure(bankId: string | undefined) {
  const invalidate = useInvalidateMeasures(bankId);
  return useMutation<void, unknown, string>({
    mutationFn: (measureId) =>
      apiCall(() => biApi.deleteBiMeasure({ bankId: bankId!, measureId })),
    onSuccess: invalidate,
  });
}

/** The maker's half: put a draft up for certification, with a reason. */
export function useProposeBiMeasure(bankId: string | undefined) {
  const invalidate = useInvalidateMeasures(bankId);
  return useMutation<
    BiMeasureRead,
    unknown,
    { measureId: string; reason: string }
  >({
    mutationFn: ({ measureId, reason }) =>
      apiCall(() =>
        biApi.proposeBiMeasurePromotion({
          bankId: bankId!,
          measureId,
          biMeasureProposalRequest: { reason },
        }),
      ),
    onSuccess: invalidate,
  });
}

/**
 * The checker's half: certify the formula for the institution, or send it back.
 *
 * `expressionDigest` is the version the CHECKER READ, taken over the exact text
 * the payload carried onto their screen. The server refuses the decision if the
 * formula has moved since — which is the point, and why this is not optional.
 * `DigestUnavailable` is raised rather than a digest being invented when the
 * browser has no `crypto.subtle`: an unverifiable decision is not sent.
 */
export function useDecideBiMeasure(bankId: string | undefined) {
  const invalidate = useInvalidateMeasures(bankId);
  return useMutation<
    BiMeasureDecisionRead,
    unknown,
    {
      measureId: string;
      /** The formula exactly as it was shown to this checker. */
      reviewedExpression: string;
      decision: "approve" | "reject";
      reason: string;
    }
  >({
    mutationFn: async ({ measureId, reviewedExpression, decision, reason }) => {
      const digest = await expressionDigest(reviewedExpression);
      return apiCall(() =>
        biApi.decideBiMeasurePromotion({
          bankId: bankId!,
          measureId,
          biMeasureDecisionRequest: {
            decision,
            reason,
            expressionDigest: digest,
          },
        }),
      );
    },
    onSuccess: invalidate,
  });
}
