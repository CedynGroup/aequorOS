/**
 * The filing chain: where a return is, who holds it, what has been decided, and
 * what happens next (docs/filing_workflow_redesign.md §4b.4).
 *
 * This replaces the six-step lifecycle stepper, which showed statuses rather
 * than people — and showed one of them as "Validated", which is the machine
 * rules check reporting a clean run, not an officer signing anything. A bank
 * read that as "the Validator signed off". It meant neither that, nor at that
 * point in the process.
 *
 * The model is PURE so the copy can be tested without a browser, and so the
 * regulator's name arrives as data: no literal "BoG" or "Bank of Ghana" is
 * written here — the caller passes the active jurisdiction's own words.
 *
 * ### What this cannot answer yet, and says so instead of inventing
 *
 * The shared stage engine (§3, §4) is not built. Until it is:
 *
 *  - a stage has a **named holder only where the attestation recipients name
 *    one** — the bank's own routing. Elsewhere `holder` is null and the panel
 *    says the officer has not been named, never a guess;
 *  - a decision's **round** is the certification cycle the signature carries. A
 *    send-back voids the cycle and the next attempt is cycle + 1, so the count
 *    is real; a decision with no signature behind it reports no round rather
 *    than borrowing one;
 *  - a send-back has **no named target** on the wire. Today every send-back
 *    returns the package to the preparer — that is the only backward edge the
 *    lifecycle has — so the panel states it as the fact it is, and the field
 *    arrives with the engine.
 */

import type {
  AttestationStatusRead,
  PackageApprovalRead,
  PackageStatus,
  SubmissionEventRead,
} from "@aequoros/risk-service-api";

export type ChainStageKey = "prepare" | "approve" | "transmit" | "regulator";

export type ChainStageState =
  /** Reached and finished. */
  | "done"
  /** Where the return is right now. */
  | "current"
  /** Still to come. */
  | "ahead"
  /** Reached, and it came back from here. */
  | "returned"
  /** The chain ended before this stage was reached. */
  | "closed";

export type ChainStage = {
  key: ChainStageKey;
  /** The stage, named for the act — never for a status value. */
  title: string;
  /** Who does it, in the bank's language. */
  roleName: string;
  /** What that officer does, one clause, present tense. */
  act: string;
  state: ChainStageState;
  /** The officer the bank has named for this stage, when it has named one. */
  holder: string | null;
  holderTitle: string | null;
};

export type ChainEntryOutcome =
  | "sent_for_approval"
  | "approved"
  | "returned"
  | "filed"
  | "acknowledged"
  | "rejected"
  | "declined";

export type ChainEntry = {
  at: Date | null;
  /** The officer, named. Null means the record does not name them. */
  actorName: string | null;
  actorTitle: string | null;
  /** The role they acted in. */
  roleName: string;
  outcome: ChainEntryOutcome;
  /** The comment that accompanied the decision, where one was required. */
  comment: string | null;
  /** The round this decision belongs to, when the record carries one. */
  round: number | null;
  /** Where a send-back sent it. */
  returnedTo: string | null;
};

export type FilingChain = {
  /** The current round, counted in certification cycles. Null when unreadable. */
  round: number | null;
  /** "With the approver" — the headline, four words or fewer. */
  position: string;
  /** The same fact as a sentence, naming the holder when the bank named one. */
  positionDetail: string;
  /** What happens next, stated in words before it is offered as a button. */
  next: string | null;
  stages: ChainStage[];
  /** Every decision, oldest first. */
  entries: ChainEntry[];
};

/** The regulator's own words, supplied by the active jurisdiction. */
export type RegulatorNaming = {
  /** e.g. "BoG" — the short form used mid-sentence. */
  short: string;
  /** e.g. "Bank of Ghana" — the full name, used where it reads better. */
  full: string;
  /** e.g. "ORASS" — the submission portal, when the jurisdiction has one. */
  portal: string | null;
};

export type FilingChainInput = {
  status: PackageStatus;
  approvals: readonly PackageApprovalRead[];
  attestation: AttestationStatusRead | null;
  events: readonly SubmissionEventRead[];
  /** Every machine check passed with no errors outstanding. */
  checksClean: boolean;
  regulator: RegulatorNaming;
  /**
   * Resolve an actor id to a display name. Returns null when the directory is
   * not readable by this officer — most preparers cannot read the organization
   * roster, and an unresolved id must read as "not recorded", never as a raw
   * identifier on a banking screen.
   */
  resolveOfficer: (actorUserId: string) => {
    name: string;
    title: string | null;
  } | null;
};

const STAGE_TITLES: Record<ChainStageKey, string> = {
  prepare: "Preparation",
  approve: "Approval",
  transmit: "Transmission",
  regulator: "The regulator",
};

const STAGE_ROLES: Record<ChainStageKey, string> = {
  prepare: "Preparer",
  approve: "Approver",
  transmit: "Validator",
  regulator: "Supervisor",
};

/**
 * Which stage each status sits at. `validated` is deliberately NOT a stage of
 * its own: the checks passing is an attribute of the figures that lets the
 * preparer send them on, not a person's decision.
 */
const STAGE_STATES: Record<
  PackageStatus,
  Record<ChainStageKey, ChainStageState>
> = {
  draft: {
    prepare: "current",
    approve: "ahead",
    transmit: "ahead",
    regulator: "ahead",
  },
  generated: {
    prepare: "current",
    approve: "ahead",
    transmit: "ahead",
    regulator: "ahead",
  },
  validated: {
    prepare: "current",
    approve: "ahead",
    transmit: "ahead",
    regulator: "ahead",
  },
  pending_approval: {
    prepare: "done",
    approve: "current",
    transmit: "ahead",
    regulator: "ahead",
  },
  approved: {
    prepare: "done",
    approve: "done",
    transmit: "current",
    regulator: "ahead",
  },
  submitted: {
    prepare: "done",
    approve: "done",
    transmit: "done",
    regulator: "current",
  },
  acknowledged: {
    prepare: "done",
    approve: "done",
    transmit: "done",
    regulator: "done",
  },
  rejected: {
    prepare: "current",
    approve: "done",
    transmit: "done",
    regulator: "returned",
  },
  declined: {
    prepare: "closed",
    approve: "done",
    transmit: "done",
    regulator: "closed",
  },
  superseded: {
    prepare: "closed",
    approve: "closed",
    transmit: "closed",
    regulator: "closed",
  },
};

function stageActs(regulator: RegulatorNaming): Record<ChainStageKey, string> {
  return {
    prepare:
      "builds the return from the bank's own figures and clears every check",
    approve: "reviews the frozen figures, then approves them or sends them back",
    transmit: `files the approved return with ${regulator.full}`,
    regulator: `acknowledges the filing, or returns it for correction`,
  };
}

/** The officer the bank named for a stage, from the attestation routing. */
function holderFor(
  key: ChainStageKey,
  attestation: AttestationStatusRead | null,
): { holder: string | null; holderTitle: string | null } {
  if (!attestation) return { holder: null, holderTitle: null };
  const role = key === "prepare" ? "preparer" : key === "approve" ? "approver" : null;
  if (role === null) return { holder: null, holderTitle: null };
  const recipient = attestation.recipients.find(
    (entry) =>
      entry.signingRole === role &&
      entry.attestationCycle === attestation.attestationCycle,
  );
  if (!recipient) return { holder: null, holderTitle: null };
  return {
    holder: recipient.recipientDisplayName ?? null,
    holderTitle: recipient.recipientJobTitle ?? null,
  };
}

function positionCopy(
  status: PackageStatus,
  regulator: RegulatorNaming,
  holder: { holder: string | null; holderTitle: string | null },
  checksClean: boolean,
): { position: string; detail: string; next: string | null } {
  const named = holder.holder
    ? `${holder.holder}${holder.holderTitle ? ` (${holder.holderTitle})` : ""}`
    : null;

  switch (status) {
    case "draft":
    case "generated":
      return {
        position: "With the preparer",
        detail: checksClean
          ? "The figures are generated and every check passed. The preparer certifies them next, which freezes them and sends them to the approver."
          : "The figures are generated and the checks have findings to clear. Nothing leaves this desk until they are clear.",
        next: "Once the preparer certifies, the approver reviews and signs, and the validator files the return.",
      };
    case "validated":
      return {
        position: "With the preparer",
        detail:
          "Every check passed. Certifying freezes these figures and sends the return to the approver the preparer names.",
        next: "The approver reviews and signs, then the validator files the return.",
      };
    case "pending_approval":
      return {
        position: "With the approver",
        detail: named
          ? `${named} has been asked to review the frozen figures and either approve them or send them back with a comment.`
          : "The approver has been asked to review the frozen figures and either approve them or send them back with a comment. The record does not name which officer holds it.",
        next: `After approval the validator — and only the validator — files the return with ${regulator.full}.`,
      };
    case "approved":
      return {
        position: "With the validator",
        detail:
          "Approved and signed. The validator is the only officer who may send it to the regulator.",
        next: `The next act on this return transmits it to ${regulator.full}. Nothing after that is undone from here — a correction needs the regulator's go-ahead.`,
      };
    case "submitted":
      return {
        position: `With ${regulator.short}`,
        detail: `Filed. ${regulator.full} has the return and has not yet given a decision.`,
        next: "Their decision is recorded here when they make it.",
      };
    case "acknowledged":
      return {
        position: "Complete",
        detail: `${regulator.full} acknowledged this filing. The obligation is discharged.`,
        next: null,
      };
    case "rejected":
      return {
        position: "Returned by the regulator",
        detail: `${regulator.full} returned this filing for correction. Their comments are on the return.`,
        next: "Correcting it means generating a new version of this return and taking it through the chain again.",
      };
    case "declined":
      return {
        position: "Refused",
        detail: `${regulator.full} refused this filing. The decision is final and the chain ends here.`,
        next: null,
      };
    case "superseded":
      return {
        position: "Superseded",
        detail:
          "A newer version of this return and reporting date replaced this one. It is kept unchanged as history.",
        next: null,
      };
    default:
      return {
        position: "Not recorded",
        detail: "The record does not say where this return is.",
        next: null,
      };
  }
}

/**
 * The signature that belongs to a decision, so the decision can name a person
 * and a round. Matched on role and on being the latest signature of that role
 * at or before the decision — signatures are append-only and a voided cycle
 * keeps its own.
 */
function signatureFor(
  attestation: AttestationStatusRead | null,
  role: string,
  at: Date | null,
): { name: string | null; title: string | null; round: number | null } | null {
  if (!attestation) return null;
  const candidates = attestation.signatures
    .filter((signature) => signature.signingRole === role)
    .sort((a, b) => a.declaredAt.getTime() - b.declaredAt.getTime());
  const match = at
    ? [...candidates]
        .reverse()
        .find((signature) => signature.declaredAt.getTime() <= at.getTime() + 60_000)
    : candidates.at(-1);
  if (!match) return null;
  return {
    name: match.signerDisplayName ?? null,
    title: match.officerTitle ?? null,
    round: match.attestationCycle ?? null,
  };
}

function approvalEntries(input: FilingChainInput): ChainEntry[] {
  return input.approvals.map((approval) => {
    const role = approval.action === "requested" ? "preparer" : "approver";
    const at = approval.occurredAt ?? null;
    const officer = input.resolveOfficer(approval.actorUserId);
    const signature = signatureFor(input.attestation, role, at);
    const outcome: ChainEntryOutcome =
      approval.action === "requested"
        ? "sent_for_approval"
        : approval.action === "approved"
          ? "approved"
          : "returned";
    return {
      at,
      actorName: officer?.name ?? signature?.name ?? null,
      actorTitle: officer?.title ?? signature?.title ?? null,
      roleName: role === "preparer" ? STAGE_ROLES.prepare : STAGE_ROLES.approve,
      outcome,
      comment: approval.reason ?? null,
      round: signature?.round ?? null,
      // Every backward edge in today's lifecycle returns the package to the
      // preparer. A named target arrives with the stage engine; until it does,
      // stating the one edge that exists is the honest answer.
      returnedTo: outcome === "returned" ? "the preparer" : null,
    };
  });
}

function eventEntries(input: FilingChainInput): ChainEntry[] {
  const entries: ChainEntry[] = [];
  for (const event of input.events) {
    // A poll is bookkeeping — it records that somebody asked, not that anybody
    // decided. The decision events below are the ones that belong in a chain.
    if (event.event === "status_poll") continue;
    const at = event.occurredAt ?? null;
    if (event.event === "submitted") {
      entries.push({
        at,
        actorName: null,
        actorTitle: null,
        roleName: STAGE_ROLES.transmit,
        outcome: "filed",
        comment: null,
        round: null,
        returnedTo: null,
      });
      continue;
    }
    const outcome: ChainEntryOutcome =
      event.event === "acknowledged"
        ? "acknowledged"
        : event.event === "declined"
          ? "declined"
          : "rejected";
    entries.push({
      at,
      actorName: input.regulator.full,
      actorTitle: null,
      roleName: STAGE_ROLES.regulator,
      outcome,
      comment:
        typeof event.detail?.comments === "string" ? event.detail.comments : null,
      round: null,
      returnedTo: null,
    });
  }
  return entries;
}

export function buildFilingChain(input: FilingChainInput): FilingChain {
  const states = STAGE_STATES[input.status] ?? STAGE_STATES.generated;
  const acts = stageActs(input.regulator);
  const stages: ChainStage[] = (
    ["prepare", "approve", "transmit", "regulator"] as ChainStageKey[]
  ).map((key) => ({
    key,
    title: STAGE_TITLES[key],
    roleName:
      key === "regulator" ? input.regulator.full : STAGE_ROLES[key],
    act: acts[key],
    state: states[key],
    ...holderFor(key, input.attestation),
  }));

  const currentStage =
    stages.find((stage) => stage.state === "current") ?? stages[0];
  const copy = positionCopy(
    input.status,
    input.regulator,
    { holder: currentStage.holder, holderTitle: currentStage.holderTitle },
    input.checksClean,
  );

  const entries = [...approvalEntries(input), ...eventEntries(input)].sort(
    (a, b) => (a.at?.getTime() ?? 0) - (b.at?.getTime() ?? 0),
  );

  return {
    round: input.attestation?.attestationCycle ?? null,
    position: copy.position,
    positionDetail: copy.detail,
    next: copy.next,
    stages,
    entries,
  };
}

/** What a decision did, in the words a filing officer would use. */
export function outcomeLabel(
  outcome: ChainEntryOutcome,
  regulator: RegulatorNaming,
): string {
  switch (outcome) {
    case "sent_for_approval":
      return "Certified the figures and sent them for approval";
    case "approved":
      return "Approved and signed";
    case "returned":
      return "Sent back for corrections";
    case "filed":
      return `Filed with ${regulator.full}`;
    case "acknowledged":
      return "Acknowledged the filing";
    case "rejected":
      return "Returned the filing for correction";
    case "declined":
      return "Refused the filing";
    default:
      return "Not recorded";
  }
}
