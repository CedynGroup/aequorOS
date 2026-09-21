"use client";

/**
 * Carry the approved Pillar 2 add-ons into the capital plan, as a DRAFT.
 *
 * The maker-checker is the capital plan's existing one and it is not weakened
 * here: whoever proposes becomes the draft's preparer, and a different person
 * must approve it in Capital planning. That sentence is on the dialog, because
 * a preparer who expects the plan to change immediately will otherwise read the
 * silence as a failure.
 *
 * NOTHING IS COMPUTED IN THIS FILE. The "% of total RWA" conversions are the
 * server's, returned on the POST response and printed exactly as they arrive —
 * a client-side conversion would need the RWA denominator and the quantum
 * rules, and would be a second implementation of a filing figure.
 */

import { useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import Dialog, {
  FieldLabel,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  useProposeIcaapCapitalPlanUpdate,
  type IcaapCapitalPlanProposalRead,
  type IcaapPillar2Register,
} from "@/lib/api/icaapRiskCapital";
import { ICON_SM, REASON_MAX } from "./display";
import { NOT_MODELLED, basisLabel, fmtAmount } from "./labels";

export default function CapitalPlanProposalDialog({
  bankId,
  cycleId,
  register,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  register: IcaapPillar2Register;
  onClose: () => void;
}) {
  const mutation = useProposeIcaapCapitalPlanUpdate(bankId, cycleId);
  const [replaceExistingDraft, setReplaceExistingDraft] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<IcaapCapitalPlanProposalRead | null>(
    null,
  );

  // Only an item whose approval is CURRENT may be carried: an approval on an
  // earlier revision is not an approval of the amount this would copy.
  const approved = (register.items ?? []).filter((item) => item.approvalCurrent);

  const draftExists =
    mutation.isError &&
    (mutation.error as { details?: { error_code?: string } } | undefined)
      ?.details?.error_code === "capital_plan_draft_exists";

  if (result) {
    return (
      <Dialog
        title="Capital plan draft created"
        description="The add-ons below are in the draft. They do not take effect until a different person approves it."
        onClose={onClose}
        wide
        footer={
          <>
            <SecondaryButton onClick={onClose}>Close</SecondaryButton>
            <Link
              href="/basel/planning"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              Open capital planning
              <ArrowUpRight size={ICON_SM} aria-hidden />
            </Link>
          </>
        }
      >
        <div className="space-y-3">
          <ul className="space-y-2">
            {(result.addons ?? []).map((conversion) => (
              <li
                key={conversion.itemKey}
                className="rounded border border-border-light p-3"
              >
                <p className="font-medium text-navy">{conversion.riskType}</p>
                <p className="tnum text-body text-navy/80">
                  {conversion.amount} — {conversion.addOnPctRwa}
                </p>
                <p className="text-caption text-slate">
                  {conversion.rationale}
                </p>
              </li>
            ))}
          </ul>
          {(result.notes ?? []).map((note) => (
            <p key={note} className="text-caption text-warning">
              {note}
            </p>
          ))}
          {(result.excludedItemKeys ?? []).length > 0 && (
            <p className="text-caption text-warning">
              Not carried:{" "}
              {(result.excludedItemKeys ?? []).join(", ")}. A plan add-on cannot
              be negative, so a diversification benefit is left behind.
            </p>
          )}
          <p className="text-caption text-slate">
            Plan version {result.version}. You are recorded as its preparer, so
            you cannot approve it yourself.
          </p>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      title="Propose a capital-plan update"
      description="Carries the approved Pillar 2 amounts into a new capital-plan draft."
      onClose={onClose}
      wide
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={
              mutation.isPending ||
              approved.length === 0 ||
              reason.trim() === ""
            }
            onClick={() =>
              mutation.mutate(
                { replaceExistingDraft, reason: reason.trim() },
                { onSuccess: (data) => setResult(data) },
              )
            }
          >
            {mutation.isPending ? "Proposing…" : "Create the draft"}
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

        {approved.length === 0 ? (
          <p className="text-body text-navy/80">
            No Pillar 2 item is approved on a current revision yet. Only
            approved amounts can be carried into the capital plan.
          </p>
        ) : (
          <>
            <p className="text-body text-navy/80">
              These approved amounts will be carried across. The percentages of
              risk-weighted assets are calculated by the server and shown on the
              next screen.
            </p>
            <ul className="space-y-1 text-body">
              {approved.map((item) => (
                <li
                  key={item.itemId}
                  className="flex items-baseline justify-between gap-3 border-b border-border-light/60 py-1"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-navy">
                      {item.title}
                    </span>
                    <span className="block text-caption text-slate">
                      {item.methodLabel} · {basisLabel(item.basis)}
                    </span>
                  </span>
                  <span className="shrink-0 tnum text-navy">
                    {item.baselineAmount === null
                      ? NOT_MODELLED
                      : fmtAmount(item.baselineAmount)}
                  </span>
                </li>
              ))}
            </ul>

            <label className="flex items-start gap-2 text-body text-navy/80">
              <input
                type="checkbox"
                checked={replaceExistingDraft}
                onChange={(event) =>
                  setReplaceExistingDraft(event.target.checked)
                }
                className="mt-1"
              />
              <span>
                Replace an existing draft.
                {draftExists && (
                  <span className="block text-caption text-warning">
                    Someone else has a capital-plan draft open. Tick this only
                    if you have agreed to replace their work.
                  </span>
                )}
              </span>
            </label>

            <p className="text-caption text-slate">
              A different person must approve the draft in Capital planning
              before it takes effect. Negative (diversification) components are
              not carried, because a plan add-on cannot be negative.
            </p>

            <FieldLabel
              label="Reason for this proposal"
              hint="Recorded in the audit trail."
            >
              <input
                value={reason}
                maxLength={REASON_MAX}
                onChange={(event) => setReason(event.target.value)}
                className="mt-1 w-full rounded-md border border-border px-2 py-2 text-body"
              />
            </FieldLabel>
          </>
        )}
      </div>
    </Dialog>
  );
}
