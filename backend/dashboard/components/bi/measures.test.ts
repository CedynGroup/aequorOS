import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { runBackend } from "../../unit/backend";
import type {
  BiCatalogueDimensionRead,
  BiCatalogueMeasureRead,
  BiMeasureRead,
  BiMeasureValidationRead,
} from "@aequoros/risk-service-api";
import {
  CALCULATED_AGGREGATION,
  EXPRESSION_MAX_LENGTH,
  MANY_MODULES,
  MAX_FIGURE_REFERENCES,
  MAX_LAG_PERIODS,
  MEASURE_DESCRIPTION_MAX_LENGTH,
  MEASURE_DIRECTIONS,
  MEASURE_FUNCTIONS,
  MEASURE_KEY_MAX_LENGTH,
  MEASURE_KEY_PATTERN,
  MEASURE_LABEL_MAX_LENGTH,
  MEASURE_REASON_MAX_LENGTH,
  MEASURE_VALUE_TYPES,
  PERIOD_GRAINS,
  calculatedMeasureOffer,
  directionLabel,
  draftFromMeasure,
  draftProblems,
  expressionDigest,
  expressionReady,
  figureReference,
  isCertified,
  measureControls,
  measureStanding,
  reasonProblem,
  mergeAddresses,
  questionAddresses,
  refusedFigures,
  refusedFiguresSentence,
  unusableSentence,
  validationRefusal,
  valueTypeLabel,
  type MeasureDraft,
} from "./measures";
import {
  hasCalculatedMeasure,
  incompatibleReason,
  measureIsCompatible,
  buildExploreQuery,
  EMPTY_SHAPE,
  type ExploreCatalogue,
} from "./exploreQuery";

let failures = 0;
function test(name: string, fn: () => void | Promise<void>): void {
  const settle = (error: unknown) => {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  };
  try {
    const result = fn();
    if (result instanceof Promise) {
      pending.push(result.catch(settle));
    }
  } catch (error) {
    settle(error);
  }
}
const pending: Promise<void>[] = [];

// --- 1. the language is the server's ----------------------------------------

const contract = runBackend<{
  fields: Record<
    string,
    { maxLength: number; pattern?: string; enum?: string[] }
  >;
  reasonMax: number;
  parserLimits: number[];
  functions: string[];
  periods: string[];
  states: string[];
}>(`
from app.bi.domain import expr
from app.bi.models.content import MEASURE_STATES
from app.bi.schemas.content import BiMeasureCreateRequest, BiMeasureProposalRequest
print(json.dumps({
    "fields": BiMeasureCreateRequest.model_json_schema()["properties"],
    "reasonMax": BiMeasureProposalRequest.model_json_schema()["properties"]["reason"]["maxLength"],
    "parserLimits": [expr.MAX_EXPRESSION_LENGTH, expr.MAX_MEMBER_REFERENCES, expr.MAX_LAG_PERIODS],
    "functions": list(expr.FUNCTION_NAMES),
    "periods": list(expr.GRAIN_WORDS),
    "states": list(MEASURE_STATES),
}))
`);

test("every form bound matches the emitted wire contract and parser", () => {
  const fields = contract.fields;
  assert.equal(EXPRESSION_MAX_LENGTH, fields.expression.maxLength);
  assert.deepEqual(
    [EXPRESSION_MAX_LENGTH, MAX_FIGURE_REFERENCES, MAX_LAG_PERIODS],
    contract.parserLimits,
  );
  assert.equal(MEASURE_KEY_MAX_LENGTH, fields.measure_key.maxLength);
  assert.equal(MEASURE_LABEL_MAX_LENGTH, fields.label.maxLength);
  assert.equal(MEASURE_REASON_MAX_LENGTH, contract.reasonMax);
  assert.equal(MEASURE_DESCRIPTION_MAX_LENGTH, fields.description.maxLength);
});

function parseResults(
  formulas: string[],
): { accepted: boolean; members: string[] }[] {
  return runBackend(
    `
from app.bi.domain.expr import parse, referenced_members, ExpressionError
results = []
for source in payload:
    try:
        tree = parse(source, retention_days=None)
    except ExpressionError:
        results.append({"accepted": False, "members": []})
    else:
        results.append({"accepted": True, "members": list(referenced_members(tree))})
print(json.dumps(results))
`,
    formulas,
  );
}

test("the editor offers exactly the functions and arities the parser accepts", () => {
  assert.deepEqual(
    MEASURE_FUNCTIONS.map((fn) => fn.name).sort(),
    [...contract.functions].sort(),
  );
  const args: Record<string, string[]> = {
    SAFE_DIV: ["[m:a]", "[m:b]"],
    IF: ["[m:a] > 0", "[m:b]", "[m:c]"],
    PCT_CHANGE: ["[m:a]", "MONTH"],
    LAG: ["[m:a]", "1", "MONTH"],
  };
  const formulas = MEASURE_FUNCTIONS.flatMap((fn) => {
    const operands = args[fn.name];
    assert.equal(fn.arity, operands.length);
    assert.equal(
      fn.signature.slice(fn.signature.indexOf("(") + 1, -1).split(",").length,
      fn.arity,
    );
    return [
      fn.name + "(" + operands.join(", ") + ")",
      fn.name + "(" + operands.slice(0, -1).join(", ") + ")",
      fn.name + "(" + [...operands, "1"].join(", ") + ")",
    ];
  });
  assert.deepEqual(
    parseResults(formulas).map((result) => result.accepted),
    MEASURE_FUNCTIONS.flatMap(() => [true, false, false]),
  );
});

test("every offered period parses, and an unknown period refuses", () => {
  assert.deepEqual(
    PERIOD_GRAINS.map((grain) => grain.word).sort(),
    [...contract.periods].sort(),
  );
  const formulas = PERIOD_GRAINS.map(
    (grain) => "PCT_CHANGE([m:a], " + grain.word + ")",
  );
  assert.deepEqual(
    parseResults([...formulas, "PCT_CHANGE([m:a], FORTNIGHT)"]).map(
      (result) => result.accepted,
    ),
    [...formulas.map(() => true), false],
  );
});

test("a figure reference reaches the server parser as the same member", () => {
  const reference = figureReference("loans.balance_rc");
  assert.deepEqual(parseResults([reference]), [
    { accepted: true, members: ["loans.balance_rc"] },
  ]);
});

test("the server enforces the editor's formula, reference and lag bounds", () => {
  const formulas = [
    "1" + " ".repeat(EXPRESSION_MAX_LENGTH - 1),
    "1" + " ".repeat(EXPRESSION_MAX_LENGTH),
    Array.from(
      { length: MAX_FIGURE_REFERENCES },
      (_, n) => "[m:a" + n + "]",
    ).join(" + "),
    Array.from(
      { length: MAX_FIGURE_REFERENCES + 1 },
      (_, n) => "[m:a" + n + "]",
    ).join(" + "),
    "LAG([m:a], " + MAX_LAG_PERIODS + ", MONTH)",
    "LAG([m:a], " + (MAX_LAG_PERIODS + 1) + ", MONTH)",
  ];
  assert.deepEqual(
    parseResults(formulas).map((result) => result.accepted),
    [true, false, true, false, true, false],
  );
});

test("the key control and vocabularies agree with backend validation", () => {
  const keys = [
    "funding_cost_ratio",
    "a.b1",
    "a",
    "1bad",
    "UPPER",
    "bad-key",
    "bad key",
    "a".repeat(MEASURE_KEY_MAX_LENGTH + 1),
  ];
  const accepted = runBackend<boolean[]>(
    `
from app.bi.schemas.content import BiMeasureCreateRequest
results = []
for key in payload:
    try:
        BiMeasureCreateRequest.model_validate({"measure_key": key, "label": "Figure",
            "expression": "[m:a]", "value_type": "amount"})
    except ValueError:
        results.append(False)
    else:
        results.append(True)
print(json.dumps(results))
`,
    keys,
  );
  assert.deepEqual(
    keys.map(
      (key) =>
        key.length <= MEASURE_KEY_MAX_LENGTH && MEASURE_KEY_PATTERN.test(key),
    ),
    accepted,
  );
  assert.deepEqual(
    MEASURE_VALUE_TYPES.map((entry) => entry.code).sort(),
    [...contract.fields.value_type.enum!].sort(),
  );
  assert.deepEqual(
    MEASURE_DIRECTIONS.map((entry) => entry.code).sort(),
    [...contract.fields.favourable_direction.enum!].sort(),
  );
  for (const entry of [...MEASURE_VALUE_TYPES, ...MEASURE_DIRECTIONS]) {
    assert.notEqual(entry.label, entry.code);
  }
  assert.equal(valueTypeLabel("pct"), "A percentage");
  assert.equal(directionLabel("lower_better"), "Lower is better");
  assert.equal(valueTypeLabel("not_a_type"), "not_a_type");
});

test("draft checks defer formula validity to the server", () => {
  const expression = "SAFE_DIV(";
  const draft: MeasureDraft = {
    measureKey: "draft_ratio",
    label: "A draft ratio",
    description: "",
    expression,
    valueType: "fraction",
    favourableDirection: "neutral",
  };
  assert.deepEqual(draftProblems(draft, { keyEditable: true }), []);
  assert.deepEqual(parseResults([expression]), [
    { accepted: false, members: [] },
  ]);
  assert.equal(expressionReady(draft, null), false);
});

// --- the digest a decision is taken against ---------------------------------

test("the digest is SHA-256 of the formula's own text", async () => {
  for (const formula of [
    "SAFE_DIV([m:loans.non_performing_rc], [m:loans.balance_rc])",
    "PCT_CHANGE([m:deposits.balance_rc], MONTH)",
    // Non-ASCII, because the server hashes UTF-8 bytes and a client that hashed
    // UTF-16 code units would disagree on exactly this.
    "[m:loans.balance_rc] * 1 /* café */",
    "",
  ]) {
    assert.equal(
      await expressionDigest(formula),
      createHash("sha256").update(Buffer.from(formula, "utf8")).digest("hex"),
      "the digest must equal the server's sha256 of the same source text",
    );
  }
  const digest = await expressionDigest("[m:a]");
  assert.match(
    digest,
    /^[0-9a-f]{64}$/,
    "the digest must match the request field's own pattern",
  );
});

// --- 2. the badge never flatters --------------------------------------------

test("the standing is total over the wire's states and degrades neutrally", () => {
  const states = contract.states;
  assert.deepEqual(
    [...states].sort(),
    ["bank_certified", "personal", "proposed"],
    "a state added to the server needs a standing here before it can be drawn",
  );
  assert.equal(measureStanding("personal").standing, "personal");
  assert.equal(measureStanding("proposed").standing, "personal");
  assert.equal(measureStanding("bank_certified").standing, "bank");
  // The neutral tone for anything unrecognised, never the favourable neighbour.
  for (const unknown of [
    "",
    "retired",
    "platform_certified",
    "BANK_CERTIFIED",
  ]) {
    const standing = measureStanding(unknown);
    assert.equal(
      standing.standing,
      "personal",
      `${unknown} must not be drawn as the institution's own figure`,
    );
    assert.equal(standing.tone, "slate");
  }
  // A proposal is NOT certified: it is one person's formula awaiting a second.
  assert.notEqual(measureStanding("proposed").tone, "success");
  assert.equal(measureStanding("bank_certified").tone, "success");
});

// --- a measure to reason about ----------------------------------------------

function measure(overrides: Partial<BiMeasureRead> = {}): BiMeasureRead {
  return {
    id: "3f6b1d1e-4c5a-4f2b-9e7d-0a1b2c3d4e5f",
    measureKey: "funding_cost_ratio",
    label: "Cost of funds against the loan book",
    description: "What funding costs, against what it funds.",
    expression:
      "SAFE_DIV([m:deposits.interest_expense_rc], [m:loans.balance_rc])",
    referencedMembers: ["deposits.interest_expense_rc", "loans.balance_rc"],
    referencedMemberLabels: ["Interest expense", "Gross loans"],
    valueType: "fraction",
    favourableDirection: "lower_better",
    state: "personal",
    badge: "personal",
    ownerUserId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ownerDisplayName: "A Analyst",
    ownedByCaller: true,
    createdAt: new Date("2026-09-01T00:00:00Z"),
    updatedAt: new Date("2026-09-01T00:00:00Z"),
    awaitingCallerDecision: false,
    ...overrides,
  };
}

// --- 3. the controls match who may act --------------------------------------

test("a draft is its author's to change, send for review and delete", () => {
  const controls = measureControls(measure());
  assert.equal(controls.canEdit, true);
  assert.equal(controls.canPropose, true);
  assert.equal(controls.canDelete, true);
  assert.equal(controls.deleteWithheld, null);
  assert.equal(controls.editConsequence, null);
  assert.equal(controls.canDecide, false);
  assert.equal(controls.proposerNotice, null);
});

test("the proposer is not offered the decision, and is told why", () => {
  const proposed = measure({
    state: "proposed",
    proposedByUserId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    proposedAt: "2026-09-02T00:00:00Z",
    proposalReason: "The board pack needs it.",
    // The server's own flag: false for the proposer, by construction.
    awaitingCallerDecision: false,
  });
  const controls = measureControls(proposed);
  assert.equal(controls.canDecide, false);
  assert.ok(
    controls.proposerNotice &&
      /cannot certify/i.test(controls.proposerNotice) &&
      /someone/i.test(controls.proposerNotice),
    "the proposer must be told that a second person has to certify it",
  );
  // And withdrawing by deleting is not the route: the reviewer is reading it.
  assert.equal(controls.canDelete, false);
  assert.ok(
    controls.deleteWithheld &&
      /waiting for someone to review/i.test(controls.deleteWithheld),
  );
});

test("only the server's own flag opens the decision", () => {
  const forChecker = measure({
    state: "proposed",
    ownedByCaller: false,
    ownerDisplayName: "A Analyst",
    proposedByUserId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    awaitingCallerDecision: true,
  });
  const controls = measureControls(forChecker);
  assert.equal(controls.canDecide, true);
  assert.equal(controls.proposerNotice, null);
  // A checker is not the owner, so nothing about the formula is theirs to change.
  assert.equal(controls.canEdit, false);
  assert.equal(controls.canDelete, false);
  assert.equal(controls.deleteWithheld, null);

  // A proposed measure this identity may READ but not decide — the flag is false
  // and nothing else may substitute for it.
  const readOnly = measureControls({
    ...forChecker,
    awaitingCallerDecision: false,
  });
  assert.equal(readOnly.canDecide, false);
});

test("a certified measure is never offered for deletion, and the reason is stated", () => {
  const certified = measure({
    state: "bank_certified",
    badge: "bank_certified",
    proposedByUserId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    proposedAt: "2026-09-02T00:00:00Z",
    proposalReason: "The board pack needs it.",
    approvedByUserId: "eeeeeeee-2222-4eee-8eee-eeeeeeeeeee2",
    approvedByDisplayName: "An Approver",
    approvedAt: "2026-09-03T00:00:00Z",
    approvalReason: "Checked against the ledger.",
    approvedExpression:
      "SAFE_DIV([m:deposits.interest_expense_rc], [m:loans.balance_rc])",
  });
  const controls = measureControls(certified);
  assert.equal(
    controls.canDelete,
    false,
    "two people certified this formula; one may not destroy it — the approved text is the record of what they agreed",
  );
  assert.ok(controls.deleteWithheld, "the withheld control must say why");
  const why = controls.deleteWithheld!;
  assert.ok(
    /certified/i.test(why) && /two people/i.test(why),
    "the reason must say that certifying took two people",
  );
  assert.ok(
    /retir/i.test(why),
    "the reason must name the governed act that replaces deletion, so it stays true when retirement lands",
  );
  assert.equal(
    /delete it anyway|remove it anyway/i.test(why),
    false,
    "the withheld control must not offer a way round itself",
  );
  // Editing it is offered — it is still its author's formula — but never as free.
  assert.equal(controls.canEdit, true);
  assert.ok(
    controls.editConsequence &&
      /takes its certification away/i.test(controls.editConsequence),
    "an edit to a certified formula must be presented as what it is",
  );
  assert.equal(isCertified(certified), true);
  assert.equal(isCertified(measure()), false);
});

test("a colleague's measure offers no control at all", () => {
  const theirs = measureControls(
    measure({ state: "bank_certified", ownedByCaller: false }),
  );
  assert.equal(theirs.canEdit, false);
  assert.equal(theirs.canDelete, false);
  assert.equal(
    theirs.deleteWithheld,
    null,
    "a non-owner is not shown a reason for a control they would never have had",
  );
});

// --- 4. a refusal names the figure ------------------------------------------

test("a refused save names the figures, preferring the server's labels", () => {
  const refusal = refusedFigures({
    error_code: "bi_authorization_denied",
    message: "Your access does not cover every field this view needs.",
    denied_members: ["loans.balance_rc"],
    denied_member_labels: ["Gross loans"],
    reason: "binding_evaluation_failed",
  });
  assert.deepEqual(refusal.figures, ["Gross loans"]);
  assert.equal(refusal.labelled, true);
  const sentence = refusedFiguresSentence(refusal)!;
  assert.ok(/Gross loans/.test(sentence));
  assert.ok(
    /Org Owner/.test(sentence),
    "the reader has to be told who can grant it",
  );
  // THE FIGURE IS THE SUBJECT, NEVER THE FORMULA OR THE MEASURE.
  assert.equal(
    /this measure|this formula is not allowed|you may not use this measure/i.test(
      sentence,
    ),
    false,
    "a formula is authorized as the figures its text names; naming the formula would be both wrong and unactionable",
  );
  assert.equal(
    /binding_evaluation_failed|bi_authorization_denied/.test(sentence),
    false,
    "no wire code may reach the sentence",
  );
});

test("ids are used only when the server sent no labels", () => {
  const refusal = refusedFigures({ denied_members: ["loans.balance_rc"] });
  assert.deepEqual(refusal.figures, ["loans.balance_rc"]);
  assert.equal(refusal.labelled, false);
  assert.ok(refusedFiguresSentence(refusal));
});

test("an unreadable refusal names nothing rather than guessing", () => {
  for (const details of [null, undefined, "nope", 7, [], {}]) {
    const refusal = refusedFigures(details);
    assert.deepEqual(refusal.figures, []);
    assert.equal(
      refusedFiguresSentence(refusal),
      null,
      "with nothing to name, the surface must fall back to the server's own sentence",
    );
  }
  // A malformed list contributes only its usable members, never an empty slot.
  assert.deepEqual(
    refusedFigures({ denied_member_labels: ["Gross loans", "", null, 3] })
      .figures,
    ["Gross loans"],
  );
});

test("a validation refusal names the figures the author wrote", () => {
  const read: BiMeasureValidationRead = {
    valid: false,
    message: "Your access does not cover every figure this formula uses.",
    referencedMembers: [],
    referencedMemberLabels: [],
    deniedMembers: ["loans.balance_rc"],
  };
  assert.deepEqual(validationRefusal(read).figures, ["loans.balance_rc"]);
  assert.deepEqual(validationRefusal(undefined).figures, []);
});

// --- 5. a certified formula becomes a catalogue entry --------------------

function published(
  id: string,
  overrides: Partial<BiCatalogueMeasureRead> = {},
): BiCatalogueMeasureRead {
  return {
    id,
    label: id,
    description: "",
    measureKind: "portfolio",
    aggregation: "sum",
    allowedDimensions: ["loans.sector", "branch.code"],
    timeBehaviour: "stock",
    valueType: "amount",
    favourableDirection: "neutral",
    grain: "portfolio",
    module: "credit",
    sensitivity: "aggregated",
    ...overrides,
  };
}

const CATALOGUE: readonly BiCatalogueMeasureRead[] = [
  published("deposits.interest_expense_rc", {
    module: "liq",
    allowedDimensions: ["branch.code", "deposits.product"],
  }),
  published("loans.balance_rc", {
    allowedDimensions: ["loans.sector", "branch.code"],
  }),
  published("loans.hhi", {
    aggregation: "hhi",
    allowedDimensions: ["branch.code"],
  }),
  published("loans.disbursed_rc", {
    timeBehaviour: "flow",
    allowedDimensions: ["branch.code"],
  }),
  published("cap.car_pct", {
    module: "cap",
    grain: "institution",
    allowedDimensions: [],
  }),
];

test("an uncertified formula is never offered as a figure", () => {
  for (const state of ["personal", "proposed"] as const) {
    const offer = calculatedMeasureOffer(measure({ state }), CATALOGUE);
    assert.equal(offer.usable, false);
    assert.equal(
      offer.usable === false ? offer.reason : "",
      "not_certified",
      "the compiler resolves only certified measures, so anything else would be a checkbox that guarantees a refusal",
    );
  }
  assert.ok(/certified/i.test(unusableSentence("not_certified")));
});

test("a certified formula carries its figures' shared breakdown, not their union", () => {
  const offer = calculatedMeasureOffer(
    measure({ state: "bank_certified", badge: "bank_certified" }),
    CATALOGUE,
  );
  assert.equal(offer.usable, true);
  if (!offer.usable) return;
  assert.deepEqual(
    offer.entry.allowedDimensions,
    ["branch.code"],
    "the compiler requires every dimension to be allowed by EVERY resolved figure, so the offer is the intersection",
  );
  assert.equal(offer.entry.id, "funding_cost_ratio");
  assert.equal(offer.entry.timeBehaviour, "stock");
  assert.equal(offer.entry.aggregation, CALCULATED_AGGREGATION);
  assert.equal(offer.entry.valueType, "fraction");
  assert.equal(offer.entry.favourableDirection, "lower_better");
  assert.equal(
    offer.entry.certified,
    false,
    "`certified` means copied from the sealed filing tier; a bank's own formula is never that",
  );
  assert.equal(
    offer.entry.module,
    MANY_MODULES,
    "a formula spanning two modules has no single module, and naming one would understate what it reads",
  );
});

test("one module, named; and an institution figure makes the whole formula one", () => {
  const single = calculatedMeasureOffer(
    measure({
      state: "bank_certified",
      referencedMembers: ["loans.balance_rc", "loans.hhi"],
    }),
    CATALOGUE,
  );
  assert.equal(single.usable, true);
  if (single.usable) assert.equal(single.entry.module, "credit");

  const institutional = calculatedMeasureOffer(
    measure({
      state: "bank_certified",
      referencedMembers: ["loans.balance_rc", "cap.car_pct"],
    }),
    CATALOGUE,
  );
  assert.equal(institutional.usable, true);
  if (institutional.usable) {
    assert.equal(institutional.entry.grain, "institution");
    assert.deepEqual(
      institutional.entry.allowedDimensions,
      [],
      "an institution figure cannot be broken down, so nor can a formula that reads one",
    );
  }
});

test("a formula that cannot be answered is named, not offered", () => {
  const mixed = calculatedMeasureOffer(
    measure({
      state: "bank_certified",
      referencedMembers: ["loans.balance_rc", "loans.disbursed_rc"],
    }),
    CATALOGUE,
  );
  assert.equal(mixed.usable, false);
  assert.equal(
    mixed.usable === false ? mixed.reason : "",
    "mixed_time_behaviour",
  );
  assert.ok(
    /position|movement/i.test(unusableSentence("mixed_time_behaviour")),
  );

  const unknown = calculatedMeasureOffer(
    measure({ state: "bank_certified", referencedMembers: ["not.a.figure"] }),
    CATALOGUE,
  );
  assert.equal(unknown.usable, false);
  assert.equal(
    unknown.usable === false ? unknown.reason : "",
    "figure_not_in_catalogue",
  );

  const empty = calculatedMeasureOffer(
    measure({ state: "bank_certified", referencedMembers: [] }),
    CATALOGUE,
  );
  assert.equal(empty.usable, false);
});

// --- Explore treats a bank formula as the figures it names -------------------

function exploreCatalogue(): ExploreCatalogue {
  const offer = calculatedMeasureOffer(
    measure({ state: "bank_certified", badge: "bank_certified" }),
    CATALOGUE,
  );
  assert.equal(offer.usable, true);
  return {
    measures: [...CATALOGUE, ...(offer.usable ? [offer.entry] : [])],
    dimensions: [
      {
        id: "branch.code",
        label: "Branch",
        description: "",
        module: "credit",
        sensitivity: "aggregated",
        valueType: "text",
        values: [],
      },
      {
        id: "loans.sector",
        label: "Sector",
        description: "",
        module: "credit",
        sensitivity: "aggregated",
        valueType: "text",
        values: [],
      },
    ],
  };
}

test("a bank formula and a concentration figure are not offered together", () => {
  const catalogue = exploreCatalogue();
  assert.equal(hasCalculatedMeasure(catalogue, ["funding_cost_ratio"]), true);
  assert.equal(hasCalculatedMeasure(catalogue, ["loans.balance_rc"]), false);

  assert.equal(
    measureIsCompatible(catalogue, ["loans.hhi"], "funding_cost_ratio"),
    false,
    "the compiler refuses a concentration figure and a calculated measure in one result",
  );
  assert.equal(
    measureIsCompatible(catalogue, ["funding_cost_ratio"], "loans.hhi"),
    false,
    "and it refuses them in the other order too",
  );
  // The stated reason is the one that applies, not the time-behaviour sentence.
  const reason = incompatibleReason(
    catalogue,
    ["loans.hhi"],
    "funding_cost_ratio",
  )!;
  assert.ok(/concentration/i.test(reason));
  assert.equal(
    /Reported as a/.test(reason),
    false,
    "a disabled option must not state a reason that does not apply — it sends a reader to remove the wrong measure",
  );
  // A flow figure still gets the time-behaviour reason.
  const behaviour = incompatibleReason(
    catalogue,
    ["funding_cost_ratio"],
    "loans.disbursed_rc",
  )!;
  assert.ok(/movement over the period/.test(behaviour));
  assert.equal(
    incompatibleReason(catalogue, [], "funding_cost_ratio"),
    null,
    "a measure that can be chosen has no reason to state",
  );
});

test("a question naming a bank formula is built, and the illegal pair refuses", () => {
  const catalogue = exploreCatalogue();
  const plan = buildExploreQuery(
    {
      ...EMPTY_SHAPE,
      measures: ["funding_cost_ratio"],
      dimensions: ["branch.code"],
    },
    catalogue,
    { asOf: "2026-06-30" },
  );
  assert.ok(plan.query, "a certified formula must be askable");
  assert.deepEqual(plan.query!.measures, ["funding_cost_ratio"]);
  assert.deepEqual(plan.query!.dimensions, ["branch.code"]);

  const refused = buildExploreQuery(
    { ...EMPTY_SHAPE, measures: ["funding_cost_ratio", "loans.hhi"] },
    catalogue,
    { asOf: "2026-06-30" },
  );
  assert.equal(refused.query, null);
  assert.ok(
    refused.problems.some(
      (problem) =>
        problem.id === "calculated-with-concentration" && problem.blocking,
    ),
    "the pair the compiler refuses must be refused before the request",
  );
});

// --- the composer's own rules ------------------------------------------------

test("a draft is checked for everything except whether the formula is valid", () => {
  const empty: MeasureDraft = {
    measureKey: "",
    label: "",
    description: "",
    expression: "",
    valueType: "amount",
    favourableDirection: "neutral",
  };
  const problems = draftProblems(empty, { keyEditable: true });
  assert.equal(problems.length, 3, problems.join(" | "));
  assert.ok(problems.some((problem) => /id/i.test(problem)));
  assert.ok(problems.some((problem) => /name/i.test(problem)));
  assert.ok(problems.some((problem) => /formula/i.test(problem)));
  for (const problem of problems) {
    assert.equal(
      /valid|syntax|parse/i.test(problem),
      false,
      "this client has no opinion on whether a formula is valid",
    );
  }

  // The id is checked against the server's own shape, and only when it is editable.
  const badKey = {
    ...empty,
    measureKey: "Funding Cost",
    label: "x",
    expression: "[m:a]",
  };
  assert.ok(
    draftProblems(badKey, { keyEditable: true }).some((problem) =>
      /lower-case/i.test(problem),
    ),
  );
  assert.deepEqual(draftProblems(badKey, { keyEditable: false }), []);
  for (const key of ["funding_cost_ratio", "a", "loans.my_ratio"]) {
    assert.deepEqual(
      draftProblems(
        { ...empty, measureKey: key, label: "x", expression: "[m:a]" },
        { keyEditable: true },
      ),
      [],
      `${key} is a legal id on the server`,
    );
  }
  for (const key of ["Funding", "_x", "1x", "x.", "x..y", "x y"]) {
    assert.equal(
      MEASURE_KEY_PATTERN.test(key),
      false,
      `${key} must be refused`,
    );
  }

  // Every length bound is applied, so the server never has to refuse one.
  const long = {
    ...empty,
    measureKey: "a",
    label: "x".repeat(MEASURE_LABEL_MAX_LENGTH + 1),
    description: "x".repeat(MEASURE_DESCRIPTION_MAX_LENGTH + 1),
    expression: "x".repeat(EXPRESSION_MAX_LENGTH + 1),
  };
  const overlong = draftProblems(long, { keyEditable: true });
  assert.equal(overlong.length, 3, overlong.join(" | "));
});

test("the save waits for the SERVER's verdict on the text on screen", () => {
  const draft: MeasureDraft = {
    measureKey: "r",
    label: "R",
    description: "",
    expression: "[m:loans.balance_rc]",
    valueType: "amount",
    favourableDirection: "neutral",
  };
  const valid: BiMeasureValidationRead = {
    valid: true,
    message: "This formula is valid.",
    referencedMembers: ["loans.balance_rc"],
    referencedMemberLabels: ["Gross loans"],
    deniedMembers: [],
  };
  assert.equal(expressionReady(draft, null), false, "unchecked is not ready");
  assert.equal(
    expressionReady(draft, { expression: draft.expression, read: valid }),
    true,
  );
  assert.equal(
    expressionReady(draft, { expression: "[m:other]", read: valid }),
    false,
    "a verdict about different text is stale, and stale is not ready",
  );
  assert.equal(
    expressionReady(draft, {
      expression: draft.expression,
      read: { ...valid, valid: false },
    }),
    false,
  );
});

test("a reason is required, and bounded", () => {
  assert.ok(reasonProblem("", "why."));
  assert.ok(reasonProblem("   ", "why."));
  assert.equal(reasonProblem("Checked against the ledger.", "why."), null);
  assert.ok(
    reasonProblem("x".repeat(MEASURE_REASON_MAX_LENGTH + 1), "why.")?.includes(
      String(MEASURE_REASON_MAX_LENGTH),
    ),
  );
});

test("editing restates the measure, id included, and never invents a field", () => {
  const source = measure({
    state: "bank_certified",
    approvedExpression: "SAFE_DIV([m:a], [m:b])",
  });
  assert.deepEqual(draftFromMeasure(source), {
    measureKey: source.measureKey,
    label: source.label,
    description: source.description,
    expression: source.expression,
    valueType: source.valueType,
    favourableDirection: source.favourableDirection,
  });
});

// --- 9. which authority a question reads, for the coverage sentence ---------
//
// The BI query payload discloses no data scope, so the browser's only honest
// source for "does this answer cover the whole book" is the per-capability scope
// on `/auth/me`, addressed by (module, sensitivity). These tests pin the
// resolution of a question's ids to those addresses — and above all the
// calculated case, where a formula spanning two modules addresses NEITHER of them
// under its own entry.

const DIMENSIONS: readonly BiCatalogueDimensionRead[] = [
  {
    id: "branch.code",
    label: "Branch",
    description: "",
    module: "credit",
    sensitivity: "aggregated",
    valueType: "text",
  },
  {
    id: "deposits.product",
    label: "Deposit product",
    description: "",
    module: "liq",
    sensitivity: "confidential",
    valueType: "text",
  },
];

const NO_FORMULAS: readonly BiMeasureRead[] = [];

test("a published figure addresses its own module and sensitivity", () => {
  const addresses = questionAddresses(
    { measures: ["loans.balance_rc"], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  assert.equal(addresses.complete, true);
  assert.deepEqual(
    [...addresses.pairs],
    [{ module: "credit", sensitivity: "aggregated" }],
  );
});

test("a formula is addressed as the figures its text names, never as itself", () => {
  // Two modules, so `calculatedMeasureOffer` gives the entry MANY_MODULES — which
  // addresses nothing. The addresses have to be its referenced figures'.
  const formula = measure({ state: "bank_certified" });
  const offer = calculatedMeasureOffer(formula, CATALOGUE);
  assert.equal(offer.usable, true);
  assert.equal(
    offer.usable === true ? offer.entry.module : "",
    MANY_MODULES,
    "the entry itself carries no addressable module — this is the trap",
  );
  const addresses = questionAddresses(
    { measures: [formula.measureKey], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    [formula],
  );
  assert.equal(addresses.complete, true);
  assert.deepEqual(
    [...addresses.pairs].sort((left, right) =>
      left.module.localeCompare(right.module),
    ),
    [
      { module: "credit", sensitivity: "aggregated" },
      { module: "liq", sensitivity: "aggregated" },
    ],
    "both of the formula's figures, because the server evaluates a sentence per pair",
  );
  assert.ok(
    !addresses.pairs.some((pair) => pair.module === MANY_MODULES),
    "the marker must never be offered as an address",
  );
});

test("a dimension and a filtered field are reads too, and are addressed", () => {
  const addresses = questionAddresses(
    {
      measures: ["loans.balance_rc"],
      dimensions: ["branch.code"],
      filterMembers: ["deposits.product"],
    },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  assert.equal(addresses.complete, true);
  assert.ok(
    addresses.pairs.some(
      (pair) => pair.module === "liq" && pair.sensitivity === "confidential",
    ),
    "a filter can itself disclose, so the field it names is part of the read",
  );
});

test("an id that resolves to nothing makes the address list incomplete", () => {
  for (const asked of [
    { measures: ["not.a.measure"], dimensions: [], filterMembers: [] },
    { measures: [], dimensions: ["not.a.field"], filterMembers: [] },
    { measures: [], dimensions: [], filterMembers: ["not.a.field"] },
  ]) {
    assert.equal(
      questionAddresses(asked, CATALOGUE, DIMENSIONS, NO_FORMULAS).complete,
      false,
      "describing a question by the coverage of the figures it could resolve would be the fail-open",
    );
  }
});

test("a formula naming a figure outside this reader's catalogue is incomplete", () => {
  const formula = measure({
    referencedMembers: ["loans.balance_rc", "not.in.catalogue"],
  });
  const addresses = questionAddresses(
    { measures: [formula.measureKey], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    [formula],
  );
  assert.equal(addresses.complete, false);
});

test("addresses are de-duplicated, so two figures of one module are one pair", () => {
  const addresses = questionAddresses(
    {
      measures: ["loans.balance_rc", "loans.hhi"],
      dimensions: ["branch.code"],
      filterMembers: ["branch.code"],
    },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  assert.equal(addresses.complete, true);
  assert.equal(addresses.pairs.length, 1);
});

test("an empty question addresses nothing, and says so completely", () => {
  const addresses = questionAddresses(
    { measures: [], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  assert.deepEqual([...addresses.pairs], []);
  assert.equal(
    addresses.complete,
    true,
    "no figure on screen is nothing unresolved — the coverage module decides what that means",
  );
});

test("a document's addresses are the union of its views', and incompleteness spreads", () => {
  const resolved = questionAddresses(
    { measures: ["loans.balance_rc"], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  const other = questionAddresses(
    { measures: ["cap.car_pct"], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  const broken = questionAddresses(
    { measures: ["not.a.measure"], dimensions: [], filterMembers: [] },
    CATALOGUE,
    DIMENSIONS,
    NO_FORMULAS,
  );
  const merged = mergeAddresses([resolved, other]);
  assert.equal(merged.complete, true);
  assert.equal(merged.pairs.length, 2);
  assert.equal(mergeAddresses([resolved, resolved]).pairs.length, 1);
  assert.equal(
    mergeAddresses([resolved, broken]).complete,
    false,
    "one unresolved view must not be described by the coverage of the others",
  );
  assert.deepEqual(mergeAddresses([]), { pairs: [], complete: true });
});

void Promise.all(pending).then(() => {
  if (failures > 0) {
    console.error(`measures.test.ts: ${failures} failing`);
    process.exit(1);
  }
  console.log(
    "measures.test.ts: the language is the server's, the badge never flatters, " +
      "a certified formula is not one person's to delete, and a refusal names the figure.",
  );
});
