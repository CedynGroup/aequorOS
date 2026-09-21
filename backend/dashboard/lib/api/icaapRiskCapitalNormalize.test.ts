/**
 * The payload normalisers, which exist because the risks tab crashed.
 *
 * `TypeError: Cannot read properties of undefined (reading 'length')` at
 * `register.parameters.length`, on a real backend that does not serve the P2
 * routes yet. The declared contract was true of the design and false of the
 * wire, and nothing checked the difference.
 *
 * Every case below is a payload the server might really send: an empty body, a
 * body with `null` where a list was declared, a body that is not an object at
 * all. None of them may throw, and none of them may produce a figure nobody
 * measured.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  ICAAP_STRESS_BLOCK_TYPES,
  normalizeAllocation,
  normalizeAppetite,
  normalizeAuditReviews,
  normalizeCapitalTriggers,
  normalizeChallenges,
  normalizeParameterRegister,
  normalizePillar2Register,
  normalizePillar2Revisions,
  normalizeReconciliation,
  normalizeRiskRegister,
  normalizeStressEvidence,
  normalizeSupervisoryAddons,
  normalizeTable5,
} from "./icaapRiskCapitalNormalize";

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
  { error: { code: "not_found", message: "Not Found" } },
];

const NORMALIZERS: [string, (value: unknown) => unknown][] = [
  ["risk register", normalizeRiskRegister],
  ["appetite", normalizeAppetite],
  ["pillar 2 register", normalizePillar2Register],
  ["table 5", normalizeTable5],
  ["reconciliation", normalizeReconciliation],
  ["audit reviews", normalizeAuditReviews],
  ["challenges", normalizeChallenges],
  ["capital allocation", normalizeAllocation],
  ["capital triggers", normalizeCapitalTriggers],
  ["pillar 2 revisions", normalizePillar2Revisions],
  ["parameter register", normalizeParameterRegister],
  ["supervisory add-ons", normalizeSupervisoryAddons],
  ["stress evidence", normalizeStressEvidence],
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

/** Walk a normalised result and assert no declared list came back undefined. */
function assertNoUndefinedLists(value: unknown, path: string): void {
  if (Array.isArray(value)) {
    value.forEach((entry, index) =>
      assertNoUndefinedLists(entry, `${path}[${index}]`),
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
    assertNoUndefinedLists(inner, `${path}.${key}`);
  }
}

test("an empty body yields a fully-populated shape with no undefined anywhere", () => {
  for (const [name, normalize] of NORMALIZERS) {
    assertNoUndefinedLists(normalize({}), name);
  }
});

test("THE CRASH: register.parameters is always an array", () => {
  // The exact expression that threw: `register.parameters.length > 0`.
  for (const body of HOSTILE) {
    const register = normalizeRiskRegister(body);
    assert.ok(Array.isArray(register.parameters));
    assert.equal(register.parameters.length, 0);
    assert.ok(Array.isArray(register.risks));
    assert.ok(Array.isArray(register.matrix.cells));
    assert.ok(Array.isArray(register.matrix.likelihoodLevels));
    assert.ok(Array.isArray(register.matrix.parameters));
  }
});

test("a null where a list was declared becomes an empty list", () => {
  const register = normalizeRiskRegister({
    risks: null,
    parameters: null,
    matrix: { cells: null, bands: null, parameters: null },
  });
  assert.deepEqual(register.risks, []);
  assert.deepEqual(register.parameters, []);
  assert.deepEqual(register.matrix.cells, []);
});

test("a list containing junk keeps only the rows that are objects", () => {
  const register = normalizeRiskRegister({
    risks: [null, "credit", 7, { riskKey: "credit_risk" }],
  });
  assert.equal(register.risks.length, 1);
  assert.equal(register.risks[0].riskKey, "credit_risk");
  // And the row that DID arrive is complete, so a component can read it.
  assert.ok(Array.isArray(register.risks[0].components));
});

// ---------------------------------------------------------------------------
// Absence stays absence — nothing is invented
// ---------------------------------------------------------------------------

test("a missing amount stays null, so it renders as Not modelled and not 0", () => {
  const register = normalizePillar2Register({ items: [{ itemId: "a" }] });
  assert.equal(register.items[0].baselineAmount, null);
  assert.equal(register.items[0].stressedAmount, null);

  const reconciliation = normalizeReconciliation({});
  assert.equal(reconciliation.requirement.totals.totalRegulatoryRequirement, null);
  assert.equal(reconciliation.resources.totals.recognisedRegulatoryCapital, null);
});

test("a missing materiality threshold stays null, never 0", () => {
  const { matrix } = normalizeRiskRegister({});
  assert.equal(matrix.materialMinScore, null);
  assert.equal(matrix.materialMinImpact, null);
});

test("a figure keeps its full precision as the string the backend sent", () => {
  const register = normalizePillar2Register({
    items: [{ itemId: "a", baselineAmount: "18.720000000000001" }],
  });
  assert.equal(register.items[0].baselineAmount, "18.720000000000001");
});

test("an unknown RAG is none, never green", () => {
  for (const rag of [undefined, null, "", "ok", "GREEN", 1]) {
    const appetite = normalizeAppetite({
      metrics: [{ metricId: "m", evaluation: { rag } }],
    });
    assert.equal(appetite.metrics[0].evaluation?.rag, "none", String(rag));
  }
  const green = normalizeAppetite({
    metrics: [{ metricId: "m", evaluation: { rag: "green" } }],
  });
  assert.equal(green.metrics[0].evaluation?.rag, "green");
});

test("no evaluation at all is null, not a fabricated one", () => {
  const appetite = normalizeAppetite({ metrics: [{ metricId: "m" }] });
  assert.equal(appetite.metrics[0].evaluation, null);
});

test("D-036: a metric with no regulatory reference is flagged as missing", () => {
  const appetite = normalizeAppetite({ metrics: [{ metricId: "m" }] });
  assert.equal(appetite.metrics[0].regulatoryReference, null);
  assert.equal(appetite.metrics[0].referenceMissing, true);
});

test("an unknown consistency verdict is 'cannot be compared', never 'agrees'", () => {
  for (const status of [undefined, null, "ok", "agrees"]) {
    const register = normalizePillar2Register({
      consistency: [{ comparisonKey: "k", status }],
    });
    assert.equal(register.consistency[0].status, "not_comparable", String(status));
  }
});

test("coverage is the SERVER's percentage, and absent when it sent none", () => {
  // The screen must never divide the two totals itself.
  const silent = normalizeReconciliation({ resources: { totals: {} } });
  assert.equal(silent.resources.totals.internalCapitalCoveragePct, null);

  const real = normalizeReconciliation({
    resources: { totals: { internalCapitalCoveragePct: "120.5" } },
  });
  assert.equal(real.resources.totals.internalCapitalCoveragePct, "120.5");
  // A mismatch flag the server did not send is "unknown", not "matches".
  assert.equal(silent.resources.totals.matchesRegulatoryTotal, null);
});

test("a capital component is NOT recognised unless the server says it is", () => {
  const lines = normalizeReconciliation({
    resources: { lines: [{ id: "l1", lineKey: "cet1" }] },
  }).resources.lines;
  assert.equal(lines[0].regulatoryEligible, false);
  assert.equal(lines[0].internalAmount, null);
});

test("an action flag the server did not send is false — no control is offered", () => {
  const register = normalizePillar2Register({ items: [{ itemId: "a" }] });
  assert.equal(register.items[0].editable, false);
  assert.equal(register.items[0].approvable, false);
  assert.equal(register.items[0].approvalCurrent, false);
  assert.equal(register.items[0].methodStatus, "not_computed");
  assert.equal(register.diversificationAllowed, false);
});

test("a stale-threshold warning is not raised unless the server raised it", () => {
  assert.equal(
    normalizeRiskRegister({ risks: [{ riskKey: "a" }] }).risks[0].thresholdsCurrent,
    true,
  );
  assert.equal(
    normalizeRiskRegister({ risks: [{ riskKey: "a", thresholdsCurrent: false }] })
      .risks[0].thresholdsCurrent,
    false,
  );
});

test("an unanswered challenge is open, so a governance gap cannot be hidden", () => {
  const withoutResponses = normalizeChallenges({
    challenges: [{ challengeId: "c" }],
  });
  assert.equal(withoutResponses.challenges[0].open, true);
  assert.deepEqual(withoutResponses.challenges[0].responses, []);

  const answered = normalizeChallenges({
    challenges: [{ challengeId: "c", responses: [{ responseId: "r" }] }],
  });
  assert.equal(answered.challenges[0].open, false);
});

test("a label falls back to its key rather than printing undefined", () => {
  const table5 = normalizeTable5({
    columns: [{ key: "baseline" }],
    rows: [{ key: "credit" }],
  });
  assert.equal(table5.columns[0].label, "baseline");
  assert.equal(table5.rows[0].label, "credit");
  assert.deepEqual(table5.rows[0].cells, {});
});

test("Table 5 cells are indexed by column key, from the wire's cell LIST", () => {
  const table5 = normalizeTable5({
    rows: [
      {
        key: "credit",
        cells: [
          { column: "base_case", value: "10" },
          { column: "stress_case", value: null },
        ],
      },
    ],
  });
  assert.deepEqual(table5.rows[0].cells, { base_case: "10", stress_case: null });
  // Absent means the grid cannot be shown at all; it is never "available".
  assert.equal(normalizeTable5({}).available, false);
});

test("a floor is higher-is-safer and a ceiling is lower-is-safer", () => {
  const floor = normalizeAppetite({ metrics: [{ id: "m", direction: "floor" }] });
  assert.equal(floor.metrics[0].direction, "higher_is_safer");
  const ceiling = normalizeAppetite({ metrics: [{ id: "m", direction: "ceiling" }] });
  assert.equal(ceiling.metrics[0].direction, "lower_is_safer");
  // An unknown direction is treated as a floor: the conservative reading for a
  // capital or liquidity measure, which is what these mostly are.
  const unknown = normalizeAppetite({ metrics: [{ id: "m" }] });
  assert.equal(unknown.metrics[0].direction, "higher_is_safer");
});

test("a summary that did not arrive counts zero, not undefined", () => {
  const summary = normalizeAppetite({}).summary;
  assert.equal(summary.metricCount, 0);
  assert.equal(summary.breachCount, 0);
});

test("the register's provenance is read from the MATRIX, where the wire puts it", () => {
  // Reading it off the top level is what threw during the walkthrough.
  const register = normalizeRiskRegister({
    matrix: { parameters: [{ paramCode: "icaap_materiality_material_min_score" }] },
  });
  assert.equal(register.parameters.length, 1);
  assert.equal(register.parameters[0].paramCode, "icaap_materiality_material_min_score");
});

test("a cell carries its band's own label, never an invented one", () => {
  const register = normalizeRiskRegister({
    matrix: {
      bands: [{ key: "high", label: "High", minScore: 13, maxScore: 25 }],
      cells: [{ likelihood: 4, impact: 4, score: 16, ratingKey: "high", material: true }],
    },
  });
  assert.equal(register.matrix.cells[0].ratingLabel, "High");
  // An unknown key degrades to the key rather than to "undefined".
  const unknown = normalizeRiskRegister({
    matrix: { cells: [{ ratingKey: "severe" }] },
  });
  assert.equal(unknown.matrix.cells[0].ratingLabel, "severe");
});


// ---------------------------------------------------------------------------
// Capital allocation
// ---------------------------------------------------------------------------

test("an allocation is not available until the server says it is", () => {
  // Defaulting to available would put an empty driver grid in front of a
  // preparer and invite them to allocate a requirement nobody has computed.
  for (const body of HOSTILE) {
    assert.equal(normalizeAllocation(body).available, false);
  }
  assert.equal(normalizeAllocation({ available: true }).available, true);
});

test("an allocated amount is never invented", () => {
  const allocation = normalizeAllocation({
    available: true,
    cells: [
      { unitKey: "retail", riskLineKey: "credit", driverValue: "40" },
      { unitKey: "retail", riskLineKey: "market", allocatedAmount: "10" },
    ],
    lines: [{ lineKey: "credit" }],
    units: [{ unitKey: "retail" }],
  });
  assert.equal(allocation.cells[0].allocatedAmount, null);
  assert.equal(allocation.cells[1].allocatedAmount, "10");
  assert.equal(allocation.totalAllocated, null);
  assert.equal(allocation.lines[0].internalAmount, null);
  assert.equal(allocation.units[0].totalAllocated, null);
});

test("an unrecognised driver claims the least about where it came from", () => {
  // "Stated percentage" says a person decided the share. Reading an unknown
  // driver as a share of risk-weighted assets would claim the platform derived
  // it from the book, which is evidence rather than judgement.
  const allocation = normalizeAllocation({
    cells: [{ unitKey: "u", riskLineKey: "l", driverKind: "something_new" }],
  });
  assert.equal(allocation.cells[0].driverKind, "manual_pct");
  assert.equal(
    normalizeAllocation({
      cells: [{ unitKey: "u", riskLineKey: "l", driverKind: "rwa_share" }],
    }).cells[0].driverKind,
    "rwa_share",
  );
});

// ---------------------------------------------------------------------------
// Capital-plan triggers
// ---------------------------------------------------------------------------

test("an unrecognised trigger status is never read as clear", () => {
  const evaluation = normalizeCapitalTriggers({
    results: [
      { metricCode: "car", currentStatus: "something_new" },
      { metricCode: "cet1" },
    ],
  });
  assert.equal(evaluation.results[0].currentStatus, "not_evaluable");
  assert.equal(evaluation.results[1].currentStatus, "not_evaluable");
});

test("a projected point's status fails closed too", () => {
  const evaluation = normalizeCapitalTriggers({
    results: [
      {
        metricCode: "car",
        points: [{ scenarioCode: "base", year: 1, status: "who_knows" }],
      },
    ],
  });
  assert.equal(evaluation.results[0].points[0].status, "not_evaluable");
  assert.equal(evaluation.results[0].points[0].value, null);
  assert.equal(evaluation.results[0].points[0].floorPct, null);
});

test("the server's own unavailable reason is carried, not replaced", () => {
  const evaluation = normalizeCapitalTriggers({
    unavailable: {
      error_code: "capital_plan_not_linked",
      message: "Link this ICAAP's capital plan.",
    },
  });
  assert.equal(evaluation.available, false);
  assert.equal(evaluation.unavailableReason, "Link this ICAAP's capital plan.");
  // With no `unavailable` object the evaluation ran, and there is no reason.
  const ran = normalizeCapitalTriggers({ results: [] });
  assert.equal(ran.available, true);
  assert.equal(ran.unavailableReason, null);
});

test("a crossing year that is not a number is dropped, not zeroed", () => {
  const evaluation = normalizeCapitalTriggers({
    results: [
      {
        metricCode: "car",
        firstCrossing: { base: { action: 3, early_warning: null } },
      },
    ],
  });
  assert.deepEqual(evaluation.results[0].firstCrossing.base, { action: 3 });
});

test("a trigger floor direction maps the wire's floor/ceiling", () => {
  const floor = normalizeCapitalTriggers({
    results: [{ metricCode: "car", direction: "ceiling" }],
  });
  assert.equal(floor.results[0].direction, "lower_is_safer");
});

// ---------------------------------------------------------------------------
// Pillar 2 item revisions
// ---------------------------------------------------------------------------

test("an unmapped revision kind reads as a recorded change", () => {
  const history = normalizePillar2Revisions({
    itemId: "item",
    revisions: [
      { id: "a", revisionNo: 2, changeKind: "computed" },
      { id: "b", revisionNo: 1, changeKind: "something_new" },
    ],
  });
  assert.equal(history.revisions[0].changeKind, "computed");
  assert.equal(history.revisions[1].changeKind, "recorded");
});

test("a revision with no working recorded says so rather than showing none", () => {
  const history = normalizePillar2Revisions({
    revisions: [{ id: "a", computation: null, snapshot: { basis: "absolute" } }],
  });
  assert.equal(history.revisions[0].computation, null);
  assert.deepEqual(history.revisions[0].snapshot, [
    { label: "basis", value: "absolute" },
  ]);
});

// ---------------------------------------------------------------------------
// Governed parameter register
// ---------------------------------------------------------------------------

test("a code with no governed value is named, never filled in", () => {
  const register = normalizeParameterRegister({
    asOf: "2026-12-31",
    parameters: [{ paramCode: "car_min", value: null, resolved: false }],
    missing: ["lcr_min", 7],
  });
  assert.equal(register.parameters[0].value, null);
  assert.equal(register.parameters[0].resolved, false);
  // Non-string entries are dropped rather than stringified into a fake code.
  assert.deepEqual(register.missing, ["lcr_min"]);
});

test("an unconfirmed calibration never reads as confirmed", () => {
  const register = normalizeParameterRegister({
    parameters: [{ paramCode: "a" }, { paramCode: "b", confirmationStatus: "confirmed" }],
  });
  assert.equal(register.parameters[0].confirmationStatus, "pending");
  assert.equal(register.parameters[1].confirmationStatus, "confirmed");
  assert.deepEqual(register.parameters[0].roles, []);
});

// ---------------------------------------------------------------------------
// Supervisory add-ons
// ---------------------------------------------------------------------------

test("an add-on is never in force unless the server says so", () => {
  const list = normalizeSupervisoryAddons({
    addons: [
      { id: "a" },
      { id: "b", status: "something_new" },
      { id: "c", status: "active" },
    ],
  });
  assert.equal(list.addons[0].status, "draft");
  assert.equal(list.addons[1].status, "draft");
  assert.equal(list.addons[2].status, "active");
});

test("an add-on is never publishable by default", () => {
  for (const body of HOSTILE) {
    assert.equal(normalizeSupervisoryAddons(body).neverPublic, true);
  }
  assert.equal(
    normalizeSupervisoryAddons({ neverPublic: false }).neverPublic,
    false,
  );
});

test("an add-on's converted amount stays absent when it cannot be converted", () => {
  const list = normalizeSupervisoryAddons({
    addons: [{ id: "a", basisValue: "1.5", basis: "pct_total_rwa" }],
  });
  assert.equal(list.addons[0].amountAtAsOf, null);
  assert.equal(list.addons[0].basisValue, "1.5");
  assert.equal(list.addons[0].basis, "pct_total_rwa");
  assert.equal(list.totalAmountAtAsOf, null);
});

// ---------------------------------------------------------------------------
// Stress and capital-plan evidence
// ---------------------------------------------------------------------------

/** A complete Appendix II, of the shape the stress module's tables consume. */
function appendix(): Record<string, unknown> {
  return {
    table1_summary: {
      current: {},
      pre_adverse: [],
      post_adverse: [],
      impact_of_adverse: [],
    },
    table2_capital: [],
    table3_profit_and_loss: [],
    table4_financial_position: [],
    table5_rwa: { rows: [] },
    table6_risk_drivers: { rows: [] },
  };
}

test("only the stress and capital-plan blocks are read, in reading order", () => {
  const evidence = normalizeStressEvidence({
    blocks: [
      { id: "1", blockType: "capital_plan" },
      { id: "2", blockType: "concentration" },
      { id: "3", blockType: "appendix_ii" },
    ],
  });
  assert.deepEqual(
    evidence.blocks.map((block) => block.blockType),
    ["appendix_ii", "capital_plan"],
  );
  assert.ok(ICAAP_STRESS_BLOCK_TYPES.includes("appendix_ii"));
});

test("an incomplete Appendix II is not half-drawn", () => {
  // The stress module's tables spread `pre_adverse` and index
  // `impact_of_adverse`; a payload without them would throw mid-render, so it
  // is reported as absent instead.
  const partial = normalizeStressEvidence({
    blocks: [
      {
        id: "1",
        blockType: "appendix_ii",
        currentBinding: {
          payload: { raw: { raw_appendix_ii: { table1_summary: {} } } },
        },
      },
    ],
  });
  assert.equal(partial.appendix, null);

  const complete = normalizeStressEvidence({
    blocks: [
      {
        id: "1",
        blockType: "appendix_ii",
        currentBinding: {
          sourceRef: { run_id: "run-1" },
          payload: { raw: { raw_appendix_ii: appendix() } },
        },
      },
    ],
  });
  assert.notEqual(complete.appendix, null);
  assert.equal(complete.runId, "run-1");
});

test("a block with no binding is not bound, and says nothing about a run", () => {
  const evidence = normalizeStressEvidence({
    blocks: [{ id: "1", blockType: "capital_plan", status: "unbound" }],
  });
  assert.equal(evidence.blocks[0].bound, false);
  assert.equal(evidence.blocks[0].runId, null);
  assert.equal(evidence.blocks[0].sourceAsOf, null);
  assert.deepEqual(evidence.blocks[0].facts, []);
  assert.equal(evidence.runId, null);
  assert.equal(evidence.appendix, null);
});

test("an unrecognised block status reads as not linked", () => {
  const evidence = normalizeStressEvidence({
    blocks: [{ id: "1", blockType: "appendix_ii", status: "something_new" }],
  });
  assert.equal(evidence.blocks[0].status, "unbound");
});

if (failures > 0) {
  console.error(`${failures} ICAAP P2 normalisation test(s) failed`);
  process.exit(1);
}
console.log("ICAAP P2 payload normalisation: all checks passed");
