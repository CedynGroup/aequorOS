/**
 * Fail-closed rendering for the ICAAP risk & capital screens.
 *
 * Every P2 component routes its figures and its status copy through
 * `labels.ts`, so this is where the two rules that matter can be pinned
 * without a DOM harness (the dashboard has none):
 *
 * 1. ABSENCE IS STATED, NEVER ZEROED. A null Pillar 2 amount reads "Not
 *    modelled" and a null ratio reads "Not assessed". A fabricated 0 is
 *    indistinguishable on screen from a measured zero, plots as a real data
 *    point, and compares below every floor — which is precisely how a breached
 *    capital ratio once rendered as compliant.
 * 2. NOT EVALUATED IS NOT GREEN. `rag: "none"` and an unassessed materiality
 *    verdict must not carry a success tone. A green chip is read as a
 *    compliance affirmation of something nobody measured.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  NOT_ASSESSED,
  NOT_MODELLED,
  NO_REGULATORY_FLOOR,
  basisLabel,
  consistencyCopy,
  directionLabel,
  fmtAmount,
  fmtInUnit,
  fmtPercent,
  fmtScore,
  componentMethodSummary,
  itemStatusCopy,
  notComputableCopy,
  orderingSentence,
  ragCopy,
  reviewStatusCopy,
  tierLabel,
  trendLabel,
  verdictLabel,
  verdictTone,
  ADDON_NEVER_PUBLIC,
  addonStatusCopy,
  appliesToBasisLabel,
  driverHint,
  driverKindLabel,
  fmtCount,
  missingParameterSentence,
  revisionKindLabel,
  triggerFindingCopy,
  triggerMetricLabel,
  triggerStatusCopy,
  unitKindLabel,
} from "./labels";

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

/** Every tone that reads on screen as "this is fine". */
const AFFIRMING = new Set(["compliant", "success"]);

// ---------------------------------------------------------------------------
// 1. Absence is stated, never zeroed
// ---------------------------------------------------------------------------

test("an absent amount is Not modelled, never 0", () => {
  for (const absent of [null, undefined, ""]) {
    const rendered = fmtAmount(absent);
    assert.equal(rendered, NOT_MODELLED);
    assert.ok(!/\d/.test(rendered), `an absent amount printed a digit: ${rendered}`);
  }
});

test("an absent ratio is Not assessed, never 0%", () => {
  for (const absent of [null, undefined, ""]) {
    const rendered = fmtPercent(absent);
    assert.equal(rendered, NOT_ASSESSED);
    assert.ok(!rendered.includes("%"));
  }
});

test("a value that will not parse is absence, not zero", () => {
  assert.equal(fmtAmount("not-a-number"), NOT_MODELLED);
  assert.equal(fmtPercent("n/a"), NOT_ASSESSED);
  assert.equal(fmtInUnit("—", "pct"), NOT_ASSESSED);
});

test("a genuine zero is still rendered as a measured zero", () => {
  // The mirror image of the rule: suppressing a real 0 would be its own defect.
  assert.ok(fmtAmount("0").includes("0"));
  assert.equal(fmtPercent("0"), "0.00%");
  assert.equal(fmtScore(0), "0");
});

test("an absent score is stated, and 0 is not used as a stand-in", () => {
  assert.equal(fmtScore(null), NOT_ASSESSED);
  assert.equal(fmtScore(undefined), NOT_ASSESSED);
  assert.notEqual(fmtScore(null), "0");
});

test("the caller may name the absence, but never defaults it to a number", () => {
  assert.equal(fmtAmount(null, NOT_ASSESSED), NOT_ASSESSED);
  assert.equal(fmtPercent(null, "Not available"), "Not available");
});

// ---------------------------------------------------------------------------
// 2. Not evaluated is not green
// ---------------------------------------------------------------------------

test('rag "none" renders as an explicit absence with a neutral tone', () => {
  const none = ragCopy("none");
  assert.equal(none.label, "Not evaluated");
  assert.ok(
    !AFFIRMING.has(none.tone),
    `"not evaluated" must never be affirming, got ${none.tone}`,
  );
});

test("a missing rag is treated exactly like rag none", () => {
  for (const missing of [null, undefined]) {
    const copy = ragCopy(missing);
    assert.equal(copy.label, "Not evaluated");
    assert.ok(!AFFIRMING.has(copy.tone));
  }
});

test("only a measured within-appetite reading is affirming", () => {
  assert.ok(AFFIRMING.has(ragCopy("green").tone));
  assert.ok(!AFFIRMING.has(ragCopy("amber").tone));
  assert.ok(!AFFIRMING.has(ragCopy("red").tone));
});

test("an unassessed materiality verdict is never green", () => {
  for (const missing of [null, undefined]) {
    assert.equal(verdictLabel(missing), NOT_ASSESSED);
    assert.ok(
      !AFFIRMING.has(verdictTone(missing)),
      "an unassessed risk must not look like a cleared one",
    );
  }
  // "not material" IS a decision, and may read as settled.
  assert.ok(AFFIRMING.has(verdictTone("not_material")));
  assert.ok(!AFFIRMING.has(verdictTone("material")));
});

test("a comparison that cannot be made never reads as agreement", () => {
  const notComparable = consistencyCopy("not_comparable");
  assert.ok(!AFFIRMING.has(notComparable.tone));
  assert.notEqual(notComparable.label, consistencyCopy("consistent").label);
  assert.ok(!AFFIRMING.has(consistencyCopy("inconsistent").tone));
});

test("an unknown trend is stated, not shown as stable", () => {
  const unknown = trendLabel(undefined);
  assert.notEqual(unknown.label, trendLabel("stable").label);
  assert.ok(!AFFIRMING.has(unknown.tone));
});

test("an item that cannot be computed never reads as a settled result", () => {
  assert.ok(!AFFIRMING.has(itemStatusCopy("not_computed").tone));
  assert.ok(!AFFIRMING.has(itemStatusCopy("incomplete").tone));
  assert.ok(!AFFIRMING.has(itemStatusCopy("not_computable").tone));
  // An interim method HAS produced a number, but it must not read as settled.
  assert.ok(!AFFIRMING.has(itemStatusCopy("interim_non_sf").tone));
  assert.ok(AFFIRMING.has(itemStatusCopy("computed").tone));
});

test("a risk with no Pillar 2 component says so, rather than implying coverage", () => {
  assert.equal(componentMethodSummary([]), NOT_ASSESSED);
  assert.equal(
    componentMethodSummary([{ methodLabel: "Benchmark bands" }, { method: "fx_p2" }]),
    "Benchmark bands, fx_p2",
  );
});

test("a draft independent review does not read as finalised", () => {
  assert.ok(!AFFIRMING.has(reviewStatusCopy("draft").tone));
  assert.ok(AFFIRMING.has(reviewStatusCopy("finalised").tone));
});

// ---------------------------------------------------------------------------
// D-036: an ungoverned regulatory floor has its own sentence
// ---------------------------------------------------------------------------

test("the ungoverned-floor sentence says not assessed, and names no number", () => {
  assert.match(NO_REGULATORY_FLOOR, /not assessed against a regulatory floor/);
  assert.ok(!/\d/.test(NO_REGULATORY_FLOOR));
});

// ---------------------------------------------------------------------------
// No raw enum reaches a reader (#203)
// ---------------------------------------------------------------------------

test("every mapped enum becomes a sentence, never its token", () => {
  const rendered = [
    basisLabel("pct_total_rwa"),
    basisLabel("pct_pillar1_credit_capital"),
    tierLabel("cet1"),
    tierLabel("at1"),
    itemStatusCopy("interim_non_sf").label,
    itemStatusCopy("not_capitalised").label,
    consistencyCopy("not_comparable").label,
    directionLabel("higher_is_safer"),
    orderingSentence("lower_is_safer"),
    ragCopy("none").label,
  ];
  for (const text of rendered) {
    assert.ok(
      !text.includes("_"),
      `a raw enum token reached the screen: ${text}`,
    );
  }
});

test("an unmapped enum degrades to readable text rather than printing a token", () => {
  const rendered = basisLabel("some_future_basis");
  assert.equal(rendered, "Some future basis");
  assert.ok(!rendered.includes("_"));
});

test("an absent basis or tier is stated, not blank", () => {
  assert.ok(basisLabel(null).trim().length > 0);
  assert.ok(tierLabel(undefined).trim().length > 0);
  assert.notEqual(basisLabel(null), "");
});

test("the consistency verdict for two absent figures is its own answer", () => {
  const bothAbsent = consistencyCopy("both_absent");
  assert.ok(!AFFIRMING.has(bothAbsent.tone));
  assert.notEqual(bothAbsent.label, consistencyCopy("consistent").label);
});

test("a not-computable reason becomes an instruction, not a code", () => {
  const copy = notComputableCopy("not_computable");
  assert.ok(!copy.includes("not_computable"));
  assert.ok(copy.length > 0);
  // An unknown code falls back to whatever the server sent, which is a sentence.
  assert.equal(notComputableCopy(null), "");
});

test("a server-written state reaches the reader unedited", () => {
  // The granularity adjustment writes its whole sentence server-side, because
  // the generic template's advice — "link the missing figures" — is wrong for
  // it: nothing is unlinked when a book has too few effective names. This is
  // the seam that carries it, and shortening or re-templating it here would
  // put the dashboard back in the business of explaining a method (GAP-4).
  const written =
    "The granularity adjustment does not engage on this book. This book has " +
    "5.000000 effective names against a floor of 50.000000. The floor is a " +
    "REPRESENTATIVE AequorOS calibration, pending confirmation with the " +
    "supervisor.";
  assert.equal(notComputableCopy(written), written);
});

// ---------------------------------------------------------------------------
// 3. The surfaces added after the first P2 pass
// ---------------------------------------------------------------------------

test("a trigger that could not be evaluated is never shown as clear", () => {
  const unknown = triggerStatusCopy("something_new");
  assert.ok(!AFFIRMING.has(unknown.tone));
  assert.notEqual(unknown.label, triggerStatusCopy("clear").label);
  // The absent case is the same answer, not a blank cell.
  assert.equal(triggerStatusCopy(null).label, unknown.label);
  assert.equal(triggerStatusCopy(undefined).label, unknown.label);
  // And the four real statuses are all distinguishable from one another.
  const labels = [
    "clear",
    "early_warning",
    "action",
    "regulatory_breach",
  ].map((status) => triggerStatusCopy(status).label);
  assert.equal(new Set(labels).size, labels.length);
});

test("only a cleared trigger reads as fine", () => {
  assert.ok(AFFIRMING.has(triggerStatusCopy("clear").tone));
  for (const status of ["early_warning", "action", "regulatory_breach"]) {
    assert.ok(!AFFIRMING.has(triggerStatusCopy(status).tone), status);
  }
});

test("a trigger metric is named, never printed as its code", () => {
  assert.equal(triggerMetricLabel("cet1", "cet1_ratio"), "Common Equity Tier 1 ratio");
  // A plan's own free-text metric is shown readably rather than as a token.
  const unknown = triggerMetricLabel(null, "some_internal_ratio");
  assert.ok(!unknown.includes("_"));
  assert.ok(unknown.length > 0);
});

test("every trigger finding is a sentence, not a code", () => {
  for (const code of [
    "trigger_metric_unknown",
    "ordering_inconsistent",
    "action_weaker_than_floor",
    "early_warning_weaker_than_floor",
  ]) {
    const copy = triggerFindingCopy(code);
    assert.ok(!copy.includes(code), code);
    assert.ok(copy.length > 0, code);
  }
  // D-024: a finding states an ordering, never a level.
  for (const code of ["ordering_inconsistent", "action_weaker_than_floor"]) {
    assert.ok(!/\d/.test(triggerFindingCopy(code)), code);
  }
});

test("a recorded add-on is not shown as being in force", () => {
  const draft = addonStatusCopy("draft");
  assert.ok(!AFFIRMING.has(draft.tone));
  assert.notEqual(draft.label, addonStatusCopy("active").label);
  // An unrecognised status reads as the draft, never as in force.
  assert.equal(addonStatusCopy("something_new").label, draft.label);
  assert.equal(addonStatusCopy(null).label, draft.label);
});

test("an add-on in force never reads as a compliance affirmation", () => {
  // It is a requirement the supervisor imposed, not a pass.
  assert.ok(!AFFIRMING.has(addonStatusCopy("active").tone));
});

test("the never-published statement carries no number and no country", () => {
  assert.ok(!/\d/.test(ADDON_NEVER_PUBLIC));
  assert.ok(ADDON_NEVER_PUBLIC.length > 0);
});

test("driver and unit kinds are named, and the two kinds of share differ", () => {
  assert.notEqual(driverKindLabel("rwa_share"), driverKindLabel("manual_pct"));
  for (const kind of ["rwa_share", "exposure_share", "manual_pct"]) {
    assert.ok(!driverKindLabel(kind).includes("_"), kind);
    assert.ok(driverHint(kind).length > 0, kind);
  }
  for (const kind of ["business_line", "legal_entity", "risk_type"]) {
    assert.ok(!unitKindLabel(kind).includes("_"), kind);
  }
  assert.ok(driverKindLabel(null).trim().length > 0);
  assert.ok(unitKindLabel(undefined).trim().length > 0);
});

test("the basis of consolidation is a sentence, never solo/consolidated", () => {
  for (const basis of ["solo", "consolidated", "both"]) {
    const label = appliesToBasisLabel(basis);
    assert.notEqual(label, basis);
    assert.ok(label.length > 0, basis);
  }
  assert.ok(appliesToBasisLabel(null).trim().length > 0);
});

test("a revision's change reads as an event, not an enum", () => {
  for (const kind of ["created", "edited", "computed", "retired"]) {
    assert.notEqual(revisionKindLabel(kind), kind);
  }
  assert.ok(revisionKindLabel(null).trim().length > 0);
  assert.ok(!revisionKindLabel("some_new_kind").includes("_"));
});

test("a missing governed value is named and nothing is put in its place", () => {
  const one = missingParameterSentence(["icaap_materiality_material_min_score"]);
  assert.ok(one.includes("icaap_materiality_material_min_score"));
  assert.ok(!/\d/.test(one.replace("icaap_materiality_material_min_score", "")));
  const many = missingParameterSentence(["a_code", "b_code"]);
  assert.ok(many.includes("a_code") && many.includes("b_code"));
});

test("an absent tally is stated, not printed as zero", () => {
  assert.notEqual(fmtCount(null), "0");
  assert.notEqual(fmtCount(undefined), "0");
  assert.equal(fmtCount(0), "0");
});

if (failures > 0) {
  console.error(`${failures} ICAAP P2 label test(s) failed`);
  process.exit(1);
}
console.log("ICAAP P2 fail-closed labels: all checks passed");
