'use client';

/**
 * Shared vocabulary for attestation & e-signature (docs/attestation_esignature.md).
 *
 * Three things in here are load-bearing rather than cosmetic:
 *
 * 1. `SignatureBlock` renders EXACTLY the four lines of §2.5. The same string
 *    appears in-app, in the signature record, and stamped into the signed PDF,
 *    and §2.5 requires all three to agree — so the layout and the timestamp
 *    format are a contract, not a styling choice.
 * 2. `fmtSignatureTimestamp` prints UTC as `YYYY-MM-DD HH:MM:SS GMT` for the
 *    same reason: a BoG examiner holding only a printout must be able to match
 *    it against the in-app record, which a locale-formatted local time defeats.
 * 3. `ATTESTATION_ERROR_HELP` turns the backend's stable 409 `error_code`s into
 *    the operator's next action. The backend messages are already good; what
 *    they cannot know is which UI affordance resolves them.
 */

import type { ReactNode } from 'react';
import { Fingerprint } from 'lucide-react';
import type {
  AttestationState,
  AttestationStatusRead,
  OutstandingSlotRead,
  SignatureRead,
  SigningRole,
} from '@aequoros/risk-service-api';
import CopyButton from '@/components/ui/CopyButton';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------

/** Noun form — for policy tables, routing lists, and outstanding-slot copy. */
export const SIGNING_ROLE_LABELS: Record<SigningRole, string> = {
  preparer: 'Preparer',
  approver: 'Approver',
  board: 'Board',
  witness: 'Witness',
};

/**
 * The signature-block role line of §2.5 ("Prepared by" / "Approved by"). Past
 * tense and passive, because the line attributes a completed act.
 */
export const SIGNING_ROLE_ATTRIBUTIONS: Record<SigningRole, string> = {
  preparer: 'Prepared by',
  approver: 'Approved by',
  board: 'Attested by the Board',
  witness: 'Witnessed by',
};

/**
 * The verb the signer sees on the action they are about to take.
 *
 * A checker's verb names BOTH halves, because one press records both: the
 * signature over the frozen figures and the approval decision, in one backend
 * transaction. There is no separate approve button anywhere for them to press
 * afterwards, and the label should not imply there is.
 */
export const SIGNING_ROLE_ACTIONS: Record<SigningRole, string> = {
  preparer: 'Certify and freeze',
  approver: 'Approve and sign',
  board: 'Attest and sign',
  witness: 'Witness and sign',
};

export function roleAttribution(role: SigningRole | string): string {
  return SIGNING_ROLE_ATTRIBUTIONS[role as SigningRole] ?? `Signed by (${role})`;
}

export function roleNoun(role: SigningRole | string): string {
  return SIGNING_ROLE_LABELS[role as SigningRole] ?? role;
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

export const ATTESTATION_STATE_LABELS: Record<AttestationState, string> = {
  unsigned: 'Unsigned',
  preparer_certified: 'Preparer certified',
  fully_certified: 'Fully certified',
  void: 'Voided',
};

const ATTESTATION_STATE_TONES: Record<AttestationState, StatusTone> = {
  unsigned: 'slate',
  // Amber, not neutral: the figures are frozen and a second signature is owed.
  preparer_certified: 'amber',
  fully_certified: 'success',
  // Critical, because a void blocks submission until the return is re-certified.
  void: 'critical',
};

/** One-liner shown under the state pill so the state reads as a consequence. */
export const ATTESTATION_STATE_BLURBS: Record<AttestationState, string> = {
  unsigned:
    'Not certified. The figures are still free to change and the return cannot be submitted.',
  preparer_certified:
    'The preparer has certified. Figures are frozen and regeneration is refused until the remaining signatures are recorded or the attestation is voided.',
  fully_certified:
    'Every signature the policy in force requires is present. The return is cleared for submission.',
  // The service resets to `unsigned` on a void rather than persisting this
  // state, so it is defensive rather than routine — but it must still read
  // correctly if a package ever reports it.
  void:
    'The attestation was withdrawn. Signatures are retained as history, never deleted; the return must be re-certified before it can be submitted.',
};

export function AttestationStatePill({ state }: { state: AttestationState }) {
  return (
    <StatusPill tone={ATTESTATION_STATE_TONES[state] ?? 'slate'}>
      {ATTESTATION_STATE_LABELS[state] ?? state}
    </StatusPill>
  );
}

/** "1 preparer · 1 approver" — the outstanding slots, in policy order. */
export function outstandingSummary(outstanding: OutstandingSlotRead[]): string {
  if (outstanding.length === 0) return 'None';
  return outstanding
    .map((slot) => `${slot.count} ${roleNoun(slot.role).toLowerCase()}`)
    .join(' · ');
}

// ---------------------------------------------------------------------------
// Submission clearance — the single answer, stated in both directions
// ---------------------------------------------------------------------------

/**
 * Whether this return may reach a channel, and why not when it may not.
 *
 * Driven by `canSubmit` and by nothing looser. The service computes it from the
 * policy in force and the signatures on record (`attestation_api._status_payload`);
 * every surface that re-derived it from the attestation state or the policy flag
 * is how "CLEARED TO SUBMIT" once rendered beside "UNSIGNED".
 *
 * The refusal is RENDERED, never left as an absence: a missing badge reads as a
 * card that has not loaded yet, not as a return that cannot be filed. It names the
 * outstanding signatures for the same reason — "blocked" without "by whom" sends
 * the operator hunting.
 */
export function SubmissionClearancePill({
  status,
}: {
  status: AttestationStatusRead;
}) {
  return (
    <span data-testid="attestation-clearance">
      {status.canSubmit ? (
        <StatusPill tone="success">Cleared to submit</StatusPill>
      ) : (
        <StatusPill tone="amber">
          Not cleared to submit — {outstandingSummary(status.outstanding)} outstanding
        </StatusPill>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Digests
// ---------------------------------------------------------------------------

/** "a4f2…9c1b" — the §4.2 shorthand the approver assertion quotes. */
export function truncateDigest(digest: string): string {
  if (digest.length <= 12) return digest;
  return `${digest.slice(0, 4)}…${digest.slice(-4)}`;
}

/**
 * A digest, truncated for scanning, with the full value on hover and a copy
 * control. Never renders a truncated value without a way to get the whole
 * thing: a digest the reader cannot verify independently is decoration.
 */
export function DigestChip({
  digest,
  label,
  className = '',
}: {
  digest: string;
  label: string;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <code
        title={digest}
        aria-label={`${label} ${digest}`}
        className="font-mono text-caption text-navy tnum rounded border border-border-light bg-surface px-1.5 py-0.5"
      >
        {truncateDigest(digest)}
      </code>
      <CopyButton text={digest} label={label} className="shrink-0" />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Timestamps
// ---------------------------------------------------------------------------

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/**
 * "2026-07-31 14:02:11 GMT" — UTC, seconds included, matching the visible PDF
 * signature appearance byte for byte (§2.5). Deliberately NOT locale-formatted.
 */
export function fmtSignatureTimestamp(value: Date | string): string {
  const at = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(at.getTime())) return '—';
  return (
    `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())} ` +
    `${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}:${pad(at.getUTCSeconds())} GMT`
  );
}

// ---------------------------------------------------------------------------
// Signature block — §2.5, exactly four lines
// ---------------------------------------------------------------------------

/**
 * The rendered signature. Four lines, in this order, with nothing between them:
 *
 *   Approved by
 *   Ama Mensah — Chief Financial Officer
 *   Signer ID: SGN-7K4M9PQR2VWX3YZ8
 *   2026-07-31 14:02:11 GMT   (RFC 3161 timestamped)
 *
 * The signee ID beneath the signature is a hard requirement — it is the only
 * identifier that survives the user row being deprovisioned (§2.6), so it is
 * what an attribution question years later actually turns on.
 *
 * `tsa_time` is trusted time and wins when present; `declared_at` is the server
 * clock and is labelled as such, because presenting a host clock as trusted
 * time would misrepresent the evidence.
 */
export function SignatureBlock({
  signature,
  className = '',
}: {
  signature: SignatureRead;
  className?: string;
}) {
  const trusted = signature.tsaTime != null;
  const timestamp = fmtSignatureTimestamp(signature.tsaTime ?? signature.declaredAt);
  // A redacted name (§6.3) must not silently read as an unsigned block.
  const name = signature.signerDisplayName ?? 'Name withheld';

  return (
    <div
      className={`rounded border border-border-light bg-surface px-3.5 py-3 ${className}`}
    >
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {roleAttribution(signature.signingRole)}
      </p>
      <p className="mt-1 text-body text-navy">
        {name}
        {signature.officerTitle ? (
          <span className="text-navy/80"> — {signature.officerTitle}</span>
        ) : null}
      </p>
      <p className="mt-1 flex items-center gap-2 flex-wrap">
        <span className="font-mono text-caption text-navy tnum">
          Signer ID: {signature.signerId}
        </span>
        <CopyButton text={signature.signerId} label="signer ID" className="shrink-0" />
      </p>
      <p className="mt-1 font-mono text-caption text-slate tnum">
        {timestamp}
        {trusted ? (
          <span className="ml-2 text-success">(RFC 3161 timestamped)</span>
        ) : (
          <span className="ml-2 text-warning">(server clock — no trusted timestamp)</span>
        )}
      </p>
    </div>
  );
}

/**
 * The cryptographic detail behind a signature — separate from the block above
 * so the four-line contract of §2.5 stays exact.
 */
export function SignatureEvidence({ signature }: { signature: SignatureRead }) {
  const runs = signature.signedSourceRuns;
  return (
    <dl className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-caption">
      <EvidenceRow label="Figures digest signed">
        <DigestChip digest={signature.certificationDigest} label="certification digest" />
      </EvidenceRow>
      <EvidenceRow label="Signature method">
        <code className="font-mono text-caption text-navy">
          {signature.signatureMethod}
        </code>
      </EvidenceRow>
      <EvidenceRow label="Content seal">
        <DigestChip digest={signature.contentDigest} label="content digest" />
      </EvidenceRow>
      <EvidenceRow label="Signing certificate">
        <DigestChip digest={signature.certificateSha256} label="certificate sha256" />
      </EvidenceRow>
      {signature.registerStateDigest && (
        <EvidenceRow label="Register-state digest">
          <DigestChip
            digest={signature.registerStateDigest}
            label="register state digest"
          />
        </EvidenceRow>
      )}
      <EvidenceRow label="Bound source runs">
        {runs.length === 0 ? (
          <span className="text-slate">
            None — this return binds master data, not an engine run
          </span>
        ) : (
          <SourceRunList runs={runs} />
        )}
      </EvidenceRow>
    </dl>
  );
}

function EvidenceRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-0.5 text-navy/85">{children}</dd>
    </div>
  );
}

/**
 * `[{module, run_id, input_hash}]` as signed. Typed as an untyped record by the
 * contract, so every field is read defensively rather than asserted.
 */
export function SourceRunList({ runs }: { runs: Array<{ [key: string]: unknown }> }) {
  return (
    <ul className="space-y-1">
      {runs.map((run, index) => {
        // `moduleName`, not `module` — Next forbids assigning that identifier.
        const moduleName =
          typeof run.module === 'string' ? run.module : 'unknown module';
        const runId = typeof run.run_id === 'string' ? run.run_id : null;
        const inputHash = typeof run.input_hash === 'string' ? run.input_hash : null;
        return (
          <li
            key={runId ?? `${moduleName}-${index}`}
            className="flex items-center gap-2 flex-wrap"
          >
            <Fingerprint size={11} className="text-slate shrink-0" aria-hidden />
            <span className="font-medium text-navy">{moduleName}</span>
            {runId && (
              <span className="font-mono text-micro text-slate tnum" title={runId}>
                run {truncateDigest(runId)}
              </span>
            )}
            {inputHash && (
              <DigestChip digest={inputHash} label={`${moduleName} input hash`} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Compact read-only summary
// ---------------------------------------------------------------------------

/**
 * The attestation record, read-only — for surfaces that report on a package
 * rather than act on it (History's expanded record, the Approvals decide panel).
 * `AttestationPanel` is the acting surface; this one deliberately offers no
 * certify or void affordance.
 *
 * Neither package payload carries `attestation_state`, so callers fetch the
 * status per selected package rather than per row — a column would mean one
 * request per table row.
 */
export function AttestationSummary({
  status,
  className = '',
}: {
  status: AttestationStatusRead;
  className?: string;
}) {
  return (
    <div className={`space-y-2.5 ${className}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <AttestationStatePill state={status.attestationState} />
        <SubmissionClearancePill status={status} />
        {!status.policy.requireSignature && (
          <StatusPill tone="slate">Signature not required</StatusPill>
        )}
        {status.certificationDigest && (
          <DigestChip
            digest={status.certificationDigest}
            label="frozen figures digest"
          />
        )}
      </div>

      {status.policy.requireSignature && (
        <p className="text-caption text-slate">
          Outstanding: {outstandingSummary(status.outstanding)}
        </p>
      )}

      {status.voidReason && (
        <p className="text-caption text-navy/85 leading-relaxed">
          <span className="font-medium text-navy">Last void reason: </span>
          {status.voidReason}
        </p>
      )}

      {status.signatures.length > 0 ? (
        <ul className="space-y-2">
          {status.signatures.map((signature) => (
            <li key={signature.id}>
              <SignatureBlock signature={signature} />
            </li>
          ))}
        </ul>
      ) : (
        status.policy.requireSignature && (
          <p className="text-caption text-slate">
            No signatures in attestation cycle #{status.attestationCycle}.
            Signatures from voided cycles are retained in the append-only trail
            rather than shown here.
          </p>
        )
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Error-code copy
// ---------------------------------------------------------------------------

export interface AttestationErrorHelp {
  /** Panel heading — states what was refused, not that something went wrong. */
  title: string;
  /** What the operator does next. The backend message says why. */
  guidance: string;
}

/**
 * The backend raises stable `error_code`s precisely so the UI can branch
 * (workflow.AttestationConflict). Each entry adds the resolution path, which is
 * UI knowledge the service does not have.
 */
export const ATTESTATION_ERROR_HELP: Record<string, AttestationErrorHelp> = {
  step_up_required: {
    title: 'Re-authentication required',
    guidance:
      'Signing is deliberately harder than browsing: confirm your identity now, against these exact figures. The authorisation is single-use and expires in minutes.',
  },
  step_up_failed: {
    title: 'Re-authentication failed',
    guidance:
      'The proof was not accepted. Password re-entry only works for password accounts; if your institution signs in through its identity provider, re-authenticate there instead.',
  },
  maker_checker: {
    title: 'Segregation of duties blocks this signature',
    guidance:
      'Each required signature must come from a different person, and the officer who generated the return cannot approve it. Ask a second officer with the approver role to certify.',
  },
  figures_changed_since_certification: {
    title: 'The figures no longer match what the preparer certified',
    guidance:
      'This return cannot be approved as it stands. Void the current attestation — which keeps every signature as history — then re-certify the corrected figures from the preparer onwards. It is never resolved by signing anyway.',
  },
  figures_changed_since_preview: {
    title: 'The figures changed while this screen was open',
    guidance:
      'You would be certifying something you have not read. Close this dialog, reload the return, review it again, and start the ceremony from the top.',
  },
  already_certified: {
    title: 'Already certified by a preparer',
    guidance:
      'A preparer certification is already on record for this cycle. Void the current attestation to start again, or continue as an approver.',
  },
  already_fully_certified: {
    title: 'Already fully certified',
    guidance:
      'Every signature the policy requires is present. Nothing further is needed before submission.',
  },
  validation_not_clean: {
    title: 'Validation has not passed cleanly',
    guidance:
      'Figures with outstanding ERROR findings cannot be attested to. Resolve the findings, re-validate, then certify.',
  },
  not_validated: {
    title: 'Not validated yet',
    guidance:
      'Run validation from the Validation card and clear every ERROR finding before certifying.',
  },
  role_not_in_policy: {
    title: 'This role is not in the policy in force',
    guidance:
      'Signing requirements are configuration, resolved as at the reporting date. Adjust the signing policy under Regulatory Reporting → Settings if the requirement is wrong.',
  },
  officer_title_mismatch: {
    title: 'Officer title does not satisfy the policy slot',
    guidance:
      'The policy names which officers may sign this return and matches them against your recorded job title. Correct your job title in Settings → Profile, or have an administrator amend the policy.',
  },
  no_signing_key: {
    title: 'No signing key enrolled',
    guidance:
      'An administrator must enrol an active signing key for this signer before they can certify. Certification is refused rather than recorded unsigned.',
  },
  signing_disabled: {
    title: 'Attestation signing is not enabled',
    guidance:
      'This deployment has signing turned off (ATTESTATION_SIGNING_ENABLED). Identity provisioning and the evidential trail stay on; producing signatures does not.',
  },
  attestation_incomplete: {
    title: 'Attestation incomplete',
    guidance:
      'Submission is gated on a complete attestation. Record the outstanding signatures listed above first.',
  },
  signature_not_required: {
    title: 'No signature is required for this return',
    guidance:
      'The policy in force exempts this return family from signing. Configure a policy under Regulatory Reporting → Settings if that is wrong.',
  },
  preparer_certification_missing: {
    title: 'The preparer has not certified yet',
    guidance:
      'An approver certifies the figures the preparer already committed to. Wait for the preparer certification, which is what freezes them.',
  },
  register_state_missing: {
    title: 'Master-data provenance is missing',
    guidance:
      'This return binds no engine run, so it needs a register-state digest. Regenerate the package so its master-data provenance is recorded, then certify.',
  },
  no_signer_identity: {
    title: 'No signer identity exists for this account',
    guidance:
      'Permanent signee IDs are derived from a deployment secret (SIGNER_ID_PEPPER) that is not configured, so no account on this platform can sign yet. An administrator must set it and provision identities.',
  },
  signing_not_configured: {
    title: 'This deployment cannot produce signatures',
    guidance:
      'Every return requires signatures, so nothing can be filed until an administrator configures the signing key. Alternatively, relax the signing policy for this return under Regulatory Reporting → Settings.',
  },
  signing_backend_unavailable: {
    title: 'No signing backend is configured',
    guidance:
      'The key backend (software, PKCS#11 or KMS) could not be reached, so no signature could be produced. Certification is refused rather than recorded without one.',
  },
  signed_pdf_signer_unavailable: {
    title: 'The signed PDF could not be produced',
    guidance:
      'This return requires the signature to be placed on the document, and the configured key backend cannot do that. An administrator must configure a backend that can, or relax require_signed_pdf for this return.',
  },
};

export function attestationErrorHelp(
  errorCode: string | null | undefined
): AttestationErrorHelp | null {
  if (!errorCode) return null;
  return ATTESTATION_ERROR_HELP[errorCode] ?? null;
}
