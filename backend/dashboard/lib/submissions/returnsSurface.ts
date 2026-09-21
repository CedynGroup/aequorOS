/**
 * What the Returns workspace shows THIS officer, and the one act it offers them.
 *
 * The rule (docs/filing_workflow_redesign.md §4b.1) has two halves and the old
 * screen got both wrong by rendering everything disabled:
 *
 *  - **Absent** when the surface is not this role's at all. A preparer never
 *    sees the regulator's channel, the downtime bundle, the submission trail or
 *    the resubmission workflow. Not greyed — gone.
 *  - **Disabled, with the reason** when the control IS this role's and it is
 *    simply not their turn yet, or a precondition is unmet.
 *
 * Both halves are decided here, purely, so they can be tested without a browser
 * and so a new surface cannot quietly acquire a control by being added to the
 * page.
 */

import type { PackageStatus } from "@aequoros/risk-service-api";
import type { FilingAuthority } from "./filingAuthority";

/** The part of the chain an officer holds. */
export type FilingStageKey = "prepare" | "approve" | "transmit";

/** Sections the workspace can show. Order is the order they are laid out. */
export type SurfaceSection =
  /** The machine checks and their findings. */
  | "checks"
  /** Certification: who has signed, who must, and the ceremony. */
  | "certification"
  /** The regulator's channel: transmit, poll, downtime bundle. */
  | "transmission"
  /** The submission trail — channel interactions, in order. */
  | "events"
  /** Asking the regulator to accept a correction. */
  | "resubmission"
  /** Superseded versions of this return and reporting date. */
  | "versions";

export type PrimaryActionKind =
  | "generate"
  | "regenerate"
  | "certify"
  | "review"
  | "transmit"
  | "reupload"
  | "poll";

export type PrimaryAction = {
  kind: PrimaryActionKind;
  /** The button's words. */
  label: string;
  /** One line under it saying what pressing it does. Always present. */
  caption: string;
  enabled: boolean;
  /** Why it is disabled. Never null when `enabled` is false. */
  reason: string | null;
};

export type SurfaceInput = {
  authority: FilingAuthority;
  status: PackageStatus;
  /** A package exists for this return and reporting date. */
  hasPackage: boolean;
  /** Every check passed with no error findings outstanding. */
  checksClean: boolean;
  /** How many checks are failing, for the disabled reason. */
  checkErrors: number;
  /** The attestation service's own verdict — never re-derived from the policy. */
  clearedToSubmit: boolean;
  /** What is still unsigned, already phrased ("1 approver signature"). */
  outstandingSummary: string | null;
  /** Signatures are required for this return under the policy in force. */
  signingRequired: boolean;
  /** This return is a practice run (D-068) and can never reach a regulator. */
  isRehearsal: boolean;
  /** Filed through the downtime bundle and awaiting re-upload to the portal. */
  pendingReupload: boolean;
  /** The channel can be asked for a decision. */
  canPoll: boolean;
  /** The regulator's name, for copy. Never a literal in this module. */
  regulatorName: string;
};

/** Which stage of the chain the return is sitting at. */
export function stageForStatus(status: PackageStatus): FilingStageKey | "regulator" | "closed" {
  switch (status) {
    case "draft":
    case "generated":
    case "validated":
    case "rejected":
      return "prepare";
    case "pending_approval":
      return "approve";
    case "approved":
      return "transmit";
    case "submitted":
      return "regulator";
    case "acknowledged":
    case "declined":
    case "superseded":
      return "closed";
    default:
      return "prepare";
  }
}

/** The stages this officer holds, in chain order. */
export function heldStages(authority: FilingAuthority): FilingStageKey[] {
  if (!authority.isResolved || authority.isReadOnly) return [];
  const held: FilingStageKey[] = [];
  if (authority.mayPrepare) held.push("prepare");
  if (authority.mayApprove) held.push("approve");
  if (authority.mayTransmit) held.push("transmit");
  return held;
}

const STAGE_ORDER: FilingStageKey[] = ["prepare", "approve", "transmit"];

const WAITING_ON: Record<FilingStageKey, string> = {
  prepare: "the preparer",
  approve: "the approver",
  transmit: "the validator",
};

/**
 * Which sections exist for this officer.
 *
 * The regulator's channel, its trail and the resubmission workflow are one
 * surface and they belong to one role. Nobody else sees any part of them.
 */
export function surfaceSections(input: SurfaceInput): SurfaceSection[] {
  const { authority } = input;
  if (!authority.isResolved || !input.hasPackage) return [];
  const sections: SurfaceSection[] = ["checks", "certification"];
  if (authority.mayTransmit) {
    sections.push("transmission", "events");
    if (
      input.status === "submitted" ||
      input.status === "acknowledged" ||
      input.status === "rejected" ||
      input.status === "declined"
    ) {
      sections.push("resubmission");
    }
  }
  sections.push("versions");
  return sections;
}

function preparerAction(input: SurfaceInput): PrimaryAction {
  if (input.status === "rejected") {
    return {
      kind: "regenerate",
      label: "Generate a corrected version",
      caption: `${input.regulatorName} returned this filing. A correction is a new version of the return, taken through the chain again — this one is kept unchanged as history.`,
      enabled: true,
      reason: null,
    };
  }
  if (!input.signingRequired) {
    return {
      kind: "certify",
      label: "Send for approval",
      caption:
        "Sends the return to a second officer for their decision. You cannot approve your own work.",
      enabled: input.status === "validated" && input.checksClean,
      reason:
        input.status === "validated" && input.checksClean
          ? null
          : input.checkErrors > 0
            ? `${input.checkErrors} ${input.checkErrors === 1 ? "check is" : "checks are"} still failing. Clear them below, then send.`
            : "The checks have not run cleanly against this version yet.",
    };
  }
  const blocked = !input.checksClean || input.status !== "validated";
  return {
    kind: "certify",
    // The same words as the certification card and the signing ceremony: one
    // act, one name, wherever it is offered from.
    label: "Certify and freeze",
    caption:
      "Freezes these figures and hands the return to whoever holds approval authority for this institution — you do not choose the person. Nothing can be regenerated for this reporting date until they decide.",
    enabled: !blocked,
    reason: !blocked
      ? null
      : input.checkErrors > 0
        ? `${input.checkErrors} ${input.checkErrors === 1 ? "check is" : "checks are"} still failing. Clear them below — figures with failing checks cannot be attested to.`
        : input.status === "generated" || input.status === "draft"
          ? "The checks have not run cleanly against this version yet."
          : "This version is past preparation — there is nothing left to certify on it.",
  };
}

function approverAction(input: SurfaceInput): PrimaryAction {
  const enabled = input.status === "pending_approval";
  const reason = enabled
    ? null
    : "Nothing has been sent to you on this return yet.";
  // The preparer's action already branches on whether a signature is required;
  // this one did not, so an installation with signing switched off offered
  // "Review and sign" for an act that takes no signature. Naming a ceremony
  // that will not happen is the same defect as the certification row claiming
  // a return was "fully signed" when nothing was signed.
  if (!input.signingRequired) {
    return {
      kind: "review",
      label: "Review and approve",
      caption:
        "Opens the return itself with the frozen figures on it. Signing is switched off here, so approving is the whole act; sending it back needs a comment.",
      enabled,
      reason,
    };
  }
  return {
    kind: "review",
    label: "Review and sign",
    caption:
      "Opens the return itself with the frozen figures on it. Approving and signing is one act; sending it back needs a comment.",
    enabled,
    reason,
  };
}

function transmitAction(input: SurfaceInput): PrimaryAction {
  if (input.pendingReupload) {
    return {
      kind: "reupload",
      label: "Re-upload to the portal",
      caption: `This return was sent by the downtime bundle. It is complete only once it has been re-uploaded to ${input.regulatorName}'s portal.`,
      enabled: !input.isRehearsal && input.clearedToSubmit,
      reason: input.isRehearsal
        ? "This is a practice run. Nothing here reaches a regulator."
        : input.clearedToSubmit
          ? null
          : `Not cleared to send — ${input.outstandingSummary ?? "the signatures could not be read"} outstanding.`,
    };
  }
  if (input.status === "submitted") {
    return {
      kind: "poll",
      label: `Check for ${input.regulatorName}'s decision`,
      caption: `Asks the channel whether ${input.regulatorName} has decided on this filing.`,
      enabled: input.canPoll,
      reason: input.canPoll
        ? null
        : "This filing was recorded outside the portal, so there is no channel to ask. Record the decision when it arrives.",
    };
  }
  const notYourTurn =
    input.status !== "approved"
      ? stageForStatus(input.status) === "closed"
        ? "This return has finished its chain — there is nothing left to file."
        : `Waiting on ${WAITING_ON[stageForStatus(input.status) as FilingStageKey] ?? "an earlier stage"}.`
      : null;
  return {
    kind: "transmit",
    label: `Approve and file with ${input.regulatorName}`,
    caption: `Records your approval as the final stage decision and sends the signed return to ${input.regulatorName}. This is the filing — it leaves the bank when you press it.`,
    enabled:
      input.status === "approved" && input.clearedToSubmit && !input.isRehearsal,
    reason:
      input.status !== "approved"
        ? notYourTurn
        : input.isRehearsal
          ? "This is a practice run. Nothing here reaches a regulator, by design."
          : input.clearedToSubmit
            ? null
            : `Not cleared to send — ${input.outstandingSummary ?? "the signatures could not be read"} outstanding.`,
  };
}

const ACTION_FOR: Record<FilingStageKey, (input: SurfaceInput) => PrimaryAction> = {
  prepare: preparerAction,
  approve: approverAction,
  transmit: transmitAction,
};

/**
 * The single act this officer may take on this return right now.
 *
 * Null means the screen offers them nothing: either they hold no stage of this
 * chain, or every stage they hold is behind the return. That is stated in words
 * rather than rendered as a row of dead buttons.
 */
export function primaryFilingAction(input: SurfaceInput): PrimaryAction | null {
  const { authority } = input;
  if (!authority.isResolved || authority.isReadOnly) return null;
  const held = heldStages(authority);
  if (held.length === 0) return null;

  if (!input.hasPackage) {
    return held.includes("prepare")
      ? {
          kind: "generate",
          label: "Generate the return",
          caption:
            "Builds an immutable version from the figures already computed for this reporting date. No engine is re-run.",
          enabled: true,
          reason: null,
        }
      : null;
  }

  const at = stageForStatus(input.status);
  if (at !== "closed" && at !== "regulator" && held.includes(at)) {
    return ACTION_FOR[at](input);
  }
  if (at === "regulator" && held.includes("transmit")) {
    return transmitAction(input);
  }

  // Not their turn. A stage they hold that is still AHEAD of the return is
  // theirs and is offered disabled, with the reason; a stage behind it is done
  // and nothing is offered.
  const position = at === "closed" ? STAGE_ORDER.length : STAGE_ORDER.indexOf(at as FilingStageKey);
  const ahead = held.find((stage) => STAGE_ORDER.indexOf(stage) > position);
  if (ahead) return ACTION_FOR[ahead](input);

  // A preparer whose return has come back to them is the one case where a stage
  // "behind" the chain is live again.
  if (input.status === "rejected" && held.includes("prepare")) {
    return preparerAction(input);
  }
  return null;
}

/** Why this officer is being offered nothing, said plainly. */
export function noActionExplanation(input: SurfaceInput): string {
  const { authority } = input;
  if (!authority.isResolved) {
    return "Checking what you are authorised to do on this return.";
  }
  if (authority.isReadOnly) {
    return "You are viewing this return as an examiner. Nothing on this screen can be changed.";
  }
  if (heldStages(authority).length === 0) {
    return "You can read this return. Preparing, approving and filing it are held by other officers.";
  }
  const at = stageForStatus(input.status);
  if (at === "closed") {
    return "This return has finished its chain. Nothing further is asked of anyone.";
  }
  if (at === "regulator") {
    return `This return is with ${input.regulatorName}. Nothing is asked of the bank until they decide.`;
  }
  return `Your part of this return is done. It is with ${WAITING_ON[at as FilingStageKey]}.`;
}
