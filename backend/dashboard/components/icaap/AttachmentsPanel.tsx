"use client";

/**
 * The documents that travel with the cycle.
 *
 * Two lists, deliberately separate: what the framework REQUIRES (with the gate
 * it is required by), and what has actually been attached. A withdrawal is
 * recorded, not erased — the row stays, marked withdrawn, with its reason, so
 * the history of what the institution relied on remains readable.
 */

import { useState } from "react";
import { CheckCircle2, Circle, Download, Paperclip, Undo2 } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import { useModuleScope } from "@/components/shell/BankContext";
import {
  downloadIcaapAttachment,
  useIcaapAttachments,
  useWithdrawIcaapAttachment,
} from "@/lib/api/icaap";
import { INPUT_CLASS, SecondaryButton } from "./Dialog";
import { attachmentGateLabel, fmtTimestampValue, shortHash } from "./format";
import UploadAttachmentDialog from "./UploadAttachmentDialog";

const MIN_REASON = 10;

export default function AttachmentsPanel({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const scope = useModuleScope();
  const attachmentsQuery = useIcaapAttachments(bankId, cycleId);
  const withdraw = useWithdrawIcaapAttachment(bankId, cycleId);
  const [uploading, setUploading] = useState(false);
  const [withdrawing, setWithdrawing] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const canEdit = scope.capitalEdit === true;

  return (
    <QueryBoundary
      isLoading={attachmentsQuery.isLoading}
      error={attachmentsQuery.error}
      onRetry={() => void attachmentsQuery.refetch()}
      contained
    >
      <div className="space-y-4">
        <SectionCard
          title="What this cycle needs"
          subtitle="Set by the framework, not by the signing policy."
          noPadding
        >
          <ul>
            {(attachmentsQuery.data?.requirements ?? [])
              .filter((requirement) => requirement.applicable)
              .map((requirement) => (
                <li
                  key={requirement.kind}
                  className="flex items-start gap-3 border-b border-border-light px-5 py-3 last:border-0"
                >
                  {requirement.satisfied ? (
                    <CheckCircle2
                      size={15}
                      className="mt-0.5 shrink-0 text-success"
                      aria-hidden
                    />
                  ) : (
                    <Circle
                      size={15}
                      className="mt-0.5 shrink-0 text-slate-light"
                      aria-hidden
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-body text-navy">{requirement.title}</p>
                    <p className="mt-0.5 text-caption text-slate">
                      {attachmentGateLabel(requirement.gate)} &middot;{" "}
                      {requirement.activeCount} of {requirement.minCount}{" "}
                      attached
                    </p>
                  </div>
                </li>
              ))}
          </ul>
        </SectionCard>

        <SectionCard
          title="Attached documents"
          actions={
            canEdit ? (
              <SecondaryButton onClick={() => setUploading(true)}>
                <Paperclip size={12} aria-hidden />
                Attach
              </SecondaryButton>
            ) : undefined
          }
          noPadding
        >
          {(attachmentsQuery.data?.attachments.length ?? 0) === 0 ? (
            <p className="px-5 py-3 text-body text-slate">
              Nothing attached yet.
            </p>
          ) : (
            <ul>
              {attachmentsQuery.data?.attachments.map((attachment) => (
                <li
                  key={attachment.id}
                  className="border-b border-border-light px-5 py-3 last:border-0"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p
                        className={`text-body ${
                          attachment.withdrawn
                            ? "text-slate line-through"
                            : "text-navy"
                        }`}
                      >
                        {attachment.title}
                      </p>
                      <p className="mt-0.5 text-caption text-slate">
                        {attachment.originalFilename} &middot;{" "}
                        {fmtTimestampValue(attachment.createdAt)} &middot;{" "}
                        <span className="font-mono">
                          {shortHash(attachment.sha256)}
                        </span>
                      </p>
                      {attachment.withdrawn && (
                        <p className="mt-0.5 text-caption text-slate">
                          Withdrawn
                          {attachment.withdrawnAt
                            ? ` on ${fmtTimestampValue(attachment.withdrawnAt)}`
                            : ""}
                          {attachment.withdrawalReason
                            ? `: ${attachment.withdrawalReason}`
                            : ""}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <SecondaryButton
                        onClick={() => {
                          setDownloadError(null);
                          void downloadIcaapAttachment(
                            bankId,
                            cycleId,
                            attachment,
                          ).catch((caught: unknown) =>
                            setDownloadError(
                              isApiError(caught)
                                ? caught.message
                                : "Could not download that document.",
                            ),
                          );
                        }}
                      >
                        <Download size={12} aria-hidden />
                        Download
                      </SecondaryButton>
                      {canEdit && !attachment.withdrawn && (
                        <SecondaryButton
                          onClick={() =>
                            setWithdrawing((current) =>
                              current === attachment.id ? null : attachment.id,
                            )
                          }
                        >
                          <Undo2 size={12} aria-hidden />
                          Withdraw
                        </SecondaryButton>
                      )}
                    </div>
                  </div>

                  {withdrawing === attachment.id && (
                    <div className="mt-2 space-y-2">
                      <input
                        className={INPUT_CLASS}
                        value={reason}
                        maxLength={2000}
                        placeholder="Why is this document being withdrawn?"
                        onChange={(event) => setReason(event.target.value)}
                      />
                      <SecondaryButton
                        disabled={
                          reason.trim().length < MIN_REASON || withdraw.isPending
                        }
                        onClick={() =>
                          withdraw.mutate(
                            {
                              attachmentId: attachment.id,
                              reason: reason.trim(),
                            },
                            {
                              onSuccess: () => {
                                setWithdrawing(null);
                                setReason("");
                              },
                            },
                          )
                        }
                      >
                        Withdraw this document
                      </SecondaryButton>
                      <p className="text-caption text-slate">
                        The document stays on file and remains downloadable; it
                        stops counting towards the framework&apos;s
                        requirements.
                      </p>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {(downloadError || withdraw.error != null) && (
            <p className="border-t border-border-light px-5 py-2 text-caption text-critical">
              {downloadError ??
                (isApiError(withdraw.error)
                  ? withdraw.error.message
                  : "That action did not complete.")}
            </p>
          )}
        </SectionCard>
      </div>

      {uploading && (
        <UploadAttachmentDialog
          bankId={bankId}
          cycleId={cycleId}
          requirements={attachmentsQuery.data?.requirements ?? []}
          onClose={() => setUploading(false)}
        />
      )}
    </QueryBoundary>
  );
}
