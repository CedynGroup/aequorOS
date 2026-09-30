/**
 * The confirmation is real, the echo-back is untouched, and no code reaches a
 * reader.
 *
 * Every assertion below exists because one specific way of getting this surface
 * wrong would be invisible on screen:
 *
 * - a proposal RE-SERIALISED between being shown and being confirmed would drop
 *   any `BiQuery` field the generated client predates, the server's digest would
 *   not match, and the reader would get a 409 about a question they did confirm
 *   (the generated serializer's own key list is read out of the package here, so
 *   the drop is demonstrated rather than asserted);
 * - a refusal rendered in this app's words would read to a bank analyst as the
 *   platform's own, and one refusal deliberately covers three different facts, so
 *   any wording of our own is a chance to tell them apart;
 * - a poll that did not stop would spend a reader's BI read budget on an answer
 *   that cannot change;
 * - an empty answer drawn as a table of dashes, or worse a zero, is a picture of
 *   a measurement that never happened;
 * - a cache key missing a dimension would hand one colleague another's proposal
 *   without asking the server anything;
 * - and a surface nothing reaches is the defect this whole build shipped four
 *   times, so the page, the tab and the transport are pinned to each other.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
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

function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const name = JSON.parse(readFileSync(manifest, "utf8")).name as string;
      if (name === "@aequoros/dashboard") return dir;
    }
    dir = dirname(dir);
  }
  throw new Error("could not locate the @aequoros/dashboard package root");
}

const ROOT = dashboardRoot();
const source = (relativePath: string): string =>
  readFileSync(join(ROOT, relativePath), "utf8");

/** The repository root, found by walking up to the directory holding `backend/`. */
function repoRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 10; i += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("could not locate the repository root from " + __dirname);
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

test("the generated BiQuery serializer would have dropped a field, and is not used", () => {
  // THE GUARD PROVEN ABLE TO FIRE. `BiQueryToJSONTyped` returns a hand-enumerated
  // object literal with no spread. Its key list is read out of the generated
  // package rather than imported, because this suite runs as plain Node with no
  // bundler — and the fixture's future field is deliberately absent from it, so
  // the "a serializer would lose this" claim is demonstrated, not asserted.
  const model = join(
    repoRoot(),
    "packages",
    "risk-service-api",
    "src",
    "models",
    "BiQuery.ts",
  );
  assert.ok(existsSync(model), `generated model not found: ${model}`);
  const serializer =
    /export function BiQueryToJSONTyped[\s\S]*?return \{([\s\S]*?)\n  \};/.exec(
      readFileSync(model, "utf8"),
    );
  assert.ok(serializer, "could not read the generated BiQuery serializer");
  const generatedKeys = [
    ...serializer![1].matchAll(/^\s{4}([a-z_0-9]+):/gm),
  ].map((match) => match[1]);
  assert.ok(generatedKeys.length > 0, "the generated key list read as empty");
  assert.equal(
    generatedKeys.includes(FUTURE_QUERY_FIELD),
    false,
    `${FUTURE_QUERY_FIELD} is now a generated key, so it no longer stands for a ` +
      `field the client predates — pick another, or this test proves nothing.`,
  );
  // The call-shaped scan proven able to fire: a planted call is caught, a mention
  // in prose is not.
  const callScan = /BiQueryToJSON(?:Typed)?\s*\(/;
  assert.equal(callScan.test("const body = BiQueryToJSON(query);"), true);
  assert.equal(
    callScan.test("// `BiQueryToJSON` drops fields, so it is not used"),
    false,
  );
  // And the transport must never touch that serializer in either direction.
  for (const file of [
    "lib/api/ask.ts",
    "lib/api/askTransport.ts",
    "app/(app)/explore/ask/page.tsx",
    "components/bi/AskConfirmation.tsx",
    "components/bi/AskAnswer.tsx",
  ]) {
    // A CALL, not a mention: these files name the serializer in prose precisely to
    // record why it is not used, and a scan that could not tell the two apart
    // would punish the documentation.
    assert.equal(
      /BiQueryToJSON(?:Typed)?\s*\(/.test(source(file)),
      false,
      `${file} serialises the proposal through the generated client. It drops any ` +
        `field it was not generated from, so the digest the server compares would ` +
        `not match and the reader would get a 409 about a question they confirmed.`,
    );
  }
});

test("the generated response parser would rename a field, and is not used", () => {
  // THE OTHER HALF OF THE SAME HAZARD, and the one the regenerated client made
  // reachable. `BiQueryFromJSONTyped` spreads `...json` and THEN assigns the
  // fields it knows under camelCase names, so a proposal carrying `top_n` comes
  // back carrying `top_n` AND `topN`. `BiQuery` is `extra="forbid"` server-side,
  // so posting that object back is a 422 — on a question the reader confirmed.
  // That is why the three generated ask operations are unusable here in BOTH
  // directions, and why this transport is permanent rather than interim.
  const model = join(
    repoRoot(),
    "packages",
    "risk-service-api",
    "src",
    "models",
    "BiQuery.ts",
  );
  assert.ok(existsSync(model), `generated model not found: ${model}`);
  const parser =
    /export function BiQueryFromJSONTyped[\s\S]*?return \{([\s\S]*?)\n  \};/.exec(
      readFileSync(model, "utf8"),
    );
  assert.ok(parser, "could not read the generated BiQuery parser");
  assert.match(
    parser![1],
    /^\s{4}\.\.\.json,/m,
    "the generated parser no longer spreads the raw JSON. Re-derive whether a " +
      "parsed proposal is still contaminated before relaxing anything here.",
  );
  // The concrete contamination: a camelCase key read out of a snake_case wire
  // name. `top_n` is in the fixture above, so this is the field that would
  // arrive twice.
  assert.match(
    parser![1],
    /topN:[\s\S]*?json\["top_n"\]/,
    "the generated parser no longer renames top_n, so this test no longer " +
      "demonstrates the contamination it exists to demonstrate. Find the " +
      "renamed field it does produce — never delete the assertion.",
  );
  assert.equal(
    Object.prototype.hasOwnProperty.call(proposedQuery, "top_n"),
    true,
    "the fixture no longer carries top_n, so the renamed field above is not " +
      "one this surface would actually receive",
  );

  // Neither the parser nor any generated ask operation may be called from the
  // surface. Call-shaped, for the same reason as the serializer scan above: these
  // files name them in prose precisely to record why they are not used.
  for (const symbol of [
    "BiQueryFromJSON",
    "BiAskReadFromJSON",
    "BiAskReadQueryFromJSON",
    "askBiQuestion",
    "getBiQuestion",
    "runBiQuestion",
  ]) {
    const callScan = new RegExp(`${symbol}(?:Typed)?\\s*\\(`);
    // Proven able to fire, and able to tell a call from a mention.
    assert.equal(callScan.test(`const p = ${symbol}(raw);`), true);
    assert.equal(callScan.test(`// \`${symbol}\` renames fields, so it is unused`), false);
    for (const file of [
      "lib/api/ask.ts",
      "lib/api/askTransport.ts",
      "app/(app)/explore/ask/page.tsx",
      "components/bi/AskConfirmation.tsx",
      "components/bi/AskAnswer.tsx",
    ]) {
      assert.equal(
        callScan.test(source(file)),
        false,
        `${file} routes the proposal through ${symbol}. The generated client ` +
          `renames every field it knows and spreads the rest, so the confirmed ` +
          `query would carry two spellings of the same field and the server ` +
          `would refuse it as an unknown one.`,
      );
    }
  }
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

test("no reason code, refusal code or state token is display copy", () => {
  // One refusal covers three different facts — an id that does not exist, one the
  // reader's grants hide, and one that exists but was not offered — and the
  // responses are byte-identical so a reader cannot use refusals to discover what
  // exists. A surface that mapped codes to its own words would be free to tell
  // them apart, so no surface file may contain one.
  const forbidden = [
    "unrecognised_member",
    "malformed_output",
    "contradictory_output",
    "unusable_query",
    "unanswerable",
    "no_matching_figure",
    "nlq_disabled",
    "question_withheld",
    "question_too_long",
    "catalogue_changed",
    "not_proposed",
  ];
  const surfaces = [
    "app/(app)/explore/ask/page.tsx",
    "components/bi/AskConfirmation.tsx",
    "components/bi/AskAnswer.tsx",
    "components/bi/AskQuestionForm.tsx",
  ];
  for (const file of surfaces) {
    const code = source(file);
    for (const token of forbidden) {
      assert.equal(
        code.includes(token),
        false,
        `${file} contains "${token}". A refusal's words are the platform's own — ` +
          `render the server's message, never a code and never copy for one.`,
      );
    }
  }
  // The guard proven able to fire: the same scan over a synthetic source that
  // does contain one must find it.
  const planted = 'const why = reason === "unrecognised_member" ? "…" : "…";';
  assert.ok(
    forbidden.some((token) => planted.includes(token)),
    "the forbidden-token scan cannot detect a code it is meant to catch",
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

test("the transport polls through that one rule and nothing else", () => {
  const transport = source("lib/api/askTransport.ts");
  assert.ok(
    /refetchInterval:\s*\(query\)\s*=>\s*\n?\s*askPollInterval\(/.test(
      transport,
    ),
    "the poll interval must be decided by askPollInterval, reading the state of " +
      "the answer already in hand",
  );
  assert.equal(
    /refetchInterval:\s*\d/.test(transport),
    false,
    "a bare numeric refetchInterval polls a terminal state forever",
  );
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

test("the answer states the absence BEFORE it could draw a table", () => {
  const answer = source("components/bi/AskAnswer.tsx");
  const absenceAt = answer.indexOf("askEmptyAnswerSentence");
  const tableAt = answer.indexOf("<DataTable");
  assert.ok(absenceAt > 0, "AskAnswer must state an absence in words");
  assert.ok(tableAt > 0, "AskAnswer must render the answer");
  assert.ok(
    absenceAt < tableAt,
    "the empty-answer branch must come first: a table of dashes reads as a result",
  );
  for (const check of ["isEmptyResult", "hasNoMeasuredValue"]) {
    assert.ok(
      answer.includes(check),
      `AskAnswer must test ${check} — an answer that matched rows but measured ` +
        `nothing reads exactly like a run of zeros`,
    );
  }
  assert.equal(
    /<EChart|EChartCanvas/.test(answer),
    false,
    "the answer is a table on purpose: a chart of an empty series draws a flat " +
      "line on the baseline, which is the fabrication the absence rule forbids",
  );
});

// --- 5. the deployment flag is three-valued ---------------------------------

test("the NLQ flag is read from the typed field, not off the raw body", () => {
  // INVERTED at the regeneration, per AGENTS.md: an interim reader that outlives
  // the client it worked around is a second contract nobody is checking.
  // `nlqEnabledFromFeatureFlags` existed because `FeatureFlagsRead` predated the
  // flag, so the value had to be read off the spread-through wire name. The
  // client now carries `biNlqEnabled` and the reader is deleted; this asserts it
  // does not come back, and that the three-valued behaviour it protected is
  // still what ships.
  assert.equal(
    existsSync(join(ROOT, "lib/api/featureFlags.ts")),
    false,
    "lib/api/featureFlags.ts is back. FeatureFlagsRead carries biNlqEnabled now, " +
      "so a hand-written reader beside it is a second, unchecked copy of the flag.",
  );
  const bi = source("lib/api/bi.ts");
  assert.equal(
    /nlqEnabledFromFeatureFlags\s*\(/.test(bi),
    false,
    "lib/api/bi.ts still calls the retired raw-body reader",
  );
  // THREE-VALUED, still: `query.data?.biNlqEnabled` is undefined until the flags
  // answer (nav hides, deep link does not 404), and undefined again on a backend
  // that does not project it — `FromJSON` simply leaves the field absent.
  assert.match(
    bi,
    /nlqEnabled:\s*query\.isError\s*\?\s*false\s*:\s*query\.data\?\.biNlqEnabled/,
    "the NLQ flag must fail closed on an error and stay undefined until answered",
  );
  const generated = join(
    dirname(dirname(ROOT)),
    "packages/risk-service-api/src/models/FeatureFlagsRead.ts",
  );
  assert.ok(existsSync(generated), `generated model not found: ${generated}`);
  assert.match(
    readFileSync(generated, "utf8"),
    /biNlqEnabled:\s*boolean/,
    "FeatureFlagsRead no longer carries biNlqEnabled — the typed read above is " +
      "reading a field that does not exist",
  );
});

// --- 6. the surface is reached ----------------------------------------------

test("the page, the tab and the transport reach each other", () => {
  const page = source("app/(app)/explore/ask/page.tsx");
  for (const symbol of [
    "useBiAsk",
    "AskConfirmation",
    "AskAnswer",
    "AskQuestionForm",
    "askRefusal",
  ]) {
    assert.ok(
      page.includes(symbol),
      `the ask page does not use ${symbol}: the piece exists and nothing on ` +
        `screen reaches it`,
    );
  }
  const layout = source("app/(app)/explore/layout.tsx");
  assert.ok(
    layout.includes('href: "/explore/ask"'),
    "the Explore layout must carry the Ask tab, or the page is unreachable by nav",
  );
  const palette = source("components/shell/CommandPalette.tsx");
  assert.ok(
    palette.includes('href: "/explore/ask"'),
    "the command palette must carry the Ask entry",
  );
  const transport = source("lib/api/askTransport.ts");
  for (const route of ["/bi/ask", "/run"]) {
    assert.ok(transport.includes(route), `the transport must call ${route}`);
  }
  assert.ok(
    transport.includes("BiQueryResultFromJSON"),
    "the answer must be parsed by the generated parser, like every other BI read",
  );
});

test("the question bound matches the route's own", () => {
  const schema = readFileSync(
    join(repoRoot(), "backend", "app", "schemas", "bi_nlq.py"),
    "utf8",
  );
  const declared = /QUESTION_MAX_CHARS\s*=\s*(\d+)/.exec(schema);
  assert.ok(
    declared,
    "could not read QUESTION_MAX_CHARS from the wire contract",
  );
  assert.equal(
    ASK_QUESTION_MAX_CHARS,
    Number(declared![1]),
    "the box would let a reader type a question the route refuses",
  );
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log(
  "ask.test.ts: the confirmation, the echo-back and the refusals hold.",
);
