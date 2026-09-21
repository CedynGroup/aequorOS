"use client";

/**
 * Regulatory Reporting — Approvals. This page is the QUEUE, not a decision
 * surface.
 *
 * It used to offer an Approve button beside the signing ceremony, so a return
 * could be approved by a checker who never signed the figures they approved (and
 * signed by one who never approved). Those are one act now — recorded in one
 * backend transaction by the signing workspace — so the queue's job is to route
 * the reviewer into it, on the document, where both exits live: approve and sign,
 * or send back for corrections with a note.
 *
 * The bare decide panel survives for exactly one case: a return whose signing
 * policy an administrator has relaxed. There is no ceremony to route those to, so
 * the bare decision is the whole of the checker act — and the backend refuses it
 * for any return that does require signatures (`approval_requires_signature`).
 */

import PageContainer from "@/components/ui/PageContainer";
import { useState } from "react";
import Link from "next/link";
import {
  CheckCircle2,
  Loader2,
  PenLine,
  UserCheck,
  XCircle,
} from "lucide-react";
import type { RegulatoryPackageSummaryRead } from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import DataTable, { type Column } from "@/components/ui/DataTable";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import QueryBoundary, { ErrorPanel } from "@/components/ui/QueryBoundary";
import EmptyState from "@/components/ui/EmptyState";
import { SkeletonTable } from "@/components/ui/Skeleton";
import { useBankContext } from "@/components/shell/BankContext";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  useDecidePackageApproval,
  useOfficerNames,
  usePackageAttestation,
  useDecidePackageFilingStage,
  usePackageFilingChain,
  useEmailFallbackInstructions,
  useHandOffPackageFilingStage,
  usePollRegulatorySubmission,
  useSubmissionEvents,
  usePackageFilingSet,
  useRegulatoryPackage,
  useRegulatoryPackages,
} from "@/lib/api/hooks";
import { hasAccountDirectoryAuthority } from "@/lib/api/accountAdministration";
import { fmtDateUTC, fmtTimestamp, isoDate, shortId } from "@/lib/api/values";
import { isApiError } from "@/lib/api/client";
import { filingAuthorityFor } from "@/lib/submissions/filingAuthority";
import {
  FAMILY_LABELS,
  RehearsalNotice,
  RehearsalPill,
  downloadEmailFallbackEml,
  returnsHref,
} from "@/components/submissions/shared";
import { AttestationSummary } from "@/components/attestation/shared";
import SnapshotPreview from "@/components/submissions/SnapshotPreview";
import { centralBankName, regShort } from "@/lib/format";

/** The chain's decisions in the bank's language — never the stored value. */
const FILING_DECISION_LABELS: Record<string, string> = {
  submitted: "Prepared and sent for review",
  reviewed: "Reviewed",
  approved: "Approved",
  returned: "Sent back",
};
import TransmitConfirmDialog from "@/components/submissions/TransmitConfirmDialog";
import FiledRecord from "@/components/submissions/FiledRecord";
import DowntimeFallback from "@/components/submissions/DowntimeFallback";

export default function ApprovalsPage() {
  const { bank } = useBankContext();
  const { effectiveAuthority } = useUserProfile();
  const bankId = bank?.id;
  const officerName = useOfficerNames(
    hasAccountDirectoryAuthority(effectiveAuthority),
  );

  // A return in review is not one status. The chain projects `pending_approval`
  // while it is with a reviewer and `approved` once the approval stage is
  // done — and a return waiting on the VALIDATOR carries `approved` while
  // still sitting at stage 3, unfiled. Querying `pending_approval` alone
  // emptied this queue the moment an approver acted, so the Validator had no
  // screen on which to find the return they were holding.
  //
  // The chain decides membership, not the status: a package belongs here while
  // it has a stage still to decide.
  const pendingQuery = useRegulatoryPackages(bankId, {
    status: "pending_approval",
    includeSuperseded: false,
    limit: 100,
  });
  const approvedQuery = useRegulatoryPackages(bankId, {
    status: "approved",
    includeSuperseded: false,
    limit: 100,
  });
  // Filed returns stay reachable: the receipt (board 5) is the answer to
  // "what exactly did we send them", and a row that disappears takes that
  // answer with it. That is every FILED state, not just `submitted` — the
  // regulator's own outcomes land on the package too, and a return that was
  // acknowledged is exactly the one an officer goes looking for.
  const submittedQuery = useRegulatoryPackages(bankId, {
    status: "submitted",
    includeSuperseded: false,
    limit: 25,
  });
  const acknowledgedQuery = useRegulatoryPackages(bankId, {
    status: "acknowledged",
    includeSuperseded: false,
    limit: 25,
  });
  const queueQuery = {
    isLoading:
      pendingQuery.isLoading ||
      approvedQuery.isLoading ||
      submittedQuery.isLoading ||
      acknowledgedQuery.isLoading,
    error:
      pendingQuery.error ??
      approvedQuery.error ??
      submittedQuery.error ??
      acknowledgedQuery.error,
    refetch: () => {
      void pendingQuery.refetch();
      void approvedQuery.refetch();
      void submittedQuery.refetch();
      void acknowledgedQuery.refetch();
    },
  };
  const queue = [
    ...(pendingQuery.data?.packages ?? []).filter(
      (pkg) => pkg.currentStageSeq != null,
    ),
    ...(approvedQuery.data?.packages ?? []).filter(
      (pkg) => pkg.currentStageSeq != null,
    ),
    ...(submittedQuery.data?.packages ?? []),
    ...(acknowledgedQuery.data?.packages ?? []),
  ];

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected =
    queue.find((pkg) => pkg.id === selectedId) ?? queue[0] ?? null;

  const columns: Column<RegulatoryPackageSummaryRead>[] = [
    {
      key: "return",
      header: "Return",
      // A practice run can reach this queue: D-068 lets a rehearsal be signed
      // and approved on purpose. An approver deciding one has to know.
      render: (pkg) => (
        <span className="inline-flex items-center gap-2">
          <span className="font-mono text-caption font-medium text-navy">
            {pkg.returnCode}
          </span>
          {pkg.isRehearsal && <RehearsalPill />}
        </span>
      ),
    },
    {
      key: "family",
      header: "Family",
      render: (pkg) => (
        <span className="text-caption text-slate">
          {FAMILY_LABELS[pkg.returnFamily] ?? pkg.returnFamily}
        </span>
      ),
    },
    {
      key: "reportingDate",
      header: "Reporting date",
      render: (pkg) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtDateUTC(pkg.reportingDate)}
        </span>
      ),
    },
    {
      key: "version",
      header: "Version",
      numeric: true,
      render: (pkg) => `v${pkg.version}`,
    },
    {
      key: "generatedBy",
      header: "Generated by (maker)",
      render: (pkg) => (
        <span className="text-caption text-navy/85">
          {officerName(pkg.generatedBy)}
        </span>
      ),
    },
    {
      key: "generatedAt",
      header: "Generated",
      render: (pkg) => (
        <span className="font-mono text-micro text-slate tnum">
          {fmtTimestamp(pkg.generatedAt)}
        </span>
      ),
    },
    {
      key: "waiting_for",
      header: "Waiting for",
      // The row's real state. `status` reads `pending_approval` for an
      // Approver stage AND a Validator stage, so a queue labelled from the
      // status alone said "Pending approval" about a return that was with the
      // Validator. The title is the bank's own word for the stage.
      render: (pkg) =>
        pkg.currentStageTitle ? (
          <StatusPill tone="pending">{pkg.currentStageTitle}</StatusPill>
        ) : (
          <span className="text-caption text-slate">Not yet in review</span>
        ),
    },
    {
      key: "validation",
      header: "Validation",
      render: (pkg) =>
        pkg.validationPassed ? (
          <StatusPill tone="success">Passed</StatusPill>
        ) : (
          <StatusPill tone="critical">Failed</StatusPill>
        ),
    },
  ];

  return (
    <>
      <PageHeader eyebrow="Regulatory Reporting" title="Approvals" />

      <PageContainer className="py-6 space-y-6">
        <QueryBoundary
          contained
          isLoading={queueQuery.isLoading}
          error={queueQuery.error}
          onRetry={() => queueQuery.refetch()}
          skeleton={
            <div className="card">
              <SkeletonTable rows={4} />
            </div>
          }
        >
          <SectionCard
            title="In review"
            subtitle={`${queue.length} package${queue.length === 1 ? "" : "s"} waiting on an officer's decision`}
            noPadding
          >
            {queue.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={UserCheck}
                  title="Queue is clear"
                  description="Returns appear here while they are with an officer — waiting on an approver, or on the validator who files them."
                />
              </div>
            ) : (
              <DataTable
                columns={columns}
                rows={queue}
                density="compact"
                onRowClick={(pkg) => setSelectedId(pkg.id)}
                rowClassName={(pkg) =>
                  pkg.id === selected?.id ? "bg-action-light/40" : ""
                }
              />
            )}
          </SectionCard>

          {selected && <DecidePanel bankId={bankId!} pkg={selected} />}
        </QueryBoundary>
      </PageContainer>
    </>
  );
}

function DecidePanel({
  bankId,
  pkg,
}: {
  bankId: string;
  pkg: RegulatoryPackageSummaryRead;
}) {
  const { effectiveAuthority, profile } = useUserProfile();
  const decide = useDecidePackageApproval(bankId);
  const detailQuery = useRegulatoryPackage(bankId, pkg.id);
  // For the selected package only — the summary payload carries no attestation
  // state, so a queue column would be one request per row.
  const attestationQuery = usePackageAttestation(bankId, pkg.id);
  // WHICH officer this return is waiting for. The legacy status says
  // `pending_approval` for the Approver stage AND the Validator stage, so a
  // page that reads only the status shows an approver's controls to a
  // Validator — which is what it did.
  const chainQuery = usePackageFilingChain(bankId, pkg.id);
  const chain = chainQuery.data ?? null;
  const currentStage =
    chain?.stages.find((stage) => stage.seq === chain.currentStageSeq) ?? null;
  // The stage says WHAT is next; the viewer's authority says whether it is
  // theirs. Branching on the stage alone put the Validator's controls —
  // Approve, Send back, Transmit to BoG — in front of the Approver who had
  // just handed the return on. A screen must not offer an act the server will
  // refuse, and it must never offer a filing to somebody with no authority to
  // file.
  const filingAuthority = filingAuthorityFor(effectiveAuthority, bankId, {
    resolved: effectiveAuthority !== undefined,
  });
  const stageTransmits = currentStage?.transmitOnApprove === true;
  const atTransmitStage = stageTransmits && filingAuthority.mayTransmit;
  const withValidatorNotMine = stageTransmits && !filingAuthority.mayTransmit;
  const stageDecision = useDecidePackageFilingStage(bankId);
  const handOff = useHandOffPackageFilingStage(bankId);
  const filingSetQuery = usePackageFilingSet(bankId, pkg.id, atTransmitStage);
  const [confirmingTransmit, setConfirmingTransmit] = useState(false);
  // Once a return is filed the queue is the wrong shape for it: the question
  // stops being "what do I decide" and becomes "what exactly did we send, and
  // what has the regulator done with it". Board 5 answers that, and it is
  // shown right here so the officer who pressed transmit sees the receipt
  // rather than watching the row vanish.
  const eventsQuery = useSubmissionEvents(bankId, pkg.id);
  const poll = usePollRegulatorySubmission(bankId);
  const filedEvents = eventsQuery.data?.events ?? [];
  const isFiled = filedEvents.some((event) => event.event === "submitted");
  // The portal refused the transmission. `channel_downtime` is the structured
  // refusal the service raises when it could not be reached — distinct from a
  // rejection, and the one case that routes to the deadline fallback.
  const stageError = stageDecision.error;
  const downtime =
    stageError && isApiError(stageError) && stageError.errorCode === "channel_downtime"
      ? stageError.message
      : null;
  const fallbackQuery = useEmailFallbackInstructions(
    bankId,
    downtime ? pkg.id : null,
  );
  const latestSubmitted = [...filedEvents]
    .reverse()
    .find((event) => event.event === "submitted");
  const pendingReupload =
    latestSubmitted?.channel === "email" &&
    latestSubmitted.detail?.["pending_orass_reupload"] === true;
  const [sendingBack, setSendingBack] = useState(false);
  const [sendBackTo, setSendBackTo] = useState<number | null>(null);
  const [sendBackComment, setSendBackComment] = useState("");
  const officerName = useOfficerNames(
    hasAccountDirectoryAuthority(effectiveAuthority),
  );
  const [reason, setReason] = useState("");

  const rejectNeedsReason = reason.trim().length === 0;
  // A return does not leave this queue when an approver decides it: the chain
  // moves it to the NEXT stage and the legacy status stays `pending_approval`,
  // so the row reappears with its buttons live. Pressing them again asks the
  // server to decide a stage this officer is barred from by separation of
  // duties, and the 409 that comes back reads like a failure rather than the
  // control working. Whoever has already decided in this review is told so,
  // and the act is not offered to them a second time.
  const alreadyDecided = Boolean(
    profile?.userId &&
      detailQuery.data?.approvals.some(
        (approval) =>
          approval.actorUserId === profile.userId &&
          approval.action !== "requested",
      ),
  );
  const requestReason = detailQuery.data?.approvals.find(
    (approval) => approval.action === "requested",
  )?.reason;
  const attestation = attestationQuery.data ?? null;
  // Strict until the policy is known: the platform default requires signatures,
  // so an unread status must not fall back to offering the bare decision.
  const signingRequired = attestation?.policy.requireSignature ?? true;
  const reviewHref = `${returnsHref(
    pkg.returnCode,
    isoDate(pkg.reportingDate),
  )}&sign=approver`;

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          {signingRequired ? "Review" : "Decide"} —{" "}
          <span className="font-mono">{pkg.returnCode}</span>{" "}
          {fmtDateUTC(pkg.reportingDate)}{" "}
          <span className="font-mono text-caption text-slate tnum">
            v{pkg.version}
          </span>
          {pkg.isRehearsal && <RehearsalPill />}
        </span>
      }
      subtitle={`Maker: ${officerName(pkg.generatedBy)} · package ${shortId(pkg.id, 8)}`}
    >
      <div className="space-y-4">
        {/* Before anything that looks like a decision: this one discharges no
            obligation and goes nowhere. */}
        {pkg.isRehearsal && <RehearsalNotice />}

        {requestReason && (
          <p className="text-caption text-navy/80 rounded border border-border-light bg-surface px-3 py-2">
            Maker&apos;s note: {requestReason}
          </p>
        )}

        {/* WHERE THIS RETURN IS, first — the founder's instruction, and the
            right shape besides: an approver's first question is not "what do
            these figures say" but "what am I being asked to do, and what
            happens when I do it". The chain answers both, including that
            approving hands the return to the Validator rather than filing it.
            Mounted open, because a queue row gives no other clue that a third
            stage exists at all. */}
        {/* The chain, from the CHAIN — not derived from `status`.
            `FilingChainPanel` infers the position from the status and the
            approval rows, which cannot distinguish stage 2 from stage 3: it
            marked "Approver" as current while the return was sitting with the
            Validator. The pinned stages and their recorded decisions are the
            authority, and the bank's own stage titles come with them. */}
        {chain ? (
          <div className="rounded border border-border-light bg-surface px-3.5 py-3">
            <div className="flex flex-wrap items-center gap-2 text-caption">
              {chain.stages.map((stage, index) => {
                const current = stage.seq === chain.currentStageSeq;
                const done =
                  chain.currentStageSeq != null && stage.seq < chain.currentStageSeq;
                return (
                  <span key={stage.seq} className="inline-flex items-center gap-2">
                    {index > 0 && <span className="text-slate">→</span>}
                    <span
                      className={
                        current
                          ? "font-medium text-navy"
                          : done
                            ? "text-positive"
                            : "text-slate"
                      }
                    >
                      {stage.title}
                    </span>
                  </span>
                );
              })}
              <span className="text-slate">→</span>
              <span className="text-slate">{centralBankName()}</span>
              <span className="ml-auto text-slate">
                round {chain.round}
              </span>
            </div>

            {chain.stages.some((stage) => stage.decisions.length > 0) && (
              <ul className="mt-3 space-y-1.5 border-t border-border-light pt-3">
                {chain.stages.flatMap((stage) =>
                  stage.decisions.map((decision) => (
                    <li
                      key={`${stage.seq}-${decision.round}-${decision.createdAt.toISOString()}`}
                      className="flex flex-wrap items-baseline gap-2 text-caption text-navy/85"
                    >
                      <span className="font-mono text-micro text-slate">
                        {fmtTimestamp(decision.createdAt)}
                      </span>
                      <span className="font-medium text-navy">
                        {decision.decidedByName}
                      </span>
                      <span className="text-slate">{stage.title}</span>
                      <span className="text-slate">round {decision.round}</span>
                      <span>{FILING_DECISION_LABELS[decision.decision]}</span>
                      {decision.comment && (
                        <span className="text-slate">— {decision.comment}</span>
                      )}
                    </li>
                  )),
                )}
              </ul>
            )}
          </div>
        ) : null}

        {/* What the preparer committed to, read-only — above the decision
            because it is a precondition of it, not a footnote to it. */}
        {attestation && (
          <div className="rounded border border-border-light bg-surface px-3.5 py-3">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Attestation
            </p>
            <div className="mt-2">
              <AttestationSummary status={attestation} />
            </div>
          </div>
        )}

        {downtime || pendingReupload ? (
          <DowntimeFallback
            returnCode={pkg.returnCode}
            reportingDate={fmtDateUTC(pkg.reportingDate)}
            deadlineLabel={null}
            message={downtime ?? "This return was filed by the downtime bundle."}
            attemptsLabel={null}
            recipient={
              fallbackQuery.data?.recipientGuidance.downtimeReturnAddress ?? null
            }
            subject={fallbackQuery.data?.subject ?? null}
            attachments={(fallbackQuery.data?.attachments ?? []).map(
              (file) => ({
                filename: file.filename,
                role: file.kind,
                sizeBytes: file.sizeBytes,
              }),
            )}
            instructions={fallbackQuery.data?.instructions ?? null}
            onDownloadEml={() => {
              void downloadEmailFallbackEml(bankId!, pkg.id);
            }}
            onRecordEmailSubmission={() =>
              decide.mutate({ packageId: pkg.id, action: "approved" })
            }
            onRetryPortal={() => setConfirmingTransmit(true)}
            recording={decide.isPending}
            error={decide.error}
            pendingReupload={Boolean(pendingReupload)}
          />
        ) : isFiled ? (
          <FiledRecord
            pkg={pkg}
            events={filedEvents}
            chain={chain}
            decisionLabels={FILING_DECISION_LABELS}
            onCheckStatus={() => poll.mutate(pkg.id)}
            checking={poll.isPending}
            checkError={poll.error}
            lastCheckedLabel={
              poll.data ? fmtTimestamp(new Date()) : null
            }
            onDownloadReceipt={null}
            onRequestResubmission={null}
          />
        ) : withValidatorNotMine ? (
          <div
            data-testid="with-validator"
            className="rounded border border-border-light bg-surface px-3.5 py-3"
          >
            <p className="text-body font-medium text-navy">
              This return is with the Validator.
            </p>
            <p className="mt-1 text-caption leading-relaxed text-navy/85">
              They review it and file it with {centralBankName()}. Filing is the
              Validator&apos;s authority alone, so there is nothing for you to
              do here.
            </p>
          </div>
        ) : atTransmitStage ? (
          // The transmitting stage. The act is not "approve and hand on" — it
          // is the filing itself, so it gets the filing's own confirmation:
          // the exact files, the gates that already hold, and a statement that
          // it cannot be recalled.
          <div
            data-testid="validator-surface"
            className="rounded border border-action/30 bg-action-light/40 px-3.5 py-3"
          >
            <p className="text-body font-medium text-navy">
              This return is with you as Validator.
            </p>
            <p className="mt-1 text-caption leading-relaxed text-navy/85">
              Approving it files it with {centralBankName()}. Read the figures
              below first — after this it cannot be recalled.
            </p>
            {/* The same three acts as the Approver, ending in the filing:
                Approve, Reject, Transmit. Transmit is unavailable until the
                approval is recorded — the Validator approves the figures, and
                only then releases them to the regulator. */}
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                data-testid="validator-approve"
                disabled={stageDecision.isPending || Boolean(chain?.awaitingHandOff)}
                onClick={() =>
                  chain &&
                  stageDecision.mutate({
                    packageId: pkg.id,
                    decision: "approved",
                    round: chain.round,
                    reviewDigest: chain.reviewDigest,
                  })
                }
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {stageDecision.isPending && (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                )}
                Approve
              </button>
              {/* The Validator's OTHER act. Filing is not the only thing they
                  can do with a return they are unhappy with — and a reviewer
                  whose only button is the irreversible one is being pushed
                  toward it. The target is named (Approver or Preparer) because
                  "returned" without a destination is a status going backwards
                  rather than a hand to somebody. */}
              <button
                type="button"
                data-testid="open-send-back"
                disabled={stageDecision.isPending || !chain}
                onClick={() => {
                  setSendingBack(true);
                  setSendBackTo(
                    chain && chain.currentStageSeq
                      ? chain.currentStageSeq - 1
                      : null,
                  );
                }}
                className="rounded-md border border-warning/40 bg-warning-light/40 px-3 py-2 text-caption font-medium text-navy hover:bg-warning-light/60 disabled:opacity-60"
              >
                Send back…
              </button>

              <button
                type="button"
                data-testid="open-transmit"
                disabled={
                  handOff.isPending ||
                  stageDecision.isPending ||
                  !chain?.awaitingHandOff
                }
                title={
                  chain?.awaitingHandOff
                    ? undefined
                    : "Approve this return first."
                }
                onClick={() => setConfirmingTransmit(true)}
                className="inline-flex items-center gap-1.5 rounded-md border border-action/40 bg-action-light/40 px-3 py-2 text-caption font-medium text-navy hover:bg-action-light/60 disabled:opacity-50"
              >
                Transmit to {regShort()}
              </button>
            </div>

            {sendingBack && chain && (
              <div
                data-testid="send-back"
                className="mt-3 rounded border border-warning/30 bg-warning-light/40 px-3.5 py-3"
              >
                <p className="text-body font-medium text-navy">
                  Send this return back
                </p>
                <label className="mt-2 block text-caption text-navy/85">
                  To
                  <select
                    value={sendBackTo ?? ""}
                    onChange={(event) =>
                      setSendBackTo(Number(event.target.value) || null)
                    }
                    className="ml-2 rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
                  >
                    {chain.stages
                      .filter(
                        (stage) =>
                          chain.currentStageSeq !== undefined &&
                          chain.currentStageSeq !== null &&
                          stage.seq < chain.currentStageSeq,
                      )
                      .map((stage) => (
                        <option key={stage.seq} value={stage.seq}>
                          {stage.title}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="mt-2 block text-caption font-medium text-navy">
                  Why
                  <span className="ml-1 font-normal text-slate">
                    (required — they act on this)
                  </span>
                  <textarea
                    value={sendBackComment}
                    onChange={(event) => setSendBackComment(event.target.value)}
                    rows={2}
                    placeholder="e.g. USD outflows exclude the maturing placement."
                    className="mt-1 w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
                  />
                </label>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    data-testid="confirm-send-back"
                    disabled={
                      stageDecision.isPending ||
                      sendBackComment.trim().length === 0 ||
                      sendBackTo === null
                    }
                    onClick={() =>
                      stageDecision.mutate(
                        {
                          packageId: pkg.id,
                          decision: "returned",
                          round: chain.round,
                          reviewDigest: chain.reviewDigest,
                          comment: sendBackComment.trim(),
                          returnToSeq: sendBackTo ?? undefined,
                        },
                        {
                          onSuccess: () => {
                            setSendingBack(false);
                            setSendBackComment("");
                          },
                        },
                      )
                    }
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-50"
                  >
                    Send back
                  </button>
                  <button
                    type="button"
                    disabled={stageDecision.isPending}
                    onClick={() => setSendingBack(false)}
                    className="rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {stageDecision.error ? (
              <div className="mt-3">
                <ErrorPanel
                  error={stageDecision.error}
                  title="The filing was not accepted"
                />
              </div>
            ) : null}
          </div>
        ) : signingRequired ? (
          <>
            <Link
              href={reviewHref}
              data-testid="review-and-sign"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              <PenLine size={13} aria-hidden />
              Review and sign
            </Link>
            <p className="text-caption text-slate leading-relaxed">
              Opens the return itself with the preparer&apos;s signature on it.
              Approving and signing are one act recorded in one transaction, so
              neither can exist without the other — and the same screen sends
              the return back for corrections with a note if you have concerns.
            </p>
          </>
        ) : (
          <>
            {/* Once this officer has decided, the decision CONTROLS go away
                entirely — not disabled beside a line saying there is nothing
                further to do, which is what this card used to show. A control
                that cannot be used is still a control: it invites the click,
                and the refusal that follows reads as a fault. What replaces
                them states what happened and where the return went. */}
            {/* "Nothing further to do" is true only once the return has
                actually LEFT this stage. While it is approved and awaiting the
                hand-off it is still here, still this officer's to send — so
                the controls stay, with Send now available. This panel used to
                fire on the approval alone: it hid the Send button the instant
                it became usable and announced the return was with the
                Validator while it had not moved. */}
            {alreadyDecided && !chain?.awaitingHandOff ? (
            <div
              data-testid="already-decided"
              className="rounded border border-success/30 bg-success-light/40 px-3.5 py-3"
            >
              <p className="text-body font-medium text-navy">
                You approved this return and sent it on.
              </p>
              <p className="mt-1 text-caption leading-relaxed text-navy/85">
                Separation of duties means whoever decides one stage cannot
                decide the next, so there is nothing further for you to do on
                this return.
              </p>
            </div>
            ) : (
              <>
              <div>
                <label className="block text-caption font-medium text-navy mb-1.5">
                  Reason{" "}
                  <span className="font-normal text-slate">
                    (required to reject)
                  </span>
                </label>
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={2}
                  placeholder="e.g. Cross-checked HQLA stock against the buffer dashboard."
                  className="w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
                />
              </div>


              {chain?.awaitingHandOff && (
                <p
                  data-testid="awaiting-hand-off"
                  className="rounded border border-success/30 bg-success-light/40 px-3.5 py-2.5 text-caption leading-relaxed text-navy/85"
                >
                  <span className="font-medium text-navy">
                    Your approval is recorded.
                  </span>{" "}
                  The return is still with you until you send it to the
                  Validator.
                </p>
              )}

              {/* Three acts, in the order the bank works in: approve the
                  figures, or send them back — and only once approved, pass the
                  return on. The hand-off is present from the start and simply
                  unavailable until the approval exists, so the officer can see
                  what comes next instead of discovering it. `awaitingHandOff`
                  is the SERVER's answer to "is it approved yet"; the screen
                  never decides that for itself. */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={decide.isPending || Boolean(chain?.awaitingHandOff)}
                  onClick={() =>
                    decide.mutate({
                      packageId: pkg.id,
                      action: "approved",
                      reason: reason.trim() || undefined,
                    })
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  {decide.isPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <CheckCircle2 size={13} aria-hidden />
                  )}
                  Approve
                </button>
                <button
                  type="button"
                  disabled={decide.isPending || rejectNeedsReason}
                  title={
                    alreadyDecided
                      ? "You have already decided on this return."
                      : rejectNeedsReason
                        ? "A reason is required when rejecting."
                        : undefined
                  }
                  onClick={() =>
                    decide.mutate({
                      packageId: pkg.id,
                      action: "rejected",
                      reason: reason.trim(),
                    })
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light disabled:opacity-60"
                >
                  <XCircle size={13} aria-hidden />
                  Reject (rework)
                </button>

                {/* Third, and unavailable until the approval exists. */}
                <button
                  type="button"
                  data-testid="send-to-validator"
                  disabled={
                    handOff.isPending ||
                    decide.isPending ||
                    !chain?.awaitingHandOff
                  }
                  title={
                    chain?.awaitingHandOff
                      ? undefined
                      : "Approve this return first."
                  }
                  onClick={() => handOff.mutate({ packageId: pkg.id })}
                  className="inline-flex items-center gap-1.5 rounded-md border border-action/40 bg-action-light/40 px-3 py-2 text-caption font-medium text-navy hover:bg-action-light/60 disabled:opacity-50"
                >
                  {handOff.isPending && (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  )}
                  Send to Validator
                </button>
              </div>

              {handOff.error ? (
                <ErrorPanel
                  error={handOff.error}
                  title="The return was not sent on"
                />
              ) : null}

              <p className="text-caption text-slate leading-relaxed">
                An administrator has relaxed signing for this return, so there is
                no ceremony to route the decision through. Approving requires the
                approver role; the decision is recorded against your login.
              </p>
              </>
            )}
          </>
        )}

        {/* The return itself, BELOW the act.

            The approver's screen is the return; the decision is the one thing
            they do to it. So the card leads with what is being asked and what
            pressing it does — chain, attestation, decision — and then gives the
            figures the full remaining page, rather than making an officer
            scroll past every LCR line to reach the button they came for.
            Earlier this card had the opposite problem and no figures at all;
            the fix for that was to show them, not to put them in the way. */}
        <div className="rounded border border-border-light bg-surface px-3.5 py-3">
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            The return
          </p>
          <p className="mt-1 text-caption text-slate">
            The immutable figures this version carries — exactly what the
            artifacts render.
          </p>
          <div className="mt-3">
            {detailQuery.isLoading ? (
              <SkeletonTable />
            ) : detailQuery.error ? (
              <ErrorPanel
                error={detailQuery.error}
                title="Could not load the return"
              />
            ) : detailQuery.data ? (
              <SnapshotPreview snapshot={detailQuery.data.snapshot} />
            ) : null}
          </div>
        </div>

        {/* Mounted as soon as it is asked for. Gating the dialog on
            `filingSetQuery.data` meant the first press rendered NOTHING while
            the preview was still in flight — the button looked dead, and the
            dialog appeared later on an unrelated click. A control must respond
            to the press that triggered it, even if what it has to show first
            is that it is loading. */}
        {confirmingTransmit && (!chain || !filingSetQuery.data) && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Preparing the filing"
            aria-busy="true"
            className="fixed inset-0 z-50 flex items-start justify-center px-4 py-10"
          >
            <button
              type="button"
              aria-label="Cancel"
              onClick={() => setConfirmingTransmit(false)}
              className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            />
            <div className="card relative w-full max-w-4xl px-6 py-5">
              {filingSetQuery.error ? (
                <ErrorPanel
                  error={filingSetQuery.error}
                  title="Could not read what would be filed"
                />
              ) : (
                <p className="inline-flex items-center gap-2 text-body text-navy">
                  <Loader2 size={15} className="animate-spin" aria-hidden />
                  Reading exactly what would be filed…
                </p>
              )}
            </div>
          </div>
        )}

        {confirmingTransmit && chain && filingSetQuery.data && (
          <TransmitConfirmDialog
            preview={{
              returnCode: pkg.returnCode,
              returnName: pkg.returnCode,
              reportingDate: fmtDateUTC(pkg.reportingDate),
              institutionName: pkg.bankId,
              deadline: null,
              channelLabel: regShort(),
              isSimulated: false,
              institutionCode: filingSetQuery.data.institutionCode ?? null,
              submissionRevision: filingSetQuery.data.submissionRevision,
              isFirstFiling: filingSetQuery.data.isFirstFiling,
              filingSet: filingSetQuery.data.filingSet.map((entry) => ({
                kind: entry.kind,
                filename: entry.filename,
                role: entry.role,
                sizeBytes: entry.sizeBytes ?? null,
                generatedAtSubmission: entry.generatedAtSubmission,
                signatureCount: entry.signatureCount ?? null,
              })),
              satisfied: filingSetQuery.data.satisfied,
              omissions: filingSetQuery.data.omissions,
              contentDigest: filingSetQuery.data.contentDigest ?? null,
            }}
            regulatorName={centralBankName()}
            pending={handOff.isPending}
            error={handOff.error}
            onCancel={() => setConfirmingTransmit(false)}
            onConfirm={() =>
              // The approval is already recorded; this is the hand-off, and at
              // the transmitting stage the hand-off IS the filing.
              handOff.mutate(
                { packageId: pkg.id },
                { onSuccess: () => setConfirmingTransmit(false) },
              )
            }
          />
        )}

        {decide.error && (
          <ErrorPanel error={decide.error} title="Decision rejected" />
        )}
        {decide.isSuccess && (
          // Never the raw status: approving does NOT move a return out of
          // `pending_approval` — the chain advances it to the next stage and
          // the legacy status stays put, so printing it said "moved to
          // 'pending_approval'" to someone who had just approved. What the
          // officer needs to know is where it went, and the chain above says
          // so.
          <p className="text-caption text-success font-medium">
            Decision recorded. The return has moved to the next officer in the
            chain — see the stages above.
          </p>
        )}
      </div>
    </SectionCard>
  );
}
