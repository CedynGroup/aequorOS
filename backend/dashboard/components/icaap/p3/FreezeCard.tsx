"use client";

/**
 * Sealing the report for filing.
 *
 * The freeze is an explicit act by a named officer (D-030/DV-011), not an
 * automatic consequence of the last approval, and it has a cost the person
 * pressing it needs to know about BEFORE they press it: whoever freezes is
 * recorded as the officer who produced the filing, and may then sign as
 * preparer but never as approver and never for the board (C-9). That is
 * stated in the confirmation, in those words.
 *
 * The preflight is the SAME list the freeze itself computes — `GET
 * …/freeze-preflight` serves `freeze_cycle`'s own checks read-only — so a
 * preparer sees every reason before pressing anything, and the screen cannot
 * say "ready" about a freeze the server will refuse.
 */

import { useState } from "react";
import { Snowflake } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
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
  DIGEST_CHARS,
  ICON_SM,
  RATIONALE_MAX,
  REASON_MIN,
  ROWS_LONG,
} from "@/components/icaap/p2/display";
import {
  useFreezeIcaapCycle,
  useIcaapFreezePreflight,
  type IcaapStages,
} from "@/lib/api/icaapFiling";
import BlockerList from "./BlockerList";
import RehearsalNotice from "./RehearsalNotice";
import {
  AWAITING_REGULATOR_TEXT_BODY,
  AWAITING_REGULATOR_TEXT_TITLE,
  FREEZE_IS_FINAL,
  FREEZE_RECORDS_YOU,
  REHEARSAL_FREEZE_WARNING,
} from "./labels";

/**
 * The refusal that means the regime's own text is still an exposure draft
 * (D-006). It is not a defect in the assessment and it is not something a
 * preparer can clear, so it is drawn as the standing statement of fact it is,
 * above the list of things they CAN act on.
 */
const AWAITING_REGULATOR_TEXT = "framework_pending_primary_text";

export default function FreezeCard({
  bankId,
  cycleId,
  stages,
  isRehearsal,
}: {
  bankId: string;
  cycleId: string;
  stages: IcaapStages;
  isRehearsal: boolean;
}) {
  const preflight = useIcaapFreezePreflight(bankId, cycleId);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const freeze = useFreezeIcaapCycle(bankId, cycleId);

  const unavailable = p2UnavailableNotice(preflight.error);
  if (unavailable) {
    return <P2Unavailable title="Sealing the report" message={unavailable} />;
  }

  const data = preflight.data;
  const digest = data?.reviewDigest ?? stages.reviewDigest ?? null;
  const canPress =
    (stages.viewer?.canFreeze ?? false) &&
    (data?.ready ?? false) &&
    digest !== null;

  return (
    <QueryBoundary
      isLoading={preflight.isLoading}
      error={preflight.error}
      onRetry={() => void preflight.refetch()}
      contained
    >
      {data && (
        <SectionCard
          title="Seal the report for filing"
          subtitle="Sealing produces the filing itself: the report, its figures and the documents that go with it, fixed as one sealed version."
          actions={
            canPress ? (
              <PrimaryButton onClick={() => setOpen(true)}>
                <Snowflake size={ICON_SM} aria-hidden />
                Seal the report
              </PrimaryButton>
            ) : undefined
          }
        >
          <div className="space-y-3">
            {stages.viewer?.canFreeze ? (
              // C-9, on the CARD and not only in the confirmation: whoever
              // seals the report is recorded as having produced the filing,
              // and that costs them the approver's and the board's
              // signatures. Somebody deciding whether to press this needs to
              // know before they open a dialog, not inside it.
              <p className="text-body text-navy/80">{FREEZE_RECORDS_YOU}</p>
            ) : (
              <p className="text-body text-navy/80">
                {stages.viewer?.blockedReason ??
                  "Somebody else has to seal this report. Your access does not include it for this institution."}
              </p>
            )}
            {(data.items ?? []).some(
              (item) => item.code === AWAITING_REGULATOR_TEXT,
            ) && (
              <div
                className="card border-l-4 border-l-action bg-action-light/30 p-3"
                data-testid="exposure-draft-notice"
              >
                <p className="text-body font-medium text-navy">
                  {AWAITING_REGULATOR_TEXT_TITLE}
                </p>
                <p className="mt-1 text-body leading-relaxed text-navy/80">
                  {AWAITING_REGULATOR_TEXT_BODY}
                </p>
              </div>
            )}
            <BlockerList
              items={data.items ?? []}
              emptyTitle="Everything needed is in place"
              emptyDescription="Sealing will produce the filing and lock the assessment."
            />
            {isRehearsal && <RehearsalNotice compact detail={REHEARSAL_FREEZE_WARNING} />}
          </div>
        </SectionCard>
      )}

      {open && digest !== null && (
        <Dialog
          title="Seal this report"
          description="This cannot be undone from here."
          onClose={() => setOpen(false)}
          footer={
            <>
              <SecondaryButton onClick={() => setOpen(false)}>
                Cancel
              </SecondaryButton>
              <PrimaryButton
                disabled={
                  freeze.isPending || reason.trim().length < REASON_MIN
                }
                onClick={() =>
                  freeze.mutate(
                    { reviewDigest: digest, reason: reason.trim() },
                    { onSuccess: () => setOpen(false) },
                  )
                }
              >
                {freeze.isPending ? "Sealing…" : "Seal the report"}
              </PrimaryButton>
            </>
          }
        >
          <div className="space-y-3">
            {freeze.isError && (
              <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
                {(freeze.error as Error).message}
              </p>
            )}
            {isRehearsal && <RehearsalNotice detail={REHEARSAL_FREEZE_WARNING} />}
            <p className="text-body text-navy/80">{FREEZE_IS_FINAL}</p>
            <p className="text-body text-navy/80">{FREEZE_RECORDS_YOU}</p>
            <p className="text-caption text-slate">
              Report fingerprint {digest.slice(0, DIGEST_CHARS)}.
            </p>
            <FieldLabel
              label="Why it is being sealed now"
              hint="Recorded in the audit trail with your name."
            >
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
      )}
    </QueryBoundary>
  );
}
