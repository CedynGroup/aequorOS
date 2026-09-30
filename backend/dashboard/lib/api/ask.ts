/**
 * Reading one natural-language question, and holding the query it proposed.
 *
 * `docs/bi.md` §Phase 5: the model emits a `BiQuery`, never SQL, and "it's shown
 * to the user for confirmation". The server enforces that confirmation by
 * DIGEST: `POST …/bi/ask/{id}/run` compares the query the client sends with the
 * proposal it stored, and refuses anything else. So the single most important
 * property of this module is what it does NOT do.
 *
 * THE PROPOSED QUERY IS OPAQUE. `proposedQuery` is the exact JSON value the
 * server sent, held and handed back untouched. It is deliberately typed
 * `unknown` rather than `BiQuery`: the generated client's `BiQueryToJSON`
 * hand-enumerates the keys it was generated from and has no spread, so a query
 * that went through it would silently LOSE any field added to `BiQuery` since
 * the package was last generated — and the reader would then confirm a sentence
 * describing one question while the browser posted another. The server would
 * answer 409 and nothing on screen could explain it. Nothing here normalises,
 * reorders, prettifies or re-serialises it; `askRunBody` returns the same
 * reference it received.
 *
 * NO MODEL PROSE PASSES THROUGH. The wire carries no free text from the model:
 * `message` is the platform's own sentence for a closed reason code, `reading`
 * is rendered server-side from catalogue labels, and a suggestion is a member id
 * with the label the PLATFORM looked up for it. This module therefore never
 * composes a refusal sentence of its own — `askRefusal` returns the server's
 * message or nothing, and a caller with nothing renders the app's ordinary
 * failure panel rather than inventing copy.
 *
 * ONE REFUSAL COVERS THREE FACTS ON PURPOSE. A figure that does not exist, one
 * this reader's grants hide, and one that exists but was not offered for this
 * question all answer identically, with no member named. Nothing here may tell
 * them apart, because being able to is the disclosure.
 *
 * Pure and dependency-free (no React, no fetch, no generated client) so
 * `ask.test.ts` can prove these properties in plain Node. The transport is
 * `./askTransport.ts`; cache identity is `./biKeys`.
 */

/**
 * The longest question the route accepts — `QUESTION_MAX_CHARS` in
 * `app/schemas/bi_nlq.py`. Mirrored so the box can stop a reader at the bound
 * rather than let the server refuse a sentence they already typed; the server
 * remains the authority and refuses independently.
 */
export const ASK_QUESTION_MAX_CHARS = 300;

/**
 * How often a question that is still being worked out is asked about again.
 *
 * Short, because a reader is watching. It is a client choice and not a server
 * instruction: the response carries no interval, only a state, and the rule that
 * matters is `askPollInterval` below — poll while and only while the state is
 * `translating`.
 */
export const ASK_POLL_MS = 2_000;

/** Where a question has got to. `translating` is the only non-terminal state. */
export type AskState = "translating" | "proposed" | "refused" | "stopped";

const ASK_STATES: readonly AskState[] = [
  "translating",
  "proposed",
  "refused",
  "stopped",
];

/** Which part of the question a clause is. The server decides; this is its list. */
export type AskClauseKind =
  | "figure"
  | "grouping"
  | "period"
  | "comparison"
  | "filter"
  | "ranking"
  | "order";

const ASK_CLAUSE_KINDS: readonly AskClauseKind[] = [
  "figure",
  "grouping",
  "period",
  "comparison",
  "filter",
  "ranking",
  "order",
];

/**
 * What each clause is, for a reader.
 *
 * The clause TEXT is the server's own phrase ("grouped by Branch", "as at
 * 2026-08-31"); this is only the heading it sits under, so the confirmation
 * reads as the checklist a person actually works down rather than as one long
 * line. A kind this build does not know is not rendered as its wire token — see
 * `askClauseHeading`.
 */
const ASK_CLAUSE_HEADINGS: Readonly<Record<AskClauseKind, string>> = {
  figure: "Figure",
  grouping: "Broken down by",
  period: "Reporting period",
  comparison: "Compared with",
  filter: "Limited to",
  ranking: "Ranking",
  order: "Order",
};

/** The heading for a clause, or `null` for a kind this build cannot name. */
export function askClauseHeading(kind: AskClauseKind | null): string | null {
  if (kind === null) return null;
  return ASK_CLAUSE_HEADINGS[kind] ?? null;
}

export type AskClause = Readonly<{
  /**
   * `null` when the server named a kind this build does not know. The clause is
   * still shown — under its text alone, never under a wire token — because
   * dropping it would hide part of what the reader is confirming, and showing
   * the token would put an enum value on a bank analyst's screen.
   */
  kind: AskClauseKind | null;
  text: string;
}>;

export type AskReading = Readonly<{
  /** Every clause joined, for the surfaces that want one line. */
  sentence: string;
  clauses: readonly AskClause[];
  /** The window as machine values, so a surface localises instead of parsing prose. */
  asOf: string | null;
  rangeStart: string | null;
  rangeEnd: string | null;
  compareTo: string | null;
  /** Every catalogue member the sentence names. */
  memberIds: readonly string[];
}>;

export type AskSuggestion = Readonly<{ memberId: string; label: string }>;

export type AskProposal = Readonly<{
  questionId: string;
  state: AskState;
  /** Production copy for this state. Always set by the server; never composed here. */
  message: string;
  /** The reader's own words, as the server recorded them. */
  question: string;
  /** The reporting date the READER chose. The model never names a date. */
  asOf: string;
  catalogueVersion: string;
  /** How many figures the model was shown. Names none of them, on purpose. */
  figuresOffered: number;
  reading: AskReading | null;
  suggestions: readonly AskSuggestion[];
  /**
   * THE PROPOSAL, OPAQUE. The exact JSON the server sent for `query`, or `null`
   * when this state carries no proposal. Never read field by field, never
   * rebuilt: `askRunBody` hands this same value back so the server's digest
   * comparison can only succeed on the query the reader was actually shown.
   */
  proposedQuery: unknown;
}>;

/**
 * The response did not have the shape the contract states.
 *
 * Thrown rather than patched over. A proposal is what a reader confirms, so a
 * body this build cannot read completely must not be presented as a question the
 * platform understood — the surface shows a failure instead.
 */
export class AskShapeError extends Error {
  constructor(what: string) {
    super(
      "Something in the platform's answer could not be read, so nothing was run. " +
        `Ask again. (${what}.)`,
    );
    this.name = "AskShapeError";
  }
}

function requiredString(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== "string") throw new AskShapeError(`missing ${key}`);
  return value;
}

function optionalDay(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length >= 10 ? trimmed.slice(0, 10) : trimmed || null;
}

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new AskShapeError(what);
  }
  return value as Record<string, unknown>;
}

function parseClauses(value: unknown): readonly AskClause[] {
  if (!Array.isArray(value)) return [];
  const clauses: AskClause[] = [];
  for (const entry of value) {
    const row = asRecord(entry, "a clause was not an object");
    const kind = row.kind;
    const text = row.text;
    if (typeof kind !== "string" || typeof text !== "string") {
      throw new AskShapeError("a clause was missing its kind or its text");
    }
    clauses.push({
      kind: ASK_CLAUSE_KINDS.includes(kind as AskClauseKind)
        ? (kind as AskClauseKind)
        : null,
      text,
    });
  }
  return clauses;
}

function parseReading(value: unknown): AskReading | null {
  if (value == null) return null;
  const row = asRecord(value, "the reading was not an object");
  return {
    sentence: requiredString(row, "sentence"),
    clauses: parseClauses(row.clauses),
    asOf: optionalDay(row.as_of),
    rangeStart: optionalDay(row.range_start),
    rangeEnd: optionalDay(row.range_end),
    compareTo: optionalDay(row.compare_to),
    memberIds: Array.isArray(row.member_ids)
      ? row.member_ids.filter((id): id is string => typeof id === "string")
      : [],
  };
}

function parseSuggestions(value: unknown): readonly AskSuggestion[] {
  if (!Array.isArray(value)) return [];
  const out: AskSuggestion[] = [];
  for (const entry of value) {
    const row = asRecord(entry, "a suggestion was not an object");
    const memberId = row.member_id;
    const label = row.label;
    // A suggestion with no PLATFORM label is dropped rather than shown by id: a
    // wire key on screen is not production copy, and the id alone tells a reader
    // nothing they can act on.
    if (typeof memberId === "string" && typeof label === "string") {
      out.push({ memberId, label });
    }
  }
  return out;
}

/**
 * One `BiAskRead` body, read into the shape the surface renders.
 *
 * The wire is snake_case and there is no generated model for it yet, so this
 * parse is hand-written — and that is precisely why `proposedQuery` is copied by
 * REFERENCE and never rebuilt. A field of the query this parse does not know
 * about still travels, because it is never looked at.
 */
export function parseAskProposal(body: unknown): AskProposal {
  const row = asRecord(body, "the answer was not an object");
  const state = row.state;
  if (typeof state !== "string" || !ASK_STATES.includes(state as AskState)) {
    // Fail closed on an unknown state: guessing would either poll forever or
    // present a question as understood when the platform said something else.
    throw new AskShapeError(
      "the platform reported a state this app cannot read",
    );
  }
  const proposedQuery = row.query ?? null;
  const reading = parseReading(row.reading);
  if (state === "proposed") {
    if (proposedQuery === null || typeof proposedQuery !== "object") {
      throw new AskShapeError("a proposal arrived with no question to confirm");
    }
    if (reading === null) {
      throw new AskShapeError(
        "a proposal arrived with nothing a reader could check",
      );
    }
  }
  const figuresOffered = row.figures_offered;
  return {
    questionId: requiredString(row, "question_id"),
    state: state as AskState,
    message: requiredString(row, "message"),
    question: requiredString(row, "question"),
    asOf: requiredString(row, "as_of").slice(0, 10),
    catalogueVersion: requiredString(row, "catalogue_version"),
    figuresOffered: typeof figuresOffered === "number" ? figuresOffered : 0,
    reading,
    suggestions: parseSuggestions(row.suggestions),
    proposedQuery: state === "proposed" ? proposedQuery : null,
  };
}

/** True while the platform is still working the question out. */
export function askIsPending(state: AskState): boolean {
  return state === "translating";
}

/**
 * The poll rule, in one place: ask again only while the state is `translating`.
 *
 * `false` is TanStack's "do not refetch on a timer". `proposed`, `refused` and
 * `stopped` are terminal, so a surface that kept polling one of them would spend
 * a reader's BI read budget on an answer that cannot change.
 */
export function askPollInterval(
  state: AskState | undefined,
  baseMs: number = ASK_POLL_MS,
): number | false {
  if (state === undefined) return false;
  return askIsPending(state) ? baseMs : false;
}

/**
 * The body of `POST …/bi/ask/{id}/run`: the proposal, echoed back unchanged.
 *
 * Returns the held reference. Not a copy, not a re-serialisation, not a
 * `BiQueryToJSON` — see the module docstring for what each of those would cost.
 */
export function askRunBody(proposal: AskProposal): { query: unknown } {
  if (proposal.proposedQuery === null) {
    throw new AskShapeError("there is no proposed question to confirm");
  }
  return { query: proposal.proposedQuery };
}

/**
 * Every refusal this surface renders the SERVER's words for.
 *
 * `bi_ask_unavailable` is one code covering a shut flag, a withheld question, a
 * spent quota and a vendor that could not be reached — the server distinguishes
 * them in `details.reason` and states each in its own sentence, and this list
 * exists only to decide WHOSE words are shown, never to compose any.
 */
export const ASK_REFUSAL_CODES: readonly string[] = [
  "bi_ask_no_figures_available",
  "bi_ask_unavailable",
  "bi_ask_not_the_proposed_query",
  "bi_authorization_denied",
  "bi_rate_limited",
];

/** The shape of a normalised API failure this module reads. Structural on purpose. */
export type AskFailureShape = Readonly<{
  status?: number | null;
  errorCode?: string | null;
  message?: string | null;
}>;

/**
 * The platform's own sentence for a refusal, or `null` when this is not one.
 *
 * `null` means "not a refusal the server wrote copy for" — a transport failure,
 * a 500, a shape error — and the caller renders the app's ordinary failure panel.
 * It must never be turned into a sentence here: a refusal the platform did not
 * author would read to a bank analyst as though it had.
 */
export function askRefusal(error: unknown): string | null {
  if (error === null || typeof error !== "object") return null;
  const failure = error as AskFailureShape;
  const code = failure.errorCode;
  if (typeof code !== "string" || !ASK_REFUSAL_CODES.includes(code))
    return null;
  const message = failure.message;
  if (typeof message !== "string" || message.trim() === "") return null;
  return message;
}

/**
 * What a reader is told when the confirmed question returned nothing.
 *
 * MISSING IS NOT ZERO. An answer with no rows, or whose every figure is absent,
 * has not measured zero — so the surface says which figures were asked for and
 * for when, and shows no table, no total and no chart. `figures` is the
 * reading's own figure clause (platform labels) and `when` is the reporting date
 * already formatted by the caller in the institution's locale.
 */
export function askEmptyAnswerSentence(figures: string, when: string): string {
  const named = figures.trim();
  const subject = named === "" ? "The figures you asked for" : named;
  return (
    `${subject} — nothing has been measured for ${when}. This is not a zero: ` +
    `the platform has no value recorded for that date, so no figure is shown.`
  );
}

/** The figure clause's text, for the empty-answer sentence. Empty when absent. */
export function askFigureText(reading: AskReading | null): string {
  if (reading === null) return "";
  return reading.clauses.find((clause) => clause.kind === "figure")?.text ?? "";
}
