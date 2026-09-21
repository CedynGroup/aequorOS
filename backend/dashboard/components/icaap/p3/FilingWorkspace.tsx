"use client";

/**
 * The Filing tab: the one screen that answers "can this be sent, and if not,
 * why not?".
 *
 * It composes the platform's existing surfaces rather than rebuilding them —
 * the lifecycle stepper, the validation report and the signing workspace are
 * the same ones the Regulatory Reporting workspace uses, so a filing officer
 * and a board member see the same thing described the same way. What is added
 * here is the ICAAP's own order: the signature order, the documents the regime
 * requires, and the fact that this return is recorded rather than transmitted.
 *
 * The blockers are the SERVER's, from the same gate the submission runs. This
 * screen never decides that a report is ready.
 */

import { useState } from "react";
import { CornerUpLeft } from "lucide-react";
import type { PackageStatus } from "@aequoros/risk-service-api";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import EmptyState from "@/components/ui/EmptyState";
import StatusPill from "@/components/ui/StatusPill";
import { PackageChainStrip } from "@/components/submissions/FilingChain";
import ChecksPanel from "@/components/submissions/ChecksPanel";
import { PackageStatusPill } from "@/components/submissions/shared";
import P2Unavailable, {
  p2UnavailableNotice,
} from "@/components/icaap/p2/availability";
import { SecondaryButton } from "@/components/icaap/Dialog";
import { useModuleScope } from "@/components/shell/BankContext";
import { useRegulatoryPackage, useValidateRegulatoryPackage } from "@/lib/api/hooks";
import { fmtLocale } from "@/lib/format";
import { DIGEST_CHARS, ICON_SM } from "@/components/icaap/p2/display";
import { useIcaapFiling, useIcaapStages } from "@/lib/api/icaapFiling";
import DisclosurePanel from "./DisclosurePanel";
import FilingDownloads from "./FilingDownloads";
import PackageAttachmentsCard from "./PackageAttachmentsCard";
import RehearsalNotice, { isRehearsal } from "./RehearsalNotice";
import SigningCard from "./SigningCard";
import SubmitIcaapCard from "./SubmitIcaapCard";
import { ReturnFromFilingDialog } from "./StageActions";
import { NOTHING_TO_FILE } from "./labels";

/** Statuses at which the sealed report may still be sent back for changes. */
const STILL_RECALLABLE = new Set([
  "generated",
  "validated",
  "pending_approval",
  "approved",
  "rejected",
]);

function reportingDay(value: string | null): string {
  if (value === null) return "";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleDateString(fmtLocale())
    : "";
}

export default function FilingWorkspace({
  bankId,
  cycleId,
  cycleKind,
  cycleTitle,
}: {
  bankId: string;
  cycleId: string;
  /** The cycle's own kind. The package read publishes `isRehearsal` too
   * (D-080), and the generic reporting surfaces use it; here the cycle is
   * already in hand and is the same fact one step earlier, so the notice
   * shows before any package exists. */
  cycleKind: string | null;
  cycleTitle: string;
}) {
  const scope = useModuleScope();
  const filingQuery = useIcaapFiling(bankId, cycleId);
  const stagesQuery = useIcaapStages(bankId, cycleId);
  const [returning, setReturning] = useState(false);

  const filing = filingQuery.data;
  const packageId = filing?.package?.id ?? null;
  const packageQuery = useRegulatoryPackage(bankId, packageId);
  const validate = useValidateRegulatoryPackage(bankId);

  const unavailable = p2UnavailableNotice(filingQuery.error);
  if (unavailable) {
    return <P2Unavailable title="Filing" message={unavailable} />;
  }

  const rehearsal = isRehearsal(cycleKind);
  const canEdit = scope.capitalEdit === true;
  const canApprove = scope.capitalApprove === true;
  const pkg = filing?.package ?? null;
  const status = (pkg?.status ?? "generated") as PackageStatus;
  const report = packageQuery.data?.validationReport ?? null;
  const validationClean = report !== null && report.passed && report.errorCount === 0;

  return (
    <div className="space-y-4">
      {rehearsal && <RehearsalNotice />}

      <QueryBoundary
        isLoading={filingQuery.isLoading}
        error={filingQuery.error}
        onRetry={() => void filingQuery.refetch()}
        contained
      >
        {filing && pkg === null && (
          <SectionCard title="Filing">
            <EmptyState title="Nothing to file yet" description={NOTHING_TO_FILE} />
          </SectionCard>
        )}

        {filing && pkg !== null && (
          <div className="space-y-4">
            <SectionCard
              title="The filing"
              subtitle={`${pkg.returnCode} · ${reportingDay(pkg.reportingDate)}`}
              actions={
                <div className="flex flex-wrap items-center gap-2">
                  <PackageStatusPill status={status} />
                  {canApprove &&
                    stagesQuery.data &&
                    STILL_RECALLABLE.has(pkg.status) && (
                      <SecondaryButton onClick={() => setReturning(true)}>
                        <CornerUpLeft size={ICON_SM} aria-hidden />
                        Send back for changes
                      </SecondaryButton>
                    )}
                </div>
              }
            >
              <div className="space-y-3">
                <PackageChainStrip
                  status={status}
                  checksClean={report?.passed === true}
                />
                <p className="text-caption text-slate">
                  Sealed version {pkg.version ?? ""}
                  {pkg.contentDigest
                    ? ` · report fingerprint ${pkg.contentDigest.slice(0, DIGEST_CHARS)}`
                    : ""}
                </p>
                {filing.submittable ? (
                  <StatusPill tone="success">Ready to file</StatusPill>
                ) : (
                  <StatusPill tone="amber">Not ready to file</StatusPill>
                )}
              </div>
            </SectionCard>

            <SectionCard
              title="Checks on the sealed report"
              subtitle="Run against the sealed figures, not the live ones."
              actions={
                canEdit ? (
                  <SecondaryButton
                    disabled={validate.isPending}
                    onClick={() => validate.mutate(pkg.id)}
                  >
                    {validate.isPending ? "Checking…" : "Run the checks"}
                  </SecondaryButton>
                ) : undefined
              }
            >
              {report ? (
                <ChecksPanel report={report} />
              ) : (
                <EmptyState
                  title="Not checked yet"
                  description="The checks report anything in the sealed report that would be refused at filing."
                />
              )}
            </SectionCard>

            <SigningCard
              bankId={bankId}
              filing={filing}
              returnLabel={`${pkg.returnCode} · ${cycleTitle}`}
              packageStatus={status}
              validationClean={validationClean}
              isRehearsal={rehearsal}
            />

            <PackageAttachmentsCard
              bankId={bankId}
              packageId={pkg.id}
              canEdit={canEdit}
              isRehearsal={rehearsal}
            />

            <SubmitIcaapCard
              bankId={bankId}
              filing={filing}
              canSubmit={canApprove}
              isRehearsal={rehearsal}
            />

            <FilingDownloads
              bankId={bankId}
              packageId={pkg.id}
              canExport={scope.capitalExport === true}
            />

            <DisclosurePanel
              bankId={bankId}
              cycleId={cycleId}
              canEdit={canEdit}
              canApprove={canApprove}
            />
          </div>
        )}
      </QueryBoundary>

      {returning && stagesQuery.data && (
        <ReturnFromFilingDialog
          bankId={bankId}
          cycleId={cycleId}
          stages={stagesQuery.data}
          onClose={() => setReturning(false)}
        />
      )}
    </div>
  );
}
