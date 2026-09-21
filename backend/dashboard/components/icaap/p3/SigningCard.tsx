"use client";

/**
 * Who has to sign the report, in what order, and where each of them stands.
 *
 * THE CEREMONY ITSELF IS NOT REBUILT HERE. `AttestationPanel` — the platform's
 * signing workspace, with the document, the adopted mark, the step-up and the
 * verification — is mounted unchanged. It is embedded rather than linked to
 * because a board member may hold no Regulatory Reporting access at all: their
 * authority is over capital, and a signature that could only be given by
 * walking through `/submissions` would be a signature they could not give.
 *
 * What this card adds on top is the ORDER, which the generic panel has no
 * reason to know about. When the report carries a third signature the
 * signatures must arrive in order, because each officer's signature locks
 * everything signed before it and leaves only the later officers' fields
 * fillable; a board signature added before the approver's would invalidate
 * the approver's. So a slot whose turn has not come is shown as waiting, with
 * the reason, rather than as an action.
 */

import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import AttestationPanel from "@/components/attestation/AttestationPanel";
import type { PackageStatus } from "@aequoros/risk-service-api";
import { fmtLocale } from "@/lib/format";
import type { IcaapFiling } from "@/lib/api/icaapFiling";
import RehearsalNotice from "./RehearsalNotice";
import {
  BOARD_SIGNS_LAST,
  attestationStateCopy,
  roleLabel,
  slotBlockedSentence,
} from "./labels";

function moment(value: string | null): string {
  if (value === null) return "";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString(fmtLocale())
    : "";
}

export default function SigningCard({
  bankId,
  filing,
  returnLabel,
  packageStatus,
  validationClean,
  isRehearsal,
}: {
  bankId: string;
  filing: IcaapFiling;
  returnLabel: string;
  packageStatus: PackageStatus;
  validationClean: boolean;
  isRehearsal: boolean;
}) {
  const state = attestationStateCopy(filing.attestationState);
  const slots = filing.slots ?? [];
  const hasBoardSlot = slots.some((slot) => slot.role === "board");

  return (
    <div className="space-y-4">
      <SectionCard
        title="Signatures"
        subtitle="Each officer signs the report itself. The signature covers the exact document and figures in front of them."
        actions={<StatusPill tone={state.tone}>{state.label}</StatusPill>}
      >
        <div className="space-y-3">
          {slots.length === 0 ? (
            <p className="text-body text-navy/80">
              No signature is required on this report under the institution&apos;s
              current signing policy.
            </p>
          ) : (
            <ol className="space-y-2" data-testid="signature-slots">
              {slots.map((slot) => (
                <li
                  key={slot.role}
                  className="rounded border border-border-light p-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium text-navy">
                        {slot.line ?? roleLabel(slot.role)}
                      </p>
                      {slot.statement && (
                        <p className="mt-1 text-body text-navy/80">
                          {slot.statement}
                        </p>
                      )}
                    </div>
                    {slot.signedBy !== null ? (
                      <StatusPill tone="success">Signed</StatusPill>
                    ) : slot.blockedBy !== null ? (
                      <StatusPill tone="slate">Waiting its turn</StatusPill>
                    ) : (
                      <StatusPill tone={slot.required ? "amber" : "slate"}>
                        {slot.required ? "Still to sign" : "Optional"}
                      </StatusPill>
                    )}
                  </div>
                  {slot.signedBy !== null ? (
                    <p className="mt-1 text-caption text-slate">
                      {slot.signedBy}
                      {slot.signedAt ? ` · ${moment(slot.signedAt)}` : ""}
                    </p>
                  ) : slot.blockedBy !== null ? (
                    <p className="mt-1 text-caption text-slate">
                      {slotBlockedSentence(slot.blockedBy)}
                    </p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}

          {hasBoardSlot && (
            <p className="text-caption text-slate">{BOARD_SIGNS_LAST}</p>
          )}

          {isRehearsal && (
            <RehearsalNotice
              compact
              detail="Signatures given on a practice run are real signatures on a document that can never be filed."
            />
          )}
        </div>
      </SectionCard>

      {filing.package !== null && (
        <AttestationPanel
          bankId={bankId}
          packageId={filing.package.id}
          returnLabel={returnLabel}
          packageStatus={packageStatus}
          validationClean={validationClean}
          // Named, not inferred: it is what tells the shared panel to offer the
          // checker act on capital authority rather than on a reporting role,
          // which is the authority the server actually asks this family for.
          returnFamily="icaap"
        />
      )}
    </div>
  );
}
