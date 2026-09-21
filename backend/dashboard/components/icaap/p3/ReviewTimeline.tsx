"use client";

/**
 * The review chain: who has to look at this assessment, in what order, and
 * what each of them said.
 *
 * This is the evidence a supervisor reads to see that the ICAAP was
 * challenged before it was approved, so the timeline shows the decisions that
 * were actually recorded — including the ones a later send-back reopened,
 * which are drawn as superseded rather than removed. Deleting them would make
 * the chain look tidier than it was.
 *
 * Two facts the screen must not fudge:
 *
 *  - **A send-back does not erase what came before it.** `stillStands` is the
 *    server's answer, computed from the round rule in
 *    `app/domain/icaap/workflow.py`; this screen never recomputes it.
 *  - **The signature stage records no decision.** The board signs the
 *    document itself, in the Filing tab; a "decide" control on that stage
 *    would be an invitation to an act the server refuses (409
 *    `stage_decided_by_signature`).
 */

import { CornerUpLeft, Lock } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import { fmtLocale } from "@/lib/format";
import { DIGEST_CHARS, ICON_SM } from "@/components/icaap/p2/display";
import type { IcaapStage, IcaapStages } from "@/lib/api/icaapFiling";
import {
  NO_REVIEW_YET,
  chainSourceSentence,
  decisionCopy,
  stageKindAsk,
  stageKindLabel,
  stageStateCopy,
} from "./labels";

/** A timestamp as the institution's own locale reads it. */
function moment(value: string | null): string {
  if (value === null) return "";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString(fmtLocale())
    : "";
}

export default function ReviewTimeline({ stages }: { stages: IcaapStages }) {
  // The normaliser guarantees a list; the `?? []` covers the case where this
  // is handed an unnormalised body, which the resilience suite exercises.
  const chain = stages.stages ?? [];
  if (chain.length === 0) {
    return (
      <SectionCard
        title="Review chain"
        subtitle="Who reads this assessment, and in what order."
      >
        <EmptyState title="The chain has not been set yet" description={NO_REVIEW_YET} />
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="Review chain"
      subtitle={`The order below is ${chainSourceSentence(stages.source)}. It is fixed when the assessment is first put forward, so every stage reviews the same report.`}
    >
      <ol className="space-y-3" data-testid="review-timeline">
        {chain.map((stage) => (
          <StageCard
            key={stage.seq}
            stage={stage}
            isCurrent={stage.seq === stages.currentStageSeq}
          />
        ))}
      </ol>
    </SectionCard>
  );
}

function StageCard({
  stage,
  isCurrent,
}: {
  stage: IcaapStage;
  isCurrent: boolean;
}) {
  const state = stageStateCopy(stage.state);
  return (
    <li
      className={`rounded border p-3 ${
        isCurrent ? "border-action/40 bg-action-light/20" : "border-border-light"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium text-navy">{stage.title}</p>
          <p className="text-caption text-slate">
            {stageKindLabel(stage.kind)} · {stageKindAsk(stage.kind)}
          </p>
          {stage.officerTitles.length > 0 && (
            <p className="mt-1 text-caption text-slate">
              Taken by: {stage.officerTitles.join(", ")}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {stage.freezeOnApprove && (
            <StatusPill tone="slate">
              <Lock size={ICON_SM} aria-hidden /> Seals the report
            </StatusPill>
          )}
          <StatusPill tone={state.tone}>{state.label}</StatusPill>
        </div>
      </div>

      {stage.decisions.length === 0 ? (
        <p className="mt-2 text-caption text-slate">
          {stage.kind === "attest"
            ? "Signed on the report itself, in the Filing tab."
            : "Nothing recorded at this stage yet."}
        </p>
      ) : (
        <ul className="mt-2 space-y-2 border-l-2 border-border-light pl-3">
          {stage.decisions.map((decision) => {
            const copy = decisionCopy(decision.decision);
            return (
              <li
                key={decision.id}
                className={decision.stillStands ? "" : "opacity-60"}
              >
                <p className="flex flex-wrap items-center gap-2 text-caption text-slate">
                  <StatusPill tone={decision.stillStands ? copy.tone : "slate"}>
                    {copy.label}
                  </StatusPill>
                  <span>
                    {decision.decidedByName}
                    {decision.officerTitle ? `, ${decision.officerTitle}` : ""}
                  </span>
                  <span>{moment(decision.createdAt)}</span>
                </p>
                {decision.returnToSeq !== null && (
                  <p className="mt-1 flex items-center gap-1 text-caption text-slate">
                    <CornerUpLeft size={ICON_SM} aria-hidden />
                    Sent back to an earlier stage for changes.
                  </p>
                )}
                {decision.comment && (
                  <p className="mt-1 text-body text-navy/80">
                    {decision.comment}
                  </p>
                )}
                <p className="mt-1 text-caption text-slate">
                  {decision.stillStands
                    ? "This decision still stands."
                    : "Superseded — the assessment was sent back after this, so this stage must look again."}
                  {decision.reviewDigest
                    ? ` Report fingerprint ${decision.reviewDigest.slice(0, DIGEST_CHARS)}.`
                    : ""}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
