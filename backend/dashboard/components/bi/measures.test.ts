/**
 * The calculated-measure surface's rules, proved without a browser.
 *
 * Five properties, each of which is a way this surface could lie to a bank:
 *
 * 1. THE LANGUAGE IS THE SERVER'S. Every function name, arity, period word and
 *    bound the editor offers is checked against `app/domain/bi/expr.py` and
 *    `app/schemas/bi_content.py` THEMSELVES, not against a copy. A palette that
 *    offered a function the parser does not have would be a control that cannot
 *    work; a length cap smaller than the column's would hide a legal formula.
 * 2. THE BADGE NEVER FLATTERS. The standing mapping is total over the three
 *    states the wire declares and an unrecognised value degrades to the neutral
 *    reading — never to the certified one.
 * 3. THE CONTROLS MATCH WHO MAY ACT. A proposer is never offered the decision; a
 *    certified measure is never offered for deletion, and the reason is stated.
 * 4. A REFUSAL NAMES THE FIGURE. The refusal helpers name the figures the server
 *    named, preferring its labels, and never the measure or the formula.
 * 5. A CERTIFIED FORMULA BECOMES A CATALOGUE ENTRY DERIVED FROM ITS OWN FIGURES.
 *    Allowed dimensions are the intersection, a mixed time behaviour refuses, and
 *    an uncertified measure is never offered.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
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

function repoRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 10; index += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`could not locate the repository root from ${__dirname}`);
}

const ROOT = repoRoot();
const EXPR_SOURCE = readFileSync(
  join(ROOT, "backend", "app", "domain", "bi", "expr.py"),
  "utf8",
);
const CONTENT_MODEL_SOURCE = readFileSync(
  join(ROOT, "backend", "app", "models", "bi_content.py"),
  "utf8",
);
const CONTENT_SCHEMA_SOURCE = readFileSync(
  join(ROOT, "backend", "app", "schemas", "bi_content.py"),
  "utf8",
);

function pythonInt(source: string, name: string, where: string): number {
  const match = new RegExp(`^${name}(?::\\s*Final)?\\s*=\\s*([\\d_]+)`, "m").exec(
    source,
  );
  assert.ok(match, `${name} is no longer declared in ${where}`);
  return Number(match![1].replace(/_/g, ""));
}

test("every bound the form applies is the server's own", () => {
  assert.equal(
    EXPRESSION_MAX_LENGTH,
    pythonInt(EXPR_SOURCE, "MAX_EXPRESSION_LENGTH", "expr.py"),
  );
  // And the column agrees with the parser, which is what makes the single number
  // on the form honest: a formula the parser accepts must fit the column.
  assert.equal(
    EXPRESSION_MAX_LENGTH,
    pythonInt(
      CONTENT_MODEL_SOURCE,
      "MEASURE_EXPRESSION_MAX_LENGTH",
      "models/bi_content.py",
    ),
  );
  assert.equal(
    MAX_FIGURE_REFERENCES,
    pythonInt(EXPR_SOURCE, "MAX_MEMBER_REFERENCES", "expr.py"),
  );
  assert.equal(
    MAX_LAG_PERIODS,
    pythonInt(EXPR_SOURCE, "MAX_LAG_PERIODS", "expr.py"),
  );
  assert.equal(
    MEASURE_KEY_MAX_LENGTH,
    pythonInt(CONTENT_MODEL_SOURCE, "MEASURE_KEY_MAX_LENGTH", "bi_content.py"),
  );
  assert.equal(
    MEASURE_LABEL_MAX_LENGTH,
    pythonInt(CONTENT_MODEL_SOURCE, "MEASURE_LABEL_MAX_LENGTH", "bi_content.py"),
  );
  assert.equal(
    MEASURE_REASON_MAX_LENGTH,
    pythonInt(CONTENT_MODEL_SOURCE, "MEASURE_REASON_MAX_LENGTH", "bi_content.py"),
  );
  assert.equal(
    MEASURE_DESCRIPTION_MAX_LENGTH,
    pythonInt(
      CONTENT_MODEL_SOURCE,
      "DASHBOARD_DESCRIPTION_MAX_LENGTH",
      "bi_content.py",
    ),
  );
});

test("the editor offers exactly the functions the parser implements", () => {
  const declared = /^FUNCTION_NAMES[^=]*=\s*\(([^)]*)\)/m.exec(
    EXPR_SOURCE,
  );
  assert.ok(declared, "FUNCTION_NAMES is no longer declared in expr.py");
  const names = [...declared![1].matchAll(/"([A-Z_]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    MEASURE_FUNCTIONS.map((fn) => fn.name).sort(),
    [...names].sort(),
    "the palette must offer the language's functions and no others",
  );
  // And each one's arity, because a signature that promised the wrong number of
  // inputs is a control that writes a formula the parser refuses.
  const arity = /^_FUNCTION_ARITY[^=]*=\s*MappingProxyType\(\s*\{([^}]*)\}/m.exec(
    EXPR_SOURCE,
  );
  assert.ok(arity, "_FUNCTION_ARITY is no longer declared in expr.py");
  const declaredArity = new Map(
    [...arity![1].matchAll(/"([A-Z_]+)":\s*(\d+)/g)].map((match) => [
      match[1],
      Number(match[2]),
    ]),
  );
  for (const fn of MEASURE_FUNCTIONS) {
    assert.equal(
      fn.arity,
      declaredArity.get(fn.name),
      `${fn.name} takes a different number of inputs in expr.py`,
    );
    // The signature a reader copies has to have that many commas in it.
    const inputs = fn.signature
      .replace(/^[A-Z_]+\(/, "")
      .replace(/\)$/, "")
      .split(",").length;
    assert.equal(
      inputs,
      fn.arity,
      `${fn.name}'s written signature does not show ${fn.arity} inputs`,
    );
  }
});

test("the three comparison periods are the language's three", () => {
  const declared = /^GRAIN_WORDS[^=]*=\s*MappingProxyType\(\s*\{([^}]*)\}/m.exec(
    EXPR_SOURCE,
  );
  assert.ok(declared, "GRAIN_WORDS is no longer declared in expr.py");
  const words = [...declared![1].matchAll(/"([A-Z]+)":/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    PERIOD_GRAINS.map((grain) => grain.word).sort(),
    [...words].sort(),
  );
});

test("a figure is spelled the way the parser reads one", () => {
  // `_MEMBER_REFERENCE` is `\[m:([a-z0-9_]+(?:\.[a-z0-9_]+)*)\]`.
  assert.ok(
    /_MEMBER_REFERENCE:\s*Final\s*=\s*re\.compile\(r"\\\[m:/.test(EXPR_SOURCE),
    "expr.py no longer spells a figure reference as [m:…]",
  );
  assert.equal(figureReference("loans.balance_rc"), "[m:loans.balance_rc]");
});

test("the id pattern and the two vocabularies are the server's", () => {
  const pattern = /^MEASURE_KEY_PATTERN\s*=\s*r"([^"]+)"/m.exec(
    CONTENT_SCHEMA_SOURCE,
  );
  assert.ok(pattern, "MEASURE_KEY_PATTERN is no longer declared");
  assert.equal(
    MEASURE_KEY_PATTERN.source,
    pattern![1],
    "the form's id pattern must be the server's, character for character",
  );
  for (const [constant, offered] of [
    ["MEASURE_VALUE_TYPES", MEASURE_VALUE_TYPES.map((entry) => entry.code)],
    [
      "MEASURE_FAVOURABLE_DIRECTIONS",
      MEASURE_DIRECTIONS.map((entry) => entry.code),
    ],
  ] as const) {
    const declared = new RegExp(
      `^${constant}:\\s*tuple\\[str, \\.\\.\\.\\]\\s*=\\s*\\(([\\s\\S]*?)\\)`,
      "m",
    ).exec(CONTENT_MODEL_SOURCE);
    assert.ok(declared, `${constant} is no longer declared in bi_content.py`);
    const values = [...declared![1].matchAll(/"([a-z_]+)"/g)].map(
      (match) => match[1],
    );
    assert.deepEqual(
      [...offered].sort(),
      [...values].sort(),
      `the form must offer exactly ${constant}`,
    );
  }
  // Every offered code reads as words, never as the wire value.
  for (const entry of [...MEASURE_VALUE_TYPES, ...MEASURE_DIRECTIONS]) {
    assert.notEqual(
      entry.label,
      entry.code,
      `${entry.code} must have a label a banker can read`,
    );
  }
  assert.equal(valueTypeLabel("pct"), "A percentage");
  assert.equal(directionLabel("lower_better"), "Lower is better");
  // An unmapped code degrades to itself rather than to another option's words.
  assert.equal(valueTypeLabel("not_a_type"), "not_a_type");
});

test("nothing in this client parses a formula", () => {
  // From the REPO, not from `__dirname`: this suite runs out of the compiled
  // `.test-out/` tree, where the TypeScript source does not exist.
  const source = readFileSync(
    join(ROOT, "backend", "dashboard", "components", "bi", "measures.ts"),
    "utf8",
  )
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^[ \t]*\/\/.*$/gm, "");
  for (const shape of [
    /function\s+\w*[Pp]arse\w*\s*\(/,
    /function\s+\w*[Tt]okeni[sz]e\w*\s*\(/,
    /function\s+\w*[Cc]ompile\w*\s*\(/,
    // A DSL call APPLIED TO A FIGURE would be evaluation. The signatures the
    // palette shows (`SAFE_DIV(top, bottom)`) are display strings and are checked
    // against `expr.py` above; this catches the shape that would be a second
    // implementation.
    /SAFE_DIV\s*\(\s*\[m:/,
    /\beval\s*\(/,
    /new Function\s*\(/,
  ]) {
    assert.equal(
      shape.test(source),
      false,
      `measures.ts must not evaluate or parse a formula (${shape}); the server is the only authority on the language`,
    );
  }
});

// --- the generated writer carries every field the request declares ----------
//
// `<Model>ToJSON` HAS NO SPREAD. It returns a hand-enumerated object literal of
// exactly the keys the generator knew about when it ran, so a field added to an
// existing request schema is STRIPPED IN THE BROWSER, the server applies its
// column default, and the call succeeds — a wrong value reported as success, and
// invisible to types and to every test that mocks the transport. (Seen for real
// this round on the grant composer, where a scope narrowed to two branches would
// have been stored as the whole institution.)
//
// The five measure requests are checked against `app/schemas/bi_content.py`
// itself. If this fails, do NOT add a cast or post a wider object: either the
// client is regenerated, or that one call is sent through a hand-written transport
// that reuses the generated `FromJSON` parsers.

function requestFields(className: string): string[] {
  const block = new RegExp(
    `class ${className}\\(BiClosedModel\\):([\\s\\S]*?)\\n\\nclass `,
  ).exec(CONTENT_SCHEMA_SOURCE);
  assert.ok(block, `${className} is no longer declared in schemas/bi_content.py`);
  // Four-space indentation is a field; anything deeper is a `Field(...)`
  // continuation, and a docstring line has no colon in that position.
  return [...block![1].matchAll(/^ {4}([a-z_]+):/gm)]
    .map((match) => match[1])
    .sort();
}

function writerFields(model: string): string[] {
  const source = readFileSync(
    join(
      ROOT,
      "packages",
      "risk-service-api",
      "src",
      "models",
      `${model}.ts`,
    ),
    "utf8",
  );
  const block = new RegExp(
    `export function ${model}ToJSONTyped\\([\\s\\S]*?\\n\\}`,
  ).exec(source);
  assert.ok(block, `${model}ToJSONTyped is no longer generated`);
  return [...block![0].matchAll(/^\s{4}([a-z_]+):/gm)]
    .map((match) => match[1])
    .sort();
}

test("every field a measure request declares survives the generated writer", () => {
  for (const model of [
    "BiMeasureCreateRequest",
    "BiMeasureUpdateRequest",
    "BiMeasureProposalRequest",
    "BiMeasureDecisionRequest",
    "BiMeasureValidationRequest",
  ]) {
    assert.deepEqual(
      writerFields(model),
      requestFields(model),
      `${model}ToJSON does not send every field the server declares. Its object ` +
        "literal is hand-enumerated with no spread, so a field the generator has " +
        "not seen is dropped in the browser and the server silently applies its " +
        "own default.",
    );
  }
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
  const declared = /^MEASURE_STATES:\s*tuple\[str, \.\.\.\]\s*=\s*\(([^)]*)\)/m.exec(
    CONTENT_MODEL_SOURCE,
  );
  assert.ok(declared, "MEASURE_STATES is no longer declared in bi_content.py");
  const states = [...declared![1].matchAll(/"([a-z_]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(
    [...states].sort(),
    ["bank_certified", "personal", "proposed"],
    "a state added to the server needs a standing here before it can be drawn",
  );
  assert.equal(measureStanding("personal").standing, "personal");
  assert.equal(measureStanding("proposed").standing, "personal");
  assert.equal(measureStanding("bank_certified").standing, "bank");
  // The neutral tone for anything unrecognised, never the favourable neighbour.
  for (const unknown of ["", "retired", "platform_certified", "BANK_CERTIFIED"]) {
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
    expression: "SAFE_DIV([m:deposits.interest_expense_rc], [m:loans.balance_rc])",
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
  published("loans.hhi", { aggregation: "hhi", allowedDimensions: ["branch.code"] }),
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
  assert.equal(mixed.usable === false ? mixed.reason : "", "mixed_time_behaviour");
  assert.ok(/position|movement/i.test(unusableSentence("mixed_time_behaviour")));

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
      { id: "branch.code", label: "Branch", description: "", module: "credit", sensitivity: "aggregated", valueType: "text", values: [] },
      { id: "loans.sector", label: "Sector", description: "", module: "credit", sensitivity: "aggregated", valueType: "text", values: [] },
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
    { ...EMPTY_SHAPE, measures: ["funding_cost_ratio"], dimensions: ["branch.code"] },
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
  const badKey = { ...empty, measureKey: "Funding Cost", label: "x", expression: "[m:a]" };
  assert.ok(
    draftProblems(badKey, { keyEditable: true }).some((problem) =>
      /lower-case/i.test(problem),
    ),
  );
  assert.deepEqual(draftProblems(badKey, { keyEditable: false }), []);
  for (const key of ["funding_cost_ratio", "a", "loans.my_ratio"]) {
    assert.deepEqual(
      draftProblems({ ...empty, measureKey: key, label: "x", expression: "[m:a]" }, { keyEditable: true }),
      [],
      `${key} is a legal id on the server`,
    );
  }
  for (const key of ["Funding", "_x", "1x", "x.", "x..y", "x y"]) {
    assert.equal(MEASURE_KEY_PATTERN.test(key), false, `${key} must be refused`);
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
  assert.deepEqual([...addresses.pairs], [
    { module: "credit", sensitivity: "aggregated" },
  ]);
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
