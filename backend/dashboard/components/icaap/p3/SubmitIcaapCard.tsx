"use client";

/**
 * Recording the filing.
 *
 * The ICAAP report is not transmitted by the platform: it is taken to the
 * regulator by the institution, and what the platform records is WHICH sealed
 * revision and WHICH documents went with it, by hash, so that "what exactly
 * did we file for that year" is answerable years later. That is why the button
 * says "record", and why there is no channel to choose.
 *
 * The blockers shown here are the SERVER's, from the same gate the submission
 * itself runs (`ensure_submittable`). This screen never decides that a report
 * is ready — `submittable` is false until the server says otherwise — and it
 * never restates a refusal in its own words.
 */

import { useState } from "react";
import { Send } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import { REASON_MAX } from "@/components/icaap/p2/display";
import { ICON_SM } from "@/components/icaap/p2/display";
import { regShort } from "@/lib/format";
import { useSubmitIcaapPackage, type IcaapFiling } from "@/lib/api/icaapFiling";
import BlockerList from "./BlockerList";
import RehearsalNotice from "./RehearsalNotice";
import { REHEARSAL_SUBMISSION_WARNING } from "./labels";

/** Statuses at which the filing has already been recorded. */
const ALREADY_FILED = new Set(["submitted", "acknowledged"]);

export default function SubmitIcaapCard({
  bankId,
  filing,
  canSubmit,
  isRehearsal,
}: {
  bankId: string;
  filing: IcaapFiling;
  /** Approval authority for this institution. The server re-decides it. */
  canSubmit: boolean;
  isRehearsal: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const mutation = useSubmitIcaapPackage(bankId);

  const pkg = filing.package;
  const filed = pkg !== null && ALREADY_FILED.has(pkg.status);

  return (
    <SectionCard
      title={isRehearsal ? "Record a practice submission" : "Record the filing"}
      subtitle={`The report is taken to ${regShort()} by the institution. What is recorded here is which sealed version and which documents went with it.`}
      actions={
        !filed && canSubmit && filing.submittable && pkg !== null ? (
          <PrimaryButton onClick={() => setOpen(true)}>
            <Send size={ICON_SM} aria-hidden />
            {isRehearsal ? "Record a practice submission" : "Record the filing"}
          </PrimaryButton>
        ) : undefined
      }
    >
      <div className="space-y-3">
        {filed ? (
          <p className="text-body text-navy/80">
            The filing has been recorded. The sealed version and the documents
            that went with it are held unchanged as the record of what was
            filed.
          </p>
        ) : (
          <>
            <BlockerList
              items={filing.blockers ?? []}
              emptyTitle="Nothing is outstanding"
              emptyDescription="The report is signed and every required document is attached."
            />
            {!canSubmit && (
              <p className="text-body text-navy/80">
                Recording the filing is an approver&apos;s act, and it cannot be
                taken by the officer who produced the filing.
              </p>
            )}
          </>
        )}

        {isRehearsal && (
          <RehearsalNotice compact detail={REHEARSAL_SUBMISSION_WARNING} />
        )}
      </div>

      {open && pkg !== null && (
        <Dialog
          title={
            isRehearsal ? "Record a practice submission" : "Record the filing"
          }
          description="This records what was filed. It does not send anything."
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
                    {
                      packageId: pkg.id,
                      externalRef: reference.trim() || null,
                    },
                    { onSuccess: () => setOpen(false) },
                  )
                }
              >
                {mutation.isPending ? "Recording…" : "Record"}
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
            {isRehearsal && (
              <RehearsalNotice detail={REHEARSAL_SUBMISSION_WARNING} />
            )}
            <FieldLabel
              label="Reference"
              hint="Optional. The receipt, letter or acknowledgement number the regulator gave you."
            >
              <input
                value={reference}
                maxLength={REASON_MAX}
                onChange={(event) => setReference(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
          </div>
        </Dialog>
      )}
    </SectionCard>
  );
}
