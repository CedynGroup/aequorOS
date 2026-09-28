/**
 * Calculated measures: the rules a bank's own formula is presented under.
 *
 * A calculated measure is a formula a person writes over figures the catalogue
 * already publishes (`SAFE_DIV([m:loans.non_performing_rc],
 * [m:loans.balance_rc])`). Four things about that are decided here, and one very
 * deliberately is NOT.
 *
 * 1. **THE SERVER OWNS VALIDITY. THERE IS NO PARSER HERE.**
 *    `app/domain/bi/expr.py` is a Pratt parser with a type system, three period
 *    grains, a retention-derived bound on how far `LAG` may reach, and a dozen
 *    named refusals. A second implementation in TypeScript would be a second
 *    opinion, and the moment the two disagree the faster one is the wrong one —
 *    a reader told their formula is fine and then refused on save is worse off
 *    than one who waited. So this module holds the language's VOCABULARY (what
 *    may be offered as a choice, and the bounds a form must not exceed), every
 *    item of it mirrored from `expr.py` and pinned to that file by
 *    `measures.test.ts`, and the verdict always comes from
 *    `POST …/bi/measures/validation`.
 *
 * 2. **A FORMULA IS AUTHORIZED AS THE FIGURES ITS TEXT NAMES, NEVER AS ITSELF.**
 *    So a refusal names the FIGURE the reader lacks — `refusedFigures` reads the
 *    server's own list, preferring the labels it sends over the ids — and the
 *    measure's own label is never the thing that was refused. A measure whose
 *    figures a reader's access does not cover is ABSENT from their list rather
 *    than shown as restricted (`content.readable_measures`), because its label
 *    is authored text that can describe the very figure they were refused. There
 *    is correspondingly no "restricted measure" state in this module to render.
 *
 * 3. **CERTIFYING TAKES TWO PEOPLE AND THE SURFACE MUST SHOW THAT.**
 *    `measureControls` never returns `canDecide` for the identity that proposed
 *    a measure, and never returns it on the strength of a role: the server sends
 *    `awaiting_caller_decision`, which is true only when the measure is proposed,
 *    the caller is not its proposer, and their access covers APPROVING every
 *    figure the formula names. The route decides again and the database refuses a
 *    self-approval whatever a client believes — this is about not offering a
 *    button that answers 409.
 *
 * 4. **A CERTIFIED FORMULA IS BOUND TO THE TEXT THAT WAS APPROVED.**
 *    `ck_bi_measures_certified_matches_expression` makes the inconsistent row
 *    unstorable, so an edit to a certified measure DROPS the certification. That
 *    is not a harmless act and `editConsequence` says so in words before the form
 *    opens.
 *
 * Pure and dependency-free (type-only imports) so `measures.test.ts` can prove
 * all of it under node, including against `expr.py` itself.
 */

import type {
  BiCatalogueDimensionRead,
  BiCatalogueMeasureRead,
  BiMeasureRead,
  BiMeasureValidationRead,
} from "@aequoros/risk-service-api";
import type { FigureAddresses, ScopePair } from "@/lib/api/dataScope";

// ---------------------------------------------------------------------------
// The language's vocabulary, mirrored from app/domain/bi/expr.py
// ---------------------------------------------------------------------------

/**
 * `MAX_EXPRESSION_LENGTH`. Also the column width
 * (`bi_content.MEASURE_EXPRESSION_MAX_LENGTH`) and the request field's
 * `max_length`, all three pinned equal by the parity test.
 */
export const EXPRESSION_MAX_LENGTH = 2_000;

/** `MAX_MEMBER_REFERENCES` — distinct figures one formula may name. */
export const MAX_FIGURE_REFERENCES = 25;

/** `MAX_LAG_PERIODS` — the language's structural ceiling on `LAG`'s reach. */
export const MAX_LAG_PERIODS = 60;

/** `bi_content.MEASURE_KEY_MAX_LENGTH` / `LABEL` / `REASON`, and the description. */
export const MEASURE_KEY_MAX_LENGTH = 64;
export const MEASURE_LABEL_MAX_LENGTH = 120;
export const MEASURE_DESCRIPTION_MAX_LENGTH = 400;
export const MEASURE_REASON_MAX_LENGTH = 400;

/**
 * `app/schemas/bi_content.py::MEASURE_KEY_PATTERN`, verbatim.
 *
 * Mirrored so the form can say what is wrong with an id BEFORE the request — the
 * server answers a bad one with a 422 whose message is about a pattern, which is
 * not something to show a banker. This is a lexical shape, not the language:
 * refusing a malformed id is nothing like deciding whether a formula is valid.
 */
export const MEASURE_KEY_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$/;

/**
 * `FUNCTION_NAMES` and `_FUNCTION_ARITY`, with what each one is FOR in words.
 *
 * The signature is the language's own (`PCT_CHANGE` and `LAG` count their period
 * word as an input, because it is written in the call and is not optional —
 * D-195: the text a checker approves states the comparison period, so one
 * certified label cannot mean month-on-month in one widget and
 * quarter-on-quarter in the next).
 */
export const MEASURE_FUNCTIONS = [
  {
    name: "SAFE_DIV",
    arity: 2,
    signature: "SAFE_DIV(top, bottom)",
    purpose:
      "Divide one figure by another. Where the bottom is zero the answer is left empty rather than reported as a number.",
  },
  {
    name: "IF",
    arity: 3,
    signature: "IF(test, then, otherwise)",
    purpose:
      "Choose between two figures on a yes-or-no test, such as one figure being larger than another.",
  },
  {
    name: "PCT_CHANGE",
    arity: 2,
    signature: "PCT_CHANGE(figure, PERIOD)",
    purpose:
      "How much a figure moved against the period before it, as a percentage. The period is written into the formula, so the measure means the same comparison everywhere it is used.",
  },
  {
    name: "LAG",
    arity: 3,
    signature: "LAG(figure, periods, PERIOD)",
    purpose:
      "The same figure as it stood a stated number of periods earlier.",
  },
] as const;

export type MeasureFunctionName = (typeof MEASURE_FUNCTIONS)[number]["name"];

/**
 * `GRAIN_WORDS` — the word a formula writes, and how it reads in prose
 * (`GRAIN_LABELS`). Exactly three, and not by preference: these are the periods
 * `bi_dim_date` carries an `is_last_in_*` flag for, which is what fixes where
 * "the period before" ENDS.
 */
export const PERIOD_GRAINS = [
  { word: "MONTH", label: "month" },
  { word: "QUARTER", label: "quarter" },
  { word: "YEAR", label: "year" },
] as const;

/** The operators the language accepts, for the reference panel. */
export const MEASURE_OPERATORS = [
  { symbol: "+ − × ÷", reads: "written + - * /, with brackets where the order matters" },
  { symbol: "> >= < <= = !=", reads: "compare two figures; the answer is a yes or no, not a number" },
  { symbol: "AND OR NOT", reads: "combine two yes-or-no answers" },
] as const;

/**
 * How a figure is written inside a formula. ONE place, because the spelling is
 * the language's (`_MEMBER_REFERENCE`) and a second one would drift.
 */
export function figureReference(memberId: string): string {
  return `[m:${memberId}]`;
}

/**
 * The aggregation an Explore catalogue entry derived from a bank's own formula
 * carries. Not a value the server publishes for a static member: it exists so the
 * Explore rules can tell a bank-defined figure from a platform one without
 * consulting a second list.
 */
export const CALCULATED_AGGREGATION = "calculated";

/**
 * The digest a checker's decision is taken against.
 *
 * It lives in `./expressionDigest` and is re-exported here so this surface has one
 * import site. The split is load-bearing, not tidiness: `lib/api/bi.ts` needs the
 * digest and is in the Command Center's initial bundle, so importing it from THIS
 * module shipped the whole calculated-measure vocabulary to every reader of the
 * home page — measured at 10 570 B before the split. See that file's own note.
 */
export { DigestUnavailable, expressionDigest } from "./expressionDigest";

// ---------------------------------------------------------------------------
// Standing
// ---------------------------------------------------------------------------

/** `bi_content.MEASURE_STATES`. */
export type MeasureState = BiMeasureRead["state"];

export type MeasureStanding = Readonly<{
  /** The badge word. `bank` only ever comes from the server saying so. */
  standing: "bank" | "personal";
  label: string;
  /** What that standing MEANS for the reader, in one sentence. */
  description: string;
  tone: "success" | "action" | "slate";
}>;

/**
 * THE STANDING IS THE SERVER'S WORD, MAPPED — and an unknown value degrades to
 * the NEUTRAL reading, never to the favourable one.
 *
 * The map is total over the three states the wire declares, so there is no path
 * on which a measure is shown as the institution's when the server called it one
 * person's. The lookup is a record rather than a chain of `if`s precisely so that
 * a fourth state added to `MEASURE_STATES` fails to type-check here instead of
 * falling into whichever branch happened to be last — and the guard below is what
 * keeps a value that reached the browser outside the declared vocabulary (an
 * older client, a widened server) from being drawn as certified.
 */
const STANDINGS: Readonly<Record<MeasureState, MeasureStanding>> = {
  personal: {
    standing: "personal",
    label: "Draft",
    description:
      "One person's own formula. Only its author can see it, change it or put it up for review.",
    tone: "slate",
  },
  proposed: {
    standing: "personal",
    label: "Waiting for a review",
    description:
      "Its author has asked for it to be certified for the institution. Someone else has to review it — the person who proposes a formula cannot be the one who certifies it.",
    tone: "action",
  },
  bank_certified: {
    standing: "bank",
    label: "Certified by this institution",
    description:
      "Two people stand behind this formula: one proposed it and another certified the exact text. It can be charted, put in a grid and read by anyone whose access covers the figures it names.",
    tone: "success",
  },
};

export function measureStanding(state: string): MeasureStanding {
  if (state === "personal" || state === "proposed" || state === "bank_certified") {
    return STANDINGS[state];
  }
  // Neutral, never the most favourable neighbour: a state this build does not
  // recognise is a measure whose standing is unknown, and unknown is a draft's
  // treatment rather than a certification's.
  return STANDINGS.personal;
}

/** Whether a measure may be named as a figure in a question. */
export function isCertified(measure: BiMeasureRead): boolean {
  return measure.state === "bank_certified";
}

// ---------------------------------------------------------------------------
// What may be done, and what may not
// ---------------------------------------------------------------------------

export type MeasureControls = Readonly<{
  /** The owner may edit their own measure in any state. */
  canEdit: boolean;
  /** What editing will cost, when it costs something. Never null for a certified one. */
  editConsequence: string | null;
  /** The owner may put a draft up for certification. */
  canPropose: boolean;
  /**
   * This identity may take the checker's decision. The SERVER's
   * `awaiting_caller_decision` and nothing else — never a role, never "not the
   * owner", never an inference from the state alone.
   */
  canDecide: boolean;
  /** The proposer is told why the decision is not theirs to take. */
  proposerNotice: string | null;
  canDelete: boolean;
  /** Why deletion is not offered, when it is not. */
  deleteWithheld: string | null;
}>;

/**
 * A CERTIFIED MEASURE IS NOT OFFERED FOR DELETION, AND THE REASON IS SAID OUT
 * LOUD.
 *
 * The route permits it — `DELETE …/bi/measures/{id}` is owner-only and
 * unconditional in every state — and that is a governance hole rather than a
 * feature (audit A9-07): certifying a formula takes two identities and deleting
 * it takes one, and the one is the MAKER. The row's `approved_expression` and
 * `approved_expression_digest` ARE the record of what was certified, so the
 * delete destroys the evidence as well as the formula, and every saved dashboard,
 * alert or scheduled report that names the measure loses its definition.
 *
 * So the control is withheld HERE, with this sentence, rather than left on screen
 * for the owner to discover. The wording names the act that will replace it —
 * retiring a certified measure, which keeps the row as the record and carries
 * maker-checker of its own — so it stays true when that lands.
 */
const CERTIFIED_DELETE_WITHHELD =
  "This formula was certified for the institution, so one person cannot remove " +
  "it: certifying it took two people, and the approved text is the record of " +
  "what they agreed. Retiring it is the governed way to take it out of use, and " +
  "that is reviewed the same way certifying it was. Ask an approver to retire it.";

const PROPOSED_DELETE_WITHHELD =
  "This formula is waiting for someone to review it. Withdraw it from review " +
  "first, by changing it, or ask the reviewer to send it back — deleting it now " +
  "would take away what they are being asked to read.";

const CERTIFIED_EDIT_CONSEQUENCE =
  "Changing this formula takes its certification away. An approver certified " +
  "the exact text, not the name, so any edit returns it to a draft and it has to " +
  "be reviewed again before it can be used in a chart or a grid.";

const PROPOSED_EDIT_CONSEQUENCE =
  "This formula is waiting for a review. Changing it now withdraws it from " +
  "review — the reviewer cannot certify text they have not read — and it returns " +
  "to a draft you can send again.";

const PROPOSER_NOTICE =
  "You proposed this formula, so you cannot certify it. Someone whose access " +
  "covers approving the figures it names has to review it.";

export function measureControls(measure: BiMeasureRead): MeasureControls {
  const owner = measure.ownedByCaller;
  const certified = measure.state === "bank_certified";
  const proposed = measure.state === "proposed";
  // ONLY THE OWNER CAN HAVE PROPOSED IT. `content.propose_measure` refuses a
  // proposal from anyone else, so for a proposed measure "the caller owns it" and
  // "the caller proposed it" are the same fact — which is what lets the surface
  // tell the proposer WHY the decision is not theirs, rather than silently
  // omitting a control. The server's own `awaiting_caller_decision` is false for
  // exactly this identity, so the two never contradict each other.
  const proposedByCaller = proposed && owner;
  return {
    canEdit: owner,
    editConsequence: certified
      ? CERTIFIED_EDIT_CONSEQUENCE
      : proposed
        ? PROPOSED_EDIT_CONSEQUENCE
        : null,
    canPropose: owner && measure.state === "personal",
    canDecide: measure.awaitingCallerDecision === true,
    proposerNotice: proposedByCaller ? PROPOSER_NOTICE : null,
    canDelete: owner && measure.state === "personal",
    deleteWithheld: !owner
      ? null
      : certified
        ? CERTIFIED_DELETE_WITHHELD
        : proposed
          ? PROPOSED_DELETE_WITHHELD
          : null,
  };
}

// ---------------------------------------------------------------------------
// Refusals
// ---------------------------------------------------------------------------

export type FigureRefusal = Readonly<{
  /** The figures the reader's access does not cover, named as the server named them. */
  figures: readonly string[];
  /**
   * Whether those names are the server's own LABELS. When false they are the ids
   * the author wrote into the formula, which is the only thing available on the
   * validation route — and which is the author's own text, not a disclosure.
   */
  labelled: boolean;
}>;

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string" && entry.length > 0)
    : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The figures a 403 from the measure routes named, from `ApiError.details`.
 *
 * Labels first (`denied_member_labels`), because a member id is a wire key and
 * `Largest single-name share` is what a banker asks an Org Owner to grant. Ids
 * only when the server sent no labels. An unreadable body yields NO figures
 * rather than a guess, so the surface falls back to the server's own sentence.
 */
export function refusedFigures(details: unknown): FigureRefusal {
  if (!isRecord(details)) return { figures: [], labelled: false };
  const labels = strings(details.denied_member_labels);
  if (labels.length > 0) return { figures: labels, labelled: true };
  return { figures: strings(details.denied_members), labelled: false };
}

/**
 * The figures a validation verdict named. Ids, because that route sends no
 * labels — and the ids ARE the author's own text: they typed `[m:loans.…]`.
 */
export function validationRefusal(
  read: BiMeasureValidationRead | undefined,
): FigureRefusal {
  const denied = read?.deniedMembers ?? [];
  return { figures: [...denied], labelled: false };
}

/**
 * One sentence for a refusal that names figures. The FIGURE is the subject, never
 * the formula: a formula is authorized as the figures its text names, so "your
 * access does not cover this measure" would be both wrong and unactionable.
 */
export function refusedFiguresSentence(refusal: FigureRefusal): string | null {
  if (refusal.figures.length === 0) return null;
  const named = refusal.figures.join(", ");
  return refusal.figures.length === 1
    ? `This formula reads ${named}, which your access does not cover. An Org Owner can grant it.`
    : `This formula reads figures your access does not cover: ${named}. An Org Owner can grant them.`;
}

// ---------------------------------------------------------------------------
// A certified measure as something to ask a question with
// ---------------------------------------------------------------------------

/**
 * Why a certified formula cannot be offered in Explore, when it cannot.
 *
 * * `not_certified` — a draft or a proposal. The compiler resolves only CERTIFIED
 *   measures (`compiler._load_certified`), so an uncertified id is left exactly
 *   as it arrived and refused as unknown. Offering it would guarantee a refusal.
 * * `figure_not_in_catalogue` — the formula names a figure this reader's
 *   catalogue does not carry. It cannot happen through the read routes (a measure
 *   whose figures a reader cannot see is absent from their list), so reaching it
 *   means the catalogue and the measure list disagree — and a question built on a
 *   figure this client cannot describe would be asked blind.
 * * `mixed_time_behaviour` — its figures are not all positions or all movements.
 *   `compiler` refuses stock and flow in one query, so the formula can never be
 *   answered; it is named rather than offered.
 * * `no_shared_breakdown` is NOT in this list on purpose: a formula whose figures
 *   share no field is still a perfectly good institution-level figure, and it is
 *   offered with nothing to break it down by.
 */
export type CalculatedUnusable =
  | "not_certified"
  | "figure_not_in_catalogue"
  | "mixed_time_behaviour";

export type CalculatedMeasureOffer =
  | Readonly<{ usable: true; entry: BiCatalogueMeasureRead }>
  | Readonly<{ usable: false; reason: CalculatedUnusable }>;

/**
 * A certified formula expressed as a catalogue entry, so the Explore rules can
 * treat it exactly like a published figure.
 *
 * DERIVED FROM ITS OWN FIGURES, never asserted. The compiler resolves a
 * calculated measure into the figures its text names and then applies every shape
 * rule to THOSE — so the fields that decide what a question may do with it are
 * properties of its references:
 *
 * * `allowedDimensions` is the INTERSECTION of its figures' allowed dimensions,
 *   because the compiler requires every dimension to be allowed by every resolved
 *   measure. A union would put a field on screen that refuses the moment it is
 *   used.
 * * `timeBehaviour` is its figures' single behaviour. Two behaviours is
 *   `mixed_time_behaviour`, not a choice.
 * * `module` and `sensitivity` are NOT derived, because a formula has no single
 *   one — it spans every pair its figures need, and the server evaluates a
 *   sentence per pair. They are carried as the wire's own "many" marker so no
 *   surface can mistake one of them for the whole authority; nothing in the
 *   Explore rules reads either field.
 *
 * `valueType` and `favourableDirection` are the AUTHOR's declarations, which is
 * why the create form makes both mandatory: a client that guessed the direction
 * would tell a banker that a rising cost ratio is good news.
 */
export function calculatedMeasureOffer(
  measure: BiMeasureRead,
  published: readonly BiCatalogueMeasureRead[],
): CalculatedMeasureOffer {
  if (!isCertified(measure)) return { usable: false, reason: "not_certified" };
  const references: BiCatalogueMeasureRead[] = [];
  for (const memberId of measure.referencedMembers) {
    const found = published.find((entry) => entry.id === memberId);
    if (!found) return { usable: false, reason: "figure_not_in_catalogue" };
    references.push(found);
  }
  if (references.length === 0) {
    return { usable: false, reason: "figure_not_in_catalogue" };
  }
  const behaviours = new Set(references.map((entry) => entry.timeBehaviour));
  if (behaviours.size !== 1) {
    return { usable: false, reason: "mixed_time_behaviour" };
  }
  const shared = references
    .map((entry) => entry.allowedDimensions ?? [])
    .reduce<readonly string[]>(
      (kept, allowed) => kept.filter((id) => allowed.includes(id)),
      references[0].allowedDimensions ?? [],
    );
  return {
    usable: true,
    entry: {
      id: measure.measureKey,
      label: measure.label,
      description: measure.description,
      measureKind: "calculated",
      aggregation: CALCULATED_AGGREGATION,
      allowedDimensions: [...shared],
      timeBehaviour: [...behaviours][0],
      valueType: measure.valueType,
      favourableDirection: measure.favourableDirection,
      // An institution's own formula is not a grain the catalogue declares. It is
      // reported wherever its figures are, so it carries their own coarsest
      // reading: `institution` when any figure is institution-level, because a
      // ratio of an institution total cannot be split by portfolio.
      grain: references.some((entry) => entry.grain === "institution")
        ? "institution"
        : "portfolio",
      module: manyModules(references),
      sensitivity: manySensitivities(references),
      // Not a platform-certified figure: `certified` means "copied from the
      // sealed filing tier" everywhere else in the catalogue, and a bank's own
      // formula is never that however many people approved it.
      certified: false,
    },
  };
}

/**
 * The single module a formula's figures share, or the marker for "more than one".
 *
 * A formula that reads only credit figures belongs to credit and saying so is
 * useful. One that spans two modules has no single module, and naming either
 * would understate the authority it needs — the server evaluates a sentence per
 * pair, so the honest answer is that there are several.
 */
export const MANY_MODULES = "several";
export const MANY_SENSITIVITIES = "several";

function manyModules(references: readonly BiCatalogueMeasureRead[]): string {
  const modules = new Set(references.map((entry) => entry.module));
  return modules.size === 1 ? [...modules][0] : MANY_MODULES;
}

function manySensitivities(
  references: readonly BiCatalogueMeasureRead[],
): string {
  const values = new Set(references.map((entry) => entry.sensitivity));
  return values.size === 1 ? [...values][0] : MANY_SENSITIVITIES;
}

/** Why a certified formula is not on offer, in the reader's words. */
export function unusableSentence(reason: CalculatedUnusable): string {
  if (reason === "not_certified") {
    return "This formula has to be certified for the institution before it can be charted or put in a grid.";
  }
  if (reason === "mixed_time_behaviour") {
    return "This formula mixes a position on a date with a movement over a period, and those cannot be read together. Its author needs to change it.";
  }
  return "One of the figures this formula reads is not in the catalogue you were served, so it cannot be offered here.";
}

// ---------------------------------------------------------------------------
// The composer's own rules
// ---------------------------------------------------------------------------

export type MeasureDraft = Readonly<{
  measureKey: string;
  label: string;
  description: string;
  expression: string;
  valueType: BiMeasureRead["valueType"];
  favourableDirection: BiMeasureRead["favourableDirection"];
}>;

export const EMPTY_MEASURE_DRAFT: MeasureDraft = {
  measureKey: "",
  label: "",
  description: "",
  expression: "",
  valueType: "amount",
  favourableDirection: "neutral",
};

export function draftFromMeasure(measure: BiMeasureRead): MeasureDraft {
  return {
    measureKey: measure.measureKey,
    label: measure.label,
    description: measure.description,
    expression: measure.expression,
    valueType: measure.valueType,
    favourableDirection: measure.favourableDirection,
  };
}

/** What a measure's number IS (`bi_content.MEASURE_VALUE_TYPES`), in words. */
export const MEASURE_VALUE_TYPES = [
  { code: "amount", label: "An amount of money" },
  { code: "pct", label: "A percentage" },
  { code: "fraction", label: "A share, between nought and one" },
  { code: "index", label: "An index number" },
  { code: "duration_years", label: "A length of time in years" },
  { code: "count", label: "A count of things" },
] as const;

/** Which way is good for the institution (`MEASURE_FAVOURABLE_DIRECTIONS`). */
export const MEASURE_DIRECTIONS = [
  { code: "higher_better", label: "Higher is better" },
  { code: "lower_better", label: "Lower is better" },
  {
    code: "magnitude_lower_better",
    label: "Closer to nought is better, either way",
  },
  { code: "neutral", label: "Neither direction is better" },
] as const;

export function valueTypeLabel(code: string): string {
  return MEASURE_VALUE_TYPES.find((entry) => entry.code === code)?.label ?? code;
}

export function directionLabel(code: string): string {
  return MEASURE_DIRECTIONS.find((entry) => entry.code === code)?.label ?? code;
}

/**
 * Everything wrong with a draft that this client can decide WITHOUT a parser:
 * an empty field, a field past the length the column holds, a malformed id.
 *
 * Deliberately NOT "is the formula valid" — that is `validateBiMeasureExpression`
 * and nothing here has an opinion on it. `expressionReady` is the separate
 * question of whether the server has SAID it is valid, which is what the save
 * button waits on.
 */
export function draftProblems(
  draft: MeasureDraft,
  options: Readonly<{ keyEditable: boolean }>,
): string[] {
  const problems: string[] = [];
  if (options.keyEditable) {
    const key = draft.measureKey.trim();
    if (key.length === 0) {
      problems.push("Give the measure an id, so a formula and a chart can name it.");
    } else if (key.length > MEASURE_KEY_MAX_LENGTH) {
      problems.push(
        `An id can be up to ${MEASURE_KEY_MAX_LENGTH} characters long.`,
      );
    } else if (!MEASURE_KEY_PATTERN.test(key)) {
      problems.push(
        "An id starts with a lower-case letter and then uses lower-case letters, digits, underscores and dots — for example funding_cost_ratio.",
      );
    }
  }
  if (draft.label.trim().length === 0) {
    problems.push("Give the measure a name that will read well on a chart.");
  } else if (draft.label.length > MEASURE_LABEL_MAX_LENGTH) {
    problems.push(`A name can be up to ${MEASURE_LABEL_MAX_LENGTH} characters long.`);
  }
  if (draft.description.length > MEASURE_DESCRIPTION_MAX_LENGTH) {
    problems.push(
      `A description can be up to ${MEASURE_DESCRIPTION_MAX_LENGTH} characters long.`,
    );
  }
  if (draft.expression.trim().length === 0) {
    problems.push("Write the formula.");
  } else if (draft.expression.length > EXPRESSION_MAX_LENGTH) {
    problems.push(
      `A formula can be up to ${EXPRESSION_MAX_LENGTH} characters long.`,
    );
  }
  return problems;
}

/**
 * Whether the SERVER has said this exact text is valid.
 *
 * The verdict is tied to the text it was given, so an edit after a check makes the
 * verdict stale and the save waits for a new one. That is the whole reason the
 * check is not a boolean in component state.
 */
export function expressionReady(
  draft: MeasureDraft,
  verdict: Readonly<{ expression: string; read: BiMeasureValidationRead }> | null,
): boolean {
  if (verdict === null) return false;
  return verdict.expression === draft.expression && verdict.read.valid;
}

/** A reason string a mutation route requires, with the field's own bound. */
export function reasonProblem(reason: string, what: string): string | null {
  const trimmed = reason.trim();
  if (trimmed.length === 0) return `Say why, so the decision is on the record: ${what}`;
  if (reason.length > MEASURE_REASON_MAX_LENGTH) {
    return `A reason can be up to ${MEASURE_REASON_MAX_LENGTH} characters long.`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Which authority a question actually reads — for the coverage sentence
// ---------------------------------------------------------------------------

/**
 * The authorization addresses one question reads, for `coverageForFigures`.
 *
 * A BI query payload carries NO data scope even though the server resolves and
 * applies one, so the only honest source in the browser is the per-capability
 * scope on `/auth/me` — and that is addressed by (module, sensitivity), which is
 * exactly how a catalogue member is addressed too. This resolves the ids in a
 * question to those addresses.
 *
 * THE CALCULATED CASE IS THE WHOLE REASON THIS LIVES HERE. A formula is
 * authorized as the figures its text names, never as itself, and
 * `calculatedMeasureOffer` gives an entry spanning two modules the marker
 * `MANY_MODULES` rather than a module — which addresses nothing. So a calculated
 * id is expanded into the addresses of the published figures it references, which
 * is the same set the server evaluates a sentence for. A member that cannot be
 * resolved makes the list INCOMPLETE rather than shrinking it: a question read
 * from an address list missing one figure would be described by the coverage of
 * the others.
 *
 * A dimension is a read too — the BI authorization path evaluates every distinct
 * (module, sensitivity) across measures, dimensions AND filters, because a filter
 * can itself disclose — so dimensions and the fields filtered on are resolved the
 * same way.
 */
export function questionAddresses(
  asked: Readonly<{
    measures: readonly string[];
    dimensions: readonly string[];
    filterMembers: readonly string[];
  }>,
  published: readonly BiCatalogueMeasureRead[],
  dimensions: readonly BiCatalogueDimensionRead[],
  calculated: readonly BiMeasureRead[],
): FigureAddresses {
  const seen = new Map<string, ScopePair>();
  let complete = true;
  const add = (module: string, sensitivity: string): void => {
    seen.set(`${module}/${sensitivity}`, { module, sensitivity });
  };
  for (const id of asked.measures) {
    const member = published.find((entry) => entry.id === id);
    if (member) {
      add(member.module, member.sensitivity);
      continue;
    }
    const formula = calculated.find((entry) => entry.measureKey === id);
    if (!formula || formula.referencedMembers.length === 0) {
      complete = false;
      continue;
    }
    for (const referenced of formula.referencedMembers) {
      const figure = published.find((entry) => entry.id === referenced);
      if (!figure) {
        complete = false;
        continue;
      }
      add(figure.module, figure.sensitivity);
    }
  }
  for (const id of [...asked.dimensions, ...asked.filterMembers]) {
    const field = dimensions.find((entry) => entry.id === id);
    if (!field) {
      complete = false;
      continue;
    }
    add(field.module, field.sensitivity);
  }
  return { pairs: [...seen.values()], complete };
}

/**
 * The addresses of a whole DOCUMENT, from its views' own questions.
 *
 * Incompleteness is contagious on purpose: one view whose figure could not be
 * resolved makes the document's coverage undeterminable, because describing the
 * document by the coverage of the views that did resolve is exactly the fail-open
 * this is here to prevent.
 */
export function mergeAddresses(
  parts: readonly FigureAddresses[],
): FigureAddresses {
  const seen = new Map<string, ScopePair>();
  let complete = true;
  for (const part of parts) {
    if (!part.complete) complete = false;
    for (const pair of part.pairs) {
      seen.set(`${pair.module}/${pair.sensitivity}`, pair);
    }
  }
  return { pairs: [...seen.values()], complete };
}

