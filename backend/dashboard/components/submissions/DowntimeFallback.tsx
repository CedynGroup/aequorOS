'use client';

/**
 * The portal is down and the deadline is not — board 6 of the approved design.
 *
 * BoG Notice BG/FMD/2026/07 as a screen: when the portal is unavailable, file
 * by email before the deadline, then re-upload once it is back. The email meets
 * the deadline; the re-upload completes the filing. Those are two different
 * facts and the screen keeps them apart, because a bank that stops after the
 * email believes it has filed and has not.
 *
 * Three things it is careful to say plainly:
 *
 *  - **Nothing was transmitted.** A failed attempt leaves the return exactly as
 *    it was; the officer has lost nothing and needs to know that before they
 *    start improvising.
 *  - **AequorOS does not send the email.** It prepares one; the bank sends it,
 *    so it leaves the bank's own mailbox and lands in its sent items. That is a
 *    product decision, not a technical limit, and it is stated rather than
 *    discovered.
 *  - **The obligation stays open** until the re-upload succeeds. This is the
 *    one case where a filed return may be submitted a second time.
 *
 * The regulator and its portal are named from the active jurisdiction.
 */

import { Download, Mail, RadioTower } from 'lucide-react';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import { centralBankName, regShort, submissionPortal } from '@/lib/format';
import { fmtBytes } from './shared';

export type DowntimeAttachment = Readonly<{
  filename: string;
  role: string;
  sizeBytes: number | null;
}>;

export default function DowntimeFallback({
  returnCode,
  reportingDate,
  deadlineLabel,
  message,
  attemptsLabel,
  recipient,
  subject,
  attachments,
  instructions,
  onDownloadEml,
  onRecordEmailSubmission,
  onRetryPortal,
  recording,
  error,
  pendingReupload,
}: {
  returnCode: string;
  reportingDate: string;
  deadlineLabel: string | null;
  /** The structured downtime refusal, in the operator's words. */
  message: string;
  attemptsLabel: string | null;
  recipient: string | null;
  subject: string | null;
  attachments: readonly DowntimeAttachment[];
  instructions: string | null;
  onDownloadEml: () => void;
  onRecordEmailSubmission: () => void;
  onRetryPortal: () => void;
  recording: boolean;
  error: unknown;
  /** Already filed by email and waiting for the portal to come back. */
  pendingReupload: boolean;
}) {
  const portal = submissionPortal() ?? `${regShort()}'s portal`;
  const regulator = centralBankName();

  return (
    <div className="space-y-4" data-testid="downtime-fallback">
      <SectionCard
        title={
          <span className="inline-flex items-center gap-2.5">
            <span className="font-mono">{returnCode}</span>
            <span className="text-caption font-normal text-slate">
              {reportingDate}
            </span>
          </span>
        }
        subtitle={
          pendingReupload
            ? `Filed by email — not complete until it is re-uploaded to ${portal}`
            : `Not filed${deadlineLabel ? ` · ${deadlineLabel}` : ''}`
        }
        actions={
          <button
            type="button"
            data-testid="retry-portal"
            onClick={onRetryPortal}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
          >
            <RadioTower size={13} aria-hidden />
            Try {portal} again
          </button>
        }
      >
        <div className="rounded border border-warning/30 bg-warning-light/40 px-3.5 py-3">
          <p className="text-body font-medium text-navy">
            {portal} could not be reached
          </p>
          <p className="mt-1 text-caption leading-relaxed text-navy/85">
            {/* Said first, because an officer watching a filing fail against a
                deadline needs to know the return is intact before anything
                else. */}
            Nothing was transmitted and this return is unchanged.
            {attemptsLabel ? ` ${attemptsLabel}` : ''}
          </p>
          <p className="mt-2 rounded border border-border-light bg-surface px-3 py-2 text-caption leading-relaxed text-navy/85">
            {message}
          </p>
          <p className="mt-2 text-caption leading-relaxed text-navy/85">
            <span className="font-medium text-navy">
              {regulator} Notice BG/FMD/2026/07
            </span>{' '}
            — when the portal is unavailable, file by email before the deadline,
            then re-upload to {portal} once it is back. The email meets the
            deadline; the re-upload completes the filing.
          </p>
        </div>
      </SectionCard>

      <div className="grid gap-4 md:grid-cols-2">
        <SectionCard
          title="The email AequorOS has prepared"
          subtitle="Ready to send from your own mailbox."
        >
          <dl className="space-y-2 text-caption">
            <Row label="To" value={recipient} mono />
            <Row label="Subject" value={subject} mono />
            <Row
              label="Attached"
              value={
                attachments.length > 0
                  ? `${attachments.length} file${attachments.length === 1 ? '' : 's'}`
                  : 'Prepared at download'
              }
            />
          </dl>

          {attachments.length > 0 && (
            <ul className="mt-3 space-y-1.5 border-t border-border-light pt-3 text-caption">
              {attachments.map((file) => (
                <li
                  key={file.filename}
                  className="flex items-baseline justify-between gap-3"
                >
                  <span className="font-mono text-micro text-navy">
                    {file.filename}
                  </span>
                  <span className="text-slate">{file.role}</span>
                  <span className="text-slate">
                    {file.sizeBytes === null ? '' : fmtBytes(file.sizeBytes)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <p className="mt-3 rounded border border-border-light bg-surface px-3 py-2 text-caption leading-relaxed text-navy/85">
            A person at {regShort()} opens these. The note in the email body says
            which file is the signed record and that the formula copy
            recalculates when opened.
          </p>

          {instructions && (
            <details className="mt-3 text-caption text-navy/80">
              <summary className="cursor-pointer font-medium text-navy">
                Read the send-ready instructions
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-border-light bg-surface p-3 font-mono text-micro leading-relaxed">
                {instructions}
              </pre>
            </details>
          )}

          <div className="mt-4 flex items-center gap-3 border-t border-border-light pt-3">
            <p className="text-caption leading-relaxed text-slate">
              AequorOS does not send this email. You send it, so it comes from
              the bank&apos;s own mailbox and lands in your sent items.
            </p>
            <button
              type="button"
              data-testid="download-eml"
              onClick={onDownloadEml}
              className="ml-auto inline-flex shrink-0 items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              <Download size={13} aria-hidden />
              Download .eml
            </button>
          </div>
        </SectionCard>

        <div className="space-y-4">
          <SectionCard title="Then, in order">
            <ol className="list-decimal space-y-2.5 pl-4 text-caption leading-relaxed text-navy/85">
              <li>
                Send the email from the bank&apos;s mailbox
                {deadlineLabel ? ` before the deadline (${deadlineLabel})` : ''}.
              </li>
              <li>
                Record it here with the date and time you sent it. The return
                becomes <span className="font-medium text-navy">Filed by email</span>{' '}
                and the deadline is met.
              </li>
              <li>
                When {portal} is back, re-upload the same package. Only then is
                the filing complete.
              </li>
            </ol>
            <button
              type="button"
              data-testid="record-email-submission"
              disabled={recording}
              onClick={onRecordEmailSubmission}
              className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface disabled:opacity-60"
            >
              <Mail size={13} aria-hidden />
              Record email submission…
            </button>
            {error ? (
              <div className="mt-3">
                <ErrorPanel error={error} title="The submission was not recorded" />
              </div>
            ) : null}
          </SectionCard>

          <SectionCard title="After you record it">
            <p className="text-caption leading-relaxed text-navy/85">
              The return carries an open obligation —{' '}
              <span className="font-medium text-warning">
                awaiting {portal} re-upload
              </span>{' '}
              — on the returns list, the calendar and the daily deadline
              notification, until the re-upload succeeds. It is the one case
              where a filed return can be submitted a second time, and the
              platform will only allow that second submission for this reason.
            </p>
          </SectionCard>

          <SectionCard title="Who can do this">
            <p className="text-caption leading-relaxed text-navy/85">
              The same Validator authority as a {portal} transmission. A downtime
              email is a filing — the channel changed, the authority did not.
            </p>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-slate">{label}</dt>
      <dd className={`text-navy ${mono ? 'font-mono text-micro' : ''}`}>
        {value ?? '—'}
      </dd>
    </div>
  );
}
