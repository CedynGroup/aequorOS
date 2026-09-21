/**
 * Production copy for every enum the P2 payloads carry, plus the fail-closed
 * formatters the risk & capital screens use.
 *
 * Two rules, both of which have cost this platform a defect before:
 *
 * NO RAW ENUM REACHES A READER (#203). `pct_total_rwa`, `not_material`,
 * `hhi_proportional_heuristic` are column values, not sentences. Every one is
 * mapped here; an unmapped value degrades to a readable form rather than
 * printing the token.
 *
 * ABSENCE IS STATED, NEVER ZEROED. A null amount is "Not modelled", a null
 * ratio is "Not assessed" — never `0`, which on screen is indistinguishable
 * from a measured zero and compares below every floor.
 *
 * There is no threshold, band edge, tolerance or floor in this file. Those
 * arrive on the payload (D-024) and are rendered by `ParameterProvenance` with
 * the citation and confirmation status that came with them.
 */

import { fmtCurrency, fmtInt, fmtPct } from "../../../lib/format";
import { numOrNull } from "../../../lib/api/values";
import type { StatusTone } from "@/components/ui/StatusPill";
import type {
  AppetiteDirection,
  IcaapAppetiteMetric,
  IcaapPillar2Item,
  IcaapRisk,
} from "@/lib/api/icaapRiskCapital";

/**
 * The vocabularies the SCREENS use, read off the adapter's view models rather
 * than re-declared. If the wire adds a RAG or a status, this breaks here — at
 * the mapping — instead of printing a raw token at the bottom of a table.
 */
export type IcaapAppetiteRag = NonNullable<
  IcaapAppetiteMetric["evaluation"]
>["rag"];
export type IcaapMaterialityVerdict = NonNullable<IcaapRisk["verdict"]>;
export type IcaapVerdictSource = IcaapRisk["verdictSource"];
export type IcaapPillar2ItemStatus = IcaapPillar2Item["methodStatus"];
import { AMOUNT_DECIMALS, PCT_DECIMALS } from "./display";

/** A figure the institution has not modelled. Never rendered as zero. */
export const NOT_MODELLED = "Not modelled";
/** A comparison that could not honestly be made. Never rendered as a pass. */
export const NOT_ASSESSED = "Not assessed";
/** A figure the platform does not hold. */
export const NOT_AVAILABLE = "Not available";

/**
 * D-036. When no regulatory floor is governed for a metric, this is the exact
 * sentence the capacity check reads. It is not an error and not a default.
 */
export const NO_REGULATORY_FLOOR = "not assessed against a regulatory floor";

/** D-039. Every representative or unconfirmed calibration carries this. */
export const PENDING_CONFIRMATION = "pending confirmation";
export const REPRESENTATIVE_CALIBRATION =
  "Representative calibration — not a published benchmark";

/** Turn an unmapped enum token into something a person can read. */
function humanise(token: string): string {
  const spaced = token.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// ---------------------------------------------------------------------------
// Fail-closed figure rendering
// ---------------------------------------------------------------------------

/**
 * An amount in the reporting currency, or the absence sentence.
 *
 * The currency argument is deliberately omitted: `fmtCurrency` reads the ACTIVE
 * jurisdiction that `BankContext` bound, which is this bank's own reporting
 * currency. Passing a second argument overrides that binding and is how eight
 * call sites elsewhere came to be pinned to one country's currency.
 */
export function fmtAmount(
  value: string | number | null | undefined,
  absent: string = NOT_MODELLED,
): string {
  const parsed = numOrNull(value);
  return parsed === null
    ? absent
    : fmtCurrency(parsed, undefined, {
        compact: false,
        decimals: AMOUNT_DECIMALS,
      });
}

/** A percentage, or the absence sentence. */
export function fmtPercent(
  value: string | number | null | undefined,
  absent: string = NOT_ASSESSED,
): string {
  const parsed = numOrNull(value);
  return parsed === null ? absent : fmtPct(parsed, PCT_DECIMALS);
}

/** A tally of things — rows, triggers, letters. Never a measurement. */
export function fmtCount(value: number | null | undefined): string {
  return value === null || value === undefined ? NOT_AVAILABLE : fmtInt(value);
}

/** A raw numeric score (a likelihood, an impact, a matrix score). */
export function fmtScore(
  value: number | null | undefined,
  absent: string = NOT_ASSESSED,
): string {
  return value === null || value === undefined ? absent : String(value);
}

/** A value in whatever unit the API declared for it, or the absence sentence. */
export function fmtInUnit(
  value: string | number | null | undefined,
  unit: string | null | undefined,
  absent: string = NOT_ASSESSED,
): string {
  const parsed = numOrNull(value);
  if (parsed === null) return absent;
  if (unit === "pct" || unit === "percent") return fmtPct(parsed, PCT_DECIMALS);
  if (unit === "amount" || unit === "currency") return fmtAmount(value, absent);
  const suffix = unit ? ` ${unit}` : "";
  return `${parsed}${suffix}`;
}

// ---------------------------------------------------------------------------
// Materiality
// ---------------------------------------------------------------------------

export function verdictLabel(
  verdict: IcaapMaterialityVerdict | null | undefined,
): string {
  if (verdict === "material") return "Material";
  if (verdict === "not_material") return "Not material";
  return NOT_ASSESSED;
}

/**
 * An unassessed verdict is NEVER green. "Nothing was decided" and "decided as
 * immaterial" are different answers and must look different.
 */
export function verdictTone(
  verdict: IcaapMaterialityVerdict | null | undefined,
): StatusTone {
  if (verdict === "material") return "critical";
  if (verdict === "not_material") return "success";
  return "pending";
}

export function verdictSourceLabel(
  source: IcaapVerdictSource | null | undefined,
): string | null {
  if (source === "override") return "Overridden by the bank";
  if (source === "matrix") return "From the matrix";
  return null;
}

/**
 * How a risk is quantified beyond Pillar 1.
 *
 * NOT a risk-row field: the wire carries no `pillar2Treatment`, it carries the
 * COMPONENTS a risk has in the Pillar 2 register, each with its own method and
 * status. A screen that offered a "treatment" select would be writing a field
 * the API does not accept, so the register shows the components instead.
 */
export function componentMethodSummary(
  components: readonly { methodLabel?: string | null; method?: string | null }[],
): string {
  const named = components
    .map((component) => component.methodLabel ?? component.method)
    .filter((label): label is string => Boolean(label));
  return named.length === 0 ? NOT_ASSESSED : named.join(", ");
}

// ---------------------------------------------------------------------------
// Appetite
// ---------------------------------------------------------------------------

/**
 * The ordering rule, in the platform's own words.
 *
 * The wire does not send a sentence for it — it enforces the rule and answers
 * 422 `appetite_ordering_invalid` with the violations. This is the standing
 * explanation shown above the metrics; it states no threshold, only the order
 * the three levels must sit in.
 */
export const ORDERING_RULE =
  "Appetite sits on the safe side of tolerance, and tolerance on the safe side " +
  "of capacity. Which side is safe depends on the metric.";

export function directionLabel(direction: AppetiteDirection): string {
  return direction === "higher_is_safer"
    ? "A higher value is safer"
    : "A lower value is safer";
}

/**
 * The ordering sentence for a metric's own direction.
 *
 * The server supplies the framework's own wording on the payload
 * (`orderingRule`) and its 422 `violations` are authoritative; this is the
 * per-metric restatement shown beside the scale so a preparer can see which way
 * round the four values must sit.
 */
export function orderingSentence(direction: AppetiteDirection): string {
  return direction === "higher_is_safer"
    ? "Appetite is at or above tolerance, and tolerance at or above capacity."
    : "Appetite is at or below tolerance, and tolerance at or below capacity.";
}

/**
 * `rag: "none"` is the contract's "not evaluated". It is rendered as an explicit
 * absence with a neutral tone — deliberately never green, because a green chip
 * on an unevaluated metric reads as a compliance affirmation.
 */
export function ragCopy(rag: IcaapAppetiteRag | null | undefined): {
  label: string;
  tone: StatusTone;
} {
  switch (rag) {
    case "green":
      return { label: "Within appetite", tone: "compliant" };
    case "amber":
      return { label: "Beyond appetite", tone: "approaching" };
    case "red":
      return { label: "Beyond tolerance", tone: "breach" };
    case "none":
    case null:
    case undefined:
    default:
      return { label: "Not evaluated", tone: "pending" };
  }
}

export function trendLabel(
  trend: string | null | undefined,
): { label: string; tone: StatusTone } {
  switch (trend) {
    case "improving":
      return { label: "Improving", tone: "success" };
    case "deteriorating":
      return { label: "Deteriorating", tone: "amber" };
    case "stable":
      return { label: "Stable", tone: "slate" };
    default:
      return { label: "Direction not known", tone: "pending" };
  }
}

// ---------------------------------------------------------------------------
// Pillar 2
// ---------------------------------------------------------------------------

export function itemStatusCopy(status: IcaapPillar2ItemStatus): {
  label: string;
  tone: StatusTone;
} {
  switch (status) {
    case "computed":
      return { label: "Computed", tone: "success" };
    case "interim_non_sf":
      return { label: "Computed — interim method", tone: "amber" };
    case "incomplete":
      return { label: "Incomplete — inputs missing", tone: "amber" };
    case "not_computable":
      return { label: "Cannot be computed here", tone: "critical" };
    case "not_capitalised":
      return { label: "Assessed as needing no capital", tone: "slate" };
    case "not_computed":
    default:
      return { label: "Not computed yet", tone: "pending" };
  }
}

/** Whether an approval is the current, live one for this item's revision. */
export function approvalCopy(current: boolean): { label: string; tone: StatusTone } {
  return current
    ? { label: "Approved", tone: "success" }
    : { label: "Not approved on this revision", tone: "pending" };
}

/**
 * How a Pillar 2 amount is expressed. The basis is DECLARED on every item
 * (B4/D-009) precisely so a percentage of RWA can never be read as an amount.
 */
export function basisLabel(basis: string | null | undefined): string {
  switch (basis) {
    case "pct_total_rwa":
      return "% of total risk-weighted assets";
    case "pct_credit_rwa":
      return "% of credit risk-weighted assets";
    case "pct_pillar1_credit_capital":
      return "% of Pillar 1 credit capital";
    case "absolute":
      return "Amount in the reporting currency";
    case null:
    case undefined:
      return NOT_AVAILABLE;
    default:
      return humanise(basis);
  }
}

export function consistencyCopy(
  status: "consistent" | "inconsistent" | "not_comparable" | "both_absent",
): { label: string; tone: StatusTone } {
  switch (status) {
    case "consistent":
      return { label: "Agrees", tone: "compliant" };
    case "inconsistent":
      return { label: "Differs — explain", tone: "breach" };
    case "both_absent":
      return { label: "Neither figure is available", tone: "pending" };
    case "not_comparable":
    default:
      return { label: "Cannot be compared", tone: "pending" };
  }
}

export function tierLabel(tier: string | null | undefined): string {
  switch (tier) {
    case "cet1":
      return "Common Equity Tier 1";
    case "at1":
      return "Additional Tier 1";
    case "tier2":
      return "Tier 2";
    case "deduction":
      return "Regulatory deduction";
    case "other":
      return "Other capital";
    case null:
    case undefined:
      return NOT_AVAILABLE;
    default:
      return humanise(tier);
  }
}

/**
 * Why a Pillar 2 item cannot be computed yet, in the preparer's language.
 *
 * The server's own sentence is preferred whenever it sends one; this maps the
 * typed codes it may send instead, so the screen never prints `not_computable`.
 */
export function notComputableCopy(reason: string | null | undefined): string {
  if (!reason) return "";
  switch (reason) {
    case "not_computable":
    case "inputs_unbound":
      return "The figures this method needs are not linked to the cycle yet. Link them on the Sections tab, then compute.";
    case "method_basis_unsupported":
      return "This method cannot produce a consolidated figure. Record the amount manually with evidence instead.";
    case "denominator_missing":
      return "The denominator this basis divides by is not available for the cycle date.";
    default:
      return reason;
  }
}

// ---------------------------------------------------------------------------
// Review
// ---------------------------------------------------------------------------

export function reviewStatusCopy(
  status: "draft" | "finalised" | "superseded",
): { label: string; tone: StatusTone } {
  switch (status) {
    case "finalised":
      return { label: "Finalised", tone: "success" };
    case "superseded":
      return { label: "Superseded", tone: "slate" };
    case "draft":
    default:
      return { label: "Draft", tone: "pending" };
  }
}

/** The committee a challenge was raised in, as a name rather than a token. */
export function forumLabel(forum: string): string {
  switch (forum) {
    case "board":
      return "Board";
    case "board_risk_committee":
      return "Board Risk Committee";
    case "board_audit_committee":
      return "Board Audit Committee";
    case "senior_management":
      return "Senior management";
    case "chief_risk_officer":
      return "Chief Risk Officer";
    case "internal_audit":
      return "Internal audit";
    default:
      return humanise(forum);
  }
}

/** The reviewer's overall opinion, as a sentence rather than an enum token. */
export function reviewOpinionLabel(opinion: string): string {
  switch (opinion) {
    case "satisfactory":
      return "Satisfactory";
    case "satisfactory_with_findings":
      return "Satisfactory, with findings";
    case "needs_improvement":
      return "Needs improvement";
    case "unsatisfactory":
      return "Unsatisfactory";
    default:
      return humanise(opinion);
  }
}

/**
 * The service's 409 when the person recording an independent review helped
 * prepare the ICAAP. Shown as the reason, not as a generic failure.
 */
export const REVIEWER_NOT_INDEPENDENT =
  "You prepared parts of this ICAAP, so you cannot record its independent review. " +
  "An independent reviewer is someone who took no part in preparing it.";

// ---------------------------------------------------------------------------
// Capital-plan triggers
// ---------------------------------------------------------------------------

/**
 * What a trigger's status means to the person who has to act on it.
 *
 * "Cannot be evaluated" is NEVER shown as clear. A trigger the platform could
 * not measure is a promise to the Board that nobody is checking, which is a
 * finding in its own right — not a pass.
 */
export function triggerStatusCopy(status: string | null | undefined): {
  label: string;
  tone: StatusTone;
} {
  switch (status) {
    case "clear":
      return { label: "Above the trigger levels", tone: "compliant" };
    case "early_warning":
      return { label: "Early-warning level reached", tone: "approaching" };
    case "action":
      return { label: "Action level reached", tone: "breach" };
    case "regulatory_breach":
      return { label: "Below the regulatory minimum", tone: "critical" };
    case "not_evaluable":
    default:
      return { label: "Cannot be evaluated", tone: "pending" };
  }
}

/** The ratio a trigger is set on, named rather than coded. */
export function triggerMetricLabel(
  metricKey: string | null | undefined,
  metricCode: string,
): string {
  switch (metricKey) {
    case "car":
      return "Total capital ratio";
    case "cet1":
      return "Common Equity Tier 1 ratio";
    case "tier1":
      return "Tier 1 ratio";
    case "leverage":
      return "Leverage ratio";
    default:
      // The capital plan stores the metric as free text, so an unrecognised one
      // is shown as the plan wrote it — the preparer needs to see which row of
      // their own framework could not be matched.
      return humanise(metricCode);
  }
}

/**
 * The findings the trigger evaluation can raise, each a sentence about the
 * framework itself rather than about this quarter's position.
 */
export function triggerFindingCopy(code: string): string {
  switch (code) {
    case "trigger_metric_unknown":
      return "The capital plan names a ratio the platform does not recognise, so this trigger is not evaluated. Rename it to one of the ratios the plan projects.";
    case "ordering_inconsistent":
      return "The early-warning level does not fire before the action level, so the plan would act before it warns.";
    case "action_weaker_than_floor":
      return "The action level sits on the wrong side of the regulatory minimum, so the plan would only act once the institution is already in breach.";
    case "early_warning_weaker_than_floor":
      return "The early-warning level sits on the wrong side of the regulatory minimum, so it cannot give warning before a breach.";
    default:
      return humanise(code);
  }
}

/** A scenario's own name, as the capital plan recorded it. */
export function scenarioLabel(code: string): string {
  return humanise(code);
}

// ---------------------------------------------------------------------------
// Capital allocation
// ---------------------------------------------------------------------------

export function unitKindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case "business_line":
      return "Business line";
    case "legal_entity":
      return "Legal entity";
    case "risk_type":
      return "Risk type";
    case null:
    case undefined:
      return NOT_AVAILABLE;
    default:
      return humanise(kind);
  }
}

/**
 * How a unit's share of a requirement line was decided.
 *
 * The distinction matters to a reviewer: a share the platform derived from
 * risk-weighted assets is evidence, a share somebody typed is a judgement.
 */
export function driverKindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case "rwa_share":
      return "Share of risk-weighted assets";
    case "exposure_share":
      return "Share of exposure";
    case "manual_pct":
      return "Stated percentage";
    case null:
    case undefined:
      return NOT_AVAILABLE;
    default:
      return humanise(kind);
  }
}

/** The one-line explanation of what a driver value means for that kind. */
export function driverHint(kind: string): string {
  return kind === "manual_pct"
    ? "A percentage of the line, stated by the institution. The shares on a line are expected to add to the whole."
    : "A weight. Each unit receives the line in proportion to its weight, and the parts add back to the line exactly.";
}

// ---------------------------------------------------------------------------
// Supervisory add-ons
// ---------------------------------------------------------------------------

export function addonStatusCopy(status: string | null | undefined): {
  label: string;
  tone: StatusTone;
} {
  switch (status) {
    case "active":
      return { label: "In force", tone: "critical" };
    case "superseded":
      return { label: "Superseded by a later letter", tone: "slate" };
    case "withdrawn":
      return { label: "Withdrawn", tone: "slate" };
    case "draft":
    default:
      // A draft is recorded but NOT in force: a second person has to confirm it
      // against the letter before it adds capital.
      return { label: "Recorded — awaiting confirmation", tone: "pending" };
  }
}

/** Which basis of consolidation a letter applies to. */
export function appliesToBasisLabel(basis: string | null | undefined): string {
  switch (basis) {
    case "solo":
      return "The institution alone";
    case "consolidated":
      return "The consolidated group";
    case "both":
      return "Both the institution and the group";
    case null:
    case undefined:
      return NOT_AVAILABLE;
    default:
      return humanise(basis);
  }
}

/** The standing statement on the add-ons panel; it is never a disclosure. */
export const ADDON_NEVER_PUBLIC =
  "A capital add-on imposed by the supervisor is never published. It is held " +
  "for the institution's own assessment and for the supervisor, and it is " +
  "excluded from every public disclosure the platform produces.";

// ---------------------------------------------------------------------------
// Pillar 2 item revisions
// ---------------------------------------------------------------------------

/** What happened at a revision, in the preparer's language. */
export function revisionKindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case "created":
      return "Item added";
    case "edited":
      return "Details changed";
    case "computed":
      return "Amount computed";
    case "retired":
      return "Item retired";
    case "recorded":
    case null:
    case undefined:
      return "Change recorded";
    default:
      return humanise(kind);
  }
}

// ---------------------------------------------------------------------------
// Governed parameters
// ---------------------------------------------------------------------------

/**
 * What a parameter is used for, where the payload says. The roles are the
 * server's own words; this only tidies the token.
 */
export function parameterRoleLabel(role: string): string {
  return humanise(role);
}

/**
 * The sentence shown for a code the control plane governs no value for.
 *
 * D-024 §4: there is never a code fallback, so the screen states the absence
 * and names the code an administrator has to add.
 */
export function missingParameterSentence(codes: readonly string[]): string {
  return codes.length === 1
    ? `No governed value is configured for ${codes[0]}. Calculations that need it will refuse until it is set in the control plane.`
    : `No governed value is configured for ${codes.join(", ")}. Calculations that need them will refuse until they are set in the control plane.`;
}

// ---------------------------------------------------------------------------
// The P5 methods: the IRRBB standardised framework, and the granularity
// adjustment
// ---------------------------------------------------------------------------

/**
 * The two interest-rate methods a Pillar 2 register can use, by name.
 *
 * These are wire keys, declared here so the register can tell one from the
 * other without matching on a label the server may reword.
 */
export const METHOD_IRRBB_SF = "irrbb_standardised_framework";
export const METHOD_IRRBB_INTERIM = "irrbb_interim_delta_eve";
export const METHOD_GRANULARITY = "granularity_adjustment";

/**
 * What a preparer needs to know about a method BEYOND its name.
 *
 * The server already sends a title for every method, and that title is what
 * the row prints. This adds the one sentence a title cannot carry: what the
 * method reads, and what it does when it cannot read it. Only the methods
 * whose answer is non-obvious are listed; an unlisted method gets nothing,
 * which is better than a generic sentence that says nothing.
 */
export function methodNote(method: string | null | undefined): string | null {
  switch (method) {
    case METHOD_IRRBB_SF:
      return (
        "Computed from a sealed standardised framework run at the cycle's " +
        "reporting date. If no run exists, or the framework refused to measure " +
        "the book, this item reports no amount rather than a zero."
      );
    case METHOD_IRRBB_INTERIM:
      return (
        "The interim economic-value method. It is the correct basis until the " +
        "standardised framework's commencement date, which your supervisor " +
        "sets; readiness says so when that date applies to this cycle."
      );
    case METHOD_GRANULARITY:
      return (
        "Computed on the exposure book as an add-on for the concentration the " +
        "single-risk-factor model assumes away. It rests on calibrations that " +
        "are representative until confirmed, and refuses rather than estimating " +
        "when the book is too small to measure."
      );
    default:
      return null;
  }
}

/**
 * The IRRBB standardised framework, as the ICAAP sees it.
 *
 * Deliberately says NOTHING about whether the framework is already required.
 * The commencement date is a governed console row this payload does not carry,
 * and a screen that guessed either way would either alarm a preparer who is
 * doing the right thing or reassure one who is not. Readiness resolves the
 * date and states the answer; this points at it.
 */
export const IRRBB_SF_SUPERVISORY_ONLY =
  "The standardised framework's own figures are produced for supervisory " +
  "monitoring, not filed as a return. They reach a filing only here — as the " +
  "Pillar 2 interest-rate capital requirement.";

export const IRRBB_SF_INTERIM_IN_USE =
  "This cycle quantifies interest-rate risk with the interim economic-value " +
  "method. The standardised framework becomes required from a commencement " +
  "date set by your supervisor, and readiness states the position for this " +
  "cycle's reporting date.";

export const IRRBB_SF_IN_USE =
  "This cycle quantifies interest-rate risk with the standardised framework, " +
  "computed from a sealed run at its reporting date.";

export const IRRBB_SF_ABSENT =
  "This cycle has no interest-rate item in the Pillar 2 register yet, so " +
  "neither the standardised framework nor the interim method is in use.";

export const IRRBB_SF_OPEN_WORKSPACE = "Open the standardised framework";

/**
 * The register finding that carries a framework-declared method mandate.
 *
 * A wire key, not a label. The finding's `statement` is the SERVER's sentence,
 * resolved from the governed commencement row against the framework's own
 * declaration — so the card prints whether the framework is required for this
 * cycle's reporting date rather than composing an answer from a date it was
 * handed. It is absent where the framework declares no mandate at all, which
 * is the honest state for a regime that names none.
 */
export const MANDATE_FINDING_CODE = "pillar2_method_mandate";

/** Said where the mandate is stated, so nobody reads it as a filing figure. */
export const IRRBB_SF_MANDATE_IS_GOVERNED =
  "This commencement date is a supervisory setting held in the platform's " +
  "control plane, not a figure in this assessment.";

/**
 * Shown when the payload carries no mandate statement.
 *
 * FAIL-CLOSED, and this is the whole reason the sentence is worded this way.
 * Two different situations produce no statement — a regime that declares no
 * commencement rule at all, and a payload that simply did not answer — and the
 * wire cannot tell them apart. Neither may be printed as "not required", which
 * is the reassurance a preparer would act on by filing under the interim
 * method. So the card says the position is not stated here and names where it
 * is resolved.
 */
export const IRRBB_SF_MANDATE_NOT_STATED =
  "This assessment does not state whether the standardised framework applies " +
  "to this reporting date. The preparation checklist resolves the " +
  "commencement date your supervisor set and says where this cycle stands.";
