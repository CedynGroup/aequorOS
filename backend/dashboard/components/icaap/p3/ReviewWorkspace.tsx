"use client";

/**
 * The Review & challenge tab.
 *
 * Four records, in the order a supervisor asks for them: the chain of people
 * who had to look at the assessment and what they said, the independent
 * review, the challenge log, and — once the chain has approved it — the act of
 * sealing it for filing.
 *
 * `AuditReview` and `ChallengeLog` were built with P2 and deliberately left
 * unrouted; this is the tab they were waiting for, mounted unchanged.
 */

import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import P2Unavailable, {
  p2UnavailableNotice,
} from "@/components/icaap/p2/availability";
import AuditReview from "@/components/icaap/p2/AuditReview";
import ChallengeLog from "@/components/icaap/p2/ChallengeLog";
import { useIcaapStages } from "@/lib/api/icaapFiling";
import FreezeCard from "./FreezeCard";
import RehearsalNotice, { isRehearsal } from "./RehearsalNotice";
import ReviewTimeline from "./ReviewTimeline";
import {
  StageDecisionPanel,
  SubmitForReviewCard,
} from "./StageActions";
import { NO_REVIEW_YET } from "./labels";

/** Statuses at which the assessment is still with its preparers. */
const NOT_YET_UNDER_REVIEW = new Set(["draft", "returned"]);

export default function ReviewWorkspace({
  bankId,
  cycleId,
  cycleKind,
}: {
  bankId: string;
  cycleId: string;
  /** From the cycle. Review happens before a package exists, so the cycle's
   * kind is the source here; the package carries `isRehearsal` of its own
   * (D-080) for every surface that reads a package instead. */
  cycleKind: string | null;
}) {
  const stagesQuery = useIcaapStages(bankId, cycleId);

  const unavailable = p2UnavailableNotice(stagesQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Review & challenge" message={unavailable} />;
  }

  const rehearsal = isRehearsal(cycleKind);

  return (
    <div className="space-y-4">
      {rehearsal && <RehearsalNotice />}

      <QueryBoundary
        isLoading={stagesQuery.isLoading}
        error={stagesQuery.error}
        onRetry={() => void stagesQuery.refetch()}
        contained
      >
        {stagesQuery.data && (
          <div className="space-y-4">
            {NOT_YET_UNDER_REVIEW.has(stagesQuery.data.status) && (
              <SubmitForReviewCard
                bankId={bankId}
                cycleId={cycleId}
                stages={stagesQuery.data}
              />
            )}

            {(() => {
              // `?? []` is not belt-and-braces here: this component is also
              // rendered against an UNNORMALISED body in the resilience suite,
              // and a payload without `stages` must produce an empty timeline
              // rather than a crash on a board-facing screen.
              const current = (stagesQuery.data.stages ?? []).find(
                (stage) =>
                  stage.seq === stagesQuery.data!.currentStageSeq &&
                  stage.kind !== "prepare" &&
                  stage.kind !== "attest",
              );
              return current ? (
                <StageDecisionPanel
                  bankId={bankId}
                  cycleId={cycleId}
                  stages={stagesQuery.data!}
                  stage={current}
                />
              ) : null;
            })()}

            {stagesQuery.data.awaitingFreeze && (
              <FreezeCard
                bankId={bankId}
                cycleId={cycleId}
                stages={stagesQuery.data}
                isRehearsal={rehearsal}
              />
            )}

            <ReviewTimeline stages={stagesQuery.data} />
          </div>
        )}
        {!stagesQuery.data && !stagesQuery.isLoading && (
          <SectionCard title="Review chain">
            <p className="text-body text-navy/80">{NO_REVIEW_YET}</p>
          </SectionCard>
        )}
      </QueryBoundary>

      <AuditReview bankId={bankId} cycleId={cycleId} />
      <ChallengeLog bankId={bankId} cycleId={cycleId} />
    </div>
  );
}
