'use client';

/**
 * The regulator's channel — and the ONLY place in the product that names it.
 *
 * Transmission is the Validator's authority and nobody else's
 * (`backend/docs/filing_submit_authority_rollout.md`). §4b.1 of the redesign
 * requires that the channel, the downtime bundle and the resubmission workflow
 * be ABSENT for every other role rather than greyed out, so the whole vocabulary
 * lives in this one file: the Returns workspace mounts it only behind the
 * projected transmission capability, and a test asserts the workspace's own
 * source contains none of these words. A control nobody else's screen can even
 * spell cannot leak onto it by accident.
 *
 * The portal's name comes from the active jurisdiction, never a literal.
 */

import type { ChannelCode } from '@aequoros/risk-service-api';
import { Download, FlaskConical, Mail, RadioTower } from 'lucide-react';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { isApiError } from '@/lib/api/client';
import { CHANNEL_LABELS } from './shared';
import { submissionPortal } from '@/lib/format';

const CHANNEL_OPTIONS: ChannelCode[] = [
  'orass_api',
  'orass_sandbox',
  'email',
  'manual',
];

/**
 * The channel a downtime filing is re-uploaded through once the portal is back.
 *
 * The sandbox, because that is the only portal implementation this platform can
 * actually reach — the production portal API is not public. Changing it is a
 * deployment decision, not a UI one.
 */
export const REUPLOAD_CHANNEL: ChannelCode = 'orass_sandbox';

/** The portal's name in the bank's own jurisdiction, or a neutral stand-in. */
function portalName(): string {
  return submissionPortal() ?? 'the regulator’s portal';
}

/**
 * The portal's structured refusal, turned into the sentence it deserves.
 *
 * Lives here rather than in the workspace for the same reason as everything
 * else in this file: the workspace must not be able to spell the portal's
 * vocabulary, or the rule that only one role sees it becomes a convention
 * instead of a fact.
 */
export function filingRefusalMessage(error: unknown): string | null {
  if (!isApiError(error) || error.errorCode !== 'channel_downtime') return null;
  return error.message;
}

/** The one line the collapsed filing row states about itself. */
export function transmissionSummary({
  pendingReupload,
  refusal,
  regulatorName,
}: {
  pendingReupload: boolean;
  refusal: string | null;
  regulatorName: string;
}): string {
  if (pendingReupload) {
    return 'Sent by the downtime bundle and not yet complete.';
  }
  if (refusal) {
    return `${portalName()} turned this filing away — an email fallback is ready.`;
  }
  return `How this return reaches ${regulatorName}.`;
}

/**
 * What the officer is about to do, said before they are offered the button.
 *
 * Filing leaves the bank. It cannot be recalled from here — a correction after
 * it needs the regulator's own go-ahead — and the officer signing it off should
 * know exactly which documents go with it.
 */
export function TransmissionNotice({
  regulatorName,
  artifactSentence,
  isRehearsal,
}: {
  regulatorName: string;
  /** Which files go, phrased for this return. */
  artifactSentence: string;
  isRehearsal: boolean;
}) {
  if (isRehearsal) return null;
  return (
    <p
      data-testid="transmission-notice"
      className="mb-2.5 rounded border border-warning/25 bg-warning-light/40 px-3 py-2 text-caption leading-relaxed text-navy/85"
    >
      <span className="font-medium text-navy">
        This sends the return to {regulatorName}.
      </span>{' '}
      It cannot be recalled from here — correcting a filed return needs{' '}
      {regulatorName}&apos;s own go-ahead. {artifactSentence}
    </p>
  );
}

export default function TransmissionCard({
  channel,
  defaultChannel,
  onChannelChange,
  latestSubmittedChannel,
  pendingReupload,
  refusal,
  instructions,
  onUseEmailFallback,
  onDownloadEml,
  emlError,
  submitError,
  pollStatus,
  pollError,
  fallbackPending,
}: {
  channel: ChannelCode;
  defaultChannel: ChannelCode;
  onChannelChange: (channel: ChannelCode) => void;
  latestSubmittedChannel: ChannelCode | null;
  pendingReupload: boolean;
  /** The portal's structured refusal, when it turned the filing away. */
  refusal: string | null;
  instructions: string | null;
  onUseEmailFallback: () => void;
  onDownloadEml: () => void;
  emlError: string | null;
  submitError: unknown;
  pollStatus: string | null;
  pollError: unknown;
  fallbackPending: boolean;
}) {
  return (
    <div className="space-y-3">
      <label className="flex items-center gap-3 text-caption text-slate">
        <span className="font-medium text-navy">Channel</span>
        <select
          value={channel}
          onChange={(event) => onChannelChange(event.target.value as ChannelCode)}
          className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
        >
          {CHANNEL_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {CHANNEL_LABELS[option]}
              {option === defaultChannel ? ' · default' : ''}
            </option>
          ))}
        </select>
        <span className="text-caption text-slate">
          Set from this return&apos;s registry entry.
        </span>
      </label>

      {channel === 'orass_sandbox' && (
        <p className="inline-flex items-center gap-1.5 rounded border border-warning/25 bg-warning-light px-2 py-1 text-micro font-medium uppercase tracking-wider text-warning">
          <FlaskConical size={11} aria-hidden />
          Sandbox — a simulated {portalName()}, not the real portal
        </p>
      )}

      {pendingReupload && (
        <div className="flex items-start gap-2.5 rounded border border-warning/30 bg-warning-light/40 px-3.5 py-2.5">
          <RadioTower size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
          <p className="text-caption leading-relaxed text-navy/85">
            <span className="font-medium text-navy">
              Sent by the downtime bundle, and not yet complete.
            </span>{' '}
            A filing made while {portalName()} was unavailable is deemed complete
            only once it has been re-uploaded through the portal. Re-upload it
            from the action above as soon as the portal is back.
          </p>
        </div>
      )}

      {refusal && (
        <div className="space-y-2.5 rounded border border-warning/30 bg-warning-light/50 px-3.5 py-3">
          <p className="inline-flex items-center gap-1.5 text-body font-medium text-navy">
            <Mail size={13} className="text-warning" aria-hidden />
            {portalName()} downtime — email fallback available
          </p>
          <p className="text-caption leading-relaxed text-navy/80">
            {refusal}
          </p>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              disabled={fallbackPending}
              onClick={onUseEmailFallback}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              <Mail size={13} aria-hidden />
              Use email fallback
            </button>
            <button
              type="button"
              onClick={onDownloadEml}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
            >
              <Download size={13} aria-hidden />
              Download the email bundle
            </button>
          </div>
          {instructions && (
            <details className="text-caption text-navy/80">
              <summary className="cursor-pointer font-medium text-navy">
                Read the send-ready instructions
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-border-light bg-surface p-3 font-mono text-micro leading-relaxed">
                {instructions}
              </pre>
            </details>
          )}
        </div>
      )}

      {latestSubmittedChannel === 'email' && !refusal && (
        <div className="space-y-1.5">
          <button
            type="button"
            onClick={onDownloadEml}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface"
          >
            <Download size={13} aria-hidden />
            Download the email bundle
          </button>
          <p className="text-micro leading-relaxed text-slate">
            Subject, instructions and attachments, ready to open in your mail
            client and send.
          </p>
        </div>
      )}

      {emlError && <p className="text-caption text-critical">{emlError}</p>}

      {pollStatus && (
        <p className="text-caption text-navy/80">
          Last check: <span className="font-mono">{pollStatus}</span>
        </p>
      )}
      {pollError ? (
        <ErrorPanel
          error={pollError}
          title="Could not reach the channel for a decision"
        />
      ) : null}
      {submitError && !refusal ? (
        <ErrorPanel error={submitError} title="The filing was not accepted" />
      ) : null}
    </div>
  );
}
