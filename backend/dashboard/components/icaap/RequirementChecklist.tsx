"use client";

/**
 * The framework's requirements for one section.
 *
 * The text shown is `resolvedText`, not the framework's raw wording: a
 * requirement may carry `{param:<code>}` placeholders, and the SERVER fills
 * them from the governed parameter set (D-024/D-040 A4). Rendering
 * `item.text` would print the placeholder, or tempt someone to substitute a
 * number here.
 *
 * Each row is a requirement the regulator's text asks for, with its citation.
 * Three rules are enforced by the server and mirrored here so the UI does not
 * promise something the API will refuse:
 *
 *  - "Not applicable" is only offered for a requirement the framework marks
 *    waivable, and always needs a reason — the reason reaches the exported
 *    document, because a waiver nobody can read is not a waiver.
 *  - A requirement whose condition does not hold for this cycle (a group-only
 *    item on a solo cycle) is shown as automatically not applicable and cannot
 *    be toggled.
 *  - A section whose primary text has not been read yet says so: its checklist
 *    is incomplete by construction (D-006).
 */

import { useState } from "react";
import { Check, CircleDashed, MinusCircle } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import { regShort } from "@/lib/format";
import {
  useSetIcaapRequirementState,
  type IcaapRequirementRead,
  type IcaapSectionRead,
} from "@/lib/api/icaap";
import { sourceStatusNote } from "./format";
import { SecondaryButton } from "./Dialog";

const MIN_REASON = 10;

function RequirementRow({
  requirement,
  readOnly,
  onSet,
  pending,
}: {
  requirement: IcaapRequirementRead;
  readOnly: boolean;
  pending: boolean;
  onSet: (status: "open" | "met" | "not_applicable", reason?: string) => void;
}) {
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reason, setReason] = useState(requirement.reason ?? "");
  const auto = !requirement.applicable;
  const met = requirement.status === "met";
  const notApplicable = requirement.status === "not_applicable";

  return (
    <li className="border-b border-border-light px-4 py-3 last:border-0">
      <div className="flex items-start gap-3">
        <button
          type="button"
          aria-label={met ? "Mark as not done" : "Mark as done"}
          aria-pressed={met}
          disabled={readOnly || auto || pending}
          onClick={() => onSet(met ? "open" : "met")}
          className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
            met
              ? "border-success bg-success text-white"
              : "border-border text-transparent hover:border-action"
          }`}
        >
          <Check size={12} aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <p
            className={`text-body ${
              notApplicable || auto ? "text-slate line-through" : "text-ink"
            }`}
          >
            {requirement.resolvedText}
          </p>
          <p className="mt-0.5 text-caption text-slate">
            {requirement.item.citations
              .map((citation) => citation.label)
              .join(" · ")}
          </p>
          {auto && (
            <p className="mt-1 inline-flex items-center gap-1 text-caption text-slate">
              <MinusCircle size={11} aria-hidden />
              Not applicable to this cycle
            </p>
          )}
          {notApplicable && requirement.reason && (
            <p className="mt-1 text-caption text-slate">
              Marked not applicable: {requirement.reason}
            </p>
          )}
          {reasonOpen && (
            <div className="mt-2 space-y-2">
              <textarea
                className="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-body text-ink focus:border-action focus:outline-none"
                rows={2}
                value={reason}
                maxLength={2000}
                placeholder="Why does this requirement not apply to this institution?"
                onChange={(event) => setReason(event.target.value)}
              />
              <div className="flex items-center gap-2">
                <SecondaryButton
                  disabled={reason.trim().length < MIN_REASON || pending}
                  onClick={() => {
                    onSet("not_applicable", reason.trim());
                    setReasonOpen(false);
                  }}
                >
                  Save reason
                </SecondaryButton>
                <SecondaryButton onClick={() => setReasonOpen(false)}>
                  Cancel
                </SecondaryButton>
                {reason.trim().length < MIN_REASON && (
                  <span className="text-caption text-slate">
                    A reason of at least {MIN_REASON} characters is required.
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
        {!readOnly && !auto && requirement.item.waivable && !reasonOpen && (
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              notApplicable ? onSet("open") : setReasonOpen(true)
            }
            className="shrink-0 text-caption font-medium text-action hover:underline disabled:opacity-40"
          >
            {notApplicable ? "Make applicable" : "Not applicable"}
          </button>
        )}
      </div>
    </li>
  );
}

export default function RequirementChecklist({
  bankId,
  cycleId,
  section,
  readOnly,
}: {
  bankId: string;
  cycleId: string;
  section: IcaapSectionRead;
  readOnly: boolean;
}) {
  const setState = useSetIcaapRequirementState(bankId, cycleId, section.key);
  const pendingNote = sourceStatusNote(section.sourceStatus, regShort());
  const open = section.requirements.filter(
    (requirement) => requirement.applicable && requirement.status === "open",
  ).length;

  return (
    <section className="card overflow-hidden">
      <div className="border-b border-border-light px-4 py-3">
        <h3 className="text-h3 text-navy">Requirements</h3>
        <p className="mt-0.5 inline-flex items-center gap-1.5 text-caption text-slate">
          <CircleDashed size={11} aria-hidden />
          {open === 0
            ? "Every applicable requirement is addressed."
            : `${open} still open`}
        </p>
        {pendingNote && (
          <p className="mt-1 text-caption text-warning">{pendingNote}</p>
        )}
      </div>
      <ul className="max-h-[32rem] overflow-y-auto">
        {section.requirements.map((requirement) => (
          <RequirementRow
            key={requirement.item.id}
            requirement={requirement}
            readOnly={readOnly}
            pending={setState.isPending}
            onSet={(status, reason) =>
              setState.mutate({
                itemId: requirement.item.id,
                status,
                reason: reason ?? null,
              })
            }
          />
        ))}
      </ul>
      {setState.error != null && (
        <p className="border-t border-border-light px-4 py-2 text-caption text-critical">
          {isApiError(setState.error)
            ? setState.error.message
            : "Could not record that change."}
        </p>
      )}
    </section>
  );
}
