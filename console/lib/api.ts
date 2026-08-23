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
 *   (app/api/op/[...path]/route.ts), in all auth modes:
 *   - password (primary): the operator session JWT lives in an HttpOnly
 *     cookie the proxy turns into the bearer — browser JS never holds it;
 *   - workforce OIDC (secondary): identical cookie mechanics, id_token;
 *   - dev token (local only): the sessionStorage token rides the
 *     Authorization header and the proxy forwards it verbatim (the backend
 *     hard-refuses dev auth on any DEPLOYED environment — the rule is an
 *     allow-list of local/test, not a production check).
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

export interface OperatorJob {
  id: string;
  organization_id: string;
  bank_id: string | null;
  job_type: string;
  status: string;
  claimed_by: string | null;
  attempts: number;
  max_attempts: number;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  run_after: string | null;
  error: string | null;
}

export interface OperatorJobsResponse {
  jobs: OperatorJob[];
}

export interface ProvisionTenantRequest {
  organization_name: string;
  bank_name: string;
  license_type: string;
  // The typed institution discriminator (docs/sdi.md §1) — REQUIRED with no
  // default (a ClosedModel 422s without it), validated against the
  // institution_types registry in the saga. `license_type` above is a SEPARATE
  // free-text field; these are not interchangeable.
  institution_type: string;
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
  /** Total rows matching the filters (across all pages), for the pager. */
  total: number;
  /** Page size the server honoured (defaults to 100, capped at 500). */
  limit: number;
  /** Row offset of this page. */
  offset: number;
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
  /** pass_through | windowed | derived | research_override | research_spread. */
  treatment?: string;
  source_series?: string[];
  as_of?: string;
  staleness_flag?: boolean;
  detail?: Record<string, unknown>;
}

/** Track-1 research judgment (Option B) — determination-scoped only. */
export interface DeskResearchAdjustment {
  series_code: string;
  kind: 'override' | 'additive_bps' | 'assumption_note';
  value?: string | null;
  rationale: string;
  applied_by?: string;
  applied_at?: string;
}

export interface DeskDerivedValues {
  /** Legacy key — follows rates_qa_passed after the rates soft-gate. */
  qa_passed?: boolean;
  rates_qa_passed?: boolean;
  curves_qa_passed?: boolean;
  package_digest?: string;
  research_adjustments?: DeskResearchAdjustment[];
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
  rates_qa_passed?: boolean;
  curves_qa_passed?: boolean;
  /** Gate name -> "pass" | "fail" (rates_package, curve_build, forward_qa). */
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
  /** Determination-scoped research adjustments (empty until Analyst applies). */
  research_adjustments: DeskResearchAdjustment[];
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
// Forward Curve Construction wire types
// (snake_case, mirroring backend/app/schemas/market_desk_curves.py — FC-3/4).
//
// Governed curve DEFINITIONS (the Eikon "Curve 1/2/3" analogue, Track 2, dual
// control, immutable after approval) plus the "Run construction" PREVIEW
// (writes nothing) and the publish-to-golden-copy fan-out. Verified against
// that schema file on 2026-08-11.
// --------------------------------------------------------------------------

/** curve_kind — a backend Literal, closed on both sides. */
export type DeskCurveKind = 'forward' | 'zero' | 'discount';

/** Which solved curve an instrument prices against (DeskCurveQuote.leg). */
export type DeskCurveLeg = 'discount' | 'projection';

/** One governed curve-definition version (DeskCurveDefinitionRead). */
export interface DeskCurveDefinition {
  id: string;
  curve_code: string;
  version: number;
  // draft -> approved. Typed string for forward compatibility.
  status: string;
  currency: string;
  calendar_name: string;
  curve_kind: string;
  projection_index: string | null;
  discount_curve_code: string | null;
  instrument_set_ref: string;
  interpolation_method: string;
  output_daycount: string;
  payment_frequency: string | null;
  payment_interval_months: number;
  curve_frequency: string;
  spot_lag_days: number;
  roll_convention: string;
  extrapolation_rule: string;
  /** FC-6d distribution tier: core < standard < premium (default standard). */
  entitlement_tier: string;
  params: Record<string, unknown>;
  change_rationale: string;
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  effective_from: string | null; // date
  created_at: string;
}

export interface DeskCurveDefinitionsResponse {
  definitions: DeskCurveDefinition[];
  total: number;
}

/** The shared governed parameter surface (create + propose bodies). */
export interface DeskCurveDefinitionFields {
  currency: string; // ISO-4217, ^[A-Z]{3}$
  calendar_name: string;
  curve_kind: DeskCurveKind;
  projection_index?: string | null;
  discount_curve_code?: string | null;
  instrument_set_ref: string;
  interpolation_method: string;
  output_daycount: string;
  payment_frequency?: string | null;
  payment_interval_months: number;
  curve_frequency: string;
  spot_lag_days: number;
  roll_convention: string;
  extrapolation_rule: string;
  /** FC-6d distribution tier; omitted defaults to standard server-side. */
  entitlement_tier?: 'core' | 'standard' | 'premium';
  params?: Record<string, unknown>;
  change_rationale: string;
}

/** DeskCurveDefinitionCreate — registers a NEW curve code at v1 (draft). */
export interface DeskCurveDefinitionCreateRequest extends DeskCurveDefinitionFields {
  curve_code: string;
}

/** DeskCurveDefinitionVersionPropose — drafts version+1 (code is a path param). */
export type DeskCurveDefinitionProposeRequest = DeskCurveDefinitionFields;

export interface DeskCurveDefinitionApproveRequest {
  effective_from: string; // date
}

/** One instrument-grid quote — rate as a DECIMAL fraction (0.0533 = 5.33%). */
export interface DeskCurveQuote {
  instrument: string;
  tenor: string;
  quote: number;
  leg: DeskCurveLeg;
}

export interface DeskCurveConstructRequest {
  curve_code: string;
  as_of: string; // date
  quotes: DeskCurveQuote[];
}

export interface DeskCurvePublishRequest extends DeskCurveConstructRequest {
  methodology_code?: string | null;
}

/** Construct + stage a DRAFT determination for per-cob maker-checker (FC-G2). */
export interface DeskCurveStageRequest extends DeskCurvePublishRequest {
  research_adjustments?: DeskResearchAdjustment[];
}

/** One row of the tenor-adjusted forward grid (the Eikon Start/End/DF/Yield). */
export interface DeskCurveGridRow {
  start: string; // date
  end: string; // date
  discount_factor: number;
  forward_yield: number; // decimal fraction (× 100 for percent)
}

export interface DeskCurvePillarView {
  instrument: string;
  tenor: string;
  leg: string;
  pillar_date: string; // date — the adjusted maturity
  quote: number;
  discount_factor: number;
  reprice_residual: number;
}

export interface DeskCurveQaView {
  passed: boolean;
  reprice_max_residual: number;
  reprice_tolerance: number;
  reprice_pass: boolean;
  pillar_count: number;
  pillar_min_count: number;
  pillar_coverage_pass: boolean;
  monotone_df_pass: boolean;
  forward_positivity_pass: boolean;
  forward_oscillation_pass: boolean;
  forward_min: number; // decimal fraction
  forward_total_variation_ratio: number;
}

/** The "Run construction" preview: forward grid + pillar nodes + QA (writes nothing). */
export interface DeskCurveConstructResponse {
  curve_code: string;
  definition_version: number;
  as_of: string; // date
  output_basis: string; // the "Convert to" output day count, e.g. ACT/360
  curve_frequency_months: number;
  input_digest: string;
  rows: DeskCurveGridRow[];
  pillars: DeskCurvePillarView[];
  qa: DeskCurveQaView;
}

// --------------------------------------------------------------------------
// Core request helper
// --------------------------------------------------------------------------

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT';
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

/** GET /operator/v1/jobs — cross-tenant queue state and worker attribution. */
export function listOperatorJobs(
  limit = 100,
  status?: string,
): Promise<OperatorJobsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set('status', status);
  return request<OperatorJobsResponse>(`/operator/v1/jobs?${params.toString()}`);
}

/** POST /operator/v1/tenants — run the provisioning saga. Returns the step record either way. */
export function provisionTenant(body: ProvisionTenantRequest): Promise<ProvisionTenantResponse> {
  return request<ProvisionTenantResponse>('/operator/v1/tenants', { method: 'POST', body });
}

// --------------------------------------------------------------------------
// Markets Desk endpoints (backend/app/operator/features/desk.py)
// --------------------------------------------------------------------------

const DESK = '/operator/v1/desk';

function query(params: Record<string, string | number | boolean | undefined>): string {
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
 * GET /desk/observations — server-side filtered AND paginated. `seriesCode` is
 * a prefix match, `asOfFrom`/`asOfTo` bound the as-of range (all applied on the
 * backend); rows come back newest as-of first. Page with `limit` (default 100,
 * max 500) + `offset`; the response echoes `total`/`limit`/`offset` for the pager.
 */
export function listDeskObservations(opts?: {
  seriesCode?: string;
  asOfFrom?: string;
  asOfTo?: string;
  includeSuperseded?: boolean;
  limit?: number;
  offset?: number;
}): Promise<DeskObservationsResponse> {
  return request<DeskObservationsResponse>(
    `${DESK}/observations${query({
      series_code: opts?.seriesCode,
      as_of_from: opts?.asOfFrom,
      as_of_to: opts?.asOfTo,
      include_superseded: opts?.includeSuperseded ? true : undefined,
      limit: opts?.limit,
      offset: opts?.offset,
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

export interface DeskCaptureContentView {
  capture_id: string;
  source_key: string;
  source_url: string | null;
  as_of_date: string;
  status: string;
  content_sha256: string;
  parser_version: string;
  kind: string;
  content_omitted: string | null;
  /** Set when these bytes are stored on an earlier capture of the same source. */
  content_deferred_to: string | null;
  content_available: boolean;
  content_bytes: number;
  text: string | null;
  truncated: boolean;
  snippet: string | null;
  needle: string | null;
  meta: Record<string, unknown>;
}

export interface DeskObservationSnippet {
  capture_id: string;
  source_key: string;
  source_url: string | null;
  kind: string;
  content_available: boolean;
  content_omitted: string | null;
  content_deferred_to: string | null;
  snippet: string | null;
  needle: string | null;
  hint: string | null;
}

/** GET /desk/captures/{id}/content — decoded silver payload (+ optional needle). */
export function getDeskCaptureContent(
  captureId: string,
  needle?: string,
): Promise<DeskCaptureContentView> {
  return request<DeskCaptureContentView>(
    `${DESK}/captures/${encodeURIComponent(captureId)}/content${query({ needle })}`,
  );
}

/** GET /desk/captures/{id}/snippet — field-level value window. */
export function getDeskCaptureSnippet(
  captureId: string,
  value?: string,
): Promise<DeskObservationSnippet> {
  return request<DeskObservationSnippet>(
    `${DESK}/captures/${encodeURIComponent(captureId)}/snippet${query({ value })}`,
  );
}

export interface DeskEntitlement {
  id: string;
  organization_id: string;
  dataset_code: string;
  tier: string | null;
  status: string;
  effective_from: string;
  effective_to: string | null;
  granted_by: string;
  revoked_by: string | null;
  revoked_at: string | null;
  notes: string | null;
  created_at: string;
}

export interface DeskEntitlementsResponse {
  entitlements: DeskEntitlement[];
  total: number;
  catalog: {
    datasets?: string[];
    tiers?: Record<string, string[]>;
    default_tier?: string;
  };
}

// Entitlements are TENANT state, so all four routes are Tenant Inspector
// surfaces: each names ONE organization, requires an ACTIVE inspection session
// for it (403 `inspection_required` otherwise), and is written to the operator
// audit log against that session. The organization is REQUIRED everywhere —
// there is no "all tenants" form (backend/app/operator/features/desk.py).

export function listDeskEntitlements(opts: {
  organizationId: string;
  includeRevoked?: boolean;
}): Promise<DeskEntitlementsResponse> {
  return request<DeskEntitlementsResponse>(
    `${DESK}/entitlements${query({
      organization_id: opts.organizationId,
      include_revoked: opts.includeRevoked ? true : undefined,
    })}`,
  );
}

export function grantDeskEntitlementTier(body: {
  organization_id: string;
  tier: 'core' | 'standard' | 'premium';
  effective_from: string;
  notes?: string;
}): Promise<DeskEntitlementsResponse> {
  return request<DeskEntitlementsResponse>(`${DESK}/entitlements/grant-tier`, {
    method: 'POST',
    body,
  });
}

/**
 * POST /desk/entitlements/{id}/revoke — end-date one grant. The organization is
 * sent in the body, not inferred from the id: it is what binds the call to the
 * inspection session and to an audit row naming the tenant, and a grant
 * belonging to a different organization is a 404.
 */
export function revokeDeskEntitlement(
  entitlementId: string,
  organizationId: string,
): Promise<DeskEntitlement> {
  return request<DeskEntitlement>(
    `${DESK}/entitlements/${encodeURIComponent(entitlementId)}/revoke`,
    { method: 'POST', body: { organization_id: organizationId } },
  );
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

/** Wizard package: completeness checklist, WoW deltas, input provenance. */
export interface DeskPackageCompletenessItem {
  series_code: string;
  required: boolean;
  status: 'present' | 'stale' | 'missing' | string;
  as_of_date: string | null;
  value: string | null;
  unit: string | null;
  age_days: number | null;
  stale_limit_days: number;
  provenance: {
    source?: string;
    capture_id?: string | null;
    source_key?: string | null;
    source_url?: string | null;
    entered_by?: string | null;
    quality_flags?: unknown[];
    attributes?: Record<string, unknown>;
  };
}

export interface DeskPackageView {
  completeness: {
    cob_date: string;
    items: DeskPackageCompletenessItem[];
    required_total: number;
    required_present: number;
    required_missing: string[];
    required_stale: string[];
    ready: boolean;
    failed_captures: Array<{
      id: string;
      source_key: string;
      as_of_date: string;
      captured_at: string | null;
      parse_error: string | null;
    }>;
  };
  week_over_week: {
    prior_determination_id: string | null;
    prior_cob_date: string | null;
    prior_published_at: string | null;
    deltas: Array<{
      series_code: string;
      current: string | null;
      prior: string | null;
      delta_pp: string | null;
      unit: string | null;
    }>;
  };
  input_provenance: Array<{
    series_code: string;
    as_of_date?: string;
    value?: string;
    provenance: DeskPackageCompletenessItem['provenance'];
  }>;
  rates_qa_passed: boolean | null;
  curves_qa_passed: boolean | null;
  package_digest: string | null;
}

/** GET /desk/determinations/{id}/package — Research Desk wizard payload. */
export function getDeskDeterminationPackage(determinationId: string): Promise<DeskPackageView> {
  return request<DeskPackageView>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/package`,
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

/**
 * PUT /desk/determinations/{id}/adjustments — replace research adjustments on a
 * draft and recompute (Option B Track-1 judgment). Requires rationale on
 * override / additive_bps entries.
 */
export function putDeskResearchAdjustments(
  determinationId: string,
  adjustments: Array<{
    series_code: string;
    kind: 'override' | 'additive_bps' | 'assumption_note';
    value?: string | null;
    rationale?: string;
  }>,
): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${DESK}/determinations/${encodeURIComponent(determinationId)}/adjustments`,
    { method: 'PUT', body: { adjustments } },
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
// Forward Curve Construction endpoints (backend/app/operator/features/curves.py)
//
// Definitions are Track-2 governed EXACTLY like the methodology register
// (dual control, immutable after approval); /construct applies the ACTIVE
// definition to a cob's quotes as a pure PREVIEW; /publish fans the
// constructed curve out through the existing determination seam.
// --------------------------------------------------------------------------

const CURVES = '/operator/v1/curves';

/** GET /curves/definitions — governed curve definitions, optionally one code. */
export function listCurveDefinitions(
  curveCode?: string,
): Promise<DeskCurveDefinitionsResponse> {
  return request<DeskCurveDefinitionsResponse>(
    `${CURVES}/definitions${query({ curve_code: curveCode })}`,
  );
}

/** POST /curves/definitions — register a NEW curve code at v1 (draft). 201. */
export function createCurveDefinition(
  body: DeskCurveDefinitionCreateRequest,
): Promise<DeskCurveDefinition> {
  return request<DeskCurveDefinition>(`${CURVES}/definitions`, { method: 'POST', body });
}

/** POST /curves/definitions/{code}/versions — Track 2: draft version+1 with a rationale. 201. */
export function proposeCurveDefinitionVersion(
  curveCode: string,
  body: DeskCurveDefinitionProposeRequest,
): Promise<DeskCurveDefinition> {
  return request<DeskCurveDefinition>(
    `${CURVES}/definitions/${encodeURIComponent(curveCode)}/versions`,
    { method: 'POST', body },
  );
}

/**
 * POST /curves/definitions/{code}/versions/{version}/approve — Track-2 approval
 * (dual control: 422 when the approver is the proposer).
 */
export function approveCurveDefinitionVersion(
  curveCode: string,
  version: number,
  body: DeskCurveDefinitionApproveRequest,
): Promise<DeskCurveDefinition> {
  return request<DeskCurveDefinition>(
    `${CURVES}/definitions/${encodeURIComponent(curveCode)}/versions/${version}/approve`,
    { method: 'POST', body },
  );
}

/**
 * POST /curves/construct — run construction against the ACTIVE definition for a
 * curve code + cob. A pure preview (writes no curve/determination state). 409
 * when no approved definition is effective on the as-of; 422 when the solve or
 * a convention/calendar resolution fails.
 */
export function constructCurve(
  body: DeskCurveConstructRequest,
): Promise<DeskCurveConstructResponse> {
  return request<DeskCurveConstructResponse>(`${CURVES}/construct`, { method: 'POST', body });
}

/**
 * POST /curves/determinations — construct THEN stage a DRAFT determination for
 * this cob (FC-G2 per-cob maker-checker). Writes a draft only: never approved,
 * never published. 409 when no approved definition/methodology is effective on
 * the as-of; 422 when the solve or a convention/calendar resolution fails. 201.
 */
export function stageCurveDetermination(
  body: DeskCurveStageRequest,
): Promise<DeskDetermination> {
  return request<DeskDetermination>(`${CURVES}/determinations`, { method: 'POST', body });
}

/**
 * POST /curves/determinations/{id}/submit — maker step: draft -> pending_review.
 * The hard QA gate is enforced here (422 when a gate failed).
 */
export function submitCurveDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${CURVES}/determinations/${encodeURIComponent(determinationId)}/submit`,
    { method: 'POST' },
  );
}

/**
 * POST /curves/determinations/{id}/approve — checker step: pending_review ->
 * approved. Four-eyes: 422 when the approver is the preparer.
 */
export function approveCurveDetermination(determinationId: string): Promise<DeskDetermination> {
  return request<DeskDetermination>(
    `${CURVES}/determinations/${encodeURIComponent(determinationId)}/approve`,
    { method: 'POST' },
  );
}

/**
 * POST /curves/determinations/{id}/publish — publish step: only an APPROVED
 * determination fans out to golden copy. 409 before approval.
 */
export function publishCurveDetermination(determinationId: string): Promise<DeskPublication> {
  return request<DeskPublication>(
    `${CURVES}/determinations/${encodeURIComponent(determinationId)}/publish`,
    { method: 'POST' },
  );
}

// --------------------------------------------------------------------------
// FX-forward construction (operator FX-forward preview, FC-6b).
// --------------------------------------------------------------------------

const FX_FORWARD = '/operator/v1/fx-forward';

export interface FxForwardLeg {
  source: 'published';
  curve_code: string;
}

export interface FxForwardConstructRequest {
  base_ccy: string;
  quote_ccy: string;
  spot: number;
  as_of: string;
  base_leg: FxForwardLeg;
  quote_leg: FxForwardLeg;
  basis_bps?: number;
  day_count?: string;
  tenor_grid: string[];
  grid_calendar: string;
  grid_spot_lag_days?: number;
}

export interface FxForwardRow {
  date: string;
  year_fraction: number;
  df_base: number;
  df_quote: number;
  forward_rate: number;
  forward_points: number;
}

export interface FxForwardConstructResponse {
  base_ccy: string;
  quote_ccy: string;
  pair: string;
  spot: number;
  as_of: string;
  basis_bps: number;
  basis_calibrated: boolean;
  day_count: string;
  base_source: string;
  quote_source: string;
  input_digest: string;
  rows: FxForwardRow[];
}

/** CIP outright-forward preview. This operator endpoint deliberately does not publish. */
export function constructFxForward(
  body: FxForwardConstructRequest,
): Promise<FxForwardConstructResponse> {
  return request<FxForwardConstructResponse>(`${FX_FORWARD}/construct`, {
    method: 'POST',
    body,
  });
}

// --------------------------------------------------------------------------
// Operating-Environment desk endpoints
// (backend/app/operator/features/operating_environment.py — spec
// docs/internal/operating_environment_score.md).
//
// The governed, data-derived jurisdiction operating-environment score
// (GHANA_OPERATING_ENVIRONMENT_SCORE ∈ [0,1]): a pure compute-preview, the
// maker-checker lifecycle (stage draft → submit → approve[≠proposer] →
// publish), and list/get. Verified against
// backend/app/schemas/market_desk_operating_environment.py on 2026-08-14.
//
// Decimal fields (score, and every numeric in the breakdown) arrive as JSON
// STRINGS — the same convention as DeskObservation.value; coerce with Number()
// at the display edge, never store as a float.
// --------------------------------------------------------------------------

const OPERATING_ENVIRONMENT = '/operator/v1/operating-environment';

/**
 * The observable inputs for one jurisdiction / COB. The twelve economic +
 * banking-system aggregates ride as decimal STRINGS (precision-preserving);
 * `regulatory_quality_score` is the one analyst judgment sub-score (1–6 int).
 * `sovereign_rating` and `policy_rate_pct` are OPTIONAL overrides — omit them
 * to auto-pull the published sovereign rating and MPR. The backend model is
 * closed (extra="forbid"), so never send keys outside this shape.
 */
export interface OperatingEnvironmentInputsBody {
  // Pillar 1 — economic risk
  real_gdp_growth_pct: string;
  gdp_per_capita_usd: string;
  cpi_inflation_pct: string;
  private_credit_to_gdp_growth_pct: string;
  system_npl_pct: string;
  private_debt_to_gdp_pct: string;
  // Pillar 2 — industry risk
  regulatory_quality_score: number; // 1..6 judgment
  system_roa_pct: string;
  system_credit_growth_pct: string;
  system_loan_to_deposit_pct: string;
  system_car_pct: string;
  external_funding_pct: string;
  // Auto-pulled unless provided.
  sovereign_rating?: string;
  policy_rate_pct?: string;
}

export interface OperatingEnvironmentComputeRequest {
  jurisdiction_code: string;
  cob_date: string; // date
  inputs: OperatingEnvironmentInputsBody;
}

/** One observable/judgment/sovereign input → its 1..6 risk sub-score. */
export interface OperatingEnvironmentInputScore {
  code: string;
  /** threshold | judgment | sovereign. */
  kind: string;
  value: string;
  sub_score: number;
  weight: string;
}

export interface OperatingEnvironmentSubFactorScore {
  code: string;
  score: string;
  weight: string;
  inputs: OperatingEnvironmentInputScore[];
}

export interface OperatingEnvironmentPillarScore {
  code: string;
  score: string;
  weight: string;
  sub_factors: OperatingEnvironmentSubFactorScore[];
}

/** How an auto-pulled/desk-entered input was sourced (sovereign, policy rate). */
export interface OperatingEnvironmentProvenance {
  sovereign?: Record<string, string>;
  policy_rate?: Record<string, string>;
}

/** Value-based input snapshot — what actually went into the computation. */
export interface OperatingEnvironmentInputSnapshot {
  jurisdiction_code: string;
  cob_date: string;
  observations: Record<string, string>;
  judgments: Record<string, number>;
  sovereign_category: string;
  provenance: OperatingEnvironmentProvenance;
}

/** The full BICRA breakdown — mirrors domain OperatingEnvironmentResult. */
export interface OperatingEnvironmentBreakdown {
  methodology_version: string;
  score: string;
  strength_raw: string;
  composite_risk: string;
  risk_min: string;
  risk_max: string;
  sovereign_category: string;
  governor_cap: string;
  governor_applied: boolean;
  input_digest?: string;
  provenance: OperatingEnvironmentProvenance;
  pillars: OperatingEnvironmentPillarScore[];
}

/** Compute-preview result — persists no assessment state. */
export interface OperatingEnvironmentPreview {
  jurisdiction_code: string;
  cob_date: string;
  methodology_version: string;
  score: string; // Decimal → JSON string
  input_digest: string;
  inputs: OperatingEnvironmentInputSnapshot;
  breakdown: OperatingEnvironmentBreakdown;
}

/** A governed assessment row (the persisted determination). */
export interface OperatingEnvironmentAssessment {
  id: string;
  jurisdiction_code: string;
  cob_date: string;
  methodology_version: string;
  score: string; // Decimal → JSON string
  // draft -> pending_review -> approved -> published. Typed string for forward compat.
  status: string;
  input_digest: string;
  inputs: OperatingEnvironmentInputSnapshot;
  breakdown: OperatingEnvironmentBreakdown;
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  published_at: string | null;
  created_at: string;
}

export interface OperatingEnvironmentAssessmentsResponse {
  assessments: OperatingEnvironmentAssessment[];
  total: number;
}

/** One bank's fan-out result (shape shared with the desk publication path). */
export interface OperatingEnvironmentPublishBankResult {
  bank_id?: string;
  ingestion_batch_id?: string;
  status?: string;
  error?: string;
}

export interface OperatingEnvironmentPublishResult {
  assessment_id: string;
  /** complete | partial | failed (per-bank failures are the contract). */
  status: string;
  banks: number;
  results: OperatingEnvironmentPublishBankResult[];
}

/**
 * POST /operating-environment/compute-preview — resolve inputs, compute, and
 * return the breakdown. Writes NO assessment state (only a staff audit row).
 * 409 when an auto-pulled sovereign/MPR input is missing and not provided;
 * 422 on a bad/incomplete input.
 */
export function computeOperatingEnvironmentPreview(
  body: OperatingEnvironmentComputeRequest,
): Promise<OperatingEnvironmentPreview> {
  return request<OperatingEnvironmentPreview>(`${OPERATING_ENVIRONMENT}/compute-preview`, {
    method: 'POST',
    body,
  });
}

/**
 * POST /operating-environment/assessments — stage a DRAFT assessment (computed
 * and persisted; never approved, never published). Same 409/422 as the
 * preview. 201.
 */
export function stageOperatingEnvironmentAssessment(
  body: OperatingEnvironmentComputeRequest,
): Promise<OperatingEnvironmentAssessment> {
  return request<OperatingEnvironmentAssessment>(`${OPERATING_ENVIRONMENT}/assessments`, {
    method: 'POST',
    body,
  });
}

/** GET /operating-environment/assessments — newest COB first, optional filters. */
export function listOperatingEnvironmentAssessments(opts?: {
  jurisdictionCode?: string;
  status?: string;
}): Promise<OperatingEnvironmentAssessmentsResponse> {
  return request<OperatingEnvironmentAssessmentsResponse>(
    `${OPERATING_ENVIRONMENT}/assessments${query({
      jurisdiction_code: opts?.jurisdictionCode,
      status: opts?.status,
    })}`,
  );
}

/** GET /operating-environment/assessments/{id} — one assessment with breakdown. */
export function getOperatingEnvironmentAssessment(
  assessmentId: string,
): Promise<OperatingEnvironmentAssessment> {
  return request<OperatingEnvironmentAssessment>(
    `${OPERATING_ENVIRONMENT}/assessments/${encodeURIComponent(assessmentId)}`,
  );
}

/** POST /operating-environment/assessments/{id}/submit — maker step: draft → pending_review. */
export function submitOperatingEnvironmentAssessment(
  assessmentId: string,
): Promise<OperatingEnvironmentAssessment> {
  return request<OperatingEnvironmentAssessment>(
    `${OPERATING_ENVIRONMENT}/assessments/${encodeURIComponent(assessmentId)}/submit`,
    { method: 'POST' },
  );
}

/**
 * POST /operating-environment/assessments/{id}/approve — checker step:
 * pending_review → approved. Four-eyes: 422 when the approver is the proposer.
 */
export function approveOperatingEnvironmentAssessment(
  assessmentId: string,
): Promise<OperatingEnvironmentAssessment> {
  return request<OperatingEnvironmentAssessment>(
    `${OPERATING_ENVIRONMENT}/assessments/${encodeURIComponent(assessmentId)}/approve`,
    { method: 'POST' },
  );
}

/**
 * POST /operating-environment/assessments/{id}/publish — fan the approved
 * score out to EVERY tenant as GHANA_OPERATING_ENVIRONMENT_SCORE. Partial
 * failure is the contract; re-publishing heals a partial fan-out.
 */
export function publishOperatingEnvironmentAssessment(
  assessmentId: string,
): Promise<OperatingEnvironmentPublishResult> {
  return request<OperatingEnvironmentPublishResult>(
    `${OPERATING_ENVIRONMENT}/assessments/${encodeURIComponent(assessmentId)}/publish`,
    { method: 'POST' },
  );
}

// --------------------------------------------------------------------------
// Operators (staff identity management — backend/app/operator)
//
// GLOBAL operator_users, separate from tenant identity by design. Shapes are
// the agreed contract; the BACKEND agent builds these routes to match.
// --------------------------------------------------------------------------

const OPERATORS = '/operator/v1/operators';

export interface OperatorUser {
  email: string;
  display_name: string | null;
  /** operator_admin | operator (service-owned vocabulary — typed string). */
  role: string;
  /** password | oidc | dev — how the operator authenticates. */
  auth_provider: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface OperatorsResponse {
  operators: OperatorUser[];
  total: number;
}

/** A newly-created operator plus the one-time password shown exactly once. */
export interface OperatorCreateResult extends OperatorUser {
  /** Plaintext shown once; only a hash is stored server-side. */
  one_time_password: string | null;
}

export interface OperatorPasswordResetResult {
  email: string;
  /** Plaintext shown once; only a hash is stored server-side. */
  one_time_password: string;
}

/** GET /operator/v1/operators — all staff operators. */
export function listOperators(): Promise<OperatorsResponse> {
  return request<OperatorsResponse>(OPERATORS);
}

/** POST /operator/v1/operators — create an operator; returns the one-time password. 201. */
export async function createOperator(body: {
  email: string;
  display_name: string;
  role: string;
}): Promise<OperatorCreateResult> {
  // Backend returns nested {operator, one_time_password}; flatten for the UI.
  const r = await request<{ operator: OperatorUser; one_time_password: string }>(OPERATORS, {
    method: 'POST',
    body,
  });
  return { ...r.operator, one_time_password: r.one_time_password };
}

/** POST /operator/v1/operators/{email}/reset-password — mint a new one-time password. */
export async function resetOperatorPassword(
  email: string,
): Promise<OperatorPasswordResetResult> {
  // Backend returns nested {operator, one_time_password}; flatten for the UI.
  const r = await request<{ operator: OperatorUser; one_time_password: string }>(
    `${OPERATORS}/${encodeURIComponent(email)}/reset-password`,
    { method: 'POST' },
  );
  return { email: r.operator.email, one_time_password: r.one_time_password };
}

/** POST /operator/v1/operators/{email}/deactivate — disable sign-in for an operator. */
export function deactivateOperator(email: string): Promise<OperatorUser> {
  return request<OperatorUser>(`${OPERATORS}/${encodeURIComponent(email)}/deactivate`, {
    method: 'POST',
  });
}

/** POST /operator/v1/operators/{email}/reactivate — re-enable a deactivated operator. */
export function reactivateOperator(email: string): Promise<OperatorUser> {
  return request<OperatorUser>(`${OPERATORS}/${encodeURIComponent(email)}/reactivate`, {
    method: 'POST',
  });
}

// --------------------------------------------------------------------------
// Regulatory-parameter control plane (backend/app/operator/features/
// regulatory_parameters.py — docs/sdi.md §7 Phase C).
//
// The global, class/type-keyed, effective-dated source of truth for regulatory
// numbers (CAR floors, exposure limits, paid-up floors, LMTD liquidity floors,
// provisioning rates, risk-weight buckets …). Reads are open to any operator;
// changes are four-eyes (propose → approve by a DIFFERENT operator). Decimal
// values arrive as JSON STRINGS — keep them strings end to end (precision), the
// same convention as DeskObservation.value. Verified against
// backend/app/schemas/operator.py on 2026-08-20.
// --------------------------------------------------------------------------

const REGULATORY_PARAMETERS = '/operator/v1/regulatory-parameters';

/** One effective-dated generation of a regulatory parameter, with provenance. */
export interface RegulatoryParameter {
  id: string;
  /** Which key space this row lives in (coarse class vs specific licence code). */
  scope_type: 'institution_class' | 'institution_type';
  /** The class ("bank"/"sdi") or type code ("savings_and_loans" …) the value binds to. */
  scope_key: string;
  param_code: string;
  jurisdiction_code: string;
  /** Decimal on the backend — serialized as a JSON string; keep it a string. */
  value_numeric: string | null;
  /** Structured value for non-scalar parameters (null for scalar rows). */
  value_json: Record<string, unknown> | null;
  unit: string;
  source_citation: string;
  /**
   * `pending` = a documented working value not yet verified against the cited
   * regulation. It IS used by the calculations, so surface it prominently — the
   * regulator whose confirmation is outstanding follows from `jurisdiction_code`
   * and must never be named as a literal in display code.
   */
  confirmation_status: 'confirmed' | 'pending';
  effective_from: string; // date
  effective_to: string | null; // date; set when a later generation supersedes this one
  // draft -> approved. Only approved rows are visible to the calculation resolver.
  status: 'draft' | 'approved';
  proposed_by: string;
  approved_by: string | null;
  approved_at: string | null;
  change_rationale: string | null;
  created_at: string;
  updated_at: string;
}

export interface RegulatoryParametersResponse {
  parameters: RegulatoryParameter[];
  total: number;
}

/** Maker step body — propose a new effective-dated generation (lands as `draft`). */
export interface RegulatoryParameterProposeRequest {
  scope_type: 'institution_class' | 'institution_type';
  scope_key: string;
  param_code: string;
  /**
   * REQUIRED — no server-side default. Jurisdiction is part of the parameter's
   * resolution key, so a proposal that omits it used to be filed under Ghana and
   * a Nigerian scope silently inherited Ghana's floors (enterprise audit
   * 2026-08-20 §6). Always send the record's own jurisdiction.
   */
  jurisdiction_code: string;
  /** Sent as a string to preserve decimal precision (the backend coerces to Decimal). */
  value_numeric: string;
  unit: string;
  source_citation: string;
  confirmation_status: 'confirmed' | 'pending';
  effective_from: string; // date
  change_rationale: string;
}

/**
 * GET /operator/v1/regulatory-parameters — every generation matching the
 * filters, newest-effective first within each (param_code, scope) group.
 */
export function listRegulatoryParameters(params?: {
  scopeType?: string;
  scopeKey?: string;
  paramCode?: string;
  confirmationStatus?: string;
  /** Default true server-side — pass false to hide unapproved drafts. */
  includeDrafts?: boolean;
}): Promise<RegulatoryParametersResponse> {
  return request<RegulatoryParametersResponse>(
    `${REGULATORY_PARAMETERS}${query({
      scope_type: params?.scopeType,
      scope_key: params?.scopeKey,
      param_code: params?.paramCode,
      confirmation_status: params?.confirmationStatus,
      include_drafts: params?.includeDrafts,
    })}`,
  );
}

/** POST /operator/v1/regulatory-parameters — propose a new generation (maker). 201. */
export function proposeRegulatoryParameter(
  body: RegulatoryParameterProposeRequest,
): Promise<RegulatoryParameter> {
  return request<RegulatoryParameter>(REGULATORY_PARAMETERS, { method: 'POST', body });
}

/**
 * POST /operator/v1/regulatory-parameters/{id}/approve — approve a draft
 * (checker). Four-eyes: 422 when the approver is the proposer.
 */
export function approveRegulatoryParameter(
  id: string,
  body: { change_rationale?: string },
): Promise<RegulatoryParameter> {
  return request<RegulatoryParameter>(
    `${REGULATORY_PARAMETERS}/${encodeURIComponent(id)}/approve`,
    { method: 'POST', body },
  );
}

// --------------------------------------------------------------------------
// Operator cockpit overview + audit log (NEW backend endpoints — contracts
// fixed here; the BACKEND agent builds them to match).
// --------------------------------------------------------------------------

export interface OverviewNeedsAttentionItem {
  kind: string;
  summary: string;
  /** ok | warn | crit (typed string for forward compat). */
  severity: string;
  href?: string | null;
}

export interface OverviewResponse {
  tenants: {
    total: number;
    banks_live: number;
    banks_empty: number;
    stale_count: number;
  };
  ingestion: { failed_24h: number };
  jobs: { failed_24h: number; running: number };
  connections: { ok: number; warn: number; crit: number };
  desk: {
    pending_determinations: number;
    pending_curve_determinations: number;
    pending_oe_assessments: number;
  };
  needs_attention: OverviewNeedsAttentionItem[];
}

/** GET /operator/v1/overview — the home cockpit rollup. */
export function getOverview(): Promise<OverviewResponse> {
  return request<OverviewResponse>('/operator/v1/overview');
}

export interface AuditLogItem {
  id: string;
  operator_email: string;
  /** password | oidc | dev | integration_key — how the actor authenticated. */
  auth_mode: string;
  action: string;
  target_org: string | null;
  detail: string;
  created_at: string;
}

export interface AuditLogResponse {
  items: AuditLogItem[];
  total: number;
}

/** GET /operator/v1/audit — the append-only operator_audit_log, filterable. */
export function getAudit(opts?: {
  operatorEmail?: string;
  targetOrg?: string;
  action?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<AuditLogResponse> {
  return request<AuditLogResponse>(
    `/operator/v1/audit${query({
      operator_email: opts?.operatorEmail,
      target_org: opts?.targetOrg,
      action: opts?.action,
      from: opts?.from,
      to: opts?.to,
      limit: opts?.limit,
      offset: opts?.offset,
    })}`,
  );
}

// --------------------------------------------------------------------------
// Per-tenant detail (NEW backend endpoints — contracts fixed here).
// --------------------------------------------------------------------------

/** GET /operator/v1/tenants/{org_id} — one tenant (same shape as a list row). */
export function getTenant(orgId: string): Promise<OperatorTenant> {
  return request<OperatorTenant>(`/operator/v1/tenants/${encodeURIComponent(orgId)}`);
}

export interface TenantUser {
  email: string;
  full_name: string | null;
  role: string;
  auth_provider: string;
  is_active: boolean;
  last_login_at?: string | null;
  created_at: string;
}

export interface TenantUsersResponse {
  users: TenantUser[];
}

/** GET /operator/v1/tenants/{org_id}/users — the tenant's own users. */
export function getTenantUsers(orgId: string): Promise<TenantUsersResponse> {
  return request<TenantUsersResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/users`,
  );
}

export interface TenantEntitlementsResponse {
  entitlements: DeskEntitlement[];
  catalog: DeskEntitlementsResponse['catalog'];
}

/** GET /operator/v1/tenants/{org_id}/entitlements — desk entitlements + catalog. */
export function getTenantEntitlements(orgId: string): Promise<TenantEntitlementsResponse> {
  return request<TenantEntitlementsResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/entitlements`,
  );
}

export interface TenantStorageResponse {
  provider?: string | null;
  bucket?: string | null;
  object_count?: number | null;
  bytes?: number | null;
  kms_key_state?: string | null;
  note?: string | null;
}

/** GET /operator/v1/tenants/{org_id}/storage — object-store footprint + KMS state. */
export function getTenantStorage(orgId: string): Promise<TenantStorageResponse> {
  return request<TenantStorageResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/storage`,
  );
}

// --------------------------------------------------------------------------
// Session-gated deep tenant reads (Tenant Inspector, step 1).
//
// Every endpoint below is a diagnostic look INSIDE one tenant and 403s with
// `{ code: "inspection_required" }` unless the caller holds an ACTIVE inspector
// session for the org (backend/app/operator/features/inspector_reads.py). The
// wire shapes mirror the new Read models in backend/app/schemas/operator.py
// (Tenant*Metric/Finding/Ingestion/Config), verified on 2026-08-15. No secret,
// credential, or vault material is ever returned by any of these.
// --------------------------------------------------------------------------

/** Latest LiveMetric for one (bank, module) — the always-fresh baseline view. */
export interface TenantLiveMetric {
  bank_id: string;
  module: string;
  status: string;
  /** Module-defined output object (LCR ratio, buffers, etc.). Shape varies by module. */
  metrics: Record<string, unknown>;
  reporting_period_id: string;
  computed_from_input_hash: string | null;
  computed_at: string;
}

/** Latest immutable RegulatoryRun for one (bank, module/family). */
export interface TenantRegulatoryRun {
  run_id: string;
  bank_id: string;
  module: string;
  scenario_code: string;
  status: string;
  input_hash: string;
  reporting_period_id: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
}

export interface TenantMetricsResponse {
  organization_id: string;
  live_metrics: TenantLiveMetric[];
  regulatory_runs: TenantRegulatoryRun[];
}

/** GET /operator/v1/tenants/{org_id}/metrics — computed module outputs. Session-gated. */
export function getTenantMetrics(orgId: string): Promise<TenantMetricsResponse> {
  return request<TenantMetricsResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/metrics`,
  );
}

export interface TenantFinding {
  bank_id: string;
  module: string;
  rule_id: string;
  severity: string;
  status: string;
  message: string;
  metric: string | null;
  reporting_period_id: string;
  created_at: string;
  updated_at: string;
}

export interface TenantFindingsResponse {
  organization_id: string;
  findings: TenantFinding[];
  open_count: number;
}

/** GET /operator/v1/tenants/{org_id}/findings?limit=N — live findings/alerts. Session-gated. */
export function getTenantFindings(orgId: string, limit = 100): Promise<TenantFindingsResponse> {
  return request<TenantFindingsResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/findings${query({ limit })}`,
  );
}

export interface TenantIngestionBatch {
  batch_id: string;
  bank_id: string;
  source_system: string;
  adapter_version: string;
  extraction_mode: string;
  status: string;
  as_of_date: string; // date
  records_extracted: number;
  records_translated: number;
  records_accepted: number;
  records_warning: number;
  records_error: number;
  records_blocked: number;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface TenantIngestionListResponse {
  organization_id: string;
  batches: TenantIngestionBatch[];
}

/** GET /operator/v1/tenants/{org_id}/ingestion?limit=N — recent batches. Session-gated. */
export function getTenantIngestion(
  orgId: string,
  limit = 100,
): Promise<TenantIngestionListResponse> {
  return request<TenantIngestionListResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/ingestion${query({ limit })}`,
  );
}

/**
 * One raw source record that failed translation — the "why did this upload
 * fail" detail. `raw_record` is the tenant's own business data (never a
 * credential).
 */
export interface TenantTranslationFailure {
  entity_type: string;
  source_locator: string;
  error_code: string;
  error_message: string;
  raw_record: Record<string, unknown>;
  created_at: string;
}

export interface TenantIngestionBatchDetail {
  batch: TenantIngestionBatch;
  validation_report: Record<string, unknown>;
  etl_report: Record<string, unknown> | null;
  translation_failures: TenantTranslationFailure[];
}

/**
 * GET /operator/v1/tenants/{org_id}/ingestion/{batch_id} — batch detail with
 * validation/ETL reports and per-row translation failures. Session-gated; 404
 * if the batch is unknown or belongs to another tenant.
 */
export function getTenantIngestionBatch(
  orgId: string,
  batchId: string,
): Promise<TenantIngestionBatchDetail> {
  return request<TenantIngestionBatchDetail>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/ingestion/${encodeURIComponent(batchId)}`,
  );
}

export interface TenantConfigBank {
  bank_id: string;
  name: string;
  jurisdiction_code: string;
  currency: string;
  license_type: string;
}

export interface TenantMappingConfig {
  /** MappingConfigRecord.id (UUID) — the fix/config `target_id` for this mapping. */
  id: string;
  bank_id: string;
  source_system: string;
  source_ref: string;
  version: number;
  name: string;
  status: string;
  config: Record<string, unknown>;
}

export interface TenantLiquidityThreshold {
  /** Threshold record id (UUID) — the fix/config `target_id` for this threshold. */
  id: string;
  institution_class: string;
  threshold_code: string;
  threshold_pct: number;
  effective_from: string; // date
  effective_to: string | null;
  approved_by: string;
}

export interface TenantCapitalThreshold {
  /** Threshold record id (UUID) — the fix/config `target_id` for this threshold. */
  id: string;
  threshold_code: string;
  value_pct: number;
  effective_from: string; // date
  effective_to: string | null;
  approved_by: string;
}

export interface TenantThresholdRegister {
  liquidity: TenantLiquidityThreshold[];
  capital: TenantCapitalThreshold[];
}

/** OIDC connection diagnostics — NEVER the client secret (`secret_configured` only). */
export interface TenantSsoConfig {
  issuer: string;
  client_id: string;
  allowed_email_domains: string[];
  enabled: boolean;
  jit_enabled: boolean;
  secret_configured: boolean;
}

/** Integration-key metadata only — the SHA-256 hash and raw key are NEVER returned. */
export interface TenantIntegrationKey {
  label: string;
  key_prefix: string;
  status: 'active' | 'revoked';
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface TenantConfigResponse {
  organization_id: string;
  banks: TenantConfigBank[];
  active_mappings: TenantMappingConfig[];
  threshold_register: TenantThresholdRegister;
  sso: TenantSsoConfig | null;
  integration_keys: TenantIntegrationKey[];
}

/** GET /operator/v1/tenants/{org_id}/config — non-secret diagnostic config. Session-gated. */
export function getTenantConfig(orgId: string): Promise<TenantConfigResponse> {
  return request<TenantConfigResponse>(
    `/operator/v1/tenants/${encodeURIComponent(orgId)}/config`,
  );
}

// --------------------------------------------------------------------------
// Tenant Inspector sessions (NEW backend endpoints — contracts fixed here).
//
// A time-boxed, audited cross-tenant read (or read/write) session an operator
// opens against one org. The console surfaces the active session as an
// un-dismissable banner (see lib/inspector.tsx).
// --------------------------------------------------------------------------

const INSPECTOR = '/operator/v1/inspector/sessions';

// Backend `OPERATOR_INSPECTOR_MODES`: `consent` = routine access with the
// tenant's knowledge; `break_glass` = emergency, admin-gated access without
// consent. Both are READ-ONLY this wave (the session's `read_only` is always
// true); the mode is the access-justification, not a write grant.
export type InspectorMode = 'consent' | 'break_glass';

export interface InspectorSession {
  session_id: string;
  organization_id: string;
  mode: string;
  read_only: boolean;
  started_by: string;
  started_at: string;
  expires_at: string;
  reason: string;
  ended_at: string | null;
  ended_by: string | null;
}

export interface InspectorSessionsResponse {
  sessions: InspectorSession[];
  total: number;
}

/** POST /operator/v1/inspector/sessions — open a time-boxed inspector session. 201. */
export function startInspectorSession(body: {
  organization_id: string;
  reason: string;
  mode: InspectorMode;
  ttl_minutes: number;
}): Promise<InspectorSession> {
  return request<InspectorSession>(INSPECTOR, { method: 'POST', body });
}

/** GET /operator/v1/inspector/sessions — sessions, optionally only active / one org. */
export function listInspectorSessions(opts?: {
  active?: boolean;
  organizationId?: string;
  limit?: number;
}): Promise<InspectorSessionsResponse> {
  return request<InspectorSessionsResponse>(
    `${INSPECTOR}${query({
      active: opts?.active ? true : undefined,
      organization_id: opts?.organizationId,
      limit: opts?.limit,
    })}`,
  );
}

/** POST /operator/v1/inspector/sessions/{id}/end — end a session early. */
export function endInspectorSession(sessionId: string): Promise<InspectorSession> {
  return request<InspectorSession>(
    `${INSPECTOR}/${encodeURIComponent(sessionId)}/end`,
    { method: 'POST' },
  );
}

/**
 * A short-lived (≤15 min) read-only examiner token minted against an active
 * inspection session — the act-as-examiner hand-off to the bank's own
 * dashboard. `dashboard_url` is that tenant's dashboard origin; the token is
 * carried to it in the URL FRAGMENT, never a query param.
 */
export interface InspectorActToken {
  act_token: string;
  expires_at: string;
  dashboard_url: string;
}

/**
 * POST /operator/v1/inspector/sessions/{id}/act-token — mint a read-only
 * examiner token for the session's org. 503 when impersonation is not
 * configured on this environment; 404 unknown session; 403 non-owner; 409 the
 * session has ended or expired.
 */
export function mintInspectorActToken(sessionId: string): Promise<InspectorActToken> {
  return request<InspectorActToken>(
    `${INSPECTOR}/${encodeURIComponent(sessionId)}/act-token`,
    { method: 'POST' },
  );
}

/**
 * POST /desk/entitlements/grant-dataset — grant one org access to one dataset
 * (org × dataset, spec §10). Companion to grantDeskEntitlementTier.
 */
export function grantDeskEntitlementDataset(body: {
  organization_id: string;
  dataset_code: string;
  effective_from: string;
  notes?: string;
}): Promise<DeskEntitlement> {
  return request<DeskEntitlement>(`${DESK}/entitlements/grant-dataset`, {
    method: 'POST',
    body,
  });
}

// --------------------------------------------------------------------------
// Tenant remediation (fix) — the WRITE side of the Tenant Inspector.
//
// Every endpoint is under /operator/v1/tenants/{org}/fix, requires an ACTIVE
// inspection session for the org (403 `inspection_required` otherwise), and
// takes a REQUIRED `note` written to the operator audit log against that
// session. Each is a single, confirmed, audited operation.
//
// NOTE (2026-08-15): these routes are being built in the backend in parallel —
// the TenantFix* Read models are NOT yet in backend/app/schemas/operator.py.
// The shapes below are typed from the agreed contract; reconcile once they land.
// --------------------------------------------------------------------------

function tenantFix(orgId: string): string {
  return `/operator/v1/tenants/${encodeURIComponent(orgId)}/fix`;
}

/**
 * A queued background job spawned by a fix action. `job_type` is
 * "pipeline_refresh" for a recompute and "official_run" for an official run;
 * the re-run-ingestion job may omit it (typed optional for that reason).
 *
 * rerun-ingestion additionally returns `batch_id` (the re-derived batch) and
 * `detail`: ingestion batches are IMMUTABLE, so the re-run re-derives via
 * pipeline_refresh and `detail` explains that fixing a source-parsing problem
 * still requires a fresh upload through the Data Engine. Surface it verbatim.
 */
export interface TenantFixJob {
  job_id: string;
  job_type?: string;
  status: string;
  /** rerun-ingestion only: the id of the batch that was re-derived. */
  batch_id?: string;
  /** rerun-ingestion only: the immutability / re-upload caveat, shown verbatim. */
  detail?: string;
}

/** POST …/fix/recompute — re-derive this bank's live metrics (debounced pipeline_refresh). */
export function fixRecompute(orgId: string, body: { note: string }): Promise<TenantFixJob> {
  return request<TenantFixJob>(`${tenantFix(orgId)}/recompute`, { method: 'POST', body });
}

/** POST …/fix/official-run — mint an immutable official run (optional as-of date). */
export function fixOfficialRun(
  orgId: string,
  body: { as_of_date?: string; note: string },
): Promise<TenantFixJob> {
  return request<TenantFixJob>(`${tenantFix(orgId)}/official-run`, { method: 'POST', body });
}

/** POST …/fix/rerun-ingestion — re-run one ingestion batch by id (the failed-upload fix). */
export function fixRerunIngestion(
  orgId: string,
  body: { batch_id: string; note: string },
): Promise<TenantFixJob> {
  return request<TenantFixJob>(`${tenantFix(orgId)}/rerun-ingestion`, { method: 'POST', body });
}

/** The two config surfaces a fix may touch (backend Literal — closed on both sides). */
export type TenantFixConfigKind = 'mapping_active' | 'threshold_value';

export interface TenantFixConfigRequest {
  kind: TenantFixConfigKind;
  /**
   * The UUID `id` of the record to change — `TenantMappingConfig.id` for a
   * mapping, or the liquidity/capital threshold's `.id`. The Config Read now
   * carries this id on every mapping and threshold; the backend resolves
   * `target_id` against it (NOT source_ref / threshold_code).
   */
  target_id: string;
  /** Boolean active-flag for `mapping_active`; numeric pct for `threshold_value`. */
  value: boolean | number | string;
  note: string;
}

/** The updated record echoed back by a config fix. Shape is backend-defined; kept loose. */
export interface TenantFixConfigResult {
  kind: string;
  target_id: string;
  status?: string;
  detail?: string;
}

/** POST …/fix/config — change one non-secret config value (mapping active flag or threshold). */
export function fixConfig(
  orgId: string,
  body: TenantFixConfigRequest,
): Promise<TenantFixConfigResult> {
  return request<TenantFixConfigResult>(`${tenantFix(orgId)}/config`, { method: 'POST', body });
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
  /** Which sign-in produced the cookie session; null when unauthenticated. */
  mode: 'password' | 'oidc' | null;
}

async function consoleJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { cache: 'no-store', ...init });
  } catch (err) {
    throw new ApiError(
      'network_error',
      err instanceof Error ? err.message : 'fetch failed',
      0,
    );
  }
  if (!res.ok) {
    // Console routes relay the backend envelope — surface its code/message
    // (e.g. the login 401's deliberately generic text) when present.
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

export interface PasswordLoginResult {
  ok: boolean;
  email: string;
  role: string | null;
}

/**
 * Staff email+password sign-in (primary path). Credentials go to the
 * console's own /api/auth/password-login route, which relays them to the
 * operator API server-side and sets the HttpOnly op_session cookie — the
 * browser never holds the operator JWT.
 */
export function passwordLogin(email: string, password: string): Promise<PasswordLoginResult> {
  return consoleJson<PasswordLoginResult>('/api/auth/password-login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
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
