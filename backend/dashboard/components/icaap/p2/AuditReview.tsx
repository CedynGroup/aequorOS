"use client";

/**
 * The independent review of the ICAAP (¶ independent review), and its record.
 *
 * NOT ROUTED YET. The Review & challenge tab ships in P3, which adds the stage
 * timeline beside these two components. This file is complete and exported so
 * P3 mounts it rather than rebuilding it; until then nothing imports it into a
 * page, and `components/icaap/tabs.ts` leaves the `review` segment disabled so
 * a typed URL is a genuine 404 rather than a half-built screen.
 *
 * The rule this screen exists to make visible: an independent review cannot be
 * recorded by someone who took part in preparing the ICAAP. The service decides
 * that — it knows every participant — and answers 409
 * `reviewer_not_independent`. The screen renders that refusal as the SENTENCE
 * it is, because "403 Forbidden" tells a head of internal audit nothing about
 * why the platform will not accept their own review.
 *
 * A finalised review is SEALED: it cannot be edited, only superseded by a later
 * one. The screen states that before the finalise button, not after.
 */

import { useState } from "react";
import { Lock, Plus } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import { isApiError } from "@/lib/api/client";
import { useModuleScope } from "@/components/shell/BankContext";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import { RestrictedWidget } from "@/components/icaap/Notices";
import {
  type IcaapReviewKind,
  type IcaapReviewOpinion,
  useCreateIcaapAuditReview,
  useFinaliseIcaapAuditReview,
  useIcaapAuditReviews,
} from "@/lib/api/icaapRiskCapital";
import ParameterProvenance from "./ParameterProvenance";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import { ICON_SM, REASON_MAX, ROWS_LONG, ROWS_MEDIUM, TITLE_MAX } from "./display";
import {
  REVIEWER_NOT_INDEPENDENT,
  reviewOpinionLabel,
  reviewStatusCopy,
} from "./labels";

export default function AuditReview({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const canCreate = scope.auditCreate === true;
  const reviewsQuery = useIcaapAuditReviews(bankId, cycleId);
  const finalise = useFinaliseIcaapAuditReview(bankId, cycleId);
  const [recording, setRecording] = useState(false);

  const data = reviewsQuery.data;
  const reviews = data?.reviews ?? [];

  const unavailable = p2UnavailableNotice(reviewsQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Independent review" message={unavailable} />;
  }

  return (
    <QueryBoundary
      isLoading={reviewsQuery.isLoading}
      error={reviewsQuery.error}
      onRetry={() => void reviewsQuery.refetch()}
      contained
    >
      {data && (
        <div className="space-y-4">
          <SectionCard
            title="Independent review"
            subtitle="The review of this ICAAP by a function that took no part in preparing it."
            actions={
              canCreate ? (
                <SecondaryButton onClick={() => setRecording(true)}>
                  <Plus size={ICON_SM} aria-hidden />
                  Record independent review
                </SecondaryButton>
              ) : undefined
            }
          >
            {!canCreate && (
              <RestrictedWidget
                title="You can read the review but not record one"
                requirement="Audit authority over this organization"
              />
            )}

            {reviews.length === 0 ? (
              <EmptyState
                title="No independent review recorded"
                description="The ICAAP is reviewed by a function independent of the one that prepared it. The review's scope, findings and conclusion are part of the submission."
              />
            ) : (
              <ul className="space-y-3">
                {reviews.map((review) => {
                  const status = reviewStatusCopy(review.status);
                  return (
                    <li
                      key={review.reviewId}
                      className="rounded border border-border-light p-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="font-medium text-navy">
                            {review.reviewerFunction}
                          </p>
                          <p className="text-caption text-slate">
                            Recorded by {review.recordedBy}
                            {review.performedOn ? ` · ${review.performedOn}` : ""}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <StatusPill tone={status.tone}>
                            {status.label}
                          </StatusPill>
                          {review.status === "draft" && canCreate && (
                            <SecondaryButton
                              disabled={finalise.isPending}
                              onClick={() =>
                                finalise.mutate({
                                  reviewId: review.reviewId,
                                  reason: "Finalise the independent review",
                                })
                              }
                            >
                              <Lock size={ICON_SM} aria-hidden />
                              Finalise
                            </SecondaryButton>
                          )}
                        </div>
                      </div>

                      {review.scope && (
                        <p className="mt-2 text-body text-navy/80">
                          <span className="text-caption text-slate">Scope: </span>
                          {review.scope}
                        </p>
                      )}
                      {review.findings.length > 0 && (
                        <ul className="mt-1 space-y-1 text-body text-navy/80">
                          {review.findings.map((finding) => (
                            <li key={finding.ref}>
                              <span className="text-caption text-slate">
                                {finding.ref} · {finding.severity}:{" "}
                              </span>
                              {finding.finding}
                            </li>
                          ))}
                        </ul>
                      )}
                      <p className="mt-1 text-body text-navy/80">
                        <span className="text-caption text-slate">Opinion: </span>
                        {reviewOpinionLabel(review.overallOpinion)}
                      </p>
                      {review.status === "finalised" && (
                        <p className="mt-2 text-caption text-slate">
                          Finalised reviews cannot be edited. Record a new one
                          to supersede this.
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}

            {finalise.isError && (
              <p className="mt-2 text-caption text-critical">
                {(finalise.error as Error).message}
              </p>
            )}
          </SectionCard>
        </div>
      )}

      {recording && (
        <RecordReviewDialog
          bankId={bankId}
          cycleId={cycleId}
          onClose={() => setRecording(false)}
        />
      )}
    </QueryBoundary>
  );
}

function RecordReviewDialog({
  bankId,
  cycleId,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  onClose: () => void;
}) {
  const mutation = useCreateIcaapAuditReview(bankId, cycleId);
  const [reviewerName, setReviewerName] = useState("");
  const [reviewerFunction, setReviewerFunction] = useState("");
  const [reviewDate, setReviewDate] = useState("");
  const [reviewScope, setReviewScope] = useState("");
  const [findings, setFindings] = useState("");
  const [conclusion, setConclusion] = useState("satisfactory");
  const [reason, setReason] = useState("");

  /**
   * The service's independence refusal, rendered as its own sentence. Everything
   * else falls back to the server's message.
   */
  const notIndependent =
    isApiError(mutation.error) &&
    (mutation.error.details as { error_code?: string } | undefined)
      ?.error_code === "reviewer_not_independent";

  return (
    <Dialog
      title="Record an independent review"
      description="The reviewer must be independent of the preparation of this ICAAP. The platform checks that against who worked on the cycle."
      onClose={onClose}
      wide
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              reviewerName.trim() === "" ||
              reviewerFunction.trim() === "" ||
              reviewDate === "" ||
              reviewScope.trim() === "" ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                {
                  reviewerFunction: reviewerFunction.trim(),
                  scope: reviewScope.trim(),
                  // One free-text finding is recorded as a single structured
                  // finding; the wire has no free-text field, and inventing a
                  // severity per sentence is not this form's job.
                  findings:
                    findings.trim() === ""
                      ? undefined
                      : [
                          {
                            ref: "F1",
                            finding: findings.trim(),
                            severity: "medium" as const,
                          },
                        ],
                  overallOpinion: conclusion as IcaapReviewOpinion,
                  reviewKind: "internal_audit" as IcaapReviewKind,
                  independenceStatement: reviewerName.trim(),
                  frequencyStatement: reason.trim(),
                  performedOn: new Date(`${reviewDate}T00:00:00Z`),
                  reason: reason.trim(),
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Recording…" : "Record review"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {notIndependent && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {REVIEWER_NOT_INDEPENDENT}
          </p>
        )}
        {mutation.isError && !notIndependent && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <FieldLabel label="Independence statement">
            <input
              value={reviewerName}
              maxLength={TITLE_MAX}
              onChange={(event) => setReviewerName(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
          <FieldLabel label="Function">
            <input
              value={reviewerFunction}
              maxLength={TITLE_MAX}
              onChange={(event) => setReviewerFunction(event.target.value)}
              className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
            />
          </FieldLabel>
        </div>

        <FieldLabel label="Date of the review">
          <input
            type="date"
            value={reviewDate}
            onChange={(event) => setReviewDate(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel label="Scope">
          <textarea
            value={reviewScope}
            rows={ROWS_MEDIUM}
            onChange={(event) => setReviewScope(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel label="Findings">
          <textarea
            value={findings}
            rows={ROWS_LONG}
            onChange={(event) => setFindings(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>

        <FieldLabel label="Overall opinion">
          <select
            aria-label="Overall opinion"
            value={conclusion}
            onChange={(event) => setConclusion(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          >
            {["satisfactory", "satisfactory_with_findings", "needs_improvement", "unsatisfactory"].map(
              (opinion) => (
                <option key={opinion} value={opinion}>
                  {reviewOpinionLabel(opinion)}
                </option>
              ),
            )}
          </select>
        </FieldLabel>

        <FieldLabel label="How often this review is performed" hint="Recorded with the review.">
          <input
            value={reason}
            maxLength={REASON_MAX}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
