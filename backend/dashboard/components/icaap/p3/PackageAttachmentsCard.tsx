"use client";

/**
 * The documents that are filed WITH the report.
 *
 * The regime asks for a board resolution, a senior management report and the
 * board minutes on the stress testing; until the package-attachment plane
 * existed a signing policy could name a document and nothing could satisfy
 * it, so the requirement passed silently on every submission (audit C-6).
 *
 * TWO THINGS THIS CARD SAYS THAT NOTHING ELSE DOES:
 *
 *  1. **Where a requirement comes from, and therefore who can change it.** A
 *     requirement the regime itself imposes is NOT relaxed by changing who
 *     signs the report (D-031/M11). The screen says so, because otherwise a
 *     bank that drops the board signature slot would reasonably expect the
 *     board resolution to go with it.
 *  2. **When it is needed.** Some documents must be in hand before the report
 *     is sealed; others accompany the filing. The two are different deadlines
 *     and the labels distinguish them.
 *
 * A withdrawal is a separate, unalterable record — never an edit and never a
 * deletion — and it is refused once the return has been filed.
 */

import { useState } from "react";
import { Download, Paperclip, Undo2 } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import P2Unavailable, {
  p2UnavailableNotice,
} from "@/components/icaap/p2/availability";
import { fmtBytes } from "@/components/submissions/shared";
import { fmtLocale } from "@/lib/format";
import {
  DESCRIPTION_MAX,
  ICON_SM,
  REASON_MIN,
  REFERENCE_MAX,
  ROWS_LONG,
  TITLE_MAX,
} from "@/components/icaap/p2/display";
import { apiBaseUrl } from "@/lib/api/client";
import { getAccessToken } from "@/lib/api/token";
import {
  usePackageAttachments,
  useUploadPackageAttachment,
  useWithdrawPackageAttachment,
  type PackageAttachment,
} from "@/lib/api/icaapFiling";
import RehearsalNotice from "./RehearsalNotice";
import {
  attachmentSourceLabel,
  gateLabel,
  requirementOriginSentence,
} from "./labels";

/** The one kind whose own facts the regime asks for alongside the file. */
const BOARD_RESOLUTION = "board_resolution";

function moment(value: string | null): string {
  if (value === null) return "";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleDateString(fmtLocale())
    : "";
}

async function downloadAttachment(
  bankId: string,
  packageId: string,
  attachment: PackageAttachment,
): Promise<void> {
  const response = await fetch(
    `${apiBaseUrl}/banks/${bankId}/regulatory-packages/${packageId}/attachments/${attachment.id}/download`,
    { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } },
  );
  if (!response.ok) {
    throw new Error(`Download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = attachment.originalFilename || "document";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

export default function PackageAttachmentsCard({
  bankId,
  packageId,
  canEdit,
  isRehearsal,
}: {
  bankId: string;
  packageId: string;
  canEdit: boolean;
  isRehearsal: boolean;
}) {
  const query = usePackageAttachments(bankId, packageId);
  const [uploadKind, setUploadKind] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState<PackageAttachment | null>(null);

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return (
      <P2Unavailable title="Documents filed with this report" message={unavailable} />
    );
  }

  const data = query.data;
  const active = (data?.attachments ?? []).filter((row) => !row.withdrawn);
  const withdrawn = (data?.attachments ?? []).filter((row) => row.withdrawn);

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {data && (
        <SectionCard
          title="Documents filed with this report"
          subtitle="What the regime requires alongside the report itself, and what has been attached."
          actions={
            canEdit ? (
              <SecondaryButton onClick={() => setUploadKind(BOARD_RESOLUTION)}>
                <Paperclip size={ICON_SM} aria-hidden />
                Attach a document
              </SecondaryButton>
            ) : undefined
          }
        >
          <div className="space-y-4">
            {(data.requirements ?? []).length > 0 && (
              <ul className="space-y-2" data-testid="attachment-requirements">
                {(data.requirements ?? []).map((requirement) => (
                  <li
                    key={requirement.kind}
                    className="rounded border border-border-light p-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium text-navy">
                          {requirement.title}
                        </p>
                        <p className="text-caption text-slate">
                          {gateLabel(requirement.gate)} ·{" "}
                          {requirementOriginSentence(requirement.origin)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusPill
                          tone={requirement.satisfied ? "success" : "amber"}
                        >
                          {requirement.satisfied
                            ? "Attached"
                            : "Still to attach"}
                        </StatusPill>
                        {canEdit && !requirement.satisfied && (
                          <SecondaryButton
                            onClick={() => setUploadKind(requirement.kind)}
                          >
                            Attach
                          </SecondaryButton>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {active.length === 0 ? (
              <EmptyState
                title="Nothing attached yet"
                description="A document is identified from its own bytes, so the same file cannot be attached twice."
              />
            ) : (
              <ul className="space-y-2">
                {active.map((attachment) => (
                  <li
                    key={attachment.id}
                    className="flex flex-wrap items-start justify-between gap-2 rounded border border-border-light p-3"
                  >
                    <div className="min-w-0">
                      <p className="font-medium text-navy">{attachment.title}</p>
                      <p className="text-caption text-slate">
                        {attachment.originalFilename}
                        {attachment.byteSize !== null
                          ? ` · ${fmtBytes(attachment.byteSize)}`
                          : ""}{" "}
                        · {attachmentSourceLabel(attachment.source)} ·{" "}
                        {moment(attachment.createdAt)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <SecondaryButton
                        onClick={() =>
                          void downloadAttachment(bankId, packageId, attachment)
                        }
                      >
                        <Download size={ICON_SM} aria-hidden />
                        Download
                      </SecondaryButton>
                      {canEdit && (
                        <SecondaryButton
                          onClick={() => setWithdrawing(attachment)}
                        >
                          <Undo2 size={ICON_SM} aria-hidden />
                          Withdraw
                        </SecondaryButton>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {withdrawn.length > 0 && (
              <div>
                <p className="text-caption font-medium text-navy">
                  Withdrawn documents
                </p>
                <p className="text-caption text-slate">
                  Kept as a record. A withdrawal is never an edit and never a
                  deletion.
                </p>
                <ul className="mt-2 space-y-1">
                  {withdrawn.map((attachment) => (
                    <li key={attachment.id} className="text-caption text-slate">
                      {attachment.title} — {attachment.withdrawalReason ?? ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {isRehearsal && (
              <RehearsalNotice
                compact
                detail="Documents attached to a practice run are kept with it and are never sent anywhere."
              />
            )}
          </div>
        </SectionCard>
      )}

      {uploadKind !== null && (
        <UploadDialog
          bankId={bankId}
          packageId={packageId}
          kind={uploadKind}
          kinds={(data?.requirements ?? []).map((row) => ({
            kind: row.kind,
            title: row.title,
          }))}
          onClose={() => setUploadKind(null)}
        />
      )}
      {withdrawing !== null && (
        <WithdrawDialog
          bankId={bankId}
          packageId={packageId}
          attachment={withdrawing}
          onClose={() => setWithdrawing(null)}
        />
      )}
    </QueryBoundary>
  );
}

function UploadDialog({
  bankId,
  packageId,
  kind,
  kinds,
  onClose,
}: {
  bankId: string;
  packageId: string;
  kind: string;
  kinds: { kind: string; title: string }[];
  onClose: () => void;
}) {
  const mutation = useUploadPackageAttachment(bankId);
  const [selectedKind, setSelectedKind] = useState(kind);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [resolutionDate, setResolutionDate] = useState("");
  const [resolutionReference, setResolutionReference] = useState("");

  const needsResolutionFacts = selectedKind === BOARD_RESOLUTION;
  const options =
    kinds.length > 0
      ? kinds
      : [{ kind: BOARD_RESOLUTION, title: "Board resolution" }];

  return (
    <Dialog
      title="Attach a document"
      description="The file is identified from its own bytes, so the same document cannot be attached twice."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              file === null ||
              title.trim() === "" ||
              (needsResolutionFacts &&
                (resolutionDate === "" || resolutionReference.trim() === ""))
            }
            onClick={() =>
              mutation.mutate(
                {
                  packageId,
                  kind: selectedKind,
                  title: title.trim(),
                  file: file!,
                  attributes: needsResolutionFacts
                    ? {
                        resolution_date: resolutionDate,
                        resolution_reference: resolutionReference.trim(),
                      }
                    : undefined,
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Uploading…" : "Upload"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}
        <FieldLabel label="Document">
          <select
            aria-label="Document"
            value={selectedKind}
            onChange={(event) => setSelectedKind(event.target.value)}
            className={INPUT_CLASS}
          >
            {options.map((option) => (
              <option key={option.kind} value={option.kind}>
                {option.title}
              </option>
            ))}
          </select>
        </FieldLabel>
        <FieldLabel label="Title">
          <input
            value={title}
            maxLength={TITLE_MAX}
            onChange={(event) => setTitle(event.target.value)}
            className={INPUT_CLASS}
          />
        </FieldLabel>
        <FieldLabel label="File">
          {/* The same three formats the server will accept, sniffed from the
              bytes rather than the extension. The hint saves a round trip; it
              decides nothing. */}
          <input
            type="file"
            accept=".pdf,.docx,.xlsx"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className={INPUT_CLASS}
          />
        </FieldLabel>
        {needsResolutionFacts && (
          <>
            <p className="text-caption text-slate">
              A board resolution is recorded with the date it was passed and its
              reference in the minutes, so the filing can be tied to the meeting
              that authorised it.
            </p>
            <FieldLabel label="Date the resolution was passed">
              <input
                type="date"
                value={resolutionDate}
                onChange={(event) => setResolutionDate(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
            <FieldLabel label="Reference in the minutes">
              <input
                value={resolutionReference}
                maxLength={REFERENCE_MAX}
                onChange={(event) =>
                  setResolutionReference(event.target.value)
                }
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </>
        )}
      </div>
    </Dialog>
  );
}

function WithdrawDialog({
  bankId,
  packageId,
  attachment,
  onClose,
}: {
  bankId: string;
  packageId: string;
  attachment: PackageAttachment;
  onClose: () => void;
}) {
  const mutation = useWithdrawPackageAttachment(bankId);
  const [reason, setReason] = useState("");

  return (
    <Dialog
      title="Withdraw this document"
      description="The document and this withdrawal are both kept. Nothing is deleted."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending || reason.trim().length < REASON_MIN
            }
            onClick={() =>
              mutation.mutate(
                {
                  packageId,
                  attachmentId: attachment.id,
                  reason: reason.trim(),
                },
                { onSuccess: onClose },
              )
            }
          >
            {mutation.isPending ? "Withdrawing…" : "Withdraw"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {mutation.isError && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(mutation.error as Error).message}
          </p>
        )}
        <FieldLabel label="Why it should not have been filed">
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
