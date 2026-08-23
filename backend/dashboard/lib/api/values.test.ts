/**
 * Fail-closed regression suite for the regulatory display primitives.
 *
 * The dashboard has no component-test harness (Playwright e2e only), so the
 * safety RULES that the stress workbench, the Command Center banner and the
 * scenario comparison now share live in `lib/api/values.ts` as pure functions
 * and are pinned here. Each block names the audit finding it defends.
 *
 * Run: `pnpm --filter @aequoros/dashboard test`
 */

import assert from 'node:assert/strict';
import {
  assessAgainstFloor,
  floorNotAssessedReason,
  floorStatus,
  fmtFloorPct,
  fmtPctOrNull,
  moduleComplianceVerdict,
  num,
  numOrNull,
} from './values';

const failures: string[] = [];

function check(name: string, body: () => void): void {
  try {
    body();
  } catch (error) {
    failures.push(`${name}: ${(error as Error).message}`);
  }
}

// --- P0-23: num() keeps its old semantics; numOrNull preserves absence -------

check('num() still maps absence to 0 for its existing callers', () => {
  assert.equal(num(null), 0);
  assert.equal(num(undefined), 0);
  assert.equal(num('not a number'), 0);
  assert.equal(num('12.5'), 12.5);
  assert.equal(num(12.5), 12.5);
});

check('numOrNull() preserves absence instead of fabricating 0', () => {
  assert.equal(numOrNull(null), null);
  assert.equal(numOrNull(undefined), null);
  assert.equal(numOrNull(''), null);
  assert.equal(numOrNull('not a number'), null);
  assert.equal(numOrNull('0'), 0, 'a MEASURED zero must survive');
  assert.equal(numOrNull('12.5'), 12.5);
  assert.equal(numOrNull(12.5), 12.5);
});

// --- P0-19: a missing capital floor must not pass a breached CAR ------------

check('P0-19 — no floor yields "not assessed", never a pass', () => {
  const assessment = assessAgainstFloor(9.8, null);
  assert.equal(assessment.assessed, false);
  assert.notEqual(floorStatus(assessment), 'ok');
  assert.equal(floorStatus(assessment), 'warn');
});

check('P0-19 — a zeroed floor counts as no floor', () => {
  // The old code did `num(coupling?.car_min_pct ?? '0')`. Every ratio clears 0,
  // so a breached CAR rendered green. A 0% floor is a sentinel, not a floor.
  const assessment = assessAgainstFloor(0.4, 0);
  assert.equal(assessment.assessed, false);
  assert.notEqual(floorStatus(assessment), 'ok');
});

check('P0-19 — a missing measurement is not a pass either', () => {
  const assessment = assessAgainstFloor(null, 13);
  assert.equal(assessment.assessed, false);
  assert.notEqual(floorStatus(assessment), 'ok');
  assert.equal(
    floorNotAssessedReason(assessment, 'capital adequacy'),
    'capital adequacy not computed — compliance not assessed'
  );
});

check('P0-19 — a real breach is still critical, a real pass still ok', () => {
  const breach = assessAgainstFloor(9.8, 13);
  assert.deepEqual(breach, { assessed: true, breach: true, value: 9.8, floor: 13 });
  assert.equal(floorStatus(breach), 'crit');

  const pass = assessAgainstFloor(14.2, 13);
  assert.equal(pass.assessed, true);
  assert.equal(floorStatus(pass), 'ok');

  // Exactly at the floor is compliant (the rule is `< floor`).
  assert.equal(floorStatus(assessAgainstFloor(13, 13)), 'ok');
});

check('a value present with no floor is not silently graded', () => {
  assert.equal(floorNotAssessedReason(assessAgainstFloor(11, null), 'capital adequacy'),
    'No capital adequacy floor configured — compliance not assessed');
  assert.equal(floorNotAssessedReason(assessAgainstFloor(14.2, 13), 'capital adequacy'), null);
});

// --- P0-21: the floor comes from the payload, at its real precision ---------

check('P0-21 — an SDI s.29 floor of 10% clears an 11% CAR', () => {
  // The hardcoded CAR_FLOOR = 13 painted this exact case as a breach.
  const sdi = assessAgainstFloor(11, 10);
  assert.equal(sdi.assessed, true);
  assert.equal(floorStatus(sdi), 'ok');

  // The same 11% against a universal bank's 13% floor IS a breach.
  assert.equal(floorStatus(assessAgainstFloor(11, 13)), 'crit');
});

check('P0-21 — a fractional floor is not rounded away in the caption', () => {
  assert.equal(fmtFloorPct(13), '13%');
  assert.equal(fmtFloorPct(10), '10%');
  assert.equal(fmtFloorPct(12.5), '12.50%', 'a 12.5% floor must not read as 13%');
});

check('P0-21 — a null CET1 renders as absence, never 0.00%', () => {
  assert.equal(fmtPctOrNull(null, 2), '—');
  assert.equal(fmtPctOrNull(undefined, 2, 'n/a'), 'n/a');
  assert.equal(fmtPctOrNull(numOrNull(null), 2, 'n/a'), 'n/a');
  assert.equal(fmtPctOrNull(0, 2), '0.00%', 'a MEASURED zero still renders');
  assert.equal(fmtPctOrNull(14.2, 2), '14.20%');
});

// --- P0-20: "All limits compliant" needs something to have been measured ----

check('P0-20 — every module not computable is NOT compliance', () => {
  assert.equal(moduleComplianceVerdict(['na', 'na', 'na', 'na']), 'not_assessed');
  assert.equal(moduleComplianceVerdict([]), 'not_assessed');
});

check('P0-20 — partial coverage is not a clean bill of health', () => {
  assert.equal(moduleComplianceVerdict(['green', 'na']), 'partial');
  assert.equal(moduleComplianceVerdict(['green', 'amber', 'na']), 'partial');
});

check('P0-20 — a breach still wins over everything', () => {
  assert.equal(moduleComplianceVerdict(['red', 'na']), 'breach');
  assert.equal(moduleComplianceVerdict(['green', 'red']), 'breach');
  assert.equal(moduleComplianceVerdict(['red']), 'breach');
});

check('P0-20 — full coverage with no breach is still compliant', () => {
  assert.equal(moduleComplianceVerdict(['green', 'green']), 'compliant');
  assert.equal(moduleComplianceVerdict(['green', 'amber']), 'compliant');
});

// --- report ----------------------------------------------------------------

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  console.error(`\n${failures.length} fail-closed check(s) failed.`);
  process.exit(1);
}
console.log('values.test.ts: all fail-closed checks passed.');
