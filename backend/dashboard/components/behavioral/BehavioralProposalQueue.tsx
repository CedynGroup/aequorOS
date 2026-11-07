"use client";

import { CheckCircle2, Loader2 } from "lucide-react";
import type { BehavioralProposalRead } from "@aequoros/risk-service-api";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useBehavioralProposalAccess } from "@/lib/api/configureAccess";
import {
  useBehavioralModelProposals,
  useReviewBehavioralModelProposal,
  type BehavioralModelSlug,
} from "@/lib/api/hooks";
import { fmtDateUTC } from "@/lib/api/values";

/**
 * Restricted estimate evidence for the maker-checker queue. The acknowledgement
 * returned to a proposer contains only id/status; every value and actor shown
 * here comes from this independently authorized read.
 */
export default function BehavioralProposalQueue({
  bankId,
  model,
}: {
  bankId: string | undefined;
  model: BehavioralModelSlug;
}) {
  const access = useBehavioralProposalAccess(bankId);
  const profile = useUserProfile();
  const query = useBehavioralModelProposals(
    access.canView ? bankId : undefined,
  );
  const review = useReviewBehavioralModelProposal(bankId);
  const proposals = (query.data?.proposals ?? []).filter(
    (proposal) => proposal.model === model,
  );

  return (
    <SectionCard
      title="Independent proposal review"
      subtitle="Restricted proposal evidence; a different authorized approver accepts the immutable snapshot"
    >
      {!access.canView ? (
        <p className="text-body text-slate">{access.viewReason}</p>
      ) : query.error ? (
        <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
      ) : query.isLoading ? (
        <p className="flex items-center gap-2 text-body text-slate">
          <Loader2 size={15} className="animate-spin" aria-hidden />
          Loading proposal evidence…
        </p>
      ) : proposals.length === 0 ? (
        <p className="text-body text-slate">
          No proposals have been recorded for this model.
        </p>
      ) : (
        <div className="space-y-4">
          {proposals.map((proposal) => (
            <ProposalEvidence
              key={proposal.proposalId}
              proposal={proposal}
              canReview={
                access.canReview &&
                proposal.status === "pending" &&
                Boolean(profile.profile?.userId) &&
                profile.profile?.userId !== proposal.proposedByUserId
              }
              reason={reviewReason(
                proposal,
                access.canReview,
                access.reviewReason,
                profile.profile?.userId,
              )}
              isPending={
                review.isPending && review.variables === proposal.proposalId
              }
              error={
                review.variables === proposal.proposalId ? review.error : null
              }
              onReview={() => {
                if (
                  !access.canReview ||
                  proposal.status !== "pending" ||
                  !profile.profile?.userId ||
                  profile.profile.userId === proposal.proposedByUserId
                )
                  return;
                review.mutate(proposal.proposalId);
              }}
            />
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function reviewReason(
  proposal: BehavioralProposalRead,
  structurallyAllowed: boolean,
  authorityReason: string,
  actorId: string | undefined,
): string | undefined {
  if (proposal.status !== "pending") return undefined;
  if (!structurallyAllowed) return authorityReason;
  if (!actorId)
    return "Could not verify the reviewer identity. Reload the page to retry.";
  if (actorId === proposal.proposedByUserId)
    return "A different authorized reviewer is required; the proposer cannot review their own proposal.";
  return undefined;
}

function ProposalEvidence({
  proposal,
  canReview,
  reason,
  isPending,
  error,
  onReview,
}: {
  proposal: BehavioralProposalRead;
  canReview: boolean;
  reason: string | undefined;
  isPending: boolean;
  error: Error | null;
  onReview: () => void;
}) {
  const reviewButton = (descriptionId?: string) => (
    <button
      type="button"
      onClick={onReview}
      disabled={!canReview || isPending}
      aria-describedby={descriptionId}
      className="inline-flex items-center gap-2 px-3 py-2 text-caption font-medium btn-primary disabled:cursor-not-allowed disabled:opacity-40"
    >
      {isPending ? (
        <Loader2 size={14} className="animate-spin" aria-hidden />
      ) : (
        <CheckCircle2 size={14} aria-hidden />
      )}
      Review and accept
    </button>
  );

  return (
    <article className="rounded-md border border-border-light p-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0 space-y-1 text-caption text-slate">
          <div className="flex items-center gap-2">
            <StatusPill
              tone={proposal.status === "accepted" ? "compliant" : "pending"}
            >
              {proposal.status}
            </StatusPill>
            <span className="font-mono">
              {proposal.proposalId.slice(0, 8)}…
            </span>
          </div>
          <p>
            Model{" "}
            <span className="font-mono text-navy">{proposal.modelVersion}</span>
            {" · "}as of {fmtDateUTC(proposal.asOfDate)}
          </p>
          <p>
            Proposed by{" "}
            <span className="font-mono text-navy">
              {proposal.proposedByUserId}
            </span>
            {" · "}
            {fmtDateUTC(proposal.proposedAt)}
          </p>
        </div>
        {proposal.status === "pending" &&
          (reason ? (
            <DisabledWithReason reason={reason}>
              {(descriptionId) => reviewButton(descriptionId)}
            </DisabledWithReason>
          ) : (
            reviewButton()
          ))}
      </div>
      <ul className="mt-3 space-y-1 text-body text-navy/85">
        {proposal.values.map((value) => (
          <li key={value.productCode} className="font-mono text-caption">
            {value.productCode}: {value.value} {value.unit ?? ""}
            {value.confidence == null
              ? ""
              : ` · ${(value.confidence * 100).toFixed(0)}% confidence`}
          </li>
        ))}
      </ul>
      {proposal.status === "accepted" && proposal.reviewedByUserId ? (
        <p className="mt-3 text-caption text-success">
          Accepted by {String(proposal.reviewedByUserId)}; the consuming model
          surfaces have been refreshed.
        </p>
      ) : null}
      {error ? (
        <p className="mt-3 text-caption text-critical">
          Could not review — {error.message}
        </p>
      ) : null}
    </article>
  );
}
