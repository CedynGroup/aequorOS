"use client";

/**
 * The public disclosure (¶82): which parts of the assessment the institution
 * publishes.
 *
 * THE RULE THIS SCREEN EXISTS TO MAKE VISIBLE: supervisory information is
 * never published. A figure the supervisor gave the institution privately —
 * an add-on set by letter, and anything the framework marks as never public —
 * is stripped from whatever is selected, and the stripped items are listed by
 * name so that nobody has to take the platform's word for it.
 *
 * Every section starts NOT selected and NOT selectable: publication is the one
 * act on this workspace that cannot be taken back, and the absence of a flag
 * on a payload must never read as permission.
 *
 * Approval is maker-checker, like every other decision here: the officer who
 * proposed what to publish cannot be the one who approves it.
 */

import { useEffect, useState } from "react";
import { Globe } from "lucide-react";
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
import {
  ICON_SM,
  RATIONALE_MAX,
  REASON_MIN,
  ROWS_LONG,
} from "@/components/icaap/p2/display";
import {
  useDecideIcaapDisclosure,
  useIcaapDisclosure,
  usePutIcaapDisclosure,
  useSubmitIcaapDisclosure,
} from "@/lib/api/icaapFiling";
import { DISCLOSURE_NEVER_PUBLIC, disclosureStatusCopy } from "./labels";

export default function DisclosurePanel({
  bankId,
  cycleId,
  canEdit,
  canApprove,
}: {
  bankId: string;
  cycleId: string;
  canEdit: boolean;
  canApprove: boolean;
}) {
  const query = useIcaapDisclosure(bankId, cycleId);
  const put = usePutIcaapDisclosure(bankId, cycleId);
  const submit = useSubmitIcaapDisclosure(bankId, cycleId);
  const decide = useDecideIcaapDisclosure(bankId, cycleId);
  const [selected, setSelected] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deciding, setDeciding] = useState<"approved" | "rejected" | null>(null);

  const data = query.data;

  // The server's selection is the starting point; the local set only diverges
  // while somebody is choosing.
  useEffect(() => {
    if (data && selected === null) {
      setSelected(
        (data.sections ?? [])
          .filter((row) => row.selected)
          .map((row) => row.key),
      );
    }
  }, [data, selected]);

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Public disclosure" message={unavailable} />;
  }

  const status = data ? disclosureStatusCopy(data.status) : null;

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      {data && (
        <SectionCard
          title="Public disclosure"
          subtitle="What the institution publishes about its own capital adequacy assessment."
          actions={
            status ? <StatusPill tone={status.tone}>{status.label}</StatusPill> : undefined
          }
        >
          {!data.available ? (
            <EmptyState
              title="Not available yet"
              description={
                data.unavailableReason ??
                "A disclosure is prepared from the sealed report, once the board has approved it."
              }
            />
          ) : (
            <div className="space-y-4">
              <p className="text-body text-navy/80">
                {DISCLOSURE_NEVER_PUBLIC}
              </p>

              <ul className="space-y-1" data-testid="disclosure-sections">
                {(data.sections ?? []).map((section) => (
                  <li key={section.key}>
                    <label className="flex items-start gap-2 text-body text-navy/80">
                      <input
                        type="checkbox"
                        className="mt-1"
                        disabled={!canEdit || !section.selectable}
                        checked={(selected ?? []).includes(section.key)}
                        onChange={(event) =>
                          setSelected((current) => {
                            const base = current ?? [];
                            return event.target.checked
                              ? [...base, section.key]
                              : base.filter((key) => key !== section.key);
                          })
                        }
                      />
                      <span>
                        {section.title}
                        {!section.selectable && (
                          <span className="ml-2 text-caption text-slate">
                            Not for publication
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>

              {(data.withheld ?? []).length > 0 && (
                <div>
                  <p className="text-caption font-medium text-navy">
                    Withheld from publication
                  </p>
                  <ul className="mt-1 space-y-1">
                    {(data.withheld ?? []).map((row, index) => (
                      <li
                        key={`${row.sectionKey}-${row.blockKey}-${index}`}
                        className="text-caption text-slate"
                      >
                        {row.sectionKey} · {row.blockKey}
                        {row.factKey ? ` · ${row.factKey}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                {canEdit && (
                  <>
                    <SecondaryButton onClick={() => setSaving(true)}>
                      <Globe size={ICON_SM} aria-hidden />
                      Save what is published
                    </SecondaryButton>
                    <SecondaryButton onClick={() => setSubmitting(true)}>
                      Put forward for approval
                    </SecondaryButton>
                  </>
                )}
                {canApprove && (
                  <>
                    <PrimaryButton onClick={() => setDeciding("approved")}>
                      Approve for publication
                    </PrimaryButton>
                    <SecondaryButton onClick={() => setDeciding("rejected")}>
                      Do not approve
                    </SecondaryButton>
                  </>
                )}
              </div>

              {data.publishedUrl && (
                <p className="text-caption text-slate">
                  Published at {data.publishedUrl}
                </p>
              )}
            </div>
          )}
        </SectionCard>
      )}

      {saving && (
        <ReasonDialog
          title="Save what is published"
          description="This records the choice. It does not publish anything."
          confirm="Save"
          onClose={() => setSaving(false)}
          run={(reason, onDone) =>
            put.mutate(
              { selectedSectionKeys: selected ?? [], reason },
              { onSuccess: onDone },
            )
          }
          pending={put.isPending}
          error={put.isError ? put.error : null}
        />
      )}
      {submitting && (
        <ReasonDialog
          title="Put the disclosure forward for approval"
          description="A different officer decides whether it may be published."
          confirm="Put forward"
          onClose={() => setSubmitting(false)}
          run={(reason, onDone) =>
            submit.mutate({ reason }, { onSuccess: onDone })
          }
          pending={submit.isPending}
          error={submit.isError ? submit.error : null}
        />
      )}
      {deciding !== null && (
        <ReasonDialog
          title={
            deciding === "approved"
              ? "Approve this for publication"
              : "Do not approve this for publication"
          }
          description="Recorded in the audit trail with your name."
          confirm={deciding === "approved" ? "Approve" : "Record"}
          onClose={() => setDeciding(null)}
          run={(reason, onDone) =>
            decide.mutate({ decision: deciding, reason }, { onSuccess: onDone })
          }
          pending={decide.isPending}
          error={decide.isError ? decide.error : null}
        />
      )}
    </QueryBoundary>
  );
}

/** A short form whose only field is the mandatory reason. */
function ReasonDialog({
  title,
  description,
  confirm,
  onClose,
  run,
  pending,
  error,
}: {
  title: string;
  description: string;
  confirm: string;
  onClose: () => void;
  run: (reason: string, onDone: () => void) => void;
  pending: boolean;
  error: unknown;
}) {
  const [reason, setReason] = useState("");
  return (
    <Dialog
      title={title}
      description={description}
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={pending || reason.trim().length < REASON_MIN}
            onClick={() => run(reason.trim(), onClose)}
          >
            {pending ? "Saving…" : confirm}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3">
        {error !== null && error !== undefined && (
          <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
            {(error as Error).message}
          </p>
        )}
        <FieldLabel label="Reason">
          <textarea
            value={reason}
            rows={ROWS_LONG}
            maxLength={RATIONALE_MAX}
            onChange={(event) => setReason(event.target.value)}
            className={INPUT_CLASS}
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
