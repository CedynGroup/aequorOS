"use client";

/**
 * A return after it has been filed — board 5 of the approved design.
 *
 * The bank's evidence of what it sent the regulator and when, and the one
 * place that answers "what exactly did we file on the 9th?". Until this
 * existed the server recorded `filed_artifacts` — kind, stored path, checksum,
 * size, and which file carried the signatures — on every submission and no
 * surface ever showed it.
 *
 * Two things it is careful about:
 *
 *  - **The regulator's status is PULLED, not pushed.** There is no background
 *    poller, so a timeline that animated itself would be fiction. It says so
 *    and gives the control.
 *  - **Rejected and declined are different outcomes** with different
 *    consequences, so they are spelled out rather than collapsed into "not
 *    accepted".
 *
 * Jurisdiction comes from the active bank; there are no country literals here.
 */

import { useState } from "react";
import Link from "next/link";
import { Download, Loader2, RefreshCw } from "lucide-react";
import type {
  PackageChainRead,
  RegulatoryPackageSummaryRead,
  SubmissionEventRead,
} from "@aequoros/risk-service-api";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import { centralBankName, regShort, submissionPortal } from "@/lib/format";
import { fmtDateUTC, fmtTimestamp } from "@/lib/api/values";
import {
  FILED_ROLE_LABEL,
  filedArtifacts,
  shortChecksum,
} from "@/lib/submissions/filedArtifacts";
import {
  CHANNEL_LABELS,
  RehearsalNotice,
  RehearsalPill,
  fmtBytes,
} from "./shared";

/** Where the regulator has it. Pulled, never pushed — see the module note. */
const REGULATOR_STEPS = [
  "Sent",
  "Received",
  "Under review",
  "Decision",
] as const;

function stepIndexFor(
  latestPollStatus: string | null,
  hasReference: boolean,
): number {
  if (latestPollStatus === "acknowledged") return 3;
  if (latestPollStatus === "rejected" || latestPollStatus === "declined")
    return 3;
  if (latestPollStatus === "pending") return 2;
  return hasReference ? 1 : 0;
}

const REGULATOR_DECISIONS = new Set(["acknowledged", "rejected", "declined"]);

/**
 * Where the regulator has the return, read from the submission record.
 *
 * A decision is its own event (`acknowledged` / `rejected` / `declined`), written
 * beside the `status_poll` that found it, so it is read first. Short of one, the
 * latest poll's outcome is what the server stored under `detail.result`
 * (`workflow.check_regulator_status`). This used to read `detail.poll_status`, a
 * key the server never writes, so the timeline stopped at "Received" whatever
 * the regulator had decided.
 */
export function regulatorStanding(events: readonly SubmissionEventRead[]): string | null {
  const latest = [...events].reverse();
  const decision = latest.find((event) => REGULATOR_DECISIONS.has(event.event));
  if (decision) return decision.event;
  const result = latest.find((event) => event.event === "status_poll")?.detail?.["result"];
  return typeof result === "string" ? result : null;
}

/** What the screen says about where the regulator has the return. */
function standingSentence(pollStatus: string | null): string {
  switch (pollStatus) {
    case "acknowledged":
      return `${centralBankName()} has accepted this return. The obligation for this reporting date is discharged.`;
    case "rejected":
      return `${centralBankName()} sent this return back for correction. Record a correction and file the corrected version; the deadline does not move.`;
    case "declined":
      return `${centralBankName()} declined this return: it was not accepted for filing.`;
    default:
      return `${centralBankName()} has the return and has not yet decided. AequorOS checks for a decision automatically and emails everyone who worked on this return when it arrives — or press Check ${regShort()} status to check now.`;
  }
}

/** An open request from the regulator for a corrected filing of this receipt. */
export type RegulatorCorrectionRequest = Readonly<{
  number: string;
  dueAt: Date | null;
  body: string;
}>;

export default function FiledRecord({
  pkg,
  events,
  chain,
  decisionLabels,
  onCheckStatus,
  checking,
  checkError,
  lastCheckedLabel,
  onDownloadReceipt,
  correctionHref,
  regulatorRequests = [],
}: {
  pkg: RegulatoryPackageSummaryRead;
  events: readonly SubmissionEventRead[];
  chain: PackageChainRead | null;
  /** The chain's decisions in the bank's language — never a stored value. */
  decisionLabels: Record<string, string>;
  onCheckStatus: () => void;
  checking: boolean;
  checkError: unknown;
  lastCheckedLabel: string | null;
  onDownloadReceipt: (() => void) | null;
  /** Where this return's corrections are recorded (the Returns workspace). */
  correctionHref: string | null;
  /** The regulator's open requests for a corrected filing of this receipt. */
  regulatorRequests?: readonly RegulatorCorrectionRequest[];
}) {
  const [showAllFiles, setShowAllFiles] = useState(true);

  const submitted = [...events]
    .reverse()
    .find((event) => event.event === "submitted");
  const pollStatus = regulatorStanding(events);
  const awaitingDecision =
    pkg.status === "submitted" && !REGULATOR_DECISIONS.has(pollStatus ?? "");

  // The evidence, read from the submission record itself.
  const files = filedArtifacts(submitted?.detail);
  const step = stepIndexFor(pollStatus, Boolean(submitted?.externalRef));
  // The gateway is named for what it is; a portal name from the jurisdiction's
  // profile describes the regulator's own system, not this channel.
  const viaGateway = submitted?.channel === "reporting_gateway";
  const portal = viaGateway
    ? CHANNEL_LABELS.reporting_gateway
    : (submissionPortal() ?? `${regShort()}'s portal`);

  return (
    <div className="space-y-4">
      <SectionCard
        title={
          <span className="inline-flex items-center gap-2.5">
            <span className="font-mono">{pkg.returnCode}</span>
            <span className="text-caption font-normal text-slate">
              {fmtDateUTC(pkg.reportingDate)} · v{pkg.version}
            </span>
            {pkg.isRehearsal && <RehearsalPill />}
          </span>
        }
        subtitle={`Filed with ${centralBankName()}`}
        actions={
          <div className="flex items-center gap-2">
            {onDownloadReceipt && (
              <button
                type="button"
                onClick={onDownloadReceipt}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
              >
                <Download size={13} aria-hidden />
                Download receipt
              </button>
            )}
            {/* Only a filed return still awaiting the regulator has anything
                to check: the server refuses a poll once a decision is
                recorded, so the control is not offered after one. */}
            {awaitingDecision && (
              <button
                type="button"
                data-testid="check-regulator-status"
                disabled={checking}
                onClick={onCheckStatus}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {checking ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <RefreshCw size={13} aria-hidden />
                )}
                Check {regShort()} status
              </button>
            )}
          </div>
        }
      >
        {/* Before anything that reads as a filing receipt: a rehearsal
            discharges no obligation and reached no regulator. */}
        {pkg.isRehearsal && <RehearsalNotice />}

        {regulatorRequests.map((request) => (
          <div
            key={request.number}
            role="note"
            className="mb-4 rounded-md border border-warning/30 bg-warning-light px-4 py-3"
          >
            <p className="text-caption font-medium text-navy">
              {centralBankName()} asked for a corrected filing ({request.number})
              {request.dueAt ? `, due ${fmtDateUTC(request.dueAt)}` : ""}.
            </p>
            {request.body ? (
              <p className="mt-1 whitespace-pre-wrap text-caption text-navy">“{request.body}”</p>
            ) : null}
            <Link
              href={`/submissions/queries?q=${encodeURIComponent(request.number)}`}
              className="mt-2 inline-block text-caption font-medium text-action hover:underline"
            >
              Start the correction
            </Link>
          </div>
        ))}

        <div className="grid gap-4 md:grid-cols-2">
          <dl className="space-y-2 text-caption">
            <Row
              label={viaGateway ? "Gateway receipt" : `${portal} reference`}
              mono
            >
              {submitted?.externalRef ?? "Not returned"}
            </Row>
            <Row label="Transmitted" mono>
              {submitted ? fmtTimestamp(submitted.occurredAt) : "—"}
            </Row>
            <Row label="Channel">
              {submitted
                ? (CHANNEL_LABELS[submitted.channel] ?? submitted.channel)
                : "—"}
            </Row>
            <Row label="Submission revision" mono>
              {pkg.submissionRevision ?? "—"}
            </Row>
          </dl>

          <div>
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-caption font-medium text-navy">
                Where {regShort()} has it
              </p>
              {lastCheckedLabel && (
                <p className="text-micro text-slate">
                  Last checked {lastCheckedLabel}
                </p>
              )}
            </div>
            <ol className="mt-2 flex items-center gap-2">
              {REGULATOR_STEPS.map((label, index) => (
                <li key={label} className="flex items-center gap-2">
                  {index > 0 && <span className="text-slate">·</span>}
                  <span
                    className={
                      index < step
                        ? "text-positive text-caption"
                        : index === step
                          ? "text-caption font-medium text-navy"
                          : "text-caption text-slate"
                    }
                  >
                    {label}
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-2 rounded-sm border border-border-light bg-surface px-3 py-2 text-caption leading-relaxed text-navy/85">
              {/* No background poller exists. A timeline that advanced on its
                  own would be fiction, so the screen says who has to ask. */}
              {standingSentence(pollStatus)}
            </p>
            {checkError ? (
              <div className="mt-2">
                <ErrorPanel
                  error={checkError}
                  title={
                    viaGateway
                      ? `Could not get a decision from the ${portal}`
                      : `Could not reach ${portal} for a decision`
                  }
                />
              </div>
            ) : null}
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title={`What went to ${regShort()}`}
        subtitle="Recorded at transmission — this is the evidence of what was filed."
      >
        {files.length === 0 ? (
          <p className="text-caption text-slate">
            The submission record does not list its files. Nothing is inferred
            here: what was filed is what the record says was filed.
          </p>
        ) : (
          <table className="w-full text-caption">
            <thead>
              <tr className="border-b border-border">
                <th
                  scope="col"
                  className="py-1.5 text-left font-medium text-slate"
                >
                  File
                </th>
                <th
                  scope="col"
                  className="py-1.5 text-left font-medium text-slate"
                >
                  Role in the filing
                </th>
                <th
                  scope="col"
                  className="w-40 py-1.5 text-left font-medium text-slate"
                >
                  Digest
                </th>
                <th
                  scope="col"
                  className="w-24 py-1.5 text-right font-medium text-slate"
                >
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {(showAllFiles ? files : files.slice(0, 3)).map((file) => (
                <tr
                  key={file.filename}
                  className="border-b border-border-light"
                >
                  <td className="py-2 pr-3 font-mono text-micro text-navy">
                    {file.filename}
                  </td>
                  <td
                    className={`py-2 pr-3 ${
                      file.role === "signed_record"
                        ? "text-positive"
                        : "text-slate"
                    }`}
                  >
                    {FILED_ROLE_LABEL[file.role]}
                  </td>
                  <td className="py-2 pr-3 font-mono text-micro text-slate">
                    {shortChecksum(file.checksum) ?? "—"}
                  </td>
                  <td className="py-2 text-right text-slate">
                    {file.sizeBytes === null ? "—" : fmtBytes(file.sizeBytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {files.length > 3 && (
          <button
            type="button"
            onClick={() => setShowAllFiles((open) => !open)}
            className="mt-2 text-caption font-medium text-action hover:underline"
          >
            {showAllFiles ? "Show fewer" : `Show all ${files.length}`}
          </button>
        )}
      </SectionCard>

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard title="Every decision on this return">
          {chain && chain.stages.some((stage) => stage.decisions.length > 0) ? (
            <ul className="space-y-1.5">
              {chain.stages.flatMap((stage) =>
                stage.decisions.map((decision) => (
                  <li
                    key={`${stage.seq}-${decision.round}-${decision.createdAt.toISOString()}`}
                    className="flex flex-wrap items-baseline gap-2 text-caption text-navy/85"
                  >
                    <span className="font-mono text-micro text-slate">
                      {fmtTimestamp(decision.createdAt)}
                    </span>
                    <span className="font-medium text-navy">
                      {decision.decidedByName}
                    </span>
                    <span className="text-slate">{stage.title}</span>
                    <span className="text-slate">round {decision.round}</span>
                    <span>
                      {decisionLabels[decision.decision] ?? decision.decision}
                    </span>
                    {decision.comment && (
                      <span className="text-slate">— {decision.comment}</span>
                    )}
                  </li>
                )),
              )}
            </ul>
          ) : (
            <p className="text-caption text-slate">No decisions recorded.</p>
          )}
        </SectionCard>

        <SectionCard title={`What ${regShort()} can come back with`}>
          <ul className="space-y-3 text-caption leading-relaxed text-navy/85">
            <li>
              <StatusPill tone="success">Acknowledged</StatusPill> accepted. The
              obligation for this reporting date is discharged.
            </li>
            <li>
              <StatusPill tone="amber">Rejected</StatusPill> returned for
              correction with the supervisor&apos;s comments. You correct and
              file a superseding version; the deadline does not move.
            </li>
            <li>
              <StatusPill tone="critical">Declined</StatusPill> not accepted for
              filing at all. Different from rejected, and handled differently.
            </li>
          </ul>
          {correctionHref && (
            <p className="mt-3 border-t border-border-light pt-3 text-caption text-slate">
              {viaGateway
                ? "To change a return after it is filed, record a correction. The corrected version goes to the gateway as an amendment that says why."
                : `To change a return after it is filed you must ask ${regShort()} for a resubmission.`}{" "}
              <Link
                href={correctionHref}
                className="font-medium text-action hover:underline"
              >
                {viaGateway ? "Record a correction" : "Request resubmission"}
              </Link>
            </p>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function Row({
  label,
  children,
  mono = false,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate">{label}</dt>
      <dd className={`text-navy ${mono ? "font-mono text-micro" : ""}`}>
        {children}
      </dd>
    </div>
  );
}
