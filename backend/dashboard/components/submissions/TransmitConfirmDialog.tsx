'use client';

/**
 * The last thing an officer sees before a return leaves the bank.
 *
 * Filing was one click: `case 'transmit': submit.mutate(...)` went straight to
 * the channel. For the act that puts a signed return in front of a regulator —
 * irreversible, correctable only with that regulator's own go-ahead — that is
 * not enough of a pause, so this dialog restates the whole act before offering
 * the button.
 *
 * It states what is TRUE AT SUBMISSION, not what the package happens to hold.
 * The two differ: `_filing_set` substitutes the signed revision for the unsigned
 * export and auto-exports a missing filing format, so a dialog built from the
 * artifact rows would name files that are not the ones filed. That exact
 * confusion once filed a fully certified return as the document nobody had
 * signed. Everything here therefore comes from the server's own preview of the
 * filing set (`GET .../filing-set`), which calls the same resolver the
 * submission does.
 *
 * Channel vocabulary lives in this module family and never on the workspace —
 * see TransmissionCard's header for why, and the guard test that pins it.
 *
 * The regulator and its portal are named from the active jurisdiction. There
 * are no country literals in this file.
 */

import { useState } from 'react';
import { AlertTriangle, Check, FlaskConical, Loader2 } from 'lucide-react';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { submissionPortal } from '@/lib/format';
import { fmtBytes } from './shared';
import {
  ROLE_COPY,
  TONE_CLASS,
  filingSetCount,
  signedCell,
  sizeCell,
  type TransmitPreview,
} from './transmitPreview';

function portalName(): string {
  return submissionPortal() ?? 'the regulator\u2019s portal';
}

export default function TransmitConfirmDialog({
  preview,
  regulatorName,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  preview: TransmitPreview;
  regulatorName: string;
  pending: boolean;
  error: unknown;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const portal = portalName();

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`File ${preview.returnCode} with ${regulatorName}`}
      data-testid="transmit-confirm"
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto px-4 py-10"
    >
      <button
        type="button"
        aria-label="Cancel"
        onClick={onCancel}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />

      <div className="card relative w-full max-w-4xl overflow-hidden">
        <div className="border-b border-border-light px-6 py-4">
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Final step · this cannot be undone
          </p>
          <h2 className="mt-1.5 text-h3 text-navy">
            File {preview.returnCode} with {regulatorName}
          </h2>
          <p className="mt-1 text-caption text-slate">
            Reporting date {preview.reportingDate} · {preview.institutionName}
            {preview.deadline ? ` · filing deadline ${preview.deadline}` : ''}
          </p>
        </div>

        <div className="grid grid-cols-3 gap-3 px-6 pt-4">
          <Fact label="Channel" value={preview.channelLabel} note={portal} />
          <Fact
            label="Reporting institution"
            value={preview.institutionCode ?? 'Not recorded'}
            note={
              preview.institutionCode
                ? 'From the institution profile'
                : 'Add it in the institution profile'
            }
            mono={preview.institutionCode !== null}
          />
          <Fact
            label="Submission revision"
            value={preview.submissionRevision}
            note={
              preview.isFirstFiling
                ? 'First filing of this return'
                : 'A superseding filing'
            }
            mono
          />
        </div>

        {preview.isSimulated && (
          <p
            data-testid="transmit-simulated"
            className="mx-6 mt-3 inline-flex items-center gap-1.5 rounded border border-warning/25 bg-warning-light px-2.5 py-1.5 text-caption font-medium text-warning"
          >
            <FlaskConical size={12} aria-hidden />
            Simulated — nothing is sent to {regulatorName}.
          </p>
        )}

        <section className="px-6 pt-5">
          <div className="flex items-baseline gap-2.5">
            <h3 className="text-body font-medium text-navy">
              What will be transmitted
            </h3>
            <p className="text-caption text-slate">
              {filingSetCount(preview)}. This list is recorded with the
              submission.
            </p>
          </div>

          <table className="mt-2.5 w-full text-caption">
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="py-1.5 text-left font-medium text-slate">
                  File
                </th>
                <th scope="col" className="py-1.5 text-left font-medium text-slate">
                  What it is
                </th>
                <th
                  scope="col"
                  className="w-36 py-1.5 text-left font-medium text-slate"
                >
                  Signed
                </th>
                <th
                  scope="col"
                  className="w-28 py-1.5 text-right font-medium text-slate"
                >
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {preview.filingSet.map((entry) => {
                const signed = signedCell(entry);
                const role = ROLE_COPY[entry.role];
                return (
                  <tr key={entry.filename} className="border-b border-border-light">
                    <td className="py-2 pr-3 font-mono text-micro text-navy">
                      {entry.filename}
                    </td>
                    <td className={`py-2 pr-3 ${TONE_CLASS[role.tone]}`}>
                      {role.label}
                    </td>
                    <td className={`py-2 pr-3 ${TONE_CLASS[signed.tone]}`}>
                      {signed.text}
                    </td>
                    <td className="py-2 text-right text-slate">
                      {sizeCell(entry, fmtBytes)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        {preview.omissions.length > 0 && (
          <ul
            data-testid="filing-omissions"
            className="mx-6 mt-2.5 space-y-1 text-caption text-slate"
          >
            {preview.omissions.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}

        <div className="grid grid-cols-2 gap-6 px-6 pt-5">
          <section>
            <h3 className="text-body font-medium text-navy">Already satisfied</h3>
            <ul className="mt-2 space-y-1.5">
              {preview.satisfied.map((item) => (
                <li key={item} className="flex items-baseline gap-2 text-caption text-navy/85">
                  <Check size={12} className="shrink-0 text-positive" aria-hidden />
                  <span>{item}</span>
                </li>
              ))}
              {preview.contentDigest && (
                <li className="flex items-baseline gap-2 text-caption text-navy/85">
                  <Check size={12} className="shrink-0 text-positive" aria-hidden />
                  <span>
                    Content unchanged since approval{' '}
                    <span className="font-mono text-micro text-slate">
                      {preview.contentDigest}
                    </span>
                  </span>
                </li>
              )}
            </ul>
          </section>

          <section>
            <h3 className="text-body font-medium text-navy">
              What happens when you press transmit
            </h3>
            <ol className="mt-2 list-decimal space-y-1.5 pl-4 text-caption leading-relaxed text-navy/85">
              <li>
                The book is re-checked against this reporting date. A filing is
                refused if it does not reconcile.
              </li>
              <li>The files above are delivered to {portal}.</li>
              <li>
                {regulatorName} returns a reference and this return becomes{' '}
                <span className="font-medium text-navy">Submitted</span>.
              </li>
              <li>The decision comes later — you check for it.</li>
            </ol>
          </section>
        </div>

        <div className="mx-6 mt-5 flex items-start gap-2.5 rounded border border-warning/30 bg-warning-light/50 px-3.5 py-2.5">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
          <p className="text-caption leading-relaxed text-navy/85">
            <span className="font-medium text-navy">
              Once {regulatorName} accepts the transmission it cannot be recalled.
            </span>{' '}
            A mistake after this point is corrected by requesting a resubmission,
            which is recorded against your institution.
          </p>
        </div>

        {error ? (
          <div className="px-6 pt-4">
            <ErrorPanel error={error} title="The filing was not accepted" />
          </div>
        ) : null}

        <div className="mt-5 flex items-center gap-4 border-t border-border-light px-6 py-4">
          <label className="flex items-center gap-2.5 text-caption text-navy/85">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              className="h-4 w-4 accent-teal"
            />
            <span>
              I am filing this return with {regulatorName} on behalf of{' '}
              {preview.institutionName}.
            </span>
          </label>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-border px-3.5 py-2 text-caption font-medium text-navy hover:bg-surface"
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="transmit-confirm-submit"
              disabled={!acknowledged || pending}
              onClick={onConfirm}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-caption font-medium btn-primary disabled:opacity-50"
            >
              {pending && <Loader2 size={13} className="animate-spin" aria-hidden />}
              {preview.isSimulated ? 'Transmit (simulated)' : `Transmit to ${portal}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Fact({
  label,
  value,
  note,
  mono = false,
}: {
  label: string;
  value: string;
  note: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded border border-border-light bg-surface px-3.5 py-2.5">
      <p className="text-micro text-slate">{label}</p>
      <p className={`mt-1 text-body text-navy ${mono ? 'font-mono' : ''}`}>{value}</p>
      <p className="mt-0.5 text-micro text-slate">{note}</p>
    </div>
  );
}
