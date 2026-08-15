/**
 * Hand-rolled fetch layer for the market-data SOURCE-SELECTION surface
 * (docs/internal/market_data_sources.md §4). These three endpoint families
 * landed after the last OpenAPI client regeneration, so — unlike the rest of
 * the Markets hub, which flows through the generated `marketDataApi` — they are
 * called directly here. The wire contract is frozen by spec §4; the parsers
 * below map its snake_case JSON onto the dashboard's camelCase view models and
 * reuse `client.ts`'s `ApiError` envelope so failures surface identically to
 * every generated-client call.
 *
 * Auth mirrors `client.ts`'s `Configuration.accessToken`: prefer the warm
 * cached bearer, fall back to the NextAuth session (which triggers a silent
 * refresh) so a call that fires before the first TokenSync effect still
 * authenticates.
 *
 * Rate/yield conventions (spec §4): forward-grid discount factors and forward
 * yields are DECIMAL FRACTIONS (0.15 = 15%; ×100 for display). Reference-rate
 * index values stay percent-valued and are rendered as-is (RatesBoard already
 * distinguishes them).
 */

import { getSession } from 'next-auth/react';
import { ApiError } from './client';
import { apiBaseUrl } from './client';
import { getAccessToken, setAccessToken } from './token';

// ---------------------------------------------------------------------------
// Contract types (spec §4) — camelCase app models.
// ---------------------------------------------------------------------------

export type MarketDataCategory = 'curves' | 'fx' | 'rates';
export type MarketDataSource = 'aequor' | 'bank' | 'vendor';

export const MARKET_DATA_CATEGORIES: MarketDataCategory[] = ['curves', 'fx', 'rates'];
export const MARKET_DATA_SOURCES: MarketDataSource[] = ['aequor', 'bank', 'vendor'];

/** UI labels for the three planes (jurisdiction-neutral, spec §1 table). */
export const SOURCE_LABELS: Record<MarketDataSource, string> = {
  aequor: 'AequorOS',
  bank: 'Bank',
  vendor: 'Vendor',
};

export const CATEGORY_LABELS: Record<MarketDataCategory, string> = {
  curves: 'Curves',
  fx: 'FX',
  rates: 'Rates',
};

/** One category's base-source choice + overlay toggle (spec §2). */
export interface CategorySourcePreference {
  source: MarketDataSource;
  overlay: boolean;
}

/** `GET /source-preferences` — defaults synthesised server-side when no row. */
export interface MarketDataSourcePreferences {
  curves: CategorySourcePreference;
  fx: CategorySourcePreference;
  rates: CategorySourcePreference;
  updatedAt: Date | null;
  updatedBy: string | null;
}

/** `PUT /source-preferences` body — partial allowed; `reason` optional/audited. */
export interface MarketDataSourcePreferencesPatch {
  curves?: Partial<CategorySourcePreference>;
  fx?: Partial<CategorySourcePreference>;
  rates?: Partial<CategorySourcePreference>;
  reason?: string;
}

/** Provenance + freshness for one plane column (mirrors SourceAttribution). */
export interface PlaneAttribution {
  sourceSystem: string;
  stale: boolean;
  ageSeconds: number;
  ingestedAt: Date | null;
  ingestionBatchId: string | null;
  /** Set when the getter fell back off the requested plane (spec §2). */
  fellBack?: boolean;
  requestedSource?: string | null;
  servedSource?: string | null;
}

/**
 * A plane's resolved items for one category. Left as the wire shape (snake_case)
 * because it is a heterogeneous union — CurveView | FxRateView | IndexView per
 * category — and the comparison table only reads a handful of identifying
 * fields per category. Never trust every field to be present.
 */
export interface PlaneItemWire {
  // curves (CurveView)
  curve_name?: string;
  currency?: string;
  curve_type?: string;
  points?: Array<{ tenor_months?: number; rate?: string }>;
  // fx (FxRateView)
  base?: string;
  quote?: string;
  rate?: string;
  rate_type?: string;
  // rates (IndexView)
  index_code?: string;
  scenario?: string;
  value?: string;
  // common
  as_of_date?: string;
}

/** One base plane, resolved for the requested category + as-of (spec §4). */
export interface MarketDataPlane {
  source: MarketDataSource;
  available: boolean;
  items: PlaneItemWire[];
  attribution: PlaneAttribution | null;
  isSelected: boolean;
}

/**
 * Overlay delta-preview entry. Spec §4 leaves the element shape loose
 * (`delta_preview: [...]`), so this reads defensively: any of these fields may
 * be present depending on the category the desk composes.
 */
export interface OverlayDeltaEntry {
  scope?: string;
  label?: string;
  tenor_months?: number;
  base?: string | number;
  adjusted?: string | number;
  delta?: string | number;
  delta_bps?: string | number;
}

/** `GET /planes?category=&as_of=` — the same scope under every plane. */
export interface MarketDataPlanesResponse {
  category: MarketDataCategory;
  asOf: string;
  selectedSource: MarketDataSource;
  overlayEnabled: boolean;
  planes: MarketDataPlane[];
  overlay: { available: boolean; deltaPreview: OverlayDeltaEntry[] };
}

/** One reference pillar backing a published forward grid (spec §4 FC-5/G1). */
export interface ForwardGridPillar {
  tenor: string;
  instrument: string;
  quote: string;
}

/** One published forward-grid row — DF + forward yield are DECIMAL FRACTIONS. */
export interface ForwardGridPublishedRow {
  start: string;
  end: string;
  discountFactor: string;
  forwardYield: string;
}

/** Immutable, desk-governed definition that produced a published forward grid. */
export interface ForwardGridAssumptions {
  version: number;
  calendarName: string;
  instrumentSetRef: string;
  projectionIndex: string | null;
  discountCurveCode: string | null;
  interpolationMethod: string;
  outputDaycount: string;
  paymentFrequency: string | null;
  paymentIntervalMonths: number;
  curveFrequency: string;
  spotLagDays: number;
  rollConvention: string;
  extrapolationRule: string;
}

/** `GET /curves/{curve_name}/forward-grid?as_of=` — the desk's published grid. */
export interface ForwardGridResponse {
  curveName: string;
  currency: string;
  asOf: string;
  methodologyRef: string | null;
  interpolation: string | null;
  gridIsAuthoritative: boolean;
  frequency: string;
  availableFrequencies: string[];
  assumptions: ForwardGridAssumptions | null;
  rows: ForwardGridPublishedRow[];
  pillars: ForwardGridPillar[];
}

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

/** Read one field off an unknown record without leaking `any`. */
function field(source: unknown, key: string): unknown {
  if (source && typeof source === 'object') {
    return (source as Record<string, unknown>)[key];
  }
  return undefined;
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function optStr(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function bool(value: unknown): boolean {
  return value === true;
}

function nummeric(value: unknown): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== 'string' || value.length === 0) return null;
  const parsed = new Date(value.length <= 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** Shape the backend `{ error: { code, message, details } }` envelope. */
function apiErrorFromEnvelope(status: number, body: unknown): ApiError {
  const envelope = field(body, 'error') ?? body;
  const code = optStr(field(envelope, 'code'));
  const details = field(envelope, 'details') ?? field(envelope, 'detail') ?? null;
  const detailMessage = optStr(field(details, 'message'));
  const message =
    detailMessage ?? optStr(field(envelope, 'message')) ?? `Request failed (${status}).`;
  const errorCode = optStr(field(details, 'error_code'));
  return new ApiError({ message, status, code, errorCode, details });
}

async function mdFetch(path: string, init?: RequestInit): Promise<unknown> {
  const token = await bearerToken();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init?.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers });
  } catch (error) {
    throw new ApiError({
      message: 'Could not reach the risk service. Check that the backend is running.',
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

function parseSource(value: unknown): MarketDataSource {
  return value === 'bank' || value === 'vendor' ? value : 'aequor';
}

function parseCategoryPreference(value: unknown): CategorySourcePreference {
  return {
    source: parseSource(field(value, 'source')),
    overlay: field(value, 'overlay') === undefined ? true : bool(field(value, 'overlay')),
  };
}

function parsePreferences(body: unknown): MarketDataSourcePreferences {
  return {
    curves: parseCategoryPreference(field(body, 'curves')),
    fx: parseCategoryPreference(field(body, 'fx')),
    rates: parseCategoryPreference(field(body, 'rates')),
    updatedAt: parseDate(field(body, 'updated_at')),
    updatedBy: optStr(field(body, 'updated_by')),
  };
}

function parseAttribution(value: unknown): PlaneAttribution | null {
  if (!value || typeof value !== 'object') return null;
  return {
    sourceSystem: str(field(value, 'source_system')),
    stale: bool(field(value, 'stale')),
    ageSeconds: nummeric(field(value, 'age_seconds')),
    ingestedAt: parseDate(field(value, 'ingested_at')),
    ingestionBatchId: optStr(field(value, 'ingestion_batch_id')),
    fellBack: bool(field(value, 'fell_back')),
    requestedSource: optStr(field(value, 'requested_source')),
    servedSource: optStr(field(value, 'served_source')),
  };
}

function parsePlane(value: unknown): MarketDataPlane {
  const rawItems = field(value, 'items');
  const items = Array.isArray(rawItems) ? (rawItems as PlaneItemWire[]) : [];
  return {
    source: parseSource(field(value, 'source')),
    available: bool(field(value, 'available')),
    items,
    attribution: parseAttribution(field(value, 'attribution')),
    isSelected: bool(field(value, 'is_selected')),
  };
}

function parsePlanes(body: unknown): MarketDataPlanesResponse {
  const rawPlanes = field(body, 'planes');
  const overlay = field(body, 'overlay');
  const rawDeltas = field(overlay, 'delta_preview');
  return {
    category: (field(body, 'category') as MarketDataCategory) ?? 'curves',
    asOf: str(field(body, 'as_of')),
    selectedSource: parseSource(field(body, 'selected_source')),
    overlayEnabled: bool(field(body, 'overlay_enabled')),
    planes: Array.isArray(rawPlanes) ? rawPlanes.map(parsePlane) : [],
    overlay: {
      available: bool(field(overlay, 'available')),
      deltaPreview: Array.isArray(rawDeltas) ? (rawDeltas as OverlayDeltaEntry[]) : [],
    },
  };
}

function parseForwardGrid(body: unknown): ForwardGridResponse {
  const rawRows = field(body, 'rows');
  const rawPillars = field(body, 'pillars');
  const rows: ForwardGridPublishedRow[] = Array.isArray(rawRows)
    ? rawRows.map((row) => ({
        start: str(field(row, 'start')),
        end: str(field(row, 'end')),
        discountFactor: str(field(row, 'discount_factor')),
        forwardYield: str(field(row, 'forward_yield')),
      }))
    : [];
  const pillars: ForwardGridPillar[] = Array.isArray(rawPillars)
    ? rawPillars.map((pillar) => ({
        tenor: str(field(pillar, 'tenor')),
        instrument: str(field(pillar, 'instrument')),
        quote: str(field(pillar, 'quote')),
      }))
    : [];
  const rawAssumptions = field(body, 'assumptions');
  const assumptions = rawAssumptions && typeof rawAssumptions === 'object'
    ? {
        version: nummeric(field(rawAssumptions, 'version')),
        calendarName: str(field(rawAssumptions, 'calendar_name')),
        instrumentSetRef: str(field(rawAssumptions, 'instrument_set_ref')),
        projectionIndex: optStr(field(rawAssumptions, 'projection_index')),
        discountCurveCode: optStr(field(rawAssumptions, 'discount_curve_code')),
        interpolationMethod: str(field(rawAssumptions, 'interpolation_method')),
        outputDaycount: str(field(rawAssumptions, 'output_daycount')),
        paymentFrequency: optStr(field(rawAssumptions, 'payment_frequency')),
        paymentIntervalMonths: nummeric(field(rawAssumptions, 'payment_interval_months')),
        curveFrequency: str(field(rawAssumptions, 'curve_frequency')),
        spotLagDays: nummeric(field(rawAssumptions, 'spot_lag_days')),
        rollConvention: str(field(rawAssumptions, 'roll_convention')),
        extrapolationRule: str(field(rawAssumptions, 'extrapolation_rule')),
      }
    : null;
  return {
    curveName: str(field(body, 'curve_name')),
    currency: str(field(body, 'currency')),
    asOf: str(field(body, 'as_of')),
    methodologyRef: optStr(field(body, 'methodology_ref')),
    interpolation: optStr(field(body, 'interpolation')),
    gridIsAuthoritative: bool(field(body, 'grid_is_authoritative')),
    frequency: str(field(body, 'frequency')),
    availableFrequencies: Array.isArray(field(body, 'available_frequencies'))
      ? (field(body, 'available_frequencies') as unknown[]).filter(
          (frequency): frequency is string => typeof frequency === 'string'
        )
      : [],
    assumptions,
    rows,
    pillars,
  };
}

// ---------------------------------------------------------------------------
// Public fetch functions (spec §4).
// ---------------------------------------------------------------------------

const base = (bankId: string) => `/banks/${encodeURIComponent(bankId)}/market-data`;

/** `GET /source-preferences`. */
export async function getMarketDataSourcePreferences(
  bankId: string
): Promise<MarketDataSourcePreferences> {
  return parsePreferences(await mdFetch(`${base(bankId)}/source-preferences`));
}

/** `PUT /source-preferences` (partial body; returns the resolved row). */
export async function putMarketDataSourcePreferences(
  bankId: string,
  patch: MarketDataSourcePreferencesPatch
): Promise<MarketDataSourcePreferences> {
  return parsePreferences(
    await mdFetch(`${base(bankId)}/source-preferences`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    })
  );
}

/** `GET /planes?category=&as_of=` — the side-by-side plane comparison. */
export async function getMarketDataPlanes(
  bankId: string,
  params: { category: MarketDataCategory; asOf?: string }
): Promise<MarketDataPlanesResponse> {
  const query = new URLSearchParams({ category: params.category });
  if (params.asOf) query.set('as_of', params.asOf);
  return parsePlanes(await mdFetch(`${base(bankId)}/planes?${query.toString()}`));
}

/** `GET /curves/{curve_name}/forward-grid?as_of=` — the published forward grid. */
export async function getForwardGrid(
  bankId: string,
  curveName: string,
  params?: { asOf?: string; frequency?: string }
): Promise<ForwardGridResponse> {
  const query = new URLSearchParams();
  if (params?.asOf) query.set('as_of', params.asOf);
  if (params?.frequency) query.set('frequency', params.frequency);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return parseForwardGrid(
    await mdFetch(`${base(bankId)}/curves/${encodeURIComponent(curveName)}/forward-grid${suffix}`)
  );
}
