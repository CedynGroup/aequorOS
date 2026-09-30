"use client";

/**
 * Certifying a formula for the institution, which takes two people.
 *
 * THE SEPARATION IS THE PRODUCT, NOT A PRECAUTION. A certified measure is the
 * institution's own vocabulary: anyone whose access covers the figures it names
 * can chart it, and a saved dashboard may only carry a calculated measure once it
 * is certified. So one person proposes and a DIFFERENT person certifies, through
 * two routes, and the database refuses a row whose approver is its proposer
 * (`ck_bi_measures_promotion_separation`) even if a service path is ever wrong
 * about it.
 *
 * This panel therefore does three separate things and never two at once:
 *
 * * THE MAKER'S HALF, for the owner of a draft: a reason, and "send for
 *   certification".
 * * THE PROPOSER'S NOTICE, for that same person once it is proposed: there is no
 *   decision control, and the reason it is absent is written out. A control that
 *   vanishes with no explanation reads as a fault.
 * * THE CHECKER'S HALF, only when the SERVER said this identity may take it
 *   (`awaiting_caller_decision`). Never on the strength of a role, never inferred
 *   from "not the owner". The checker is shown the exact formula they are being
 *   asked to certify, and the decision is sent with the digest of that text — so
 *   a formula edited underneath them cannot be certified by a decision about the
 *   version they read.
 *
 * A REFUSAL NAMES THE FIGURE, NEVER THE FORMULA. Certifying needs authority to
 * APPROVE every figure the formula reads, not merely to view it, so a checker may
 * hold enough to read a proposal and not enough to certify it. That 403 is
 * rendered as the figures it named — the ones written in the formula on the same
 * screen — because "you may not certify this measure" tells the reviewer nothing
 * they can act on.
 */

import { useState } from "react";
import { ShieldCheck, Undo2 } from "lucide-react";
import type { BiMeasureRead } from "@aequoros/risk-service-api";
import {
  MEASURE_REASON_MAX_LENGTH,
  measureControls,
  reasonProblem,
} from "@/components/bi/measures";
import type { SodFinding } from "@/lib/api/sodDecision";
import { fmtDate } from "@/lib/format";

/**
 * A governance timestamp, as a date.
 *
 * `proposed_at` and `approved_at` arrive as ISO STRINGS, not `Date`: the generated
 * client emits a named nullable alias for each and the `format: date-time` is lost
 * on the way, so a caller that trusted the declared type and called a `Date`
 * method on one would throw on the first proposal. Parsed here, once, and an
 * unparseable value renders as nothing rather than as "Invalid Date".
 */
function stamp(value: string | null | undefined): string | null {
  if (value == null) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : fmtDate(parsed);
}

export type MeasureDecision = "approve" | "reject";

export default function MeasureReview({
  measure,
  proposing,
  deciding,
  refusal,
  sodFindings: findings,
  sodRemedy,
  onPropose,
  onDecide,
}: {
  measure: BiMeasureRead;
  proposing: boolean;
  deciding: boolean;
  /** Whatever the last proposal or decision refused with, worded for a reader. */
  refusal: string | null;
  /** The separation-of-duties findings behind a refused promotion, if any. */
  sodFindings: readonly SodFinding[];
  sodRemedy: string | null;
  onPropose: (reason: string) => void;
  onDecide: (decision: MeasureDecision, reason: string) => void;
}) {
  // One DOM id per MEASURE. `/explore/measures` renders one of these per
  // measure, so a fixed id put two elements under the same id as soon as a
  // second draft was on screen, and `<label for>` then bound to whichever came
  // first — a reader clicking one draft's label focused another's box. Found by
  // the browser journeys, which had been ordered to keep only one draft visible
  // whenever a reason field was used: a test working around a defect.
  const proposalReasonId = `bi-measure-proposal-reason-${measure.id}`;
  const decisionReasonId = `bi-measure-decision-reason-${measure.id}`;
  const controls = measureControls(measure);
  const proposedOn = stamp(measure.proposedAt);
  const approvedOn = stamp(measure.approvedAt);
  const [proposalReason, setProposalReason] = useState("");
  const [decisionReason, setDecisionReason] = useState("");

  const proposalFault = reasonProblem(
    proposalReason,
    "what this figure is for and why the institution should stand behind it.",
  );
  const decisionFault = reasonProblem(
    decisionReason,
    "what you checked, and what you concluded.",
  );

  return (
    <div className="flex flex-col gap-3">
      {proposedOn !== null && (
        <p className="text-caption text-slate">
          Proposed by {measure.proposedByDisplayName ?? "a colleague"} on{" "}
          {proposedOn}
          {measure.proposalReason ? ` — “${measure.proposalReason}”` : "."}
        </p>
      )}

      {approvedOn !== null && (
        <div className="rounded-md border border-success/30 bg-success-light px-3 py-2">
          <p className="flex items-start gap-1.5 text-caption text-navy">
            <ShieldCheck size={13} aria-hidden className="mt-0.5 shrink-0" />
            <span>
              Certified by {measure.approvedByDisplayName ?? "an approver"} on{" "}
              {approvedOn}
              {measure.approvalReason ? ` — “${measure.approvalReason}”` : "."}
            </span>
          </p>
          {measure.approvedExpression != null && (
            <p className="mt-1 font-mono text-micro text-navy">
              {measure.approvedExpression}
            </p>
          )}
          <p className="mt-1 text-caption text-slate">
            That is the exact text that was certified. It is what the figure is
            worked out from, and any change to the formula has to be reviewed
            again.
          </p>
        </div>
      )}

      {controls.canPropose && (
        <div className="flex flex-col gap-2 rounded-md border border-border-light p-3">
          <h4 className="text-caption font-medium text-navy">
            Send this for certification
          </h4>
          <p className="text-caption text-slate">
            Someone else has to review it — you cannot certify a formula you
            proposed. Once it is certified, anyone whose access covers the
            figures it reads can chart it or put it in a grid.
          </p>
          <label
            htmlFor={proposalReasonId}
            className="text-caption font-medium text-navy"
          >
            Why it should be certified
          </label>
          <textarea
            id={proposalReasonId}
            value={proposalReason}
            rows={2}
            maxLength={MEASURE_REASON_MAX_LENGTH}
            onChange={(event) => setProposalReason(event.target.value)}
            className="rounded-md border border-border px-3 py-2 text-caption text-navy"
          />
          {proposalFault !== null && proposalReason.length > 0 && (
            <p className="text-caption text-navy">{proposalFault}</p>
          )}
          <div>
            <button
              type="button"
              disabled={proposing || proposalFault !== null}
              onClick={() => onPropose(proposalReason.trim())}
              className="rounded-md bg-action px-3 py-2 text-caption font-medium text-white disabled:opacity-50"
            >
              {proposing ? "Sending…" : "Send for certification"}
            </button>
          </div>
        </div>
      )}

      {controls.proposerNotice !== null && (
        <p
          className="rounded-md border border-border bg-surface px-3 py-2 text-caption text-navy"
          role="status"
        >
          {controls.proposerNotice}
        </p>
      )}

      {controls.canDecide && (
        <div className="flex flex-col gap-2 rounded-md border border-action/30 bg-action-light p-3">
          <h4 className="text-caption font-medium text-navy">
            Review this formula
          </h4>
          <p className="text-caption text-navy">
            You are being asked to certify this exact text for the institution.
            Read it against what its author says it is for.
          </p>
          <p className="rounded border border-border bg-white px-2 py-1 font-mono text-micro text-navy">
            {measure.expression}
          </p>
          <p className="text-caption text-slate">
            It reads {measure.referencedMemberLabels.join(", ")}.
          </p>
          <label
            htmlFor={decisionReasonId}
            className="text-caption font-medium text-navy"
          >
            What you concluded
          </label>
          <textarea
            id={decisionReasonId}
            value={decisionReason}
            rows={2}
            maxLength={MEASURE_REASON_MAX_LENGTH}
            onChange={(event) => setDecisionReason(event.target.value)}
            className="rounded-md border border-border bg-white px-3 py-2 text-caption text-navy"
          />
          {decisionFault !== null && decisionReason.length > 0 && (
            <p className="text-caption text-navy">{decisionFault}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={deciding || decisionFault !== null}
              onClick={() => onDecide("approve", decisionReason.trim())}
              className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white disabled:opacity-50"
            >
              <ShieldCheck size={13} aria-hidden />
              {deciding ? "Recording…" : "Certify for the institution"}
            </button>
            <button
              type="button"
              disabled={deciding || decisionFault !== null}
              onClick={() => onDecide("reject", decisionReason.trim())}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-white px-3 py-2 text-caption text-navy hover:bg-surface disabled:opacity-50"
            >
              <Undo2 size={13} aria-hidden />
              Send it back
            </button>
          </div>
          <p className="text-caption text-slate">
            Sending it back returns the formula to its author as a draft, with
            what you wrote on the record.
          </p>
        </div>
      )}

      {refusal !== null && (
        <div
          className="rounded-md border border-critical/30 bg-critical-light px-3 py-2"
          role="alert"
        >
          <p className="text-caption text-navy">{refusal}</p>
          {findings.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {findings.map((finding) => (
                <li key={finding.code} className="text-caption text-navy">
                  {finding.message}
                </li>
              ))}
            </ul>
          )}
          {sodRemedy !== null && (
            <p className="mt-1 text-caption text-navy">{sodRemedy}</p>
          )}
        </div>
      )}
    </div>
  );
}
