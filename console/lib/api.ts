/**
 * AequorOS Operator Console — API client.
 *
 * The ENTIRE wire contract with the operator API (backend/app/operator) lives
 * in this ONE file: base URL, bearer auth, error envelope handling, wire
 * types, and one typed function per endpoint. If the backend contract drifts,
 * this is the only file to fix.
 *
 * Wire types mirror backend/app/schemas/operator.py (TenantRead,
 * ProvisioningResultRead, TenantActivityRead, DataEngineConnectionRead) —
 * verified against that file on 2026-08-09.
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
// Endpoints (the five operator API calls)
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
