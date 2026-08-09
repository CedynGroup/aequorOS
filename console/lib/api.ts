/**
 * AequorOS Operator Console — API client.
 *
 * The ENTIRE wire contract with the operator API (backend/app/operator) lives
 * in this ONE file: base URL, bearer auth, error envelope handling, wire
 * types, and one typed function per endpoint. If the backend contract drifts,
 * this is the only file to fix.
 *
 * Wire types mirror backend/app/schemas/operator.py (TenantRead,
 * ProvisioningResultRead, TenantActivityRead, DataEngineConnectionRead) and
 * backend/app/schemas/market_desk.py (the Desk* models) — verified against
 * those files on 2026-08-09.
 *
 * Conventions:
 * - Wire types are snake_case and are used AS-IS throughout the UI. There is
 *   deliberately no camelCase mapping layer: one representation, zero
 *   translation drift.
 * - Every non-2xx response is thrown as ApiError. The backend error envelope
 *   is `{ error: { code, message, ... } }` (app/core/errors.py); non-JSON
 *   bodies degrade to `http_<status>`.
 * - ALL operator API traffic goes through the console's own /api/op proxy
 *   (app/api/op/[...path]/route.ts), in both auth modes:
 *   - workforce OIDC: the id_token lives in an HttpOnly cookie the proxy
 *     turns into the bearer — browser JS never holds the credential;
 *   - dev token (local only): the sessionStorage token rides the
 *     Authorization header and the proxy forwards it verbatim (the backend
 *     hard-refuses dev auth when APP_ENV=production).
 *   The proxy also means the operator API itself never needs to be reachable
 *   from the operator's browser — only from the console server.
 */

export const API_BASE = '/api/op';

// --------------------------------------------------------------------------
// Session token (dev auth — sessionStorage only, never persisted to disk)
// --------------------------------------------------------------------------

const TOKEN_KEY = 'aeq-operator-token';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.sessionStorage.removeItem(TOKEN_KEY);
}

// --------------------------------------------------------------------------
// Errors
// --------------------------------------------------------------------------

export class ApiError extends Error {
  /** Machine code from the backend envelope, or `network_error` / `http_<status>`. */
  code: string;
  /** HTTP status; 0 when the request never reached the server. */
  status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError('unknown_error', err instanceof Error ? err.message : String(err), 0);
}

// --------------------------------------------------------------------------
// Wire types (snake_case, mirroring backend/app/schemas/operator.py)
// --------------------------------------------------------------------------

export interface TenantFreshnessSummary {
  is_stale: boolean;
  stale_modules: string[];
  modules_reported: number;
  latest_computed_at: string | null;
}

export interface TenantIngestionSummary {
  batch_id: string;
  status: string;
  source_system: string;
  as_of_date: string;
  completed_at: string | null;
}

export interface OperatorTenant {
  organization_id: string; // OR-XXXXXXXX platform id
  organization_name: string;
  organization_created_at: string;
  // Bank fields are null for an organization with no bank yet (should not
  // happen through the saga, but the API never hides a half-provisioned
  // tenant — render a dash, don't crash).
  bank_id: string | null; // BK-XXXXXXXX platform id
  bank_name: string | null;
  jurisdiction_code: string | null;
  currency: string | null;
  license_type: string | null;
  bank_created_at: string | null;
  period_count: number;
  latest_period_end: string | null; // date
  freshness: TenantFreshnessSummary | null;
  last_ingestion: TenantIngestionSummary | null;
  sso_configured: boolean;
  sso_enabled: boolean;
  storage_provider: string | null;
}

export interface TenantsResponse {
  tenants: OperatorTenant[];
}

// kind is a backend Literal ("ingestion_batch" | "job" | "official_run" |
// "package" | "audit_event") — typed as string so a new kind renders instead
// of breaking the feed.
export interface TenantActivityItem {
  ts: string; // ISO timestamp
  kind: string;
  summary: string;
  status: string;
}

export interface TenantActivityResponse {
  organization_id: string;
  items: TenantActivityItem[];
}

// engine is a backend Literal ("market_data" | "database_direct" | "t24") —
// typed as string for the same forward-compatibility reason as kind.
export interface DataEngineConnection {
  organization_id: string;
  bank_id: string;
  engine: string;
  /** Vendor (market data), backend (database-direct), or core system (T24). */
  system: string;
  display_name: string;
  status: string;
  last_activity_at: string | null;
  last_activity_status: string | null;
  credential_expires_at: string | null;
  created_at: string;
}

export interface DataEnginesResponse {
  connections: DataEngineConnection[];
}

export interface ProvisionTenantRequest {
  organization_name: string;
  bank_name: string;
  license_type: string;
  jurisdiction_code: string;
  currency: string; // ISO-4217, ^[A-Z]{3}$ — schema-level 422 otherwise
  admin_email: string;
  admin_full_name: string;
}

export type ProvisionStepStatus = 'succeeded' | 'failed' | 'skipped' | 'rolled_back';

// step is a backend Literal (organization | bank | storage | kms | sso_stub |
// first_admin | readiness | cleanup) — typed as string so a new saga step
// renders instead of breaking the result view.
export interface ProvisionStep {
  step: string;
  status: ProvisionStepStatus;
  detail: string;
}

export interface ProvisionTenantResponse {
  succeeded: boolean;
  organization_id: string | null;
  bank_id: string | null;
  admin_email: string | null;
  /** Plaintext shown exactly once; only a hash is stored server-side. */
  admin_one_time_password: string | null;
  /** The two OIDC redirect URIs bank IT must register (sign-in AND signing step-up). */
  sso_redirect_uris: string[];
  steps: ProvisionStep[];
  warnings: string[];
}

export interface HealthResponse {
  service: string;
  environment: string;
  status: 'ok';
}

// --------------------------------------------------------------------------
// Markets Desk wire types (snake_case, mirroring backend/app/schemas/market_desk.py)
//
// The desk is AequorOS' own market research operation (staff surface under
// /operator/v1/desk): methodology register (Track 2), observations with the
// manual-entry fallback, Track-1 determinations, and publication fan-out.
// --------------------------------------------------------------------------

/** DeskObservationCreate.unit — a backend Literal, closed on both sides. */
export type DeskObservationUnit = 'pct' | 'rate' | 'ghs' | 'index';

// status is a service-owned vocabulary ("draft" | "approved" | …) — typed as
// string so a new state renders instead of breaking the register.
export interface DeskMethodology {
  id: string;
  methodology_code: string;
  version: number;
  status: string;
  /** The full versioned parameter set (spec §5) — shape is methodology-defined. */
  parameters: Record<string, unknown>;
  change_rationale: string;
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  effective_from: string | null; // date
  created_at: string;
}

export interface DeskMethodologiesResponse {
  methodologies: DeskMethodology[];
  total: number;
}

export interface DeskMethodologyCreateRequest {
  methodology_code: string;
  parameters: Record<string, unknown>;
  change_rationale: string;
}

export interface DeskMethodologyProposeRequest {
  parameters: Record<string, unknown>;
  change_rationale: string;
}

export interface DeskMethodologyApproveRequest {
  effective_from: string; // date
}

export interface DeskObservation {
  id: string;
  /** Set when the observation came from an ingested capture; null for manual entry. */
  capture_id: string | null;
  series_code: string;
  as_of_date: string; // date
  /** Decimal on the backend — serialized as a JSON string, keep it a string. */
  value: string;
  unit: string;
  attributes: Record<string, unknown>;
  quality_flags: unknown[];
  /** Operator email for manual entries; null for parser-written rows. */
  entered_by: string | null;
  /** Non-null means a later row superseded this one (append-only corrections). */
  superseded_by: string | null;
  created_at: string;
}

export interface DeskObservationsResponse {
  observations: DeskObservation[];
  total: number;
}

export interface DeskObservationCreateRequest {
  series_code: string;
  as_of_date: string; // date
  /** Sent as a string to preserve decimal precision end to end. */
  value: string;
  unit: DeskObservationUnit;
  attributes?: Record<string, unknown>;
  quality_flags?: string[];
}

export interface DeskCapture {
  id: string;
  source_key: string;
  captured_at: string;
  as_of_date: string; // date
  source_url: string | null;
  content_sha256: string;
  storage_path: string | null;
  parser_version: string;
  status: string;
  parse_error: string | null;
  created_by: string;
}

export interface DeskCapturesResponse {
  captures: DeskCapture[];
  total: number;
}

// ---- determination derived values / QA results ----------------------------
// The wire fields are dict[str, Any]; the shapes below are the ones produced
// by backend/app/services/market_desk/calculation.py::run_pipeline. Every
// field is optional so an evolved pipeline renders degraded, never crashes.

export interface DeskCurvePoint {
  tenor_months?: number;
  rate_pct?: string;
}

export interface DeskCurveBlock {
  curve_type?: string;
  points?: DeskCurvePoint[];
  nodes?: { tenor_years?: string; value_pct?: string }[];
  definition?: Record<string, unknown>;
  digest?: string;
  /** Present (with empty points) when the curve could not be built. */
  build_error?: string;
  // AGD-only extras (synthetic discounting proxy disclosure).
  quote_basis?: string;
  overnight_anchor_pct?: string;
  basis?: Record<string, unknown>;
  disclosure?: string;
}

export interface DeskRateEntry {
  value?: string;
  unit?: string;
  /** pass_through | windowed | derived (methodology series treatment). */
  treatment?: string;
  source_series?: string[];
  as_of?: string;
  staleness_flag?: boolean;
  detail?: Record<string, unknown>;
}

export interface DeskDerivedValues {
  qa_passed?: boolean;
  curves?: Record<string, DeskCurveBlock>;
  rates?: Record<string, DeskRateEntry>;
  reference_rates?: Record<string, string>;
  fx?: Record<string, unknown>;
  fx_rates?: Record<string, string>;
}

export interface DeskForwardQa {
  min_forward?: string;
  positivity_required?: boolean;
  positivity_pass?: boolean;
  slope_sign_changes?: number;
  total_variation_ratio?: string;
  oscillation_tolerance?: string;
  oscillation_pass?: boolean;
  passed?: boolean;
}

export interface DeskGrrCheck {
  status?: string;
  reference_month?: string;
  published_pct?: string;
  reconstructed_pct?: string;
  gap_pp?: string;
  tolerance_pp?: string;
  inputs?: Record<string, unknown>;
}

export interface DeskQaFlag {
  series?: string;
  flag?: string;
  detail?: string;
}

export interface DeskQaResults {
  qa_passed?: boolean;
  /** Hard pre-publish gates: gate name -> "pass" | "fail". */
  gates?: Record<string, string>;
  forward_qa?: DeskForwardQa | null;
  nss_fallback_used?: boolean;
  overnight_spread?: Record<string, unknown>;
  cointegration_diagnostic?: Record<string, unknown>;
  grr_check?: DeskGrrCheck;
  flags?: DeskQaFlag[];
}

export interface DeskDetermination {
  id: string;
  cob_date: string; // date
  methodology_code: string;
  methodology_version: number;
  /** Value-based, id-free observation entries ({series_code, as_of_date, value}). */
  input_snapshot: unknown[];
  input_digest: string;
  /** Empty object until the draft is computed. */
  derived_values: DeskDerivedValues;
  qa_results: DeskQaResults;
  // draft -> pending_review -> approved -> published; rejected; corrections
  // create a NEW draft carrying supersedes_id. Typed string for forward compat.
  status: string;
  prepared_by: string;
  reviewed_by: string | null;
  review_note: string | null;
  published_at: string | null;
  supersedes_id: string | null;
  created_at: string;
}

export interface DeskDeterminationsResponse {
  determinations: DeskDetermination[];
  total: number;
}

export interface DeskDeterminationCreateRequest {
  cob_date: string; // date
  /** Defaults to the register's default code (AEQ-GHS-CURVES) when omitted. */
  methodology_code?: string;
}

// Wire is list[Any]; entries as produced by services/market_desk/publication.py.
export interface DeskPublicationResult {
  bank_id?: string;
  ingestion_batch_id?: string;
  status?: string;
  error?: string;
}

export interface DeskPublication {
  id: string;
  determination_id: string;
  published_by: string;
  published_at: string;
  /** complete | partial | failed (per-bank failures are the contract, not an error). */
  status: string;
  results: DeskPublicationResult[];
}

export interface DeskPublicationsResponse {
  publications: DeskPublication[];
  total: number;
}

// --------------------------------------------------------------------------
// Core request helper
// --------------------------------------------------------------------------

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  /** Default true — set false for the unauthenticated health probe. */
  auth?: boolean;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };

  if (opts.auth !== false) {
    // Dev-token mode sends the bearer explicitly; workforce mode sends
    // nothing — the HttpOnly session cookie rides along and the /api/op
    // proxy attaches the id_token server-side.
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: 'no-store',
    });
  } catch (err) {
    throw new ApiError(
      'network_error',
      `Cannot reach the operator API at ${API_BASE} (${err instanceof Error ? err.message : 'fetch failed'}).`,
      0,
    );
  }

  if (!res.ok) {
    let code = `http_${res.status}`;
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (
        body &&
        typeof body === 'object' &&
        'error' in body &&
        body.error &&
        typeof body.error === 'object'
      ) {
        const env = body.error as { code?: unknown; message?: unknown };
        if (typeof env.code === 'string' && env.code) code = env.code;
        if (typeof env.message === 'string' && env.message) message = env.message;
      }
    } catch {
      // non-JSON error body — keep the http_<status> fallback
    }
    throw new ApiError(code, message, res.status);
  }

  return (await res.json()) as T;
}

// --------------------------------------------------------------------------
// Endpoints (the operator API calls)
// --------------------------------------------------------------------------

/** GET /operator/health — unauthenticated reachability probe. */
export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/operator/health', { auth: false });
}

/** GET /operator/v1/tenants — all onboarded institutions. Also the token-verification call. */
export function listTenants(): Promise<TenantsResponse> {
  return request<TenantsResponse>('/operator/v1/tenants');
}

/** GET /operator/v1/tenants/{orgId}/activity?limit=N — recent activity for one org. */
export function getTenantActivity(orgId: string, limit = 100): Promise<TenantActivityResponse> {
  return request<TenantActivityResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/activity?limit=${limit}`,
  );
}

/** GET /operator/v1/data-engines — data-engine connections across all orgs. */
export function listDataEngines(): Promise<DataEnginesResponse> {
  return request<DataEnginesResponse>('/operator/v1/data-engines');
}

/** POST /operator/v1/tenants — run the provisioning saga. Returns the step record either way. */
export function provisionTenant(body: ProvisionTenantRequest): Promise<ProvisionTenantResponse> {
  return request<ProvisionTenantResponse>('/operator/v1/tenants', { method: 'POST', body });
}

// --------------------------------------------------------------------------
// Markets Desk endpoints (backend/app/operator/features/desk.py)
// --------------------------------------------------------------------------

const DESK = '/operator/v1/desk';

function query(params: Record<string, string | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

/** GET /desk/methodologies — the methodology register, optionally one code. */
export function listDeskMethodologies(
  methodologyCode?: string,
): Promise<DeskMethodologiesResponse> {
  return request<DeskMethodologiesResponse>(
    `${DESK}/methodologies${query({ methodology_code: methodologyCode })}`,
  );
}

/** POST /desk/methodologies — register a NEW methodology code at v1 (draft). 201. */
export function createDeskMethodology(
  body: DeskMethodologyCreateRequest,
): Promise<DeskMethodology> {
  return request<DeskMethodology>(`${DESK}/methodologies`, { method: 'POST', body });
}

/**
 * POST /desk/methodologies/ensure-default — idempotent bootstrap of the
 * AEQ-GHS-CURVES v1 draft. Approval still happens through Track 2.
 */
export function ensureDefaultDeskMethodology(): Promise<DeskMethodology> {
  return request<DeskMethodology>(`${DESK}/methodologies/ensure-default`, { method: 'POST' });
}

/** POST /desk/methodologies/{code}/versions — Track 2: draft version+1 with a rationale. 201. */
export function proposeDeskMethodologyVersion(
  methodologyCode: string,
  body: DeskMethodologyProposeRequest,
): Promise<DeskMethodology> {
  return request<DeskMethodology>(
    `${DESK}/methodologies/${encodeURIComponent(methodologyCode)}/versions`,
    { method: 'POST', body },
  );
}

/**
 * POST /desk/methodologies/{code}/versions/{version}/approve — Track-2
 * approval (dual control: 422 when the approver is the proposer).
 */
export function approveDeskMethodologyVersion(
  methodologyCode: string,
  version: number,
  body: DeskMethodologyApproveRequest,
): Promise<DeskMethodology> {
  return request<DeskMethodology>(
    `${DESK}/methodologies/${encodeURIComponent(methodologyCode)}/versions/${version}/approve`,
    { method: 'POST', body },
  );
}

/**
 * GET /desk/observations — series_code and as_of_date are EXACT-match
 * backend filters; prefix/range narrowing is done client-side.
 */
export function listDeskObservations(opts?: {
  seriesCode?: string;
  asOfDate?: string;
  includeSuperseded?: boolean;
}): Promise<DeskObservationsResponse> {
  return request<DeskObservationsResponse>(
    `${DESK}/observations${query({
      series_code: opts?.seriesCode,
      as_of_date: opts?.asOfDate,
      include_superseded: opts?.includeSuperseded ? true : undefined,
    })}`,
  );
}

/**
 * POST /desk/observations — the manual-entry fallback (spec §3): a
 * first-class observation with operator provenance; a re-entry for the same
 * (series, as-of) supersedes append-only. 201.
 */
export function createDeskObservation(
  body: DeskObservationCreateRequest,
): Promise<DeskObservation> {
  return request<DeskObservation>(`${DESK}/observations`, { method: 'POST', body });
}

/** GET /desk/captures — raw source captures, newest first, optionally one source. */
export function listDeskCaptures(sourceKey?: string): Promise<DeskCapturesResponse> {
  return request<DeskCapturesResponse>(`${DESK}/captures${query({ source_key: sourceKey })}`);
}

/** GET /desk/determinations — newest COB first, optional cob_date/status filters. */
export function listDeskDeterminations(opts?: {
  cobDate?: string;
  status?: string;
}): Promise<DeskDeterminationsResponse> {
  return request<DeskDeterminationsResponse>(
    `${DESK}/determinations${query({ cob_date: opts?.cobDate, status: opts?.status })}`,
  );
}

/** GET /desk/determinations/{id} — one determination with snapshot, results, QA. */
export function getDeskDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}`,
  );
}

/**
 * POST /desk/determinations — open a draft bound to the ACTIVE methodology
 * version. 409 when no approved methodology is effective for the COB date or
 * no observations exist on or before it. 201.
 */
export function createDeskDetermination(
  body: DeskDeterminationCreateRequest,
): Promise<DeskDetermination> {
  return request<DeskDetermination>(`${DESK}/determinations`, { method: 'POST', body });
}

/** POST /desk/determinations/{id}/compute — run the §5 pipeline on a DRAFT (409 otherwise). */
export function computeDeskDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/compute`,
    { method: 'POST' },
  );
}

/** POST /desk/determinations/{id}/submit — maker step complete; draft -> pending_review. */
export function submitDeskDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/submit`,
    { method: 'POST' },
  );
}

/**
 * POST /desk/determinations/{id}/approve — checker step. Refused when the
 * reviewer is the preparer (maker-checker) or a hard QA gate failed
 * (qa_passed=false) — surface both as explicit UI states.
 */
export function approveDeskDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/approve`,
    { method: 'POST' },
  );
}

/** POST /desk/determinations/{id}/reject — checker rejection with a required reason. */
export function rejectDeskDetermination(
  determinationId: string,
  reason: string,
): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/reject`,
    { method: 'POST', body: { reason } },
  );
}

/**
 * POST /desk/determinations/{id}/supersede — correction path: a NEW draft for
 * the same COB date carrying supersedes_id; the published original is never
 * edited. Returns the new draft. 201.
 */
export function supersedeDeskDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/supersede`,
    { method: 'POST' },
  );
}

/**
 * POST /desk/determinations/{id}/publish — fan the approved determination out
 * to every bank. Partial failure is the contract: the publication row records
 * a per-bank result either way, and re-publishing heals partial fan-outs.
 */
export function publishDeskDetermination(determinationId: string): Promise<DeskPublication> {
  return request<DeskPublication>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/publish`,
    { method: 'POST' },
  );
}

/** GET /desk/publications — newest first, optionally for one determination. */
export function listDeskPublications(
  determinationId?: string,
): Promise<DeskPublicationsResponse> {
  return request<DeskPublicationsResponse>(
    `${DESK}/publications${query({ determination_id: determinationId })}`,
  );
}

// --------------------------------------------------------------------------
// Workforce session (console-local /api/auth routes, not the operator API)
// --------------------------------------------------------------------------

export interface AuthConfig {
  oidc_configured: boolean;
  issuer_host: string | null;
  api_host: string;
}

export interface WorkforceSession {
  authenticated: boolean;
  email: string | null;
  expires_at: string | null;
}

async function consoleJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: 'no-store', ...init });
  if (!res.ok) throw new ApiError(`http_${res.status}`, res.statusText, res.status);
  return (await res.json()) as T;
}

/** Which sign-in methods this deployment offers + the API host for badges. */
export function getAuthConfig(): Promise<AuthConfig> {
  return consoleJson<AuthConfig>('/api/auth/config');
}

/** The current workforce OIDC session, if any (dev sessions are sessionStorage-only). */
export function getWorkforceSession(): Promise<WorkforceSession> {
  return consoleJson<WorkforceSession>('/api/auth/session');
}

/** End the workforce session (clears the HttpOnly cookie). */
export function workforceLogout(): Promise<{ ok: boolean }> {
  return consoleJson<{ ok: boolean }>('/api/auth/logout', { method: 'POST' });
}
