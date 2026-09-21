"use client";

/**
 * Attach a supporting document.
 *
 * THE SERVER IS AUTHORITATIVE on what a document is: it sniffs the bytes, so a
 * PDF renamed `.docx` is refused however the browser labelled it. The accept
 * hint here is a convenience for the file picker, never a check.
 */

import { useState } from "react";
import { UploadCloud } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import {
  useUploadIcaapAttachment,
  type IcaapAttachmentRequirementStatusRead,
} from "@/lib/api/icaap";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "./Dialog";
import { attachmentGateLabel } from "./format";

export default function UploadAttachmentDialog({
  bankId,
  cycleId,
  requirements,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  requirements: readonly IcaapAttachmentRequirementStatusRead[];
  onClose: () => void;
}) {
  const applicable = requirements.filter((requirement) => requirement.applicable);
  const [kind, setKind] = useState(applicable[0]?.kind ?? "");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const upload = useUploadIcaapAttachment(bankId, cycleId);
  const selected = applicable.find((requirement) => requirement.kind === kind);

  return (
    <Dialog
      title="Attach a document"
      description="PDF or Word. Spreadsheets are accepted only where the framework allows them."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose} disabled={upload.isPending}>
            Cancel
          </SecondaryButton>
          <PrimaryButton
            disabled={!kind || !file || !title.trim() || upload.isPending}
            onClick={() =>
              file &&
              upload.mutate(
                { kind, title: title.trim(), file },
                { onSuccess: onClose },
              )
            }
          >
            <UploadCloud size={13} aria-hidden />
            {upload.isPending ? "Uploading…" : "Upload"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-4">
        <FieldLabel
          label="Document"
          hint={selected ? attachmentGateLabel(selected.gate) : undefined}
        >
          <select
            aria-label="Document"
            className={INPUT_CLASS}
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {applicable.map((requirement) => (
              <option key={requirement.kind} value={requirement.kind}>
                {requirement.title}
              </option>
            ))}
          </select>
        </FieldLabel>

        <FieldLabel label="Title">
          <input
            className={INPUT_CLASS}
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
          />
        </FieldLabel>

        <FieldLabel label="File">
          <input
            type="file"
            accept=".pdf,.docx,.xlsx"
            className={INPUT_CLASS}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </FieldLabel>

        {upload.isPending && (
          <div>
            {/* Indeterminate: `fetch`, which the generated client uses, cannot
                report upload progress, and a second transport purely for a
                percentage would re-implement auth outside the contract. */}
            <div
              className="h-1.5 w-full overflow-hidden rounded bg-surface"
              role="progressbar"
              aria-label="Uploading"
            >
              <div className="h-full w-1/3 animate-pulse rounded bg-action" />
            </div>
            <p className="mt-1 text-caption text-slate">
              Uploading. Large documents can take a moment.
            </p>
          </div>
        )}

        {upload.error != null && (
          <p className="text-body text-critical">
            {isApiError(upload.error)
              ? upload.error.message
              : "Could not upload that file."}
          </p>
        )}
      </div>
    </Dialog>
  );
}
