import assert from "node:assert/strict";
import { runBackend } from "../../unit/backend";
import {
  ASK_POLL_MS,
  ASK_QUESTION_MAX_CHARS,
  ASK_REFUSAL_CODES,
  AskShapeError,
  askClauseHeading,
  askEmptyAnswerSentence,
  askFigureText,
  askIsPending,
  askPollInterval,
  askRefusal,
  askRunBody,
  parseAskProposal,
  type AskClauseKind,
} from "./ask";

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

// --- the fixture ------------------------------------------------------------
//
// A proposal as the wire spells it: snake_case, ISO dates, and a query carrying
// one field the generated `BiQuery` model does not know about. The unknown field
// is the whole point — it stands for every field added to `BiQuery` since the
// client was last generated, and it must survive the round trip untouched.

const FUTURE_QUERY_FIELD = "segment_scope";

const proposedQuery = {
  measures: ["loans.gross_rc"],
  dimensions: ["loans.branch"],
  filters: [{ member: "loans.branch", op: "in", values: ["B001", "B002"] }],
  time: { as_of: "2026-08-31" },
  sort: [{ member: "loans.gross_rc", direction: "desc" }],
  top_n: { dimension: "loans.branch", n: 10, other: true },
  [FUTURE_QUERY_FIELD]: { kind: "branch", values: ["B001", "B002"] },
};

function body(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    question_id: "6f2a1c90-1d3e-4a5b-8c6d-9e0f1a2b3c4d",
    state: "proposed",
    message: "Check this is the question you meant, then run it.",
    question: "gross loans by branch, largest first",
    as_of: "2026-08-31",
    catalogue_version: "bi-catalogue-v7",
    figures_offered: 40,
    query: proposedQuery,
    reading: {
      sentence:
        "Gross loans, grouped by Branch, as at 2026-08-31, where Branch is one of Adum and Asokwa",
      clauses: [
        { kind: "figure", text: "Gross loans" },
        { kind: "grouping", text: "grouped by Branch" },
        { kind: "period", text: "as at 2026-08-31" },
        { kind: "filter", text: "where Branch is one of Adum and Asokwa" },
      ],
      as_of: "2026-08-31",
      range_start: null,
      range_end: null,
      compare_to: null,
      member_ids: ["loans.gross_rc", "loans.branch"],
    },
    suggestions: [],
    ...overrides,
  };
}

// --- 1. the echo-back is the same value, byte for byte -----------------------

test("the confirmed query is the same object the server sent", () => {
  const wire = body();
  const parsed = parseAskProposal(wire);
  // The strictest form of "unmodified": the same reference, so there is no step
  // between receiving and returning at which a field could be lost.
  assert.equal(parsed.proposedQuery, wire.query);
  assert.equal(askRunBody(parsed).query, wire.query);
});

test("the confirmed query serialises identically to what arrived", () => {
  // The server digests `BiQuery.model_dump_json()` of the PARSED model on both
  // sides, so the contract is value identity, and this is it — over a JSON
  // round trip, which is what `fetch` actually does to the body.
  const wire = body();
  const overTheWire = JSON.parse(JSON.stringify(wire)) as Record<
    string,
    unknown
  >;
  const parsed = parseAskProposal(overTheWire);
  assert.deepEqual(askRunBody(parsed).query, wire.query);
  assert.equal(
    JSON.stringify(askRunBody(parsed).query),
    JSON.stringify(overTheWire.query),
  );
});

test("a state with no proposal has nothing to confirm", () => {
  const refused = parseAskProposal(
    body({
      state: "refused",
      query: proposedQuery,
      reading: null,
      message: "The platform did not recognise every figure in that question.",
    }),
  );
  assert.equal(refused.proposedQuery, null);
  assert.throws(() => askRunBody(refused), AskShapeError);
});

test("a proposal with no query, or no reading, is refused rather than shown", () => {
  assert.throws(() => parseAskProposal(body({ query: null })), AskShapeError);
  assert.throws(() => parseAskProposal(body({ reading: null })), AskShapeError);
  assert.throws(
    () => parseAskProposal(body({ state: "thinking_about_it" })),
    AskShapeError,
  );
});

// --- 2. every refusal is the SERVER's sentence -------------------------------

test("each refusal code renders the platform's own message", () => {
  assert.ok(
    ASK_REFUSAL_CODES.length >= 5,
    "the refusal code list read as empty",
  );
  for (const errorCode of ASK_REFUSAL_CODES) {
    const message = `Production copy for ${errorCode}.`;
    assert.equal(askRefusal({ status: 409, errorCode, message }), message);
  }
});

test("anything that is not a server-authored refusal renders nothing here", () => {
  // `null` sends the surface to the app's ordinary failure panel. Returning a
  // sentence of our own would put words a bank analyst reads as the platform's
  // into its mouth.
  assert.equal(askRefusal(null), null);
  assert.equal(askRefusal(new Error("boom")), null);
  assert.equal(
    askRefusal({ status: null, errorCode: null, message: "network down" }),
    null,
  );
  assert.equal(
    askRefusal({ status: 500, errorCode: "some_other_thing", message: "hi" }),
    null,
  );
  // A refusal whose copy did not arrive is not rendered as a blank panel.
  assert.equal(
    askRefusal({ status: 409, errorCode: "bi_ask_unavailable", message: "  " }),
    null,
  );
});

test("a clause heading is never the wire token, and an unknown kind has none", () => {
  const kinds: AskClauseKind[] = [
    "figure",
    "grouping",
    "period",
    "comparison",
    "filter",
    "ranking",
    "order",
  ];
  for (const kind of kinds) {
    const heading = askClauseHeading(kind);
    assert.ok(heading, `${kind} must have a heading`);
    assert.notEqual(
      heading,
      kind,
      `${kind}: the heading must be production copy, not the wire token`,
    );
  }
  assert.equal(askClauseHeading(null), null);
});

test("a clause whose kind this build cannot head is still shown", () => {
  const parsed = parseAskProposal(
    body({
      reading: {
        ...(body().reading as Record<string, unknown>),
        clauses: [
          { kind: "figure", text: "Gross loans" },
          { kind: "something_new", text: "restated at closing rates" },
        ],
      },
    }),
  );
  assert.equal(parsed.reading?.clauses.length, 2);
  assert.equal(parsed.reading?.clauses[1].kind, null);
  assert.equal(parsed.reading?.clauses[1].text, "restated at closing rates");
});

test("a suggestion with no platform label is dropped, never shown by id", () => {
  const parsed = parseAskProposal(
    body({
      state: "refused",
      query: null,
      reading: null,
      message: "The platform has no figure that answers that question.",
      suggestions: [
        { member_id: "loans.gross_rc", label: "Gross loans" },
        { member_id: "loans.mystery_rc" },
      ],
    }),
  );
  assert.deepEqual(parsed.suggestions, [
    { memberId: "loans.gross_rc", label: "Gross loans" },
  ]);
});

// --- 3. the poll rule -------------------------------------------------------

test("polling happens while and only while the question is being worked out", () => {
  assert.equal(askIsPending("translating"), true);
  assert.equal(askPollInterval("translating"), ASK_POLL_MS);
  assert.equal(askPollInterval("translating", 500), 500);
  for (const terminal of ["proposed", "refused", "stopped"] as const) {
    assert.equal(askIsPending(terminal), false, terminal);
    assert.equal(askPollInterval(terminal), false, terminal);
  }
  // Nothing in hand yet: do not poll on a guess.
  assert.equal(askPollInterval(undefined), false);
});

// --- 4. an empty answer says what is absent ---------------------------------

test("an empty answer names the figures and the date, and shows no zero", () => {
  const sentence = askEmptyAnswerSentence("Gross loans", "31 Aug 2026");
  assert.match(sentence, /Gross loans/);
  assert.match(sentence, /31 Aug 2026/);
  assert.match(sentence, /not a zero/i);
  assert.equal(
    /\b0\b/.test(sentence),
    false,
    "the absence sentence must not contain a zero",
  );
  // With no figure clause it still says whose absence it is.
  assert.match(
    askEmptyAnswerSentence("", "31 Aug 2026"),
    /figures you asked for/i,
  );
});

test("askFigureText reads the figure clause, or nothing", () => {
  const parsed = parseAskProposal(body());
  assert.equal(askFigureText(parsed.reading), "Gross loans");
  assert.equal(askFigureText(null), "");
});

// --- 5. the deployment flag is three-valued ---------------------------------

// --- 6. the surface is reached ----------------------------------------------

test("the question bound matches the route's own", () => {
  const accepted = runBackend<boolean[]>(
    `
from app.bi.schemas.nlq import BiAskRequest
results = []
for question in payload:
    try:
        BiAskRequest.model_validate({"question": question, "as_of": "2026-06-30"})
    except ValueError:
        results.append(False)
    else:
        results.append(True)
print(json.dumps(results))
`,
    [
      "q".repeat(ASK_QUESTION_MAX_CHARS),
      "q".repeat(ASK_QUESTION_MAX_CHARS + 1),
      "",
    ],
  );
  assert.deepEqual(accepted, [true, false, false]);
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log(
  "ask.test.ts: the confirmation, the echo-back and the refusals hold.",
);
