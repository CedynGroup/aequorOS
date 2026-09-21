/**
 * What a governed parameter's value must LOOK like — the console's mirror of
 * `backend/app/domain/policy/parameter_shapes.py`.
 *
 * D-024 says every regulatory number reaches the engines from this console.
 * D-037 adds the other kind of number: the ICAAP Pillar 2 engine reads BAND
 * TABLES, an FX shock table, an operational severity map and a sovereign
 * haircut grid. Those are structural bodies, and a console that can write one
 * it has not checked is worse than one that cannot write it at all — a band
 * table with a gap in it silently returns *no* add-on for the exposures inside
 * the gap, and nothing downstream can tell that from a genuine zero.
 *
 * THE SERVER IS STILL THE AUTHORITY. `operator/services/regulatory_parameters`
 * validates at BOTH propose and approve and answers
 * `422 {error_code: "parameter_shape_invalid", param_code, path, message}`.
 * This module exists so the operator gets the same message against the same
 * path while they are editing, instead of after they submit. It is a mirror,
 * never a substitute: if the two ever disagree, the server wins and this file
 * is the bug.
 *
 * NOTHING HERE IS A REGULATORY VALUE. Every bound below is structural — a
 * percentage lies between 0 and 100, a ratio between 0 and 1, a band table
 * covers its scale without gaps — and says nothing about what any particular
 * threshold should be.
 *
 * Kept free of React and of any runtime import so it runs under node in
 * `pnpm --filter @aequoros/console test`.
 */

// ---------------------------------------------------------------------------
// Structural vocabulary — mirrors the module constants of parameter_shapes.py
// ---------------------------------------------------------------------------

/** The add-on basis a band table may name (`ICAAP_BASES`, the AMOUNT basis). */
export const BAND_TABLE_BASES = [
  'pct_total_rwa',
  'pct_credit_rwa',
  'pct_pillar1_credit_capital',
  'absolute',
] as const;

const BAND_SCALES = ['unit_interval'] as const;
const BAND_MODES = ['step', 'linear'] as const;
const CONCENTRATION_DIMENSIONS = ['single_name', 'sector'] as const;
const CONCENTRATION_METRICS = ['hhi', 'gini', 'crn'] as const;
const SEVERITY_BASES = ['pct_annual_gross_income'] as const;
const CURRENCY_KINDS = ['reporting', 'foreign'] as const;

const CURRENCY_RE = /^[A-Z]{3}$/;
const KEY_RE = /^[a-z][a-z0-9_]*$/;
const DEFAULT_KEY = 'default';

const PERCENT_MAX = 100;

/** (metric, dimension) each band-table code must declare. */
const BAND_TABLE_CODES: Record<string, { metric: string; dimension: string }> = {
  ccr_name_bands_hhi: { metric: 'hhi', dimension: 'single_name' },
  ccr_name_bands_gini: { metric: 'gini', dimension: 'single_name' },
  ccr_name_bands_crn: { metric: 'crn', dimension: 'single_name' },
  ccr_sector_bands_hhi: { metric: 'hhi', dimension: 'sector' },
};

// ---------------------------------------------------------------------------
// The error
// ---------------------------------------------------------------------------

/**
 * A value that does not match its code's shape, with the PATH of the offending
 * part — the same `value_json.bands[2].lower` the server names, so the console
 * can point at the same place whichever side found it.
 */
export interface ShapeError {
  path: string;
  message: string;
}

function err(path: string, message: string): ShapeError {
  return { path, message };
}

// ---------------------------------------------------------------------------
// Primitive checks
// ---------------------------------------------------------------------------

/**
 * A number, from a JSON number or a decimal string (the preferred form).
 *
 * `Number()` is used for the BOUND check only. The value that is SENT is always
 * the JSON the operator wrote, byte for byte, so no precision is lost on the
 * wire; and the server re-validates with `Decimal`, which is the authority at
 * the boundary of a bound.
 */
function asNumber(value: unknown, path: string): number | ShapeError {
  if (typeof value === 'boolean' || value === null || value === undefined) {
    return err(path, 'must be a number (a decimal string is preferred)');
  }
  if (typeof value !== 'number' && typeof value !== 'string') {
    return err(path, 'must be a number (a decimal string is preferred)');
  }
  const text = String(value).trim();
  if (text === '' || !/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(text)) {
    return err(path, 'must be a number');
  }
  return Number(text);
}

function isShapeError(value: unknown): value is ShapeError {
  return (
    typeof value === 'object' &&
    value !== null &&
    'path' in value &&
    'message' in value
  );
}

function bounded(
  value: unknown,
  path: string,
  low: number,
  high: number | null,
): number | ShapeError {
  const number = asNumber(value, path);
  if (isShapeError(number)) return number;
  if (number < low || (high !== null && number > high)) {
    const bound = high !== null ? `between ${low} and ${high}` : `at least ${low}`;
    return err(path, `must be ${bound}`);
  }
  return number;
}

function asRecord(
  value: unknown,
  path: string,
): Record<string, unknown> | ShapeError {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return err(path, 'must be an object');
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown, path: string): unknown[] | ShapeError {
  if (!Array.isArray(value)) return err(path, 'must be a list');
  return value;
}

function required(
  body: Record<string, unknown>,
  key: string,
  path: string,
): unknown | ShapeError {
  if (!(key in body)) return err(`${path}.${key}`, 'is required');
  return body[key];
}

function oneOf(
  value: unknown,
  allowed: readonly string[],
  path: string,
): string | ShapeError {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    return err(path, `must be one of ${allowed.join(', ')}`);
  }
  return value;
}

function schemaIs(
  body: Record<string, unknown>,
  expected: string,
): ShapeError | null {
  if (!('schema' in body)) return err('value_json.schema', 'is required');
  if (body.schema !== expected) {
    return err('value_json.schema', `must be '${expected}'`);
  }
  return null;
}

function asInteger(value: unknown, path: string): number | ShapeError {
  const number = asNumber(value, path);
  if (isShapeError(number)) return number;
  if (!Number.isInteger(number)) return err(path, 'must be a whole number');
  return number;
}

// ---------------------------------------------------------------------------
// Scalar validators
// ---------------------------------------------------------------------------

type Validator = (
  valueNumeric: number | null,
  valueJson: Record<string, unknown> | null,
) => ShapeError | null;

function scalarBool(value: number | null): ShapeError | null {
  if (value === null) return err('value_numeric', 'is required');
  return value === 0 || value === 1
    ? null
    : err('value_numeric', 'must be 0 (no) or 1 (yes)');
}

function scalarIntMin1(value: number | null): ShapeError | null {
  if (value === null) return err('value_numeric', 'is required');
  return Number.isInteger(value) && value >= 1
    ? null
    : err('value_numeric', 'must be a whole number of at least 1');
}

function scalarPct(value: number | null): ShapeError | null {
  if (value === null) return err('value_numeric', 'is required');
  return value >= 0 && value <= PERCENT_MAX
    ? null
    : err('value_numeric', 'must be a percentage between 0 and 100');
}

function scalarRatio(value: number | null): ShapeError | null {
  if (value === null) return err('value_numeric', 'is required');
  return value >= 0 && value <= 1
    ? null
    : err('value_numeric', 'must be a ratio between 0 and 1');
}

/**
 * A likelihood x impact score. The matrix's SIZE is framework data, not a
 * parameter, so this checks the form only and leaves "does 26 fit a 5x5 matrix"
 * to the materiality domain, which knows the scale it was given.
 */
const scalarScore = scalarIntMin1;

// ---------------------------------------------------------------------------
// Structural validators
// ---------------------------------------------------------------------------

/**
 * A band table covers its whole scale, once, with a non-decreasing add-on.
 *
 * A gap returns no add-on for the exposures inside it and an overlap returns
 * two; both read downstream as an ordinary answer. The cover is therefore a
 * SHAPE rule, not a methodology choice, which is why the console refuses one.
 */
function validateBands(bands: unknown[]): ShapeError | null {
  if (bands.length === 0) {
    return err('value_json.bands', 'must contain at least one band');
  }
  let previousUpper: number | null = null;
  let previousAddon: number | null = null;
  const last = bands.length - 1;

  for (let index = 0; index <= last; index += 1) {
    const path = `value_json.bands[${index}]`;
    const band = asRecord(bands[index], path);
    if (isShapeError(band)) return band;

    const lowerRaw = required(band, 'lower', path);
    if (isShapeError(lowerRaw)) return lowerRaw;
    const lower = bounded(lowerRaw, `${path}.lower`, 0, null);
    if (isShapeError(lower)) return lower;
    if (index === 0 && lower !== 0) {
      return err(`${path}.lower`, 'the first band must start at 0');
    }
    if (previousUpper !== null && lower !== previousUpper) {
      return err(
        `${path}.lower`,
        `must continue the previous band exactly (expected ${previousUpper})`,
      );
    }

    const upperRaw = required(band, 'upper', path);
    if (isShapeError(upperRaw)) return upperRaw;
    if (upperRaw === null) {
      if (index !== last) {
        return err(`${path}.upper`, 'only the last band may be open-ended');
      }
      previousUpper = null;
    } else {
      const upper = bounded(upperRaw, `${path}.upper`, 0, null);
      if (isShapeError(upper)) return upper;
      if (upper <= lower) {
        return err(`${path}.upper`, 'must be greater than lower');
      }
      if (index === last) {
        return err(`${path}.upper`, 'the last band must be open-ended (null)');
      }
      previousUpper = upper;
    }

    const addonRaw = required(band, 'addon', path);
    if (isShapeError(addonRaw)) return addonRaw;
    const addon = bounded(addonRaw, `${path}.addon`, 0, null);
    if (isShapeError(addon)) return addon;
    if (previousAddon !== null && addon < previousAddon) {
      return err(
        `${path}.addon`,
        'must not fall as the metric rises (bands are ordered)',
      );
    }
    previousAddon = addon;
  }
  return null;
}

function bandTable(code: string): Validator {
  const expected = BAND_TABLE_CODES[code];
  return (_numeric, body) => {
    if (body === null) return err('value_json', 'is required');
    const schema = schemaIs(body, 'icaap-band-table-v1');
    if (schema) return schema;

    const metric = required(body, 'metric', 'value_json');
    if (isShapeError(metric)) return metric;
    if (metric !== expected.metric) {
      return err('value_json.metric', `must be '${expected.metric}' for ${code}`);
    }
    const dimension = required(body, 'dimension', 'value_json');
    if (isShapeError(dimension)) return dimension;
    if (dimension !== expected.dimension) {
      return err(
        'value_json.dimension',
        `must be '${expected.dimension}' for ${code}`,
      );
    }

    for (const [key, allowed] of [
      ['scale', BAND_SCALES],
      ['mode', BAND_MODES],
      ['basis', BAND_TABLE_BASES],
    ] as const) {
      const raw = required(body, key, 'value_json');
      if (isShapeError(raw)) return raw;
      const checked = oneOf(raw, allowed, `value_json.${key}`);
      if (isShapeError(checked)) return checked;
    }

    const bandsRaw = required(body, 'bands', 'value_json');
    if (isShapeError(bandsRaw)) return bandsRaw;
    const bands = asArray(bandsRaw, 'value_json.bands');
    if (isShapeError(bands)) return bands;
    return validateBands(bands);
  };
}

function scoreBands(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-score-bands-v1');
  if (schema) return schema;

  const bandsRaw = required(body, 'bands', 'value_json');
  if (isShapeError(bandsRaw)) return bandsRaw;
  const bands = asArray(bandsRaw, 'value_json.bands');
  if (isShapeError(bands)) return bands;
  if (bands.length === 0) {
    return err('value_json.bands', 'must contain at least one band');
  }

  const keys = new Set<string>();
  let expectedMin = 1;
  for (let index = 0; index < bands.length; index += 1) {
    const path = `value_json.bands[${index}]`;
    const band = asRecord(bands[index], path);
    if (isShapeError(band)) return band;

    const key = required(band, 'key', path);
    if (isShapeError(key)) return key;
    if (typeof key !== 'string' || !KEY_RE.test(key)) {
      return err(`${path}.key`, 'must be a lowercase identifier');
    }
    if (keys.has(key)) return err(`${path}.key`, `'${key}' is used twice`);
    keys.add(key);

    const label = required(band, 'label', path);
    if (isShapeError(label)) return label;
    if (typeof label !== 'string' || label.trim() === '') {
      return err(`${path}.label`, 'must be a non-empty label');
    }

    const minRaw = required(band, 'min_score', path);
    if (isShapeError(minRaw)) return minRaw;
    const minimum = asInteger(minRaw, `${path}.min_score`);
    if (isShapeError(minimum)) return minimum;

    const maxRaw = required(band, 'max_score', path);
    if (isShapeError(maxRaw)) return maxRaw;
    const maximum = asInteger(maxRaw, `${path}.max_score`);
    if (isShapeError(maximum)) return maximum;

    if (minimum !== expectedMin) {
      return err(
        `${path}.min_score`,
        `must be ${expectedMin} so the bands cover every score without a gap`,
      );
    }
    if (maximum < minimum) {
      return err(`${path}.max_score`, 'must not be below min_score');
    }
    expectedMin = maximum + 1;
  }
  return null;
}

function metricSet(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-ccr-metric-set-v1');
  if (schema) return schema;

  for (const dimension of CONCENTRATION_DIMENSIONS) {
    const path = `value_json.${dimension}`;
    const raw = required(body, dimension, 'value_json');
    if (isShapeError(raw)) return raw;
    const metrics = asArray(raw, path);
    if (isShapeError(metrics)) return metrics;
    if (metrics.length === 0) return err(path, 'must name at least one metric');

    const seen = new Set<string>();
    for (let index = 0; index < metrics.length; index += 1) {
      const name = oneOf(
        metrics[index],
        CONCENTRATION_METRICS,
        `${path}[${index}]`,
      );
      if (isShapeError(name)) return name;
      if (seen.has(name)) {
        return err(`${path}[${index}]`, `'${name}' is listed twice`);
      }
      seen.add(name);
    }
  }
  return null;
}

function codeList(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-code-list-v1');
  if (schema) return schema;

  const codesRaw = required(body, 'codes', 'value_json');
  if (isShapeError(codesRaw)) return codesRaw;
  const codes = asArray(codesRaw, 'value_json.codes');
  if (isShapeError(codes)) return codes;
  if (codes.length === 0) {
    return err('value_json.codes', 'must contain at least one code');
  }

  const seen = new Set<string>();
  for (let index = 0; index < codes.length; index += 1) {
    const path = `value_json.codes[${index}]`;
    const code = codes[index];
    if (typeof code !== 'string' || !KEY_RE.test(code)) {
      return err(path, 'must be a lowercase identifier');
    }
    if (seen.has(code)) return err(path, `'${code}' is listed twice`);
    seen.add(code);
  }

  const requiredRaw = required(body, 'required', 'value_json');
  if (isShapeError(requiredRaw)) return requiredRaw;
  const requiredCodes = asArray(requiredRaw, 'value_json.required');
  if (isShapeError(requiredCodes)) return requiredCodes;
  for (let index = 0; index < requiredCodes.length; index += 1) {
    const code = requiredCodes[index];
    if (typeof code !== 'string' || !seen.has(code)) {
      return err(
        `value_json.required[${index}]`,
        `'${String(code)}' is not in codes`,
      );
    }
  }
  return null;
}

function fxShocks(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-fx-shocks-v1');
  if (schema) return schema;

  for (const direction of ['depreciation', 'appreciation'] as const) {
    const path = `value_json.${direction}`;
    const raw = required(body, direction, 'value_json');
    if (isShapeError(raw)) return raw;
    const shocks = asRecord(raw, path);
    if (isShapeError(shocks)) return shocks;

    if (!(DEFAULT_KEY in shocks)) {
      return err(
        path,
        "must carry a 'default' shock; a currency with no entry would otherwise be shocked by nothing",
      );
    }
    for (const [currency, shock] of Object.entries(shocks)) {
      if (currency !== DEFAULT_KEY && !CURRENCY_RE.test(currency)) {
        return err(
          `${path}.${currency}`,
          "must be 'default' or an ISO 4217 code",
        );
      }
      const checked = bounded(shock, `${path}.${currency}`, 0, PERCENT_MAX);
      if (isShapeError(checked)) return checked;
    }
  }
  return null;
}

function severityMap(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-severity-map-v1');
  if (schema) return schema;

  const basisRaw = required(body, 'basis', 'value_json');
  if (isShapeError(basisRaw)) return basisRaw;
  const basis = oneOf(basisRaw, SEVERITY_BASES, 'value_json.basis');
  if (isShapeError(basis)) return basis;

  const scenariosRaw = required(body, 'scenarios', 'value_json');
  if (isShapeError(scenariosRaw)) return scenariosRaw;
  const scenarios = asRecord(scenariosRaw, 'value_json.scenarios');
  if (isShapeError(scenarios)) return scenarios;
  if (Object.keys(scenarios).length === 0) {
    return err('value_json.scenarios', 'must name at least one scenario');
  }

  for (const [name, severity] of Object.entries(scenarios)) {
    const path = `value_json.scenarios.${name}`;
    if (!KEY_RE.test(name)) {
      return err(path, 'scenario keys must be lowercase identifiers');
    }
    const checked = bounded(severity, path, 0, PERCENT_MAX);
    if (isShapeError(checked)) return checked;
  }
  return null;
}

function tenorBuckets(
  body: Record<string, unknown>,
): string[] | ShapeError {
  const raw = required(body, 'tenor_buckets', 'value_json');
  if (isShapeError(raw)) return raw;
  const buckets = asArray(raw, 'value_json.tenor_buckets');
  if (isShapeError(buckets)) return buckets;
  if (buckets.length === 0) {
    return err('value_json.tenor_buckets', 'must contain at least one bucket');
  }

  const keys: string[] = [];
  let previousMax: number | null = null;
  const last = buckets.length - 1;

  for (let index = 0; index <= last; index += 1) {
    const path = `value_json.tenor_buckets[${index}]`;
    const bucket = asRecord(buckets[index], path);
    if (isShapeError(bucket)) return bucket;

    const key = required(bucket, 'key', path);
    if (isShapeError(key)) return key;
    if (typeof key !== 'string' || key.trim() === '') {
      return err(`${path}.key`, 'must be a non-empty key');
    }
    if (keys.includes(key)) return err(`${path}.key`, `'${key}' is used twice`);
    keys.push(key);

    const maxRaw = required(bucket, 'max_years', path);
    if (isShapeError(maxRaw)) return maxRaw;
    if (maxRaw === null) {
      if (index !== last) {
        return err(`${path}.max_years`, 'only the last bucket may be open-ended');
      }
      previousMax = null;
      continue;
    }
    const years = bounded(maxRaw, `${path}.max_years`, 0, null);
    if (isShapeError(years)) return years;
    if (previousMax !== null && years <= previousMax) {
      return err(`${path}.max_years`, 'buckets must be ordered by tenor');
    }
    if (index === last) {
      return err(`${path}.max_years`, 'the last bucket must be open-ended (null)');
    }
    previousMax = years;
  }
  return keys;
}

function haircutGrid(
  _numeric: number | null,
  body: Record<string, unknown> | null,
): ShapeError | null {
  if (body === null) return err('value_json', 'is required');
  const schema = schemaIs(body, 'icaap-haircut-grid-v1');
  if (schema) return schema;

  const bucketKeys = tenorBuckets(body);
  if (isShapeError(bucketKeys)) return bucketKeys;

  const kindsRaw = required(body, 'currency_kinds', 'value_json');
  if (isShapeError(kindsRaw)) return kindsRaw;
  const kinds = asArray(kindsRaw, 'value_json.currency_kinds');
  if (isShapeError(kinds)) return kinds;
  if (kinds.length === 0) {
    return err('value_json.currency_kinds', 'must name at least one kind');
  }

  const haircutsRaw = required(body, 'haircut_pct', 'value_json');
  if (isShapeError(haircutsRaw)) return haircutsRaw;
  const haircuts = asRecord(haircutsRaw, 'value_json.haircut_pct');
  if (isShapeError(haircuts)) return haircuts;

  for (let index = 0; index < kinds.length; index += 1) {
    const kind = oneOf(
      kinds[index],
      CURRENCY_KINDS,
      `value_json.currency_kinds[${index}]`,
    );
    if (isShapeError(kind)) return kind;

    const path = `value_json.haircut_pct.${kind}`;
    if (!(kind in haircuts)) {
      return err(path, `is required: '${kind}' is a declared currency kind`);
    }
    const row = asRecord(haircuts[kind], path);
    if (isShapeError(row)) return row;

    for (const bucketKey of bucketKeys) {
      const cell = `${path}.${bucketKey}`;
      if (!(bucketKey in row)) {
        return err(
          cell,
          'is required: every declared tenor bucket needs a haircut',
        );
      }
      const checked = bounded(row[bucketKey], cell, 0, PERCENT_MAX);
      if (isShapeError(checked)) return checked;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// The registry
// ---------------------------------------------------------------------------

/** The kind of editor a code needs: a number field, or the table editor. */
export type ShapeKind = 'scalar' | 'structural';

export interface Shape {
  /** The form's name, as the server names it: `band_table`, `shock_table`, … */
  name: string;
  kind: ShapeKind;
  validator: Validator;
}

function scalar(name: string, validator: Validator): Shape {
  return { name, kind: 'scalar', validator };
}

function structural(name: string, validator: Validator): Shape {
  return { name, kind: 'structural', validator };
}

/**
 * Every ICAAP governed code, with the form its value must take. A code NOT
 * listed here is unconstrained, exactly as before D-037 — registering a code is
 * a deliberate act that makes both the console and the operator API enforce its
 * shape, and the two lists are held equal by test on the backend side.
 */
export const SHAPES: Readonly<Record<string, Shape>> = {
  // workspace (seeded by 202609190055)
  icaap_submission_months: scalar('months', (n) => scalarIntMin1(n)),
  icaap_disclosure_submission_months: scalar('months', (n) => scalarIntMin1(n)),
  icaap_deadline_amber_days: scalar('days', (n) => scalarIntMin1(n)),
  icaap_materiality_material_min_score: scalar('score', (n) => scalarScore(n)),
  icaap_materiality_material_min_impact: scalar('score', (n) => scalarScore(n)),
  icaap_materiality_rating_bands: structural('score_bands', scoreBands),
  icaap_stress_horizon_years_min: scalar('years', (n) => scalarIntMin1(n)),
  icaap_capital_planning_horizon_years_min: scalar('years', (n) =>
    scalarIntMin1(n),
  ),
  // Pillar 2 engine (seeded by 202609190056)
  icaap_independent_review_max_months: scalar('months', (n) => scalarIntMin1(n)),
  icaap_review_max_months: scalar('months', (n) => scalarIntMin1(n)),
  icaap_diversification_benefit_allowed: scalar('boolean', (n) => scalarBool(n)),
  icaap_pillar2_source_tolerance_pct: scalar('percent', (n) => scalarPct(n)),
  icaap_car_min_includes_ccb1: scalar('boolean', (n) => scalarBool(n)),
  ccr_min_dimension_coverage_pct: scalar('percent', (n) => scalarPct(n)),
  ccr_metric_set: structural('metric_set', metricSet),
  ccr_name_cr_n: scalar('count', (n) => scalarIntMin1(n)),
  ccr_name_bands_hhi: structural('band_table', bandTable('ccr_name_bands_hhi')),
  ccr_name_bands_gini: structural(
    'band_table',
    bandTable('ccr_name_bands_gini'),
  ),
  ccr_name_bands_crn: structural('band_table', bandTable('ccr_name_bands_crn')),
  ccr_sector_bands_hhi: structural(
    'band_table',
    bandTable('ccr_sector_bands_hhi'),
  ),
  ccr_name_hhi_coeff: scalar('ratio', (n) => scalarRatio(n)),
  ccr_sector_hhi_coeff: scalar('ratio', (n) => scalarRatio(n)),
  irrbb_outlier_threshold_pct_tier1: scalar('percent', (n) => scalarPct(n)),
  icaap_irrbb_interim_scenarios: structural('code_list', codeList),
  fx_p2_shock_pct: structural('shock_table', fxShocks),
  op_p2_scenario_severity_pct_gross_income: structural(
    'severity_map',
    severityMap,
  ),
  sov_p2_haircut_pct: structural('haircut_grid', haircutGrid),
  sov_p2_exposure_categories: structural('code_list', codeList),
};

/** The declared shape of `paramCode`, or null when it is unconstrained. */
export function shapeFor(paramCode: string): Shape | null {
  return SHAPES[paramCode.trim()] ?? null;
}

/** Whether `paramCode` is a registered TABLE-valued parameter. */
export function isStructural(paramCode: string): boolean {
  return shapeFor(paramCode)?.kind === 'structural';
}

/**
 * Check a governed value against its code's declared shape.
 *
 * Returns null for an unregistered code, and for a registered one whose value
 * matches. Otherwise a `ShapeError` naming the offending path, the same way the
 * server's 422 does.
 *
 * Exclusivity is checked first and in both directions, because getting it wrong
 * is the silent failure: a scalar written into a structural code would be
 * ignored by the engine, and a table written into a scalar code would be read
 * as no value at all.
 */
export function validateShape(
  paramCode: string,
  valueNumeric: string | number | null,
  valueJson: Record<string, unknown> | null,
): ShapeError | null {
  const shape = shapeFor(paramCode);
  if (shape === null) return null;

  if (shape.kind === 'structural') {
    if (valueJson === null) {
      return err(
        'value_json',
        `${paramCode} is a ${shape.name}: it carries a structured value, not a number`,
      );
    }
    if (valueNumeric !== null && String(valueNumeric).trim() !== '') {
      return err(
        'value_numeric',
        `${paramCode} is a ${shape.name}: it carries a structured value, not a number`,
      );
    }
    return shape.validator(null, valueJson);
  }

  if (valueNumeric === null || String(valueNumeric).trim() === '') {
    return err(
      'value_numeric',
      `${paramCode} is a scalar (${shape.name}): give a number`,
    );
  }
  if (valueJson !== null) {
    return err(
      'value_json',
      `${paramCode} is a scalar (${shape.name}): give a number`,
    );
  }
  const parsed = asNumber(valueNumeric, 'value_numeric');
  if (isShapeError(parsed)) return parsed;
  return shape.validator(parsed, null);
}
