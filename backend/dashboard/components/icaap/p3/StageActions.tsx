"use client";

/**
 * The three acts of the review chain: put it forward, decide on it, send it
 * back.
 *
 * WHAT THIS SCREEN WILL NOT DO:
 *
 *  - **It will not offer a control the server would refuse.** Every
 *    affordance is driven by `stages.viewer`, which the server computes from
 *    the same evaluator that decides the act. When a control is withheld, the
 *    server's own plain-language reason is shown — "You wrote part of this
 *    report, so a different officer must review it" is a rule a Head of Risk
 *    should meet before pressing a button, not after.
 *  - **It will not decide what the reviewer is reviewing.** Every decision
 *    carries the report fingerprint and the round the server reported. If the
 *    report has moved since the page loaded, the server refuses
 *    (`review_basis_changed` / `stage_round_moved`) and the refusal is shown
 *    as written. A screen that re-read the digest and retried would be
 *    recording an approval of something the officer never saw.
 */

import { useState } from "react";
import { CornerUpLeft, Send, ShieldCheck } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  DESCRIPTION_MAX,
  DIGEST_CHARS,
  ICON_SM,
  RATIONALE_MAX,
  REASON_MIN,
  ROWS_LONG,
} from "@/components/icaap/p2/display";
import {
  useDecideIcaapStage,
  useReturnIcaapCycle,
  useSubmitIcaapForReview,
  type IcaapStage,
  type IcaapStages,
} from "@/lib/api/icaapFiling";
import { stageKindLabel } from "./labels";

/** The reason/comment fields the API requires a real sentence in. */
function tooShort(text: string): boolean {
  return text.trim().length < REASON_MIN;
}

function MutationError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
      {(error as Error).message}
    </p>
  );
}

/** The plain-language reason a control is withheld, shown instead of it. */
function WithheldReason({ reason }: { reason: string | null }) {
  return (
    <p className="text-body text-navy/80">
      {reason ??
        "Somebody else has to take this step. Your access does not include it for this institution."}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Put it forward for review
// ---------------------------------------------------------------------------

export function SubmitForReviewCard({
  bankId,
  cycleId,
  stages,
}: {
  bankId: string;
  cycleId: string;
  stages: IcaapStages;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const mutation = useSubmitIcaapForReview(bankId, cycleId);
  const digest = stages.reviewDigest ?? null;

  return (
    <SectionCard
      title="Put this assessment forward for review"
      subtitle="Once it is with the reviewers, the text and the figures are locked, so every stage reads the same report."
      actions={
        stages.viewer?.canSubmit && digest !== null ? (
          <PrimaryButton onClick={() => setOpen(true)}>
            <Send size={ICON_SM} aria-hidden />
            Put forward for review
          </PrimaryButton>
        ) : undefined
      }
    >
      {stages.viewer?.canSubmit ? (
        <p className="text-body text-navy/80">
          The preparation checklist must be clear and every section committed
          before it can go forward. Anything still unsaved is refused by name.
        </p>
      ) : (
        <WithheldReason reason={stages.viewer?.blockedReason ?? null} />
      )}

      {open && digest !== null && (
        <Dialog
          title="Put this assessment forward for review"
          description="The reviewers will see exactly the version that is committed now."
          onClose={() => setOpen(false)}
          footer={
            <>
              <SecondaryButton onClick={() => setOpen(false)}>
                Cancel
              </SecondaryButton>
              <PrimaryButton
                disabled={mutation.isPending}
                onClick={() =>
                  mutation.mutate(
                    { reviewDigest: digest, note: note.trim() || null },
                    { onSuccess: () => setOpen(false) },
                  )
                }
              >
                {mutation.isPending ? "Sending…" : "Put forward"}
              </PrimaryButton>
            </>
          }
        >
          <div className="space-y-3">
            <MutationError error={mutation.isError ? mutation.error : null} />
            <p className="text-body text-navy/80">
              Report fingerprint {digest.slice(0, DIGEST_CHARS)}. Every reviewer
              records their decision against this exact version; if the report
              changes, their decisions must be taken again.
            </p>
            <FieldLabel
              label="Note for the reviewers"
              hint="Optional. What you would like them to look at first."
            >
              <textarea
                value={note}
                rows={ROWS_LONG}
                maxLength={RATIONALE_MAX}
                onChange={(event) => setNote(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </div>
        </Dialog>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Decide on it
// ---------------------------------------------------------------------------

export function StageDecisionPanel({
  bankId,
  cycleId,
  stages,
  stage,
}: {
  bankId: string;
  cycleId: string;
  stages: IcaapStages;
  stage: IcaapStage;
}) {
  const [decision, setDecision] = useState<"forward" | "return" | null>(null);
  const [comment, setComment] = useState("");
  const [returnTo, setReturnTo] = useState<number | null>(null);
  const mutation = useDecideIcaapStage(bankId, cycleId);

  const round = stages.round ?? null;
  const digest = stages.reviewDigest ?? null;
  const forward = stage.kind === "approve" ? "approved" : "reviewed";
  const earlier = (stages.stages ?? []).filter((entry) => entry.seq < stage.seq);
  const ready = digest !== null && round !== null;

  return (
    <SectionCard
      title={`${stageKindLabel(stage.kind)}: ${stage.title}`}
      subtitle="Your decision is recorded against the exact version of the report you are reading."
    >
      {!stages.viewer.canDecide ? (
        <WithheldReason reason={stages.viewer?.blockedReason ?? null} />
      ) : (
        <div className="space-y-3">
          <p className="text-body text-navy/80">
            {stage.kind === "approve"
              ? "Approving means this is the institution's own assessment of the capital it needs."
              : "Reviewing records that you have read and challenged it. It is not an approval."}
            {digest
              ? ` Report fingerprint ${digest.slice(0, DIGEST_CHARS)}.`
              : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            <PrimaryButton
              disabled={!ready}
              onClick={() => setDecision("forward")}
            >
              <ShieldCheck size={ICON_SM} aria-hidden />
              {stage.kind === "approve" ? "Approve" : "Record my review"}
            </PrimaryButton>
            {stages.viewer?.canReturn && earlier.length > 0 && (
              <SecondaryButton
                disabled={!ready}
                onClick={() => {
                  setReturnTo(earlier[0]!.seq);
                  setDecision("return");
                }}
              >
                <CornerUpLeft size={ICON_SM} aria-hidden />
                Send back for changes
              </SecondaryButton>
            )}
          </div>
        </div>
      )}

      {decision !== null && ready && (
        <Dialog
          title={
            decision === "return"
              ? "Send this assessment back"
              : stage.kind === "approve"
                ? "Approve this assessment"
                : "Record your review"
          }
          description={
            decision === "return"
              ? "The stage you choose, and every stage after it, will have to look again."
              : "This is recorded in the institution's audit trail with your name and role."
          }
          onClose={() => setDecision(null)}
          footer={
            <>
              <SecondaryButton onClick={() => setDecision(null)}>
                Cancel
              </SecondaryButton>
              <PrimaryButton
                disabled={
                  mutation.isPending ||
                  (decision === "return" &&
                    (tooShort(comment) || returnTo === null))
                }
                onClick={() =>
                  mutation.mutate(
                    {
                      seq: stage.seq,
                      decision: decision === "return" ? "returned" : forward,
                      round: round!,
                      reviewDigest: digest!,
                      returnToSeq: decision === "return" ? returnTo : null,
                      comment: comment.trim() || null,
                    },
                    { onSuccess: () => setDecision(null) },
                  )
                }
              >
                {mutation.isPending
                  ? "Recording…"
                  : decision === "return"
                    ? "Send back"
                    : stage.kind === "approve"
                      ? "Approve"
                      : "Record review"}
              </PrimaryButton>
            </>
          }
        >
          <div className="space-y-3">
            <MutationError error={mutation.isError ? mutation.error : null} />
            {decision === "return" && (
              <FieldLabel label="Send it back to">
                <select
                  aria-label="Send it back to"
                  value={returnTo ?? ""}
                  onChange={(event) => setReturnTo(Number(event.target.value))}
                  className={INPUT_CLASS}
                >
                  {earlier.map((entry) => (
                    <option key={entry.seq} value={entry.seq}>
                      {entry.title}
                    </option>
                  ))}
                </select>
              </FieldLabel>
            )}
            <FieldLabel
              label={
                decision === "return"
                  ? "Why it is going back"
                  : "Comment for the record"
              }
              hint={
                decision === "return"
                  ? "The preparers see this. Say what has to change."
                  : "Optional."
              }
            >
              <textarea
                value={comment}
                rows={ROWS_LONG}
                maxLength={DESCRIPTION_MAX}
                onChange={(event) => setComment(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </div>
        </Dialog>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Send a sealed report back (post-freeze)
// ---------------------------------------------------------------------------

export function ReturnFromFilingDialog({
  bankId,
  cycleId,
  stages,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  stages: IcaapStages;
  onClose: () => void;
}) {
  const mutation = useReturnIcaapCycle(bankId, cycleId);
  const targets = (stages.stages ?? []).filter(
    (entry) => entry.kind !== "attest",
  );
  const [returnTo, setReturnTo] = useState<number>(targets[0]?.seq ?? 1);
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title="Send the sealed report back for changes"
      description="This unseals the report. Any signatures already given are cancelled, and the sealed version is kept, unchanged, as a record of what was signed."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              tooShort(reason) ||
              // The server decides against the round and digest it issued.
              // Without them this can only 422, so the act is not offered.
              stages.round === null ||
              stages.reviewDigest === null
            }
            onClick={() =>
              mutation.mutate(
                {
                  returnToSeq: returnTo,
                  reason: reason.trim(),
                  round: stages.round ?? 0,
                  reviewDigest: stages.reviewDigest ?? "",
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Sending back…" : "Send back"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        <MutationError error={mutation.isError ? mutation.error : null} />
        <FieldLabel label="Send it back to">
          <select
            aria-label="Send it back to"
            value={returnTo}
            onChange={(event) => setReturnTo(Number(event.target.value))}
            className={INPUT_CLASS}
          >
            {targets.map((entry) => (
              <option key={entry.seq} value={entry.seq}>
                {entry.title}
              </option>
            ))}
          </select>
        </FieldLabel>
        <FieldLabel
          label="Why it is going back"
          hint="Recorded in the audit trail beside the sealed version."
        >
          <textarea
            value={reason}
            rows={ROWS_LONG}
            maxLength={DESCRIPTION_MAX}
            onChange={(event) => setReason(event.target.value)}
            className={INPUT_CLASS}
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
