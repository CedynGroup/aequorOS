'use client';

/**
 * The signing ceremony for a role the document has no field for
 * (docs/attestation_esignature.md §4.6).
 *
 * `preparer` and `approver` are placeable on the artifact, so they sign in the
 * workspace, where the box they are about to appear in is visible. `board` and
 * `witness` have no field on the return (`pdf_signing.ROLE_FIELD_NAMES`), so
 * there is nothing to show them on the page and this dialog remains their
 * ceremony. The review sections and the step-up form are shared with the
 * workspace through `./review` — the two surfaces are the same commitment made
 * against the same evidence, and must not be allowed to drift.
 *
 * Two server calls, deliberately separate, so that presence is proved against
 * the exact figures being signed and the authorisation cannot outlive them:
 *
 *   POST …/attestation/step-up   → re-authenticate, receive a single-use token
 *   POST …/attestation/certify   → spend the token, produce the signature
 *
 * On the password path the dialog does BOTH on one submit: the button the signer
 * presses is the commitment, and there is no window in which they have
 * re-authenticated but not signed.
 *
 * The SSO path cannot work that way — re-authentication is a full redirect to the
 * bank's IdP, so the signer necessarily returns to a fresh page. The window is
 * closed by construction instead of by timing: the authorisation is minted
 * server-side, held in an HttpOnly cookie the page cannot read, bound to
 * (user, package, role, digest), single-use, and short-lived. Signing still
 * requires a deliberate press against a re-rendered statement and digest, and if
 * the figures moved during the redirect the digests disagree and the signature is
 * refused rather than silently applied to something else.
 *
 * What-you-see-is-what-you-sign is not a slogan here. The statement is rendered
 * in full — never truncated, never paraphrased, never behind a "show more" —
 * because the statement text is covered by the signature, so the commitment is
 * to the words the person actually read. The digest the browser rendered is sent
 * back as `expectedCertificationDigest` and compared server-side, so a stale tab
 * cannot certify figures nobody looked at.
 */

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { SigningRole } from '@aequoros/risk-service-api';
import { SkeletonCard } from '@/components/ui/Skeleton';
import {
  useCertificationPreview,
  useCertifyPackage,
  useCertifyWithHeldAuthorization,
  useStepUpForSigning,
} from '@/lib/api/hooks';
import { SIGNING_ROLE_ACTIONS, roleNoun } from './shared';
import {
  CertificationFailure,
  FiguresSection,
  PreviewError,
  SsoOutcomeNotice,
  StatementSection,
  StepUpSection,
  isTerminalFailure,
} from './review';
import { startSsoStepUp } from '@/lib/attestation/ssoStepUp';

export default function CertifyDialog({
  bankId,
  packageId,
  signingRole,
  returnLabel,
  onClose,
  onCertified,
  onRequestVoid,
  ssoOutcome,
}: {
  bankId: string;
  packageId: string;
  signingRole: SigningRole;
  /** e.g. "BSD3 · 31 Mar 2026 v2" — for the dialog heading. */
  returnLabel: string;
  onClose: () => void;
  onCertified?: () => void;
  /**
   * Opens the void affordance. Offered when the backend refuses because the
   * figures diverged from the frozen ones — voiding is the only correct exit.
   */
  onRequestVoid?: () => void;
  /**
   * The `stepUp` marker the SSO callback route redirected back with, if the
   * signer has just returned from their identity provider. `ready` means an
   * authorisation is held server-side and only the signature remains.
   */
  ssoOutcome?: string | null;
}) {
  const previewQuery = useCertificationPreview(bankId, packageId, signingRole);
  const stepUp = useStepUpForSigning(bankId);
  const certify = useCertifyPackage(bankId);
  const certifyWithHeld = useCertifyWithHeldAuthorization(bankId);

  const [password, setPassword] = useState('');
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Escape closes, matching the shell's drawers. Focus lands on the heading so a
  // screen reader announces what the dialog is before the signer meets a field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    headingRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const preview = previewQuery.data;
  const pending = stepUp.isPending || certify.isPending || certifyWithHeld.isPending;
  // Any of the three calls can refuse; the signer cares about one failure, not three.
  const failure = certifyWithHeld.error ?? certify.error ?? stepUp.error ?? null;
  const terminal = isTerminalFailure(failure);
  const authorizationHeld = ssoOutcome === 'ready';

  const finish = () => {
    setPassword('');
    onCertified?.();
    onClose();
  };

  const onPasswordSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!preview) return;
    // Rejections are rendered from the mutations' own error state.
    void (async () => {
      const granted = await stepUp.mutateAsync({ packageId, signingRole, password });
      await certify.mutateAsync({
        packageId,
        signingRole,
        authorizationToken: granted.authorizationToken,
        // The digest the ceremony was built around — not the one the server just
        // recomputed, so a change between preview and submit is caught, not hidden.
        expectedCertificationDigest: preview.certificationDigest,
      });
      finish();
    })().catch(() => undefined);
  };

  /** Spend the cookie-held authorisation minted by the SSO callback. */
  const onHeldAuthorizationSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!preview) return;
    void certifyWithHeld
      .mutateAsync({
        packageId,
        signingRole,
        expectedCertificationDigest: preview.certificationDigest,
      })
      .then(finish)
      .catch(() => undefined);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Certify ${returnLabel}`}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
    >
      <button
        type="button"
        aria-label="Cancel certification"
        onClick={onClose}
        className="fixed inset-0 bg-black/50 backdrop-blur-sm"
      />
      <section className="relative w-full max-w-3xl rounded-lg bg-surface-raised border border-border shadow-pop">
        <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border-light">
          <div className="min-w-0">
            <h2 ref={headingRef} tabIndex={-1} className="text-h3 text-navy outline-none">
              {SIGNING_ROLE_ACTIONS[signingRole] ?? 'Certify'} — {returnLabel}
            </h2>
            <p className="mt-0.5 text-caption text-slate">
              Signing as {roleNoun(signingRole).toLowerCase()} · your signature covers
              the figures digest and the statement exactly as shown below
            </p>
          </div>
          <button
            type="button"
            aria-label="Cancel certification"
            onClick={onClose}
            className="shrink-0 w-9 h-9 rounded text-slate hover:bg-surface inline-flex items-center justify-center"
          >
            <X size={16} aria-hidden />
          </button>
        </header>

        <div className="px-5 py-4 space-y-5 max-h-[70vh] overflow-y-auto">
          {previewQuery.isLoading ? (
            <SkeletonCard />
          ) : previewQuery.error ? (
            <PreviewError error={previewQuery.error} />
          ) : preview ? (
            <>
              <FiguresSection preview={preview} signingRole={signingRole} />
              <StatementSection statement={preview.statement} />
            </>
          ) : null}

          {failure != null && (
            <CertificationFailure
              error={failure}
              onClose={onClose}
              onRequestVoid={
                onRequestVoid
                  ? () => {
                      onClose();
                      onRequestVoid();
                    }
                  : undefined
              }
            />
          )}

          {ssoOutcome && <SsoOutcomeNotice outcome={ssoOutcome} />}

          {preview && !terminal && (
            <StepUpSection
              signingRole={signingRole}
              password={password}
              onPasswordChange={setPassword}
              onSubmit={authorizationHeld ? onHeldAuthorizationSubmit : onPasswordSubmit}
              onSsoClick={() =>
                startSsoStepUp({ bankId, packageId, signingRole, resume: 'certify' })
              }
              pending={pending}
              authorizationHeld={authorizationHeld}
              stage={
                certify.isPending || certifyWithHeld.isPending
                  ? 'signing'
                  : stepUp.isPending
                    ? 'proving'
                    : 'idle'
              }
              onCancel={onClose}
            />
          )}
        </div>
      </section>
    </div>
  );
}
