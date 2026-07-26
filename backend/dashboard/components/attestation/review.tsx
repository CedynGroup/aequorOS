'use client';

/**
 * The parts of the signing ceremony that must be identical wherever signing
 * happens (docs/attestation_esignature.md §4.6).
 *
 * Two surfaces sign: the workspace, for the roles that have a field on the
 * document, and `CertifyDialog`, for the roles that do not. What-you-see-is-
 * what-you-sign cannot be a property of one of them. The figures, the statement,
 * the step-up form and the refusal copy therefore live here and are consumed by
 * both, so an improvement to one is an improvement to both and a regression in
 * one is impossible to ship in isolation.
 *
 * The statement is rendered in full — never truncated, never paraphrased, never
 * behind a "show more" — because the statement text is covered by the signature.
 * The digest the browser rendered is what gets sent back as
 * `expectedCertificationDigest`, so a stale tab cannot certify figures nobody
 * looked at.
 */

import {
  AlertTriangle,
  KeyRound,
  Loader2,
  Lock,
  PenLine,
  ShieldCheck,
} from 'lucide-react';
import type { CertificationPreviewRead, SigningRole } from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { isApiError } from '@/lib/api/client';
import { fmtDateUTC } from '@/lib/api/values';
import {
  DigestChip,
  SIGNING_ROLE_ACTIONS,
  SourceRunList,
  attestationErrorHelp,
  outstandingSummary,
  truncateDigest,
} from './shared';

/**
 * Codes that mean "this deployment cannot sign", not "your credentials are
 * wrong". They must be framed and placed differently: rendering a configuration
 * failure above the password field reads as a rejected password, and sends the
 * signer to re-type a password that was never checked. An administrator has to
 * act; the signer can do nothing, so they are not offered a field to try.
 */
export const CONFIGURATION_CODES = new Set([
  'signing_not_configured',
  'no_signer_identity',
  'signing_disabled',
  'no_signing_key',
  'signing_backend_unavailable',
  'signed_pdf_signer_unavailable',
]);

/** Codes for which re-trying the ceremony is pointless — the way out is elsewhere. */
export const TERMINAL_CODES = new Set([
  'figures_changed_since_certification',
  'already_certified',
  'already_fully_certified',
  'validation_not_clean',
  'not_validated',
  'role_not_in_policy',
  'officer_title_mismatch',
  'no_signing_key',
  'signing_disabled',
  'signature_not_required',
  'preparer_certification_missing',
  'register_state_missing',
  'maker_checker',
]);

/**
 * What came back from the SSO round trip. The callback route never puts the
 * authorisation in the URL — only one of these markers — so a link someone
 * copies out of the address bar carries no signing power.
 */
export const SSO_OUTCOMES: Record<string, string> = {
  ready:
    'Your institution confirmed your identity. Review the figures once more and sign — ' +
    'the authorisation is single-use, expires within minutes, and is bound to this ' +
    'return and these exact figures.',
  declined:
    'Your identity provider did not complete the sign-in. Nothing was signed. Try again, ' +
    'or sign with your password.',
  expired:
    'The step-up took too long and the request expired. Nothing was signed. Start the ' +
    're-authentication again.',
  invalid:
    'The response from your identity provider did not match this request, so it was ' +
    'rejected. Nothing was signed. Start the re-authentication again.',
  rejected:
    'Your institution authenticated you, but the platform would not accept it for signing — ' +
    'usually because the sign-in was not fresh, or the account does not match the signed-in ' +
    'user. Nothing was signed.',
  no_id_token:
    'Your identity provider returned no identity token, so presence could not be proved. ' +
    'Nothing was signed. Ask your administrator to confirm the OpenID Connect scopes.',
  unavailable:
    'Single sign-on is not configured for this institution, so SSO step-up is unavailable. ' +
    'Sign with your password instead.',
  idp_unreachable:
    'Your institution’s identity provider could not be reached, so presence could not be ' +
    'proved. Nothing was signed. Sign with your password, or try again once it is back.',
  failed:
    'The re-authentication could not be completed. Nothing was signed. Try again, or sign ' +
    'with your password.',
};

/** The refusal, framed by what the operator can actually do about it. */
export function CertificationFailure({
  error,
  onRequestVoid,
  onClose,
}: {
  error: unknown;
  /**
   * Offered only when the backend refuses because the figures diverged from the
   * frozen ones — voiding is the only correct exit.
   */
  onRequestVoid?: () => void;
  onClose?: () => void;
}) {
  const errorCode = isApiError(error) ? error.errorCode : null;
  const help = attestationErrorHelp(errorCode);
  const misconfigured = errorCode != null && CONFIGURATION_CODES.has(errorCode);
  const terminal = errorCode != null && (TERMINAL_CODES.has(errorCode) || misconfigured);

  return (
    <div
      role="alert"
      className={`rounded border px-3.5 py-3 ${
        terminal
          ? 'border-critical/30 bg-critical-light/50'
          : 'border-warning/30 bg-warning-light/50'
      }`}
    >
      <p className="inline-flex items-center gap-2 text-body font-medium text-navy">
        <AlertTriangle
          size={14}
          className={terminal ? 'text-critical' : 'text-warning'}
          aria-hidden
        />
        {misconfigured
          ? 'This platform is not set up to sign yet'
          : (help?.title ?? 'Certification was refused')}
      </p>
      {misconfigured && (
        <p className="mt-1 text-caption text-navy/85 leading-relaxed">
          Nothing is wrong with your credentials — signing was never attempted. An
          administrator has to finish configuring the signing key before any return
          can be certified.
        </p>
      )}
      <p className="mt-1 text-caption text-navy/85 leading-relaxed">
        {error instanceof Error ? error.message : String(error)}
      </p>
      {help && (
        <p className="mt-1.5 text-caption text-navy/85 leading-relaxed">{help.guidance}</p>
      )}
      <div className="mt-2.5 flex items-center gap-2 flex-wrap">
        {errorCode === 'figures_changed_since_certification' && onRequestVoid && (
          <button
            type="button"
            onClick={onRequestVoid}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light"
          >
            <PenLine size={13} aria-hidden />
            Void this attestation instead
          </button>
        )}
        {terminal && onClose && (
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
          >
            Close
          </button>
        )}
      </div>
    </div>
  );
}

/** True when re-trying the ceremony cannot help, so the form is withdrawn. */
export function isTerminalFailure(error: unknown): boolean {
  const code = isApiError(error) ? error.errorCode : null;
  return code != null && (TERMINAL_CODES.has(code) || CONFIGURATION_CODES.has(code));
}

/** The marker the SSO callback redirected back with, rendered honestly. */
export function SsoOutcomeNotice({ outcome }: { outcome: string }) {
  const held = outcome === 'ready';
  const blurb = SSO_OUTCOMES[outcome];
  if (!blurb) return null;
  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
        held ? 'border-success/25 bg-success-light/50' : 'border-warning/30 bg-warning-light/50'
      }`}
    >
      {held ? (
        <ShieldCheck size={15} className="shrink-0 mt-0.5 text-success" aria-hidden />
      ) : (
        <AlertTriangle size={15} className="shrink-0 mt-0.5 text-warning" aria-hidden />
      )}
      <div className="min-w-0">
        <p className="text-body font-medium text-navy">
          {held ? 'Identity confirmed — not yet signed' : 'Re-authentication did not complete'}
        </p>
        <p className="mt-0.5 text-caption text-navy/85 leading-relaxed">{blurb}</p>
      </div>
    </div>
  );
}

/**
 * A preview can be refused for the same reasons a signature can (not validated,
 * role not in policy, …), so the guidance table is used here too rather than
 * dropping the operator into a bare error.
 */
export function PreviewError({ error }: { error: unknown }) {
  const code = isApiError(error) ? error.errorCode : null;
  const help = attestationErrorHelp(code);
  if (!help) return <ErrorPanel error={error} title="Could not load the certification preview" />;
  return (
    <div role="alert" className="rounded border border-critical/30 bg-critical-light/50 px-3.5 py-3">
      <p className="text-body font-medium text-navy">{help.title}</p>
      <p className="mt-1 text-caption text-navy/85 leading-relaxed">
        {error instanceof Error ? error.message : String(error)}
      </p>
      <p className="mt-1.5 text-caption text-navy/85 leading-relaxed">{help.guidance}</p>
    </div>
  );
}

/** The figures being signed: identity, digest, and the bound source runs. */
export function FiguresSection({
  preview,
  signingRole,
}: {
  preview: CertificationPreviewRead;
  signingRole: SigningRole;
}) {
  // §4.2: the approver's UI must state explicitly that these are the identical
  // figures the preparer certified, and quote the digest.
  const frozen = preview.frozenCertificationDigest;
  const showFrozenAssertion = signingRole !== 'preparer' && frozen != null;

  return (
    <div className="space-y-3">
      {showFrozenAssertion && (
        <div
          className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
            preview.matchesFrozen
              ? 'border-success/25 bg-success-light/50'
              : 'border-critical/30 bg-critical-light/50'
          }`}
        >
          <ShieldCheck
            size={15}
            className={`shrink-0 mt-0.5 ${
              preview.matchesFrozen ? 'text-success' : 'text-critical'
            }`}
            aria-hidden
          />
          <div className="min-w-0 text-body">
            {preview.matchesFrozen ? (
              <>
                <p className="font-medium text-navy">
                  You are certifying the identical figures the preparer certified —
                  digest <span className="font-mono">{truncateDigest(frozen!)}</span>,
                  verified.
                </p>
                <p className="mt-0.5 text-caption text-navy/80 leading-relaxed">
                  The server recomputed the figures digest from the live package and it
                  matches the frozen value. Both signatures are over the same digest, so a
                  later mismatch is provable by anyone, offline.
                </p>
              </>
            ) : (
              <>
                <p className="font-medium text-navy">
                  These are NOT the figures the preparer certified.
                </p>
                <p className="mt-0.5 text-caption text-navy/80 leading-relaxed">
                  Frozen <span className="font-mono">{truncateDigest(frozen!)}</span>,
                  current{' '}
                  <span className="font-mono">
                    {truncateDigest(preview.certificationDigest)}
                  </span>
                  . The signature will be refused. This return must be voided and
                  re-certified from the preparer onwards.
                </p>
              </>
            )}
          </div>
        </div>
      )}

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
        <Row label="Return">
          <span className="font-mono text-caption text-navy">{preview.returnCode}</span>{' '}
          <span className="text-caption text-navy/85">
            {fmtDateUTC(preview.reportingDate)} · {preview.basis}
          </span>
        </Row>
        <Row label="Figures digest">
          <DigestChip digest={preview.certificationDigest} label="figures digest" />
        </Row>
        <Row label="Content seal">
          <DigestChip digest={preview.contentDigest} label="content digest" />
        </Row>
        <Row label="Binding">
          <span className="text-caption text-navy/85">
            {preview.bindingClass === 'engine_run'
              ? 'Engine runs — the digest binds which immutable runs are in scope'
              : 'Master data — the digest binds the register state at generation'}
          </span>
        </Row>
        {preview.registerStateDigest && (
          <Row label="Register-state digest">
            <DigestChip digest={preview.registerStateDigest} label="register state digest" />
          </Row>
        )}
        <Row label="Still to sign after you">
          <span className="text-caption text-navy/85">
            {outstandingSummary(
              preview.outstanding.filter(
                (slot) => slot.role !== signingRole || slot.count > 1
              )
            )}
          </span>
        </Row>
      </dl>

      <div>
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Source runs bound by this signature
        </p>
        <div className="mt-1 text-caption text-navy/85">
          {preview.signedSourceRuns.length === 0 ? (
            <p className="text-slate">
              None — this return binds master data rather than an engine run.
            </p>
          ) : (
            <SourceRunList runs={preview.signedSourceRuns} />
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

/**
 * The statement, in full. Rendered as flowing text with `whitespace-pre-wrap`
 * and no height cap: the wording is covered by the signature, so abbreviating it
 * here would mean the signer committed to words this screen never showed them.
 */
export function StatementSection({ statement }: { statement: string }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Attestation statement
        </p>
        <StatusPill tone="action">Covered by your signature</StatusPill>
      </div>
      <blockquote className="mt-1.5 rounded border-l-4 border-l-action border border-border-light bg-surface px-4 py-3 text-body text-navy leading-relaxed whitespace-pre-wrap">
        {statement}
      </blockquote>
    </div>
  );
}

/**
 * Step-up: the signer proves presence NOW, not at session start (§stepup). Two
 * proofs exist server-side — a password re-entry for password accounts and a
 * fresh OIDC id_token for SSO accounts — and both are offered because the
 * platform does not expose which provider an account uses.
 *
 * When `authorizationHeld` is set the signer has just come back from their IdP
 * and the proof is already made, so the password field is withdrawn rather than
 * left on screen asking for something that is no longer needed.
 *
 * `blockedReason` gates BOTH proofs, not just the submit: leaving for an identity
 * provider with the ceremony half-assembled means coming back to assemble it
 * again, having spent a re-authentication that expires in minutes.
 */
export function StepUpSection({
  signingRole,
  password,
  onPasswordChange,
  onSubmit,
  onSsoClick,
  pending,
  authorizationHeld,
  stage,
  onCancel,
  actionLabel,
  blockedReason,
}: {
  signingRole: SigningRole;
  password: string;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  onSsoClick: () => void;
  pending: boolean;
  /** An SSO authorisation is held server-side; only the signature remains. */
  authorizationHeld: boolean;
  stage: 'idle' | 'proving' | 'signing';
  onCancel?: () => void;
  /** Overrides the role verb — the workspace also sends, not only certifies. */
  actionLabel?: string;
  /** Why the ceremony cannot be started yet, in the signer's own terms. */
  blockedReason?: string | null;
}) {
  const blocked = Boolean(blockedReason);
  return (
    <form onSubmit={onSubmit} className="pt-4 border-t border-border-light space-y-3">
      <div className="flex items-center gap-2">
        <Lock size={13} className="text-slate" aria-hidden />
        <p className="text-body font-medium text-navy">
          {authorizationHeld ? 'Sign now' : 'Confirm it is you, now'}
        </p>
      </div>
      <p className="text-caption text-slate leading-relaxed">
        {authorizationHeld
          ? 'Your single-use authorisation is held by the server and expires within minutes. ' +
            'It is bound to this return, this role, and the figures digest above — it cannot be ' +
            'replayed, and it will not sign anything else.'
          : 'Signing is deliberately harder than browsing. Re-authenticating produces a ' +
            'single-use authorisation bound to these exact figures — it cannot be replayed, ' +
            'cannot be used for another return, and does not survive the figures changing.'}
      </p>

      {!authorizationHeld && (
        <label className="block max-w-sm">
          <span className="block text-caption font-medium text-navy mb-1.5">Your password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => onPasswordChange(event.target.value)}
            className="w-full rounded border border-border bg-surface px-3 py-2 text-body text-navy"
          />
        </label>
      )}

      {blockedReason && (
        <p role="status" className="text-caption text-warning leading-relaxed">
          {blockedReason}
        </p>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="submit"
          disabled={pending || blocked || (!authorizationHeld && password.trim().length === 0)}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
        >
          {pending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <PenLine size={13} aria-hidden />
          )}
          {stage === 'proving'
            ? 'Confirming identity…'
            : stage === 'signing'
              ? 'Signing…'
              : (actionLabel ?? SIGNING_ROLE_ACTIONS[signingRole] ?? 'Certify')}
        </button>
        <button
          type="button"
          onClick={onSsoClick}
          disabled={pending || blocked}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60"
        >
          <KeyRound size={13} aria-hidden />
          {authorizationHeld
            ? 'Re-authenticate again'
            : 'Re-authenticate with single sign-on'}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="inline-flex items-center px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-60"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
