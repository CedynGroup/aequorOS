/**
 * Hand-rolled fetch layer for the Reports "Compare" surface —
 * `GET /banks/{bankId}/reports/comparison`.
 *
 * The comparison endpoint post-dates the last OpenAPI client regeneration, so —
 * like the market-data source-selection layer in ./marketDataSources — it is
 * called directly here rather than through the generated `regulatoryReportingApi`.
 * The parsers below map its snake_case JSON onto the dashboard's camelCase view
 * models and reuse `client.ts`'s `ApiError` envelope so failures (404 missing,
 * 422 `not_comparable`) surface identically to every generated-client call.
 *
 * Auth mirrors `client.ts`'s `Configuration.accessToken`: prefer the warm cached
 * bearer, fall back to the NextAuth session (which triggers a silent refresh) so
 * a call that fires before the first TokenSync effect still authenticates.
 *
 * Value conventions (backend contract): every line value is a decimal STRING
 * (parse with `Number`); `delta_ccy` is the absolute delta in the value's native
 * unit (a currency amount for `ccy` lines, percentage points for `pct`/`ratio`,
 * a raw count for `count`); `delta_pct` is the RELATIVE change versus the left
 * base and is `null` when that base is zero (a new / base-zero line, flagged by
 * `new`). Both `pct` and `ratio` values are percentage-scaled and render with a
 * "%" suffix — never re-scaled here.
 */

import { getSession } from 'next-auth/react';
import { ApiError, apiBaseUrl } from './client';
import { getAccessToken, setAccessToken } from './token';

// ---------------------------------------------------------------------------
// Contract types — camelCase app models.
// ---------------------------------------------------------------------------

export type ComparisonMode = 'version' | 'period';
export type ComparisonModule =
  | 'liquidity'
  | 'capital'
  | 'irr'
  | 'fx'
  | 'ftp'
  | 'forecast';
export type ComparisonUnit = 'ccy' | 'pct' | 'ratio' | 'count';
export type ComparisonDirection = 'up' | 'down' | 'flat';
export type ComparisonFavorability = 'favorable' | 'adverse' | 'neutral';

/** One side of the diff — a run (version mode) or a period (period mode). */
export interface ComparisonSide {
  runId: string | null;
  version: number | null;
  label: string;
  periodLabel: string;
  reportingDate: Date | null;
  reportingPeriodId: string | null;
  scenarioCode: string;
  engineVersion: string;
}

/** One line item within a group. Values parsed off the wire's decimal strings. */
export interface ComparisonLine {
  key: string;
  label: string;
  unit: ComparisonUnit;
  leftValue: number | null;
  rightValue: number | null;
  /** Absolute delta in the line's native unit (currency / pp / count). */
  deltaCcy: number | null;
  /** Relative % change vs the left base; `null` when the base is zero. */
  deltaPct: number | null;
  direction: ComparisonDirection;
  favorability: ComparisonFavorability;
  /** The line is new / grew from a zero base — no relative % is defined. */
  isNew: boolean;
}

export interface ComparisonGroup {
  title: string;
  lines: ComparisonLine[];
}

export interface ReportComparison {
  mode: ComparisonMode;
  module: ComparisonModule;
  left: ComparisonSide;
  right: ComparisonSide;
  groups: ComparisonGroup[];
  favorableCount: number;
  adverseCount: number;
  neutralCount: number;
}

export interface ReportComparisonParams {
  mode: ComparisonMode;
  module: ComparisonModule;
  /** Run id (version mode) or reporting-period id (period mode). */
  left: string;
  /** Run id (version mode) or reporting-period id (period mode). */
  right: string;
  scenarioCode?: string;
}

export const COMPARISON_MODULES: ComparisonModule[] = [
  'liquidity',
  'capital',
  'irr',
  'fx',
  'ftp',
  'forecast',
];

/** Display labels for the return-family (module) picker. */
export const COMPARISON_MODULE_LABELS: Record<ComparisonModule, string> = {
  liquidity: 'Liquidity',
  capital: 'Capital',
  irr: 'IRRBB',
  fx: 'FX',
  ftp: 'FTP',
  forecast: 'Forecast',
};

// ---------------------------------------------------------------------------
// Transport — bearer auth + ApiError envelope, matching client.ts.
// ---------------------------------------------------------------------------

async function bearerToken(): Promise<string> {
  const cached = getAccessToken();
  if (cached) return cached;
  const session = await getSession();
  const token = session?.accessToken ?? '';
  if (token) setAccessToken(token);
  return token;
}

function field(source: unknown, key: string): unknown {
  if (source && typeof source === 'object') {
    return (source as Record<string, unknown>)[key];
  }
  return undefined;
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function bool(value: unknown): boolean {
  return value === true;
}

/** Parse a wire decimal string (or number) to a finite number, else null. */
function numOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== 'string' || value.length === 0) return null;
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Pull the domain `error_code` from wherever the backend places it: the
 * standard envelope's `error.details.error_code`, a bare `details.error_code`,
 * a FastAPI `detail.error_code`, or a top-level `error_code`. This is what lets
 * the page recognise a 422 `not_comparable` and show its friendly message.
 */
function extractErrorCode(body: unknown): string | null {
  const envelope = field(body, 'error') ?? body;
  const candidates = [
    field(field(envelope, 'details'), 'error_code'),
    field(field(body, 'details'), 'error_code'),
    field(field(body, 'detail'), 'error_code'),
    field(body, 'error_code'),
  ];
  const hit = candidates.find((c) => typeof c === 'string');
  return typeof hit === 'string' ? hit : null;
}

function apiErrorFromEnvelope(status: number, body: unknown): ApiError {
  const envelope = field(body, 'error') ?? body;
  const code = str(field(envelope, 'code')) || null;
  const details =
    field(envelope, 'details') ?? field(envelope, 'detail') ?? body ?? null;
  const message =
    str(field(details, 'message')) ||
    str(field(envelope, 'message')) ||
    `Request failed (${status}).`;
  return new ApiError({
    message,
    status,
    code,
    errorCode: extractErrorCode(body),
    details,
  });
}

async function comparisonFetch(path: string): Promise<unknown> {
  const token = await bearerToken();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { headers });
  } catch (error) {
    throw new ApiError({
      message:
        'Could not reach the risk service. Check that the backend is running.',
      status: null,
      code: 'network_error',
      errorCode: null,
      details: error instanceof Error ? error.message : error,
    });
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw apiErrorFromEnvelope(response.status, body);
  return body;
}

// ---------------------------------------------------------------------------
// Parsers (wire snake_case → app camelCase).
// ---------------------------------------------------------------------------

function parseSide(raw: unknown): ComparisonSide {
  return {
    runId: (field(raw, 'run_id') as string | null) ?? null,
    version: numOrNull(field(raw, 'version')),
    label: str(field(raw, 'label')),
    periodLabel: str(field(raw, 'period_label')),
    reportingDate: parseDate(field(raw, 'reporting_date')),
    reportingPeriodId:
      (field(raw, 'reporting_period_id') as string | null) ?? null,
    scenarioCode: str(field(raw, 'scenario_code')) || 'baseline',
    engineVersion: str(field(raw, 'engine_version')),
  };
}

function parseUnit(value: unknown): ComparisonUnit {
  return value === 'pct' || value === 'ratio' || value === 'count'
    ? value
    : 'ccy';
}

function parseDirection(value: unknown): ComparisonDirection {
  return value === 'up' || value === 'down' ? value : 'flat';
}

function parseFavorability(value: unknown): ComparisonFavorability {
  return value === 'favorable' || value === 'adverse' ? value : 'neutral';
}

function parseLine(raw: unknown): ComparisonLine {
  return {
    key: str(field(raw, 'key')),
    label: str(field(raw, 'label')),
    unit: parseUnit(field(raw, 'unit')),
    leftValue: numOrNull(field(raw, 'left_value')),
    rightValue: numOrNull(field(raw, 'right_value')),
    deltaCcy: numOrNull(field(raw, 'delta_ccy')),
    deltaPct: numOrNull(field(raw, 'delta_pct')),
    direction: parseDirection(field(raw, 'direction')),
    favorability: parseFavorability(field(raw, 'favorability')),
    isNew: bool(field(raw, 'new')),
  };
}

function parseGroup(raw: unknown): ComparisonGroup {
  const lines = field(raw, 'lines');
  return {
    title: str(field(raw, 'title')),
    lines: Array.isArray(lines) ? lines.map(parseLine) : [],
  };
}

function parseComparison(raw: unknown): ReportComparison {
  const groups = field(raw, 'groups');
  return {
    mode: field(raw, 'mode') === 'period' ? 'period' : 'version',
    module: parseUnitModule(field(raw, 'module')),
    left: parseSide(field(raw, 'left')),
    right: parseSide(field(raw, 'right')),
    groups: Array.isArray(groups) ? groups.map(parseGroup) : [],
    favorableCount: numOrNull(field(raw, 'favorable_count')) ?? 0,
    adverseCount: numOrNull(field(raw, 'adverse_count')) ?? 0,
    neutralCount: numOrNull(field(raw, 'neutral_count')) ?? 0,
  };
}

function parseUnitModule(value: unknown): ComparisonModule {
  return COMPARISON_MODULES.includes(value as ComparisonModule)
    ? (value as ComparisonModule)
    : 'liquidity';
}

// ---------------------------------------------------------------------------
// Endpoint.
// ---------------------------------------------------------------------------

/** `GET /banks/{bankId}/reports/comparison` — the server-computed line diff. */
export async function getReportComparison(
  bankId: string,
  params: ReportComparisonParams
): Promise<ReportComparison> {
  const query = new URLSearchParams({
    mode: params.mode,
    module: params.module,
    left: params.left,
    right: params.right,
  });
  if (params.scenarioCode) query.set('scenario_code', params.scenarioCode);
  const path = `/banks/${encodeURIComponent(bankId)}/reports/comparison?${query.toString()}`;
  return parseComparison(await comparisonFetch(path));
}
