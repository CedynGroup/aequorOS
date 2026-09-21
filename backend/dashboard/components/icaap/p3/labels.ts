/**
 * Production copy for the ICAAP review and filing screens.
 *
 * Everything here is what a Head of Risk, a Board member and a Bank of Ghana
 * examiner actually read. The rules, same as the P2 labels module:
 *
 *  1. **No raw enum ever reaches a reader.** `pending_primary_text`,
 *     `stage_moved`, `review_basis_changed`, `attachments_missing` and
 *     `board_resolution` are the API's vocabulary, not English. A token that
 *     arrives here without a translation is shown as a neutral sentence
 *     naming what happened, never as the token.
 *  2. **Fail closed in TONE as well as in logic.** An unrecognised state is
 *     never drawn as an approval, a pass, or a signature. `StatusPill`'s
 *     affirming tones (`success`, `compliant`) are reserved for states the
 *     server has positively asserted.
 *  3. **No digits, ever** (D-024). A sentence that states a count, a floor or
 *     a deadline would be a regulatory number written into display code; the
 *     count comes from the payload and is interpolated by the caller.
 *  4. **No jurisdiction literal.** No `'GHS'`, no `'BoG'`, no `'Ghana'` —
 *     `regShort()` and `centralBankName()` own those, and they are read from
 *     the institution's own jurisdiction.
 *
 * Kept free of React so it runs under node in the test suite.
 */

import type { StatusTone } from "@/components/ui/StatusPill";
import type {
  IcaapDisclosureStatus,
  IcaapSeverity,
  IcaapStageKind,
  IcaapStageState,
  IcaapTemplateStatus,
} from "@/lib/api/icaapFilingNormalize";

// ---------------------------------------------------------------------------
// THE REHEARSAL SENTENCE — the single most important copy in this workspace
// ---------------------------------------------------------------------------

/**
 * D-068 lets a rehearsal cycle freeze, sign and record a submission, into a
 * package that can never be filed. Five separate mechanisms stop a rehearsal
 * reaching a regulator; this sentence stops a person mistaking one for a
 * filing, which is the failure none of those mechanisms can catch.
 *
 * It is deliberately blunt and repeated on every surface that can show a
 * rehearsal — the header, the freeze confirmation, the signature card, the
 * submission card and the documents card — because the cost of it being read
 * once and forgotten is a bank believing it has filed.
 */
export const REHEARSAL_HEADLINE = "Practice run — this will never be filed";

/**
 * The same sentence, shortened to a marker.
 *
 * A rehearsal package reaches the GENERIC reporting surfaces too — History,
 * the approvals queue, the return workspace — where there is room for a pill
 * beside the return code and not for a paragraph. It is the same vocabulary
 * deliberately: a preparer who read "practice run" on the cycle and an
 * approver who reads it on a queue row must be reading about one thing.
 */
export const REHEARSAL_SHORT = "Practice run";

export const REHEARSAL_BODY =
  "This cycle is a practice run. It goes through every step of a real filing — " +
  "freezing the report, signing it and recording a submission — so that your " +
  "officers can rehearse those steps safely. Nothing here is sent to the " +
  "regulator, nothing here satisfies a filing obligation, and the report it " +
  "produces is marked as a practice run on every page. To file for real, start " +
  "an annual cycle instead.";

/** Shown on the freeze confirmation, where the act looks identical to a real one. */
export const REHEARSAL_FREEZE_WARNING =
  "You are sealing a practice run. It produces a report marked as a practice " +
  "run, which can be signed but can never be sent to the regulator and never " +
  "counts towards a filing deadline.";

/** Shown beside the recorded submission of a rehearsal. */
export const REHEARSAL_SUBMISSION_WARNING =
  "Recording a submission on a practice run is part of the rehearsal. Nothing " +
  "leaves the platform, and this does not discharge any filing obligation.";

// ---------------------------------------------------------------------------
// The exposure-draft reality (D-006), stated rather than papered over
// ---------------------------------------------------------------------------

/**
 * Most sections of the Ghana framework are still awaiting the regulator's own
 * published text. The workspace says so, readiness reports it, and the freeze
 * path refuses a real filing while it is true. This screen repeats it rather
 * than looking finished.
 *
 * The regulator is referred to generically ("the regulator") rather than by
 * name: this module carries no jurisdiction literal, and a caller that wants
 * the short name uses `regShort()`, which reads it from the institution.
 */
export const AWAITING_REGULATOR_TEXT_TITLE =
  "Parts of this framework are still an exposure draft";

export const AWAITING_REGULATOR_TEXT_BODY =
  "Several sections of this assessment are built from an exposure draft, and " +
  "the regulator's final text for them has not been published. A cycle can be " +
  "prepared, reviewed and rehearsed in full, but it cannot be frozen for a " +
  "real filing until that text arrives. The preparation checklist names every " +
  "section this affects.";

// ---------------------------------------------------------------------------
// Stages
// ---------------------------------------------------------------------------

const STAGE_KIND_COPY: Record<IcaapStageKind, string> = {
  prepare: "Preparation",
  review: "Review",
  approve: "Approval",
  attest: "Signature",
};

export function stageKindLabel(kind: IcaapStageKind): string {
  return STAGE_KIND_COPY[kind];
}

/** What this stage's officer is being asked to do, in one sentence. */
const STAGE_KIND_ASK: Record<IcaapStageKind, string> = {
  prepare: "Written and put forward by the preparers.",
  review: "Read and challenged, without approving it.",
  approve: "Approved as the institution's own assessment.",
  attest: "Signed on the report itself — no decision is recorded here.",
};

export function stageKindAsk(kind: IcaapStageKind): string {
  return STAGE_KIND_ASK[kind];
}

const STAGE_STATE_COPY: Record<IcaapStageState, { label: string; tone: StatusTone }> = {
  pending: { label: "Not reached", tone: "slate" },
  current: { label: "With this stage now", tone: "action" },
  done: { label: "Complete", tone: "success" },
  returned: { label: "Sent back", tone: "amber" },
};

export function stageStateCopy(state: IcaapStageState): {
  label: string;
  tone: StatusTone;
} {
  return STAGE_STATE_COPY[state];
}

/**
 * What a recorded decision says. The API's words are `submitted`, `reviewed`,
 * `approved`, `returned`, `frozen` and `attested`; none of them is a sentence.
 */
const DECISION_COPY: Record<string, { label: string; tone: StatusTone }> = {
  submitted: { label: "Put forward for review", tone: "action" },
  reviewed: { label: "Reviewed", tone: "success" },
  approved: { label: "Approved", tone: "success" },
  returned: { label: "Sent back for changes", tone: "amber" },
  frozen: { label: "Sealed for filing", tone: "action" },
  attested: { label: "Signed", tone: "success" },
  attested_by_resolution: {
    label: "Approved by resolution of the board",
    tone: "success",
  },
};

export function decisionCopy(decision: string): {
  label: string;
  tone: StatusTone;
} {
  // Fail closed in tone: something this build has never seen must not be drawn
  // as an approval.
  return DECISION_COPY[decision] ?? { label: "Decision recorded", tone: "slate" };
}

// ---------------------------------------------------------------------------
// Severity, and the blockers a preflight returns
// ---------------------------------------------------------------------------

const SEVERITY_COPY: Record<IcaapSeverity, { label: string; tone: StatusTone }> = {
  blocking: { label: "Must be resolved", tone: "critical" },
  warning: { label: "Worth checking", tone: "amber" },
  info: { label: "For information", tone: "slate" },
};

export function severityCopy(severity: IcaapSeverity): {
  label: string;
  tone: StatusTone;
} {
  return SEVERITY_COPY[severity];
}

/**
 * A short heading for the refusal codes the freeze and submission gates
 * return, so a list of blockers reads as a list of things to do.
 *
 * The server's own `message` is always shown underneath and is never replaced:
 * these are titles, not translations. A code with no entry simply has no
 * title, and the sentence stands alone — which is why nothing here invents a
 * reason.
 */
const BLOCKER_TITLES: Record<string, string> = {
  cycle_not_frozen: "The report has not been sealed yet",
  cycle_not_awaiting_freeze: "The approval stage has not been reached",
  rehearsal_not_fileable: "This is a practice run",
  framework_pending_primary_text: "The regulator's text is still outstanding",
  filing_not_available_for_framework: "This framework cannot be filed yet",
  framework_digest_mismatch: "The framework has changed since this cycle began",
  review_basis_changed: "The report changed after it was approved",
  annex_missing: "A supporting return is missing",
  annex_stale: "A supporting return is out of date",
  return_not_yet_effective: "This return does not yet apply",
  prior_filing_with_regulator: "An earlier version is already with the regulator",
  no_computed_position: "There are no computed figures for this date",
  filing_reconciliation: "The book does not reconcile",
  missing_parameter: "A governed value has not been set",
  maker_checker: "A different officer must do this",
  attachments_missing: "A required document is missing",
  signing_not_configured: "Signing is not set up on this deployment",
  signatures_outstanding: "The report is not fully signed",
  digest_changed: "The figures changed after they were signed",
  icaap_package_not_current: "This version has been superseded",
  cycle_locked: "The report is sealed and can no longer be edited",
  signing_order: "The officers must sign in order",
  channel_not_supported_for_return: "This return is recorded, not transmitted",
  external_ref_required: "A published address is required",
  uncommitted_changes: "There is unsaved writing in the report",
  stage_moved: "This stage has already moved on",
  stage_round_moved: "The report has been sent back since you opened this",
  officer_title_mismatch: "Your role is not one this stage accepts",
  revision_requires_a_filing: "The current version has not been filed",
};

export function blockerTitle(code: string): string | null {
  return BLOCKER_TITLES[code] ?? null;
}

// ---------------------------------------------------------------------------
// Signatures
// ---------------------------------------------------------------------------

const ROLE_COPY: Record<string, string> = {
  preparer: "Preparer",
  approver: "Approver",
  board: "Board",
};

export function roleLabel(role: string): string {
  return ROLE_COPY[role] ?? "Officer";
}

/**
 * Why a slot is not yet available to sign.
 *
 * The server sends the ROLE that must go first. The sentence says what the
 * ordering is for, because "blocked" on its own reads like a fault.
 */
export function slotBlockedSentence(blockedBy: string): string {
  return blockedBy === "board"
    ? `${roleLabel(blockedBy)} must sign before this signature can be added.`
    : `The ${roleLabel(blockedBy).toLowerCase()} signs first. This signature ` +
        "can be added once they have.";
}

export const BOARD_SIGNS_LAST =
  "The board signs last, over the document management approved.";

const ATTESTATION_STATE_COPY: Record<
  string,
  { label: string; tone: StatusTone }
> = {
  not_started: { label: "Not started", tone: "slate" },
  unsigned: { label: "Not signed", tone: "slate" },
  preparer_certified: { label: "Signed by the preparer", tone: "action" },
  pending_approver: { label: "Waiting for the approver", tone: "amber" },
  fully_certified: { label: "Fully signed", tone: "success" },
  voided: { label: "Signatures cancelled", tone: "amber" },
};

export function attestationStateCopy(state: string): {
  label: string;
  tone: StatusTone;
} {
  return (
    ATTESTATION_STATE_COPY[state] ?? { label: "Not signed", tone: "slate" }
  );
}

// ---------------------------------------------------------------------------
// Documents filed with the return
// ---------------------------------------------------------------------------

/**
 * When a document is needed. The regime distinguishes documents that must be
 * in hand before the report is sealed from those that accompany the filing.
 */
const GATE_COPY: Record<string, string> = {
  freeze: "Needed before the report is sealed",
  submission: "Needed before the report is filed",
  optional: "Optional",
};

export function gateLabel(gate: string): string {
  return GATE_COPY[gate] ?? "Needed before the report is filed";
}

/**
 * Where a document requirement comes from, and therefore who can change it.
 *
 * A family requirement is the regime's own: relaxing the signing policy does
 * not relax it (D-031). Saying which is the point — the two are answerable by
 * different people.
 */
export function requirementOriginSentence(
  origin: "family" | "signing_policy",
): string {
  return origin === "family"
    ? "Required by the regime itself. Changing who signs the report does not remove this."
    : "Required by this institution's own signing policy for this return.";
}

const ATTACHMENT_SOURCE_COPY: Record<string, string> = {
  package_upload: "Attached to the filing",
  icaap_cycle: "Carried over from the assessment",
};

export function attachmentSourceLabel(source: string): string {
  return ATTACHMENT_SOURCE_COPY[source] ?? "Attached to the filing";
}

// ---------------------------------------------------------------------------
// The bank's review chain (settings) and the public disclosure
// ---------------------------------------------------------------------------

const TEMPLATE_STATUS_COPY: Record<
  IcaapTemplateStatus,
  { label: string; tone: StatusTone }
> = {
  draft: { label: "Draft", tone: "slate" },
  pending_approval: { label: "Waiting for approval", tone: "amber" },
  approved: { label: "In force", tone: "success" },
  published: { label: "In force", tone: "success" },
  rejected: { label: "Not approved", tone: "critical" },
  superseded: { label: "Replaced", tone: "slate" },
};

export function templateStatusCopy(status: IcaapTemplateStatus): {
  label: string;
  tone: StatusTone;
} {
  return TEMPLATE_STATUS_COPY[status];
}

const DISCLOSURE_STATUS_COPY: Record<
  IcaapDisclosureStatus,
  { label: string; tone: StatusTone }
> = {
  draft: { label: "Draft", tone: "slate" },
  pending_approval: { label: "Waiting for approval", tone: "amber" },
  approved: { label: "Approved for publication", tone: "success" },
  published: { label: "Published", tone: "success" },
  rejected: { label: "Not approved", tone: "critical" },
  superseded: { label: "Replaced", tone: "slate" },
};

export function disclosureStatusCopy(status: IcaapDisclosureStatus): {
  label: string;
  tone: StatusTone;
} {
  return DISCLOSURE_STATUS_COPY[status];
}

export const DISCLOSURE_NEVER_PUBLIC =
  "Supervisory information is removed from anything published here. A figure " +
  "the supervisor gave the institution privately is never part of a public " +
  "disclosure, whichever sections are chosen.";

export const CHAIN_SOURCE_COPY: Record<string, string> = {
  framework_default: "the regime's own default chain",
  bank_template: "this institution's approved chain",
};

export function chainSourceSentence(source: string): string {
  return CHAIN_SOURCE_COPY[source] ?? CHAIN_SOURCE_COPY.framework_default;
}

// ---------------------------------------------------------------------------
// Composing a chain
// ---------------------------------------------------------------------------

/** The stage kinds, in the order a chain reads. */
export const STAGE_KIND_OPTIONS: readonly IcaapStageKind[] = [
  "prepare",
  "review",
  "approve",
  "attest",
];

export const STAGE_REFERENCE_HINT =
  "A short name this stage is recorded under, in lower case with underscores " +
  "instead of spaces. It appears in the audit trail and never changes once a " +
  "cycle has been put forward under this chain.";

export const STAGE_TAKEN_BY_HINT =
  "The officers or committees this stage belongs to, separated by commas — " +
  "for example: Chief Risk Officer, Board Risk Committee. Leave it empty if " +
  "the institution has not settled who takes it.";

export const STAGE_SEALS_HINT =
  "This is the approval that seals the report. Exactly one stage does it, and " +
  "it is the last thing that happens before the signature — the document " +
  "signed is the one sealing produced.";

/**
 * Said on the composer, because a maker should know where the answer comes
 * from before they press Save rather than after.
 */
export const BUILDER_HOW_IT_IS_DECIDED =
  "Whether these stages make a chain the platform can run is decided when you " +
  "save, by the same rules the regime's own chain is held to. If they do not, " +
  "nothing is saved and the reason is shown here in full.";

export const BUILDER_SAVE_IS_A_DRAFT =
  "Saving produces a draft. It governs nothing until a different officer " +
  "approves it, and an assessment already under review keeps the chain it was " +
  "put forward under.";

export const BUILDER_COMPOSE_TITLE = "Compose a review chain";
export const BUILDER_EDIT_TITLE = "Edit this proposed chain";

/** Offered where there is no proposal open, beside the chain in force. */
export const BUILDER_START_FROM_EFFECTIVE =
  "Start from the chain in force";

export const BUILDER_EDIT_DRAFT = "Edit the stages";

/** Why the compose action is absent while a proposal is open. */
export const BUILDER_ONE_AT_A_TIME =
  "One proposal at a time. Finish or reject the open one before composing " +
  "another.";

// ---------------------------------------------------------------------------
// Sentences the panels share
// ---------------------------------------------------------------------------

/** Shown where a freeze is offered, and the reason C-9 exists. */
export const FREEZE_RECORDS_YOU =
  "You will be recorded as the officer who produced this filing. You can then " +
  "sign it as the preparer, but not as the approver and not for the board.";

export const FREEZE_IS_FINAL =
  "Sealing the report fixes its text and its figures. After this, nothing in " +
  "the assessment can be edited — a correction means sending it back to a " +
  "review stage, which cancels any signatures already given.";

export const NOTHING_TO_FILE =
  "There is nothing to file yet. The report is sealed at the end of the " +
  "review chain, and the filing appears here once it is.";

export const NO_REVIEW_YET =
  "This assessment has not been put forward for review yet. The preparers " +
  "submit it when the preparation checklist is clear.";

/** A figure or file the reader may be about to look for. */
export const DOWNLOAD_WORKING_COPY_NOTE =
  "A working copy is for drafting and internal review. It is never filed and " +
  "never signed.";
