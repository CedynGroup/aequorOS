/**
 * The IRRBB Standardised Framework normalisers, against the payloads a server
 * might really send.
 *
 * Same discipline as `icaapFilingNormalize.test.ts`: an empty body, a body
 * with `null` where a list was declared, a body that is not an object at all.
 * None of them may throw, and none of them may produce a figure, a verdict or
 * a tally nobody measured.
 *
 * The cases that matter most on THIS screen are the ones where an
 * over-generous default is a measurement:
 *
 *   - a missing economic-value loss rendered as `0` is indistinguishable from
 *     a book with no interest-rate risk;
 *   - an unreadable mandate rendered as "not required" tells a preparer the
 *     interim method is still fine when it may not be;
 *   - an unreported assumption tally rendered as `0` says a modelling default
 *     was never used when the truth is that nobody counted;
 *   - an outlier verdict computed against a missing threshold is a pass nobody
 *     granted.
 *
 * Each of those has its own test below.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  normalizeAssumption,
  normalizeAttempts,
  normalizeCurrencyScope,
  normalizeDataQuality,
  normalizeMandate,
  normalizeMeasures,
  normalizeParameter,
  normalizeRunSummary,
  normalizeScenario,
  normalizeStandardisedFramework,
  normalizeTable8Row,
} from "./irrbbSfNormalize";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

/** Everything a server could plausibly hand back that is not the contract. */
const HOSTILE: unknown[] = [
  {},
  null,
  undefined,
  [],
  "",
  0,
  "not json",
  { detail: "Not Found" },
  { error: { code: "not_found", message: "Bank not found." } },
];

/** The row-level normalisers take a record; the rest take anything. */
const ROW = (fn: (row: Record<string, unknown>) => unknown) => (value: unknown) =>
  fn(
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {},
  );

const NORMALIZERS: [string, (value: unknown) => unknown][] = [
  ["framework", normalizeStandardisedFramework],
  ["attempts", normalizeAttempts],
  ["mandate", normalizeMandate],
  ["measures", normalizeMeasures],
  ["run summary", normalizeRunSummary],
  ["data quality", normalizeDataQuality],
  ["parameter", ROW(normalizeParameter)],
  ["scenario", ROW(normalizeScenario)],
  ["currency scope", ROW(normalizeCurrencyScope)],
  ["disclosure row", ROW(normalizeTable8Row)],
  ["assumption", ROW(normalizeAssumption)],
];

// ---------------------------------------------------------------------------
// Nothing throws, on anything
// ---------------------------------------------------------------------------

test("no normaliser throws on a body that is not the contract", () => {
  for (const [name, normalize] of NORMALIZERS) {
    for (const body of HOSTILE) {
      assert.doesNotThrow(
        () => normalize(body),
        `${name} threw on ${JSON.stringify(body) ?? String(body)}`,
      );
    }
  }
});

/** Walk a normalised result and assert no declared field came back undefined. */
function assertNoUndefined(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      assertNoUndefined(entry, `${path}[${index}]`),
    );
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const [key, inner] of Object.entries(value)) {
    assert.notEqual(
      inner,
      undefined,
      `${path}.${key} is undefined — a component would crash reading it`,
    );
    assertNoUndefined(inner, `${path}.${key}`);
  }
}

test("an empty body yields a fully-populated shape with no undefined anywhere", () => {
  for (const [name, normalize] of NORMALIZERS) {
    assertNoUndefined(normalize({}), name);
  }
});

test("a null where a list was declared becomes an empty list", () => {
  const view = normalizeStandardisedFramework({
    parameters: null,
    scenarios: null,
    currencies: null,
    excludedCurrencies: null,
    table8: null,
    nmdCategories: null,
    ladders: null,
    buckets: null,
    statements: null,
  });
  assert.deepEqual(view.parameters, []);
  assert.deepEqual(view.scenarios, []);
  assert.deepEqual(view.currencies, []);
  assert.deepEqual(view.excludedCurrencies, []);
  assert.deepEqual(view.table8, []);
  assert.deepEqual(view.nmdCategories, []);
  assert.deepEqual(view.ladders, []);
  assert.deepEqual(view.buckets, []);
  assert.deepEqual(view.statements, []);
  assert.deepEqual(view.dataQuality.assumptions, []);
  assert.deepEqual(view.dataQuality.exclusions, []);
});

test("a list containing junk keeps only the rows that are objects", () => {
  const view = normalizeStandardisedFramework({
    scenarios: [null, "parallel_up", 7, { code: "parallel_up" }],
  });
  assert.equal(view.scenarios.length, 1);
  assert.equal(view.scenarios[0].code, "parallel_up");
  assert.ok(Array.isArray(view.scenarios[0].byCurrency));
});

// ---------------------------------------------------------------------------
// NO FIGURE IS EVER INVENTED
// ---------------------------------------------------------------------------

test("every money and ratio figure is null when absent, NEVER zero", () => {
  const view = normalizeStandardisedFramework({});
  const figures: [string, unknown][] = [
    ["tier1", view.tier1],
    ["pctTier1", view.pctTier1],
    ["outlierThresholdPct", view.outlierThresholdPct],
    ["outlier-set measure", view.measures.outlierSet.measure],
    ["mandatory measure", view.measures.mandatory.measure],
    ["all-scenario measure", view.measures.allScenarios.measure],
    [
      "average repricing maturity",
      view.table7Quantitative.averageRepricingMaturityYears,
    ],
  ];
  for (const [name, value] of figures) {
    assert.equal(value, null, `${name} was invented as ${String(value)}`);
    assert.notEqual(value, 0, `${name} came back as a measured zero`);
  }
});

test("a figure is kept as TEXT, so no precision is lost on the way in", () => {
  const row = normalizeTable8Row({ deltaEve: "-1234567.891234" });
  assert.equal(row.deltaEve, "-1234567.891234");
  assert.equal(typeof row.deltaEve, "string");
});

test("a figure that is not a number at all is absent, not NaN", () => {
  const row = normalizeTable8Row({ deltaEve: "pending", deltaNii: {} });
  assert.equal(row.deltaEve, null);
  assert.equal(row.deltaNii, null);
});

test("a real zero survives — the absence rule must not erase a measured zero", () => {
  const row = normalizeTable8Row({ deltaEve: "0", deltaNii: 0 });
  assert.equal(row.deltaEve, "0");
  assert.equal(row.deltaNii, "0");
});

// ---------------------------------------------------------------------------
// The mandate: neither side is assumed
// ---------------------------------------------------------------------------

test("an unreadable mandate says neither required nor not required", () => {
  const mandate = normalizeMandate({});
  assert.equal(mandate.stated, false);
  assert.equal(mandate.mandatory, false);
  assert.equal(mandate.statement, null);
});

test("only a literal true makes the framework mandatory", () => {
  for (const value of [undefined, null, "true", 1, {}]) {
    assert.equal(normalizeMandate({ mandatory: value }).mandatory, false);
  }
  assert.equal(normalizeMandate({ mandatory: true }).mandatory, true);
});

test("a mandate is SETTLED only when the server says confirmed", () => {
  assert.equal(normalizeMandate({ confirmationStatus: "pending" }).settled, false);
  assert.equal(normalizeMandate({}).settled, false);
  assert.equal(
    normalizeMandate({ confirmationStatus: "confirmed" }).settled,
    true,
  );
});

test("the mandate's own sentence is carried verbatim", () => {
  const statement = "The framework applies to reporting dates from 2026-12-31.";
  assert.equal(normalizeMandate({ statement }).statement, statement);
  assert.equal(normalizeMandate({ statement }).stated, true);
});

// ---------------------------------------------------------------------------
// Provenance: representative and pending default to the STRICTER reading
// ---------------------------------------------------------------------------

test("a parameter is representative and pending unless the payload says it is not", () => {
  const vague = normalizeParameter({ code: "x" });
  assert.equal(vague.representative, true);
  assert.equal(vague.pendingConfirmation, true);
  const stated = normalizeParameter({
    code: "x",
    representative: false,
    pendingConfirmation: false,
  });
  assert.equal(stated.representative, false);
  assert.equal(stated.pendingConfirmation, false);
});

test("a parameter with no label falls back to its code, never to nothing", () => {
  assert.equal(normalizeParameter({ code: "irrbb_sf_x" }).label, "irrbb_sf_x");
});

test("the engine's own provenance sentence is carried verbatim", () => {
  const statement = "Representative only — not a published supervisory value.";
  assert.equal(normalizeParameter({ statement }).statement, statement);
});

// ---------------------------------------------------------------------------
// Assumption tallies: how MANY, never flattened and never zeroed
// ---------------------------------------------------------------------------

test("an unreported assumption tally is null, not zero", () => {
  const assumption = normalizeAssumption({ marker: "default_profile" });
  assert.equal(assumption.count, null);
  assert.notEqual(assumption.count, 0);
});

test("an assumption tally is carried as the number of applications", () => {
  const assumption = normalizeAssumption({
    marker: "default_profile",
    label: "Default cash-flow profile",
    count: 40,
  });
  assert.equal(assumption.count, 40);
  assert.equal(assumption.label, "Default cash-flow profile");
});

test("each assumption keeps its OWN tally — the list is never collapsed", () => {
  const quality = normalizeDataQuality({
    assumptions: [
      { marker: "a", count: 40 },
      { marker: "b", count: 1 },
    ],
  });
  assert.equal(quality.assumptions.length, 2);
  assert.deepEqual(
    quality.assumptions.map((row) => row.count),
    [40, 1],
  );
});

test("an exclusion carries both its tally and the value excluded", () => {
  const quality = normalizeDataQuality({
    exclusions: [{ marker: "no_leg_terms", count: 3, amountReporting: "912.5" }],
  });
  assert.equal(quality.exclusions[0].count, 3);
  assert.equal(quality.exclusions[0].amountReporting, "912.5");
});

// ---------------------------------------------------------------------------
// The outlier verdict needs BOTH sides
// ---------------------------------------------------------------------------

test("the outlier test is not assessable when either side is missing", () => {
  assert.equal(normalizeStandardisedFramework({}).outlierAssessable, false);
  assert.equal(
    normalizeStandardisedFramework({ pctTier1: "18.4" }).outlierAssessable,
    false,
  );
  assert.equal(
    normalizeStandardisedFramework({ outlierThresholdPct: "15" })
      .outlierAssessable,
    false,
  );
  assert.equal(
    normalizeStandardisedFramework({
      pctTier1: "18.4",
      outlierThresholdPct: "15",
    }).outlierAssessable,
    true,
  );
});

test("only a literal true flags an outlier", () => {
  for (const value of [undefined, null, "true", 1]) {
    assert.equal(normalizeStandardisedFramework({ outlier: value }).outlier, false);
  }
  assert.equal(
    normalizeStandardisedFramework({ outlier: true }).outlier,
    true,
  );
});

test("a currency is material only when the server says so", () => {
  assert.equal(normalizeCurrencyScope({ currency: "USD" }).material, false);
  assert.equal(
    normalizeCurrencyScope({ currency: "USD", material: true }).material,
    true,
  );
});

test("a scenario is in the outlier set only when the server says so", () => {
  assert.equal(normalizeScenario({ code: "x" }).inOutlierSet, false);
  assert.equal(normalizeScenario({ code: "x" }).mandatory, false);
});

// ---------------------------------------------------------------------------
// A refusal keeps its ONE name, and reaches the screen
// ---------------------------------------------------------------------------

test("a run's refusal code is carried unmapped", () => {
  const run = normalizeRunSummary({
    id: "r1",
    status: "failed",
    error: { code: "irrbb_sf_options_unsupported", message: "refused" },
  });
  assert.equal(run.errorCode, "irrbb_sf_options_unsupported");
  assert.equal(run.errorMessage, "refused");
});

test("a succeeded run carries no refusal", () => {
  const run = normalizeRunSummary({ id: "r1", status: "succeeded", error: null });
  assert.equal(run.errorCode, null);
  assert.equal(run.errorMessage, null);
});

test("the attempt history reads the page field the contract declares", () => {
  // `RegulatoryRunListRead.runs`. Reading the wrong key fails silently — an
  // empty page is a valid answer — so the screen would report "nobody has run
  // it" over a refusal. This pins the name in both directions.
  assert.equal(
    normalizeAttempts({ runs: [{ id: "r1", status: "succeeded" }] }).attempted,
    true,
  );
  assert.equal(
    normalizeAttempts({ items: [{ id: "r1", status: "succeeded" }] }).attempted,
    false,
  );
});

test("the attempt history finds the newest refusal, and says it was tried", () => {
  const attempts = normalizeAttempts({
    runs: [
      { id: "r2", status: "failed", error: { code: "irrbb_sf_options_unsupported" } },
      { id: "r1", status: "succeeded" },
    ],
  });
  assert.equal(attempts.attempted, true);
  assert.equal(attempts.latest?.id, "r2");
  assert.equal(attempts.refusal?.id, "r2");
  assert.equal(attempts.refusal?.errorCode, "irrbb_sf_options_unsupported");
});

test("no attempts means no refusal and nothing tried — not a silent pass", () => {
  const attempts = normalizeAttempts({});
  assert.equal(attempts.attempted, false);
  assert.equal(attempts.latest, null);
  assert.equal(attempts.refusal, null);
});

test("a failed run with no code is not mistaken for a typed refusal", () => {
  const attempts = normalizeAttempts({ runs: [{ id: "r1", status: "failed" }] });
  assert.equal(attempts.refusal, null);
  assert.equal(attempts.latest?.status, "failed");
});

// ---------------------------------------------------------------------------
// The dedicated attempts route (GAP-4 item 2)
// ---------------------------------------------------------------------------
//
// `GET /banks/{id}/irr/standardised-framework/attempts` answers "was it tried,
// and what happened" directly, instead of the screen inferring it from the
// generic run registry — which is where the `runs`/`items` defect above lived.
// The adapter accepts BOTH shapes so the transport can be swapped in one line
// once the generated client carries the operation.

test("the dedicated route's own shape is read, and its sentence carried", () => {
  const attempts = normalizeAttempts({
    attempted: true,
    hasResult: false,
    statement:
      "The standardised framework could not be measured for this date because " +
      "the banking book holds interest-rate options.",
    latest: {
      runId: "r2",
      status: "failed",
      statusLabel: "Refused to measure",
      errorCode: "irrbb_sf_options_unsupported",
      refusalStatement: "The standardised framework could not be measured.",
    },
    refusal: {
      runId: "r2",
      status: "failed",
      errorCode: "irrbb_sf_options_unsupported",
      refusalStatement: "The standardised framework could not be measured.",
    },
    attempts: [
      {
        runId: "r2",
        status: "failed",
        statusLabel: "Refused to measure",
        errorCode: "irrbb_sf_options_unsupported",
        refusalStatement: "The standardised framework could not be measured.",
      },
    ],
  });
  assert.equal(attempts.attempted, true);
  assert.equal(attempts.hasResult, false);
  assert.equal(attempts.latest?.id, "r2");
  assert.equal(attempts.latest?.statusLabel, "Refused to measure");
  assert.equal(attempts.refusal?.errorCode, "irrbb_sf_options_unsupported");
  // The SERVER's sentence, so the card can print it instead of its own mirror.
  assert.equal(
    attempts.refusal?.statement,
    "The standardised framework could not be measured.",
  );
  assert.ok(attempts.statement?.startsWith("The standardised framework could not"));
});

test("an untried date on the dedicated route is an answer, not a pass", () => {
  const attempts = normalizeAttempts({
    attempted: false,
    hasResult: false,
    latest: null,
    refusal: null,
    attempts: [],
    statement: "The standardised framework has not been run for this date.",
  });
  assert.equal(attempts.attempted, false);
  assert.equal(attempts.latest, null);
  assert.equal(attempts.refusal, null);
  assert.equal(attempts.hasResult, false);
  assert.equal(
    attempts.statement,
    "The standardised framework has not been run for this date.",
  );
});

test("a payload that denies an attempt while carrying one still says tried", () => {
  // Fail-closed: silence and contradiction both resolve towards "it was run".
  const attempts = normalizeAttempts({
    attempted: false,
    attempts: [{ runId: "r1", status: "succeeded" }],
  });
  assert.equal(attempts.attempted, true);
  assert.equal(attempts.latest?.id, "r1");
});

test("the registry shape still carries no server sentence and no result flag", () => {
  const attempts = normalizeAttempts({
    runs: [{ id: "r1", status: "succeeded" }],
  });
  assert.equal(attempts.statement, null);
  assert.equal(attempts.hasResult, false);
  assert.equal(attempts.latest?.statement, null);
});

// ---------------------------------------------------------------------------
// The option and floor statements are reported as absent, never skipped
// ---------------------------------------------------------------------------

test("an absent option statement is null, so the screen can say it is missing", () => {
  const view = normalizeStandardisedFramework({});
  assert.equal(view.automaticOptionStatement, null);
  assert.equal(view.postShockFloorStatement, null);
});

test("statements that are not strings are dropped rather than rendered", () => {
  const view = normalizeStandardisedFramework({
    statements: ["A real sentence.", null, 7, ""],
  });
  assert.deepEqual(view.statements, ["A real sentence."]);
});

if (failures > 0) {
  console.error(`${failures} IRRBB standardised framework normalisation test(s) failed`);
  process.exit(1);
}
console.log(
  "IRRBB standardised framework payload normalisation: all checks passed",
);
