/**
 * Chronological submission-event feed for one package: channel + event type,
 * external reference, sandbox chip on simulated ORASS interactions, and the
 * per-event detail message. The pending-ORASS-re-upload banner lives in the
 * workspace (it carries the action); this feed just renders the trail.
 */

import { FlaskConical, Radio } from 'lucide-react';
import type { SubmissionEventRead } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import { fmtTimestamp, shortId } from '@/lib/api/values';
import { CHANNEL_LABELS } from './shared';

const EVENT_TONES: Record<string, StatusTone> = {
  submitted: 'action',
  status_poll: 'slate',
  acknowledged: 'success',
  rejected: 'critical',
};

const EVENT_LABELS: Record<string, string> = {
  submitted: 'Submitted',
  status_poll: 'Status poll',
  acknowledged: 'Acknowledged',
  rejected: 'Rejected',
};

function detailMessage(detail: Record<string, unknown>): string | null {
  const message = detail.message ?? detail.note ?? detail.response;
  return typeof message === 'string' ? message : null;
}

export default function EventsFeed({ events }: { events: SubmissionEventRead[] }) {
  // API order is newest-first; the feed reads chronologically.
  const chronological = [...events].sort(
    (a, b) => a.occurredAt.getTime() - b.occurredAt.getTime()
  );

  if (chronological.length === 0) {
    return (
      <p className="text-caption text-slate">
        No channel interactions yet — events appear once the package is
        submitted.
      </p>
    );
  }

  return (
    <ol className="space-y-0">
      {chronological.map((event, i) => {
        const sandbox = event.detail?.sandbox === true;
        const pendingReupload = event.detail?.pending_orass_reupload === true;
        const message = detailMessage(event.detail ?? {});
        return (
          <li key={event.id} className="relative pl-6 pb-4 last:pb-0">
            {i < chronological.length - 1 && (
              <span
                aria-hidden
                className="absolute left-[7px] top-4 bottom-0 w-px bg-border-light"
              />
            )}
            <span
              aria-hidden
              className="absolute left-0 top-1 inline-flex items-center justify-center w-[15px] h-[15px] rounded-full border border-border bg-surface text-slate"
            >
              <Radio size={8} />
            </span>
            <div className="flex items-center gap-2 flex-wrap">
              <StatusPill tone={EVENT_TONES[event.event] ?? 'slate'}>
                {EVENT_LABELS[event.event] ?? event.event}
              </StatusPill>
              <span className="text-caption text-slate">
                via {CHANNEL_LABELS[event.channel] ?? event.channel}
              </span>
              {sandbox && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-warning/25 bg-warning-light text-warning text-micro font-medium uppercase tracking-wider">
                  <FlaskConical size={10} aria-hidden />
                  Sandbox
                </span>
              )}
              {pendingReupload && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-warning/25 bg-warning-light text-warning text-micro font-medium uppercase tracking-wider">
                  Pending ORASS re-upload
                </span>
              )}
              <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                {fmtTimestamp(event.occurredAt)}
              </span>
            </div>
            {event.externalRef && (
              <p className="mt-1 flex items-center gap-1.5 font-mono text-caption text-slate">
                ref {shortId(event.externalRef, 34)}
                <CopyButton text={event.externalRef} label="external reference" />
              </p>
            )}
            {message && (
              <p className="mt-1 text-caption text-navy/80 leading-relaxed">
                {message}
              </p>
            )}
          </li>
        );
      })}
    </ol>
  );
}
