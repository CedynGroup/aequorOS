/**
 * Node tests for the console's mirror of `parameter_shapes.py` (D-037).
 *
 * What is being protected: a staff operator can now WRITE the tables the ICAAP
 * Pillar 2 engine reads — band tables, the FX shock table, the operational
 * severity map, the sovereign haircut grid. A malformed one is not a crash, it
 * is a quiet wrong answer: a gap in a band table's cover returns *no* add-on
 * for the exposures inside the gap, and nothing downstream can tell that from a
 * genuine zero. So the shape rules are pinned here, on the same cases the
 * backend suite pins, and the exclusivity between the two value arms is pinned
 * in both directions.
 *
 * The server remains the authority (it validates at propose AND at approve).
 * These tests pin the MIRROR, so a divergence shows up here rather than as a
 * 422 the operator cannot act on.
 *
 * Run by `pnpm --filter @aequoros/console test`.
 */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SHAPES,
  isStructural,
  shapeFor,
  validateShape,
} from './parameter-shapes';

// ---------------------------------------------------------------------------
// Fixtures — the shapes as the seed catalogue writes them
// ---------------------------------------------------------------------------

function bandTable(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 'icaap-band-table-v1',
    metric: 'hhi',
    dimension: 'single_name',
    scale: 'unit_interval',
    mode: 'step',
    basis: 'pct_total_rwa',
    bands: [
      { lower: 0, upper: '0.1', addon: 0 },
      { lower: '0.1', upper: '0.18', addon: '0.5' },
      { lower: '0.18', upper: null, addon: '1.5' },
    ],
    ...overrides,
  };
}

function scoreBands(): Record<string, unknown> {
  return {
    schema: 'icaap-score-bands-v1',
    bands: [
      { key: 'low', label: 'Low', min_score: 1, max_score: 6 },
      { key: 'moderate', label: 'Moderate', min_score: 7, max_score: 12 },
      { key: 'high', label: 'High', min_score: 13, max_score: 25 },
    ],
  };
}

function fxShocks(): Record<string, unknown> {
  return {
    schema: 'icaap-fx-shocks-v1',
    depreciation: { default: '12', USD: '10' },
    appreciation: { default: '5.5' },
  };
}

function haircutGrid(): Record<string, unknown> {
  return {
    schema: 'icaap-haircut-grid-v1',
    tenor_buckets: [
      { key: 'short', max_years: 1 },
      { key: 'medium', max_years: 5 },
      { key: 'long', max_years: null },
    ],
    currency_kinds: ['reporting', 'foreign'],
    haircut_pct: {
      reporting: { short: 0, medium: '2', long: '4' },
      foreign: { short: '1', medium: '5', long: '9' },
    },
  };
}

// ---------------------------------------------------------------------------
// A valid table is accepted
// ---------------------------------------------------------------------------

test('a well-formed band table is accepted', () => {
  assert.equal(validateShape('ccr_name_bands_hhi', null, bandTable()), null);
});

test('a well-formed table of every registered structural form is accepted', () => {
  const cases: [string, Record<string, unknown>][] = [
    ['ccr_name_bands_hhi', bandTable()],
    ['ccr_sector_bands_hhi', bandTable({ dimension: 'sector' })],
    ['ccr_name_bands_gini', bandTable({ metric: 'gini' })],
    ['ccr_name_bands_crn', bandTable({ metric: 'crn' })],
    ['icaap_materiality_rating_bands', scoreBands()],
    ['fx_p2_shock_pct', fxShocks()],
    ['sov_p2_haircut_pct', haircutGrid()],
    [
      'ccr_metric_set',
      {
        schema: 'icaap-ccr-metric-set-v1',
        single_name: ['hhi', 'gini', 'crn'],
        sector: ['hhi'],
      },
    ],
    [
      'icaap_irrbb_interim_scenarios',
      {
        schema: 'icaap-code-list-v1',
        codes: ['parallel_up', 'parallel_down'],
        required: ['parallel_up'],
      },
    ],
    [
      'op_p2_scenario_severity_pct_gross_income',
      {
        schema: 'icaap-severity-map-v1',
        basis: 'pct_annual_gross_income',
        scenarios: { internal_fraud: '15', system_outage: '8.5' },
      },
    ],
  ];
  for (const [code, body] of cases) {
    assert.equal(validateShape(code, null, body), null, code);
  }
});

test('a valid scalar is accepted on each scalar form', () => {
  assert.equal(validateShape('icaap_submission_months', '3', null), null);
  assert.equal(validateShape('icaap_pillar2_source_tolerance_pct', '1', null), null);
  assert.equal(validateShape('ccr_name_hhi_coeff', '0.5', null), null);
  assert.equal(validateShape('icaap_car_min_includes_ccb1', '0', null), null);
});

// ---------------------------------------------------------------------------
// A malformed table is rejected, with the path the server names
// ---------------------------------------------------------------------------

test('a gap in the cover is rejected, naming the band that starts it', () => {
  const broken = bandTable({
    bands: [
      { lower: 0, upper: '0.1', addon: 0 },
      // 0.1 -> 0.12 is a gap: exposures inside it would get NO add-on, which
      // reads downstream exactly like a genuine zero.
      { lower: '0.12', upper: null, addon: '1' },
    ],
  });
  const error = validateShape('ccr_name_bands_hhi', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.bands[1].lower');
  assert.match(error.message, /continue the previous band/);
});

test('an overlap is rejected for the same reason as a gap', () => {
  const broken = bandTable({
    bands: [
      { lower: 0, upper: '0.1', addon: 0 },
      { lower: '0.05', upper: null, addon: '1' },
    ],
  });
  const error = validateShape('ccr_name_bands_hhi', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.bands[1].lower');
});

test('a table that does not start at 0 is rejected', () => {
  const broken = bandTable({
    bands: [{ lower: '0.1', upper: null, addon: '1' }],
  });
  const error = validateShape('ccr_name_bands_hhi', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.bands[0].lower');
  assert.match(error.message, /must start at 0/);
});

test('only the LAST band may be open-ended, and it must be', () => {
  const openInTheMiddle = bandTable({
    bands: [
      { lower: 0, upper: null, addon: 0 },
      { lower: '0.1', upper: null, addon: '1' },
    ],
  });
  const first = validateShape('ccr_name_bands_hhi', null, openInTheMiddle);
  assert.ok(first);
  assert.match(first.message, /only the last band/);

  const closedAtTheTop = bandTable({
    bands: [
      { lower: 0, upper: '0.1', addon: 0 },
      { lower: '0.1', upper: '0.5', addon: '1' },
    ],
  });
  const second = validateShape('ccr_name_bands_hhi', null, closedAtTheTop);
  assert.ok(second);
  assert.equal(second.path, 'value_json.bands[1].upper');
  assert.match(second.message, /open-ended/);
});

test('a falling add-on is rejected — bands are ordered', () => {
  const broken = bandTable({
    bands: [
      { lower: 0, upper: '0.1', addon: '2' },
      { lower: '0.1', upper: null, addon: '1' },
    ],
  });
  const error = validateShape('ccr_name_bands_hhi', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.bands[1].addon');
});

test("a sector table pasted into the single-name slot is rejected", () => {
  const error = validateShape(
    'ccr_name_bands_hhi',
    null,
    bandTable({ dimension: 'sector' }),
  );
  assert.ok(error);
  assert.equal(error.path, 'value_json.dimension');
});

test('an unknown or missing schema tag is rejected', () => {
  const wrong = validateShape(
    'ccr_name_bands_hhi',
    null,
    bandTable({ schema: 'icaap-band-table-v2' }),
  );
  assert.ok(wrong);
  assert.equal(wrong.path, 'value_json.schema');

  const missing = bandTable();
  delete missing.schema;
  const absent = validateShape('ccr_name_bands_hhi', null, missing);
  assert.ok(absent);
  assert.equal(absent.path, 'value_json.schema');
});

test('score bands must cover every score from 1, without a gap or a repeat', () => {
  const gap = {
    schema: 'icaap-score-bands-v1',
    bands: [
      { key: 'low', label: 'Low', min_score: 1, max_score: 6 },
      { key: 'high', label: 'High', min_score: 9, max_score: 25 },
    ],
  };
  const gapError = validateShape('icaap_materiality_rating_bands', null, gap);
  assert.ok(gapError);
  assert.equal(gapError.path, 'value_json.bands[1].min_score');

  const repeated = {
    schema: 'icaap-score-bands-v1',
    bands: [
      { key: 'low', label: 'Low', min_score: 1, max_score: 6 },
      { key: 'low', label: 'Low again', min_score: 7, max_score: 25 },
    ],
  };
  const repeatedError = validateShape(
    'icaap_materiality_rating_bands',
    null,
    repeated,
  );
  assert.ok(repeatedError);
  assert.equal(repeatedError.path, 'value_json.bands[1].key');
});

test("an FX shock table without a 'default' is rejected", () => {
  const broken = { ...fxShocks(), depreciation: { USD: '10' } };
  const error = validateShape('fx_p2_shock_pct', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.depreciation');
  // The reason matters: a currency with no entry would be shocked by nothing.
  assert.match(error.message, /shocked by nothing/);
});

test('a non-ISO currency key in a shock table is rejected', () => {
  const broken = { ...fxShocks(), appreciation: { default: '5', dollars: '4' } };
  const error = validateShape('fx_p2_shock_pct', null, broken);
  assert.ok(error);
  assert.equal(error.path, 'value_json.appreciation.dollars');
});

test('a haircut grid with a missing cell is rejected, naming the cell', () => {
  const grid = haircutGrid();
  const haircuts = grid.haircut_pct as Record<string, Record<string, unknown>>;
  delete haircuts.foreign.medium;
  const error = validateShape('sov_p2_haircut_pct', null, grid);
  assert.ok(error);
  assert.equal(error.path, 'value_json.haircut_pct.foreign.medium');
  assert.match(error.message, /every declared tenor bucket/);
});

test('a percentage outside 0-100 is rejected wherever it appears', () => {
  const grid = haircutGrid();
  (grid.haircut_pct as Record<string, Record<string, unknown>>).foreign.long = '140';
  const error = validateShape('sov_p2_haircut_pct', null, grid);
  assert.ok(error);
  assert.equal(error.path, 'value_json.haircut_pct.foreign.long');
});

test('a scalar outside its declared range is rejected', () => {
  const pct = validateShape('icaap_pillar2_source_tolerance_pct', '140', null);
  assert.ok(pct);
  assert.equal(pct.path, 'value_numeric');

  const ratio = validateShape('ccr_name_hhi_coeff', '2', null);
  assert.ok(ratio);

  const months = validateShape('icaap_submission_months', '0', null);
  assert.ok(months);

  const bool = validateShape('icaap_car_min_includes_ccb1', '2', null);
  assert.ok(bool);
});

// ---------------------------------------------------------------------------
// Exclusivity: a code carries one arm, and the wrong one is refused BOTH ways
// ---------------------------------------------------------------------------

test('a number written into a table-valued code is refused', () => {
  const error = validateShape('ccr_name_bands_hhi', '13', null);
  assert.ok(error);
  assert.equal(error.path, 'value_json');
  assert.match(error.message, /structured value, not a number/);
});

test('a table written into a scalar code is refused', () => {
  const error = validateShape(
    'icaap_pillar2_source_tolerance_pct',
    null,
    bandTable(),
  );
  assert.ok(error);
  assert.equal(error.path, 'value_numeric');
  assert.match(error.message, /is a scalar/);
});

test('both arms at once is refused', () => {
  const error = validateShape('ccr_name_bands_hhi', '13', bandTable());
  assert.ok(error);
  assert.equal(error.path, 'value_numeric');
});

// ---------------------------------------------------------------------------
// The registry itself
// ---------------------------------------------------------------------------

test('an unregistered code is unconstrained, exactly as before D-037', () => {
  assert.equal(validateShape('car_min', '13', null), null);
  assert.equal(validateShape('sdi_rwa_composition', null, { anything: true }), null);
  assert.equal(shapeFor('car_min'), null);
  assert.equal(isStructural('car_min'), false);
});

test('every registered code declares a form and a kind', () => {
  for (const [code, shape] of Object.entries(SHAPES)) {
    assert.ok(shape.name.length > 0, code);
    assert.ok(['scalar', 'structural'].includes(shape.kind), code);
    assert.equal(isStructural(code), shape.kind === 'structural', code);
  }
});

test('the structural codes are exactly the table-valued ones the engine reads', () => {
  const structural = Object.keys(SHAPES).filter(isStructural).sort();
  assert.deepEqual(structural, [
    'ccr_metric_set',
    'ccr_name_bands_crn',
    'ccr_name_bands_gini',
    'ccr_name_bands_hhi',
    'ccr_sector_bands_hhi',
    'fx_p2_shock_pct',
    'icaap_irrbb_interim_scenarios',
    'icaap_materiality_rating_bands',
    'op_p2_scenario_severity_pct_gross_income',
    'sov_p2_exposure_categories',
    'sov_p2_haircut_pct',
  ]);
});

test('a code code is matched after trimming, as the form sends it', () => {
  assert.equal(isStructural('  ccr_name_bands_hhi  '), true);
  assert.equal(validateShape('  ccr_name_bands_hhi  ', null, bandTable()), null);
});
