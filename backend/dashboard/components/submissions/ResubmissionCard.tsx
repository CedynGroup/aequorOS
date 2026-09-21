'use client';

/**
 * Asking the regulator to accept a correction to a return that has already been
 * filed.
 *
 * This is part of the TRANSMISSION surface and belongs to the officer who holds
 * it: a correction is a second conversation with the regulator, and only the
 * Validator has that conversation (docs/filing_workflow_redesign.md §4b.1). It
 * lives in its own file so the Returns workspace mounts it behind the projected
 * transmission capability and nowhere else.
 */

import { useState } from 'react';
import { CheckCircle2, Loader2, RotateCcw, XCircle } from 'lucide-react';
import type { ChannelCode, ResubmissionRequestRead } from '@aequoros/risk-service-api';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { fmtTimestamp } from '@/lib/api/values';
import { ResubmissionStatusPill } from './shared';

export default function ResubmissionCard({
  status,
  requests,
  requestsError,
  latestSubmittedChannel,
  canRequest,
  onRequest,
  requestPending,
  requestError,
  onDecide,
  decidePending,
  decideError,
  regulatorName,
}: {
  status: string;
  requests: ResubmissionRequestRead[];
  requestsError: unknown;
  latestSubmittedChannel: ChannelCode | null;
  canRequest: boolean;
  onRequest: (reason: string) => void;
  requestPending: boolean;
  requestError: unknown;
  onDecide: (
    requestId: string,
    decision: 'granted' | 'denied',
    note: string | undefined
  ) => void;
  decidePending: boolean;
  decideError: unknown;
  regulatorName: string;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  const manualDecide =
    latestSubmittedChannel === 'email' || latestSubmittedChannel === 'manual';
  const hasOpenRequest = requests.some((entry) => entry.status === 'requested');
  const grantedPending = requests.some(
    (entry) => entry.status === 'granted' && entry.consumedByPackageId == null
  );

  return (
    <div className="space-y-3">
      <p className="text-caption text-slate leading-relaxed">
        A return that has already been filed can only be corrected with{' '}
        {regulatorName}&apos;s go-ahead. Ask for it here, then generate the
        corrected version once it is granted.
      </p>

      {canRequest && !hasOpenRequest && (
        <>
          {!formOpen ? (
            <button
              type="button"
              onClick={() => setFormOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
            >
              <RotateCcw size={13} aria-hidden />
              Ask to resubmit
            </button>
          ) : (
            <div className="space-y-2 max-w-2xl">
              <label
                className="block text-caption font-medium text-navy"
                htmlFor="resubmission-reason"
              >
                Why the filed return has to change{' '}
                <span className="font-normal text-slate">(required)</span>
              </label>
              <textarea
                id="resubmission-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={2}
                placeholder="e.g. Corrected a liquid-asset misclassification found after filing."
                className="w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={requestPending || reason.trim().length === 0}
                  onClick={() => {
                    onRequest(reason.trim());
                    setFormOpen(false);
                    setReason('');
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  {requestPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <RotateCcw size={13} aria-hidden />
                  )}
                  Send the request
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setFormOpen(false);
                    setReason('');
                  }}
                  className="inline-flex items-center rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {requestError ? (
        <ErrorPanel error={requestError} title="The request was not accepted" />
      ) : null}
      {requestsError ? (
        <ErrorPanel
          error={requestsError}
          title="Could not read the correction requests"
        />
      ) : null}

      {grantedPending && (
        <p className="rounded border border-success/25 bg-success-light/50 px-3 py-2 text-caption leading-relaxed text-navy/85">
          Granted. Generating the corrected version is the preparer&apos;s act;
          the next filing carries the next revision number.
        </p>
      )}

      {requests.length > 0 && (
        <ul className="space-y-2">
          {requests.map((entry) => (
            <li
              key={entry.id}
              className="space-y-1.5 rounded border border-border-light bg-surface px-3 py-2"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <ResubmissionStatusPill status={entry.status} />
                <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                  {fmtTimestamp(entry.occurredAt)}
                </span>
              </div>
              <p className="text-caption leading-relaxed text-navy/80">
                {entry.reason}
              </p>
              {entry.decidedAt && (
                <p className="font-mono text-micro text-slate tnum">
                  decided {fmtTimestamp(new Date(entry.decidedAt))}
                </p>
              )}
              {entry.status === 'requested' && manualDecide && (
                <div className="space-y-1.5 pt-1">
                  <input
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="Note on the decision (optional)"
                    aria-label="Note on the decision"
                    className="w-full rounded border border-border bg-surface-raised px-2.5 py-1.5 text-caption text-navy placeholder:text-slate-light"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={decidePending}
                      onClick={() => {
                        onDecide(entry.id, 'granted', note.trim() || undefined);
                        setNote('');
                      }}
                      className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-success/30 bg-success-light/40 px-3 py-1.5 text-caption font-medium text-success hover:bg-success-light disabled:opacity-60"
                    >
                      <CheckCircle2 size={13} aria-hidden />
                      They granted it
                    </button>
                    <button
                      type="button"
                      disabled={decidePending}
                      onClick={() => {
                        onDecide(entry.id, 'denied', note.trim() || undefined);
                        setNote('');
                      }}
                      className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-md border border-critical/30 bg-critical-light/40 px-3 py-1.5 text-caption font-medium text-critical hover:bg-critical-light disabled:opacity-60"
                    >
                      <XCircle size={13} aria-hidden />
                      They refused it
                    </button>
                  </div>
                  <p className="text-micro leading-relaxed text-slate">
                    This return was filed outside the portal, so record{' '}
                    {regulatorName}&apos;s answer here when it arrives. A filing
                    made through the portal is answered by the portal itself.
                  </p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {decideError ? (
        <ErrorPanel error={decideError} title="The decision was not recorded" />
      ) : null}
      {status === 'superseded' && (
        <p className="text-caption text-slate">
          This version has been superseded; corrections belong to the version
          that replaced it.
        </p>
      )}
    </div>
  );
}
