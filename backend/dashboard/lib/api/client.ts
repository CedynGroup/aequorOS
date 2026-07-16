/**
 * Risk-service API client wiring for the demo app.
 *
 * Instantiates the generated OpenAPI classes against the local risk-service
 * and threads the demo tenant headers onto every request. All financial data
 * shown in the app flows through these clients — never hand-rolled fetches.
 */

import {
  BanksApi,
  BehavioralModelsApi,
  CashflowForecastApi,
  Configuration,
  ForecastingApi,
  JobsApi,
  LiveEngineApi,
  MarketDataApi,
  RegulatoryCapitalApi,
  RegulatoryFtpApi,
  RegulatoryFxApi,
  RegulatoryIrrApi,
  RegulatoryLiquidityApi,
  ResponseError,
} from '@aequoros/risk-service-api';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8003/api/v1';
const DEFAULT_ORG_ID = '11111111-1111-4111-8111-111111111111';
const DEFAULT_USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

const baseUrl =
  process.env.NEXT_PUBLIC_RISK_API_BASE_URL ?? DEFAULT_BASE_URL;

/** Fully-qualified /api/v1 base of the risk service (for display + health checks). */
export const apiBaseUrl = baseUrl;

/**
 * Base with the trailing /api/v1 stripped. Generated OpenAPI paths already
 * carry /api/v1, so the client is configured with this. The single source of
 * truth for the API origin — import it instead of re-deriving from env.
 */
export const apiOrigin = baseUrl.replace(/\/api\/v1\/?$/, '');

/** Demo tenant identity, injected as X-Org-Id / X-User-Id on every call. */
export const tenant = {
  orgId: process.env.NEXT_PUBLIC_DEMO_ORG_ID ?? DEFAULT_ORG_ID,
  userId: process.env.NEXT_PUBLIC_DEMO_USER_ID ?? DEFAULT_USER_ID,
} as const;

const configuration = new Configuration({ basePath: apiOrigin });

export const banksApi = new BanksApi(configuration);
export const behavioralModelsApi = new BehavioralModelsApi(configuration);
export const regulatoryLiquidityApi = new RegulatoryLiquidityApi(configuration);
export const regulatoryCapitalApi = new RegulatoryCapitalApi(configuration);
export const regulatoryIrrApi = new RegulatoryIrrApi(configuration);
export const regulatoryFxApi = new RegulatoryFxApi(configuration);
export const regulatoryFtpApi = new RegulatoryFtpApi(configuration);
export const forecastingApi = new ForecastingApi(configuration);
export const cashflowForecastApi = new CashflowForecastApi(configuration);
export const liveEngineApi = new LiveEngineApi(configuration);
export const jobsApi = new JobsApi(configuration);
export const marketDataApi = new MarketDataApi(configuration);

/**
 * Normalized error surfaced to the UI. `status` is the HTTP status code,
 * `code` the backend envelope code (e.g. "conflict"), and `errorCode` the
 * domain code carried in `details.error_code` (e.g. "no_baseline_run").
 */
export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly errorCode: string | null;
  readonly details: unknown;

  constructor(options: {
    message: string;
    status: number | null;
    code: string | null;
    errorCode: string | null;
    details?: unknown;
  }) {
    super(options.message);
    this.name = 'ApiError';
    this.status = options.status;
    this.code = options.code;
    this.errorCode = options.errorCode;
    this.details = options.details;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/**
 * Unwrap the backend error envelope
 * `{ error: { code, message, details, request_id } }` from a generated-client
 * failure. 409s carry `details.error_code` (e.g. "no_baseline_run"); the
 * cashflow proxy returns 503 when the ML sidecar is offline.
 */
export async function normalizeApiError(error: unknown): Promise<ApiError> {
  if (error instanceof ApiError) return error;
  if (error instanceof ResponseError) {
    const status = error.response.status;
    let code: string | null = null;
    let errorCode: string | null = null;
    let message = `Request failed (${status}).`;
    let details: unknown;
    try {
      const body = await error.response.clone().json();
      const envelope = body?.error ?? body;
      if (envelope && typeof envelope === 'object') {
        code = typeof envelope.code === 'string' ? envelope.code : null;
        if (typeof envelope.message === 'string') message = envelope.message;
        details = envelope.details ?? envelope.detail ?? null;
        const detailObj = details as { error_code?: string; message?: string } | null;
        if (detailObj && typeof detailObj === 'object') {
          if (typeof detailObj.error_code === 'string') {
            errorCode = detailObj.error_code;
          }
          if (typeof detailObj.message === 'string') {
            message = detailObj.message;
          }
        }
      }
    } catch {
      // Response body was not JSON — keep the generic message.
    }
    return new ApiError({ message, status, code, errorCode, details });
  }
  if (error instanceof Error) {
    return new ApiError({
      message:
        'Could not reach the risk service. Check that the backend is running.',
      status: null,
      code: 'network_error',
      errorCode: null,
      details: error.message,
    });
  }
  return new ApiError({
    message: 'Unexpected error.',
    status: null,
    code: 'unknown',
    errorCode: null,
    details: error,
  });
}

/** Run a generated-client call, rethrowing failures as normalized ApiError. */
export async function apiCall<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (error) {
    throw await normalizeApiError(error);
  }
}
