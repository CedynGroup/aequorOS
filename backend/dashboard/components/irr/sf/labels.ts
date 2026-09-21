/**
 * Production copy for the IRRBB Standardised Framework screen.
 *
 * Every sentence a reader of that screen can meet which the server did not
 * write lives here, so it can be reviewed in one place and tested as copy
 * rather than as markup. The rules it is held to, each with a test next door:
 *
 *   - **no raw token reaches a reader.** Not a scenario code, not a deposit
 *     category, not an assumption marker, not a refusal code, not a run
 *     status. Where the server sends a label, the label wins and these are the
 *     fallbacks for when it does not;
 *   - **no digit.** A number in a sentence here would be a regulatory value
 *     written into display code (D-024). Every figure arrives on a payload and
 *     is formatted where it is rendered;
 *   - **no jurisdiction literal.** No currency code, no regulator name, no
 *     country. The active jurisdiction is bound into `lib/format.ts`;
 *   - **nothing unrecognised is given an affirming tone.** An unknown status
 *     is neutral, never a pass.
 *
 * Kept free of React so it runs under node in
 * `pnpm --filter @aequoros/dashboard test`.
 */

import type { SfMandate } from "../../../lib/api/irrbbSfNormalize";

// ---------------------------------------------------------------------------
// What these figures are — and what they are not
// ---------------------------------------------------------------------------

/**
 * THE SENTENCE THAT KEEPS THE SCREEN HONEST.
 *
 * The framework's figures are registered as supervisory monitoring, not as
 * filed figures: no return is generated from a framework run, and the only
 * route by which one of these numbers reaches a filing is the ICAAP Pillar 2
 * capital amount. A screen that showed an economic-value loss beside an
 * outlier verdict without saying so would read as a return, and a preparer
 * would look for a submit button that must never exist.
 */
export const SUPERVISORY_MONITORING =
  "These figures are produced for supervisory monitoring and internal use. " +
  "They are not a return the institution files. Where the framework feeds a " +
  "filing, it does so through the ICAAP Pillar 2 capital requirement.";

export const IMMUTABLE_RUN =
  "Each run is an immutable record of the book as it stood at the reporting " +
  "date. Running the framework again adds a run; it never edits one.";

// ---------------------------------------------------------------------------
// The mandate
// ---------------------------------------------------------------------------

export type SfMandateCopy = {
  heading: string;
  /** The server's own sentence, or the honest fallback when none arrived. */
  body: string;
  /** Said only when the governed commencement date is not yet confirmed. */
  qualifier: string | null;
  tone: "action" | "amber" | "slate";
};

/**
 * WHEN THE RULE COULD NOT BE READ, SAY SO.
 *
 * The dangerous failure is not "we do not know"; it is "not required", which
 * is what silence reads as. A preparer who believes the framework is optional
 * files under the interim method, and the freeze then refuses at the worst
 * possible moment.
 */
export const MANDATE_UNREADABLE =
  "Whether the standardised framework is required for this reporting date " +
  "could not be read from this result. Treat it as unresolved rather than as " +
  "not required, and check the commencement date with your supervisor.";

/**
 * A PENDING COMMENCEMENT DATE IS A PROPOSAL, NOT A RULE.
 *
 * The date is a governed console row seeded pending confirmation (D-039).
 * Printing the server's sentence without this qualifier would present a
 * proposal as settled — and the whole point of the confirmation status is that
 * the platform must not do that.
 */
export const MANDATE_PENDING =
  "This commencement date is pending confirmation with the supervisor. It may " +
  "change before it takes effect.";

export const MANDATE_HEADING_REQUIRED =
  "The standardised framework applies to this reporting date";
export const MANDATE_HEADING_NOT_YET =
  "The standardised framework is not yet required for this reporting date";
export const MANDATE_HEADING_UNKNOWN = "The commencement rule could not be read";

/**
 * Before the commencement date the interim method is the correct one — this
 * screen must not imply a preparer is doing something wrong by using it.
 */
export const INTERIM_STILL_VALID =
  "Until then, the interim economic-value method remains the correct basis for " +
  "the ICAAP Pillar 2 interest-rate requirement. Running the framework now is " +
  "preparation, not a correction.";

/** From the commencement date the interim method is superseded. */
export const INTERIM_SUPERSEDED =
  "From this date the interim economic-value method no longer satisfies the " +
  "ICAAP Pillar 2 interest-rate requirement, and a report that still uses it " +
  "cannot be frozen.";

export function mandateCopy(mandate: SfMandate): SfMandateCopy {
  if (!mandate.stated) {
    return {
      heading: MANDATE_HEADING_UNKNOWN,
      body: MANDATE_UNREADABLE,
      qualifier: null,
      tone: "amber",
    };
  }
  const qualifier = mandate.settled ? null : MANDATE_PENDING;
  if (mandate.mandatory) {
    return {
      heading: MANDATE_HEADING_REQUIRED,
      body: `${mandate.statement ?? ""} ${INTERIM_SUPERSEDED}`.trim(),
      qualifier,
      tone: "action",
    };
  }
  return {
    heading: MANDATE_HEADING_NOT_YET,
    body: `${mandate.statement ?? ""} ${INTERIM_STILL_VALID}`.trim(),
    qualifier,
    tone: "slate",
  };
}

// ---------------------------------------------------------------------------
// The options refusal — one name, one sentence, never a zero
// ---------------------------------------------------------------------------

/**
 * The engine's ONE refusal name (D-061), and the sentence a reader gets.
 *
 * It is deliberately the same explanation the ICAAP register prints, because
 * an examiner reading the run row against the ICAAP report must not meet two
 * accounts of one condition.
 *
 * THE ABSOLUTE RULE THIS ENCODES: a refusal is a refusal. The screen says the
 * framework could not value the book's option positions — it never shows an
 * economic-value loss with those positions quietly left out, and it never
 * shows a blank where a measure would be. An understated loss is worse than no
 * loss at all, because it reads as a measurement.
 */
export const SF_OPTIONS_UNSUPPORTED = "irrbb_sf_options_unsupported";

const REFUSAL_COPY: Record<string, string> = {
  [SF_OPTIONS_UNSUPPORTED]:
    "The banking book holds interest-rate options, and the framework's " +
    "standardised add-on for valuing them is not available on this platform " +
    "yet. The measure is refused rather than reported without those positions, " +
    "which would understate it.",
};

export const REFUSAL_HEADING = "The framework could not measure this book";

/** What a preparer does about it. Not a reassurance — a next step. */
export const REFUSAL_NEXT_STEP =
  "Quantify this interest-rate risk another way, or agree the treatment of " +
  "those positions with your supervisor. No economic-value measure is shown " +
  "for this reporting date, because one that omitted those positions would " +
  "understate the risk.";

/**
 * An unrecognised refusal code is still a refusal.
 *
 * It falls back to the server's own message, and to a truthful sentence when
 * there is no message either — never to a raw token, and never to silence.
 */
export function refusalSentence(
  code: string | null,
  message: string | null,
): string {
  const known = code === null ? undefined : REFUSAL_COPY[code];
  if (known) return known;
  const sent = message === null ? "" : message.trim();
  if (sent !== "") return sent;
  return "The framework refused this measurement and did not say why.";
}

// ---------------------------------------------------------------------------
// Provenance of the governed inputs
// ---------------------------------------------------------------------------

export const REPRESENTATIVE_CHIP = "Representative calibration";
export const REPRESENTATIVE_MEANING =
  "A representative calibration is the platform's own working value, not a " +
  "published supervisory one. Confirm it with your supervisor before a figure " +
  "resting on it is relied on.";

export const PENDING_CHIP = "Pending confirmation";
export const PENDING_MEANING =
  "This governed value has been proposed but not confirmed with the supervisor.";

export const CONFIRMED_CHIP = "Confirmed";

export const PARAMETERS_HEADING = "Governed inputs behind these figures";
export const PARAMETERS_EMPTY =
  "This result records no governed inputs. Without them the figures cannot be " +
  "traced to the values that produced them.";

// ---------------------------------------------------------------------------
// Assumption and exclusion tallies
// ---------------------------------------------------------------------------

export const ASSUMPTIONS_HEADING = "Modelling defaults applied";

/**
 * WHY THE COUNT IS ON THE SCREEN AND NOT IN A BADGE.
 *
 * A default applied to one small placement and the same default applied across
 * most of the book are completely different exposures, and a badge saying
 * "defaults applied" cannot tell them apart. So each default is listed with
 * how many positions it was applied to.
 */
export const ASSUMPTIONS_MEANING =
  "Where the book did not carry a term the framework needs, a modelling " +
  "default was applied. How many positions each default covered is shown " +
  "beside it, because a default applied across most of the book is a different " +
  "exposure from one applied to a single placement.";

export const ASSUMPTIONS_NONE =
  "No modelling defaults were applied — every position carried the terms the " +
  "framework reads.";

export const EXCLUSIONS_HEADING = "Positions the framework did not measure";
export const EXCLUSIONS_MEANING =
  "These positions were excluded by rule rather than measured as zero, and " +
  "their value is shown so the size of what is missing is visible.";
export const EXCLUSIONS_NONE =
  "No positions were excluded — every position in scope was measured.";

/** "applied to N positions", with the count supplied by the caller. */
export const APPLIED_TO = "applied to";
export const POSITIONS = "positions";
export const COUNT_NOT_REPORTED = "count not reported";

// ---------------------------------------------------------------------------
// Verdicts, measures and absences
// ---------------------------------------------------------------------------

/** Every absent figure prints this. Never a dash that could read as zero. */
export const NOT_REPORTED = "Not reported";
export const NOT_ASSESSED = "Not assessed";

export const OUTLIER_HEADING = "Outlier test";
export const OUTLIER_ABOVE =
  "The economic-value risk measure is at or above the supervisory outlier " +
  "threshold for this institution.";
export const OUTLIER_BELOW =
  "The economic-value risk measure is below the supervisory outlier threshold " +
  "for this institution.";

/**
 * A verdict needs BOTH sides. A measure compared with a missing threshold is a
 * pass nobody granted — the same fail-open the platform's floor guard exists to
 * prevent.
 */
export const OUTLIER_NOT_ASSESSABLE =
  "The outlier test cannot be stated: either the risk measure or the governed " +
  "threshold is missing from this result, and a measure judged against a " +
  "threshold that is not there would be a pass nobody granted.";

export function outlierSentence(
  assessable: boolean,
  outlier: boolean,
): string {
  if (!assessable) return OUTLIER_NOT_ASSESSABLE;
  return outlier ? OUTLIER_ABOVE : OUTLIER_BELOW;
}

export function outlierTone(
  assessable: boolean,
  outlier: boolean,
): "breach" | "compliant" | "slate" {
  if (!assessable) return "slate";
  return outlier ? "breach" : "compliant";
}

/** Fallbacks for a labelled code the server did not label. Never the code. */
export const UNNAMED_SCENARIO = "Unnamed scenario";
export const UNNAMED_MEASURE = "Unnamed measure set";
export const UNNAMED_CATEGORY = "Unnamed deposit category";
export const UNNAMED_BUCKET = "Unnamed time bucket";
export const UNNAMED_DEFAULT = "Unnamed modelling default";
export const UNNAMED_LINE = "Unnamed disclosure line";

/**
 * A labelled code, or the fallback. Never the raw code.
 *
 * Printing the code would leak an internal vocabulary to a reader and would
 * hide the fact that the server failed to label it — which is a defect
 * somebody should see as a defect.
 */
export function labelled(label: string | null, fallback: string): string {
  return label === null || label.trim() === "" ? fallback : label;
}

// ---------------------------------------------------------------------------
// Run status
// ---------------------------------------------------------------------------

const RUN_STATUS_COPY: Record<string, { label: string; tone: "compliant" | "amber" | "breach" | "slate" }> = {
  succeeded: { label: "Completed", tone: "compliant" },
  failed: { label: "Refused", tone: "breach" },
  running: { label: "Running", tone: "amber" },
  queued: { label: "Queued", tone: "slate" },
};

/** An unrecognised status is neutral — never a pass. */
export function runStatusCopy(status: string | null): {
  label: string;
  tone: "compliant" | "amber" | "breach" | "slate";
} {
  const known = status === null ? undefined : RUN_STATUS_COPY[status];
  return known ?? { label: "Status not recognised", tone: "slate" };
}

// ---------------------------------------------------------------------------
// Section headings and standing explanations
// ---------------------------------------------------------------------------

export const PAGE_TITLE = "Standardised Framework";
export const PAGE_EYEBROW = "IRRBB";

export const RUN_ACTION = "Run the standardised framework";
export const RUN_ACTION_PENDING = "Running…";
export const RUN_FAILED =
  "The standardised framework could not be run for this reporting date.";
export const RUN_NEEDS_PERIOD =
  "Choose a reporting date before running the framework.";
export const RUN_NEEDS_AUTHORITY =
  "Producing a standardised framework run is a confidential IRRBB action, and " +
  "your access does not include it. Ask an interest-rate risk approver to run it.";
export const NO_RESULT_ASK_COLLEAGUE =
  "Ask a colleague with confidential IRRBB access to run the framework for " +
  "this reporting date.";
export const NO_RESULT_RUN_IT =
  "Run the standardised framework to produce one for this reporting date.";

/**
 * The screen itself is gated on aggregated IRRBB view, so this is reached only
 * by a deep link. It says what is missing without naming the institution.
 */
export const VIEW_NEEDS_AUTHORITY_TITLE = "You do not have access to this view";
export const VIEW_NEEDS_AUTHORITY =
  "Reading the IRRBB standardised framework needs interest-rate risk access " +
  "for this institution. Ask an account administrator to grant it.";

export const MEASURES_HEADING = "Economic value risk measure";
export const MEASURES_MEANING =
  "The framework reports the largest economic-value loss across a prescribed " +
  "set of interest-rate shocks. Gains do not offset losses across currencies, " +
  "so the loss and the arithmetic net are shown side by side.";

export const SCENARIOS_HEADING = "Prescribed interest-rate shocks";
export const SCENARIOS_MEANING =
  "The framework prescribes the whole set of shocks and reports them together, " +
  "so none of them is a choice the institution makes.";

export const TABLE8_HEADING = "Disclosure grid";
export const TABLE8_MEANING =
  "The change in economic value and in net interest income under each " +
  "prescribed shock, beside the prior period, which is how the disclosure is " +
  "read.";

export const CURRENCIES_HEADING = "Currencies measured";
export const CURRENCIES_MEANING =
  "Each material currency is measured on its own curve and converted to the " +
  "reporting currency. The curve each one was measured on is named, with the " +
  "date it was taken.";
export const CURRENCIES_EXCLUDED = "Currencies below the materiality test:";

export const NMD_HEADING = "Non-maturity deposits";
export const NMD_MEANING =
  "Deposits with no contractual maturity are split into a core and a non-core " +
  "part, with the core part capped by the governed limit for its category. A " +
  "capped category is flagged, because the cap — not the institution's own " +
  "behavioural view — is then what decides the profile.";

export const LADDER_HEADING = "Repricing ladder";
export const LADDER_MEANING =
  "Principal and interest flows slotted into the framework's prescribed time " +
  "buckets, per currency.";

export const TABLE7_HEADING = "Repricing maturity of non-maturity deposits";

export const DATA_QUALITY_HEADING = "How complete this measurement is";
export const INSTRUMENTS_MEASURED = "Positions measured";

export const POST_SHOCK_FLOOR_HEADING = "Post-shock rates";
