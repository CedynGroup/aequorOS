'use client';

/**
 * Attestation status for one package (docs/attestation_esignature.md §4.6):
 * the current state, who has signed, who must still sign under the policy in
 * force, the frozen figures digest, the void action, and the verification
 * affordance.
 *
 * The panel owns the certify affordances too, so the routing rule lives in one
 * place: the preparer sees "Certify and freeze" while the return is unsigned,
 * the approver sees "Approve and certify" once the figures are frozen, and
 * neither is ever shown to someone the policy cannot accept. Server-side guards
 * are the enforcement (§4.4); this is only about not offering a dead action.
 *
 * Pressing either opens the SIGNING WORKSPACE (./signing/SigningWorkspace): the
 * generated return itself, with the signature fields on it, the signer's adopted
 * mark, the named approver, and the certification — one act, because the DocMDP
 * policy the preparer's signature applies means a field cannot be added to the
 * document afterwards.
 *
 * For the CHECKER that workspace is also where the approval decision is made:
 * "Approve and sign" writes the signature and the decision in one transaction,
 * and the same screen sends the return back for corrections with a note. The
 * Approvals tab is the queue that routes them here — it offers no bare approve,
 * because approving figures read on another screen is not a review.
 *
 * A void does NOT leave the package in a `void` state: the service increments
 * `attestation_cycle`, resets the state to `unsigned`, and returns the package to
 * `generated` for rework, while `voided_at` / `void_reason` persist as history.
 * The signature list is scoped to the CURRENT cycle, so voided signatures are not
 * shown here — they live in the append-only trail and the audit view, never
 * deleted. That is why the void metadata below is labelled as history rather than
 * as the present state: an unsigned return that was previously voided must not
 * read as a voided one.
 */

import { useEffect, useRef, useState } from 'react';
import {
  BadgeCheck,
  FileSearch,
  Loader2,
  PenLine,
  Snowflake,
  Undo2,
  UserCheck,
} from 'lucide-react';
import type {
  AttestationStatusRead,
  PackageStatus,
  SignatureRead,
  SigningRole,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { isApiError } from '@/lib/api/client';
import { useModuleScope } from '@/components/shell/BankContext';
import { useUserProfile } from '@/components/profile/ProfileProvider';
import { filingAuthorityFor } from '@/lib/submissions/filingAuthority';
import {
  usePackageAttestation,
  useVerifyPackageAttestation,
  useVoidAttestation,
} from '@/lib/api/hooks';
import { fmtTimestamp } from '@/lib/api/values';
import CertifyDialog from './CertifyDialog';
import SigningWorkspace, { isPlaceableRole } from './signing/SigningWorkspace';
import VerificationPanel from './VerificationPanel';
import {
  ATTESTATION_STATE_BLURBS,
  AttestationStatePill,
  DigestChip,
  SIGNING_ROLE_ACTIONS,
  SignatureBlock,
  SignatureEvidence,
  SubmissionClearancePill,
  attestationErrorHelp,
  outstandingSummary,
  roleNoun,
} from './shared';

/** Package statuses from which a preparer certification is accepted (§4.1 T1). */
const CERTIFIABLE_STATUSES: PackageStatus[] = ['validated'];

export default function AttestationPanel({
  bankId,
  packageId,
  returnLabel,
  packageStatus,
  validationClean,
  returnFamily,
  certifyRole: controlledCertifyRole,
  onCertifyRoleChange,
  showInlineCertifyActions = true,
}: {
  bankId: string;
  packageId: string;
  /** e.g. "BSD3 · 31 Mar 2026 v2" — used in the ceremony heading. */
  returnLabel: string;
  packageStatus: PackageStatus;
  /** Every check passed with no findings to fix — the T1 precondition. */
  validationClean: boolean;
  /**
   * The package's return family. Required rather than optional because it
   * decides which authority the checker affordance is offered on, and a new
   * mount that did not answer would silently get the prudential answer.
   */
  returnFamily: string;
  /**
   * The open ceremony, when the surface around this panel owns it. The Returns
   * workspace lifts the certify act into its command bar so the officer meets
   * one act at the top of the screen instead of hunting for it in a card; the
   * ceremony itself still lives here, because the workspace it opens is this
   * panel's.
   */
  certifyRole?: SigningRole | null;
  onCertifyRoleChange?: (role: SigningRole | null) => void;
  /** False when the surface has lifted the certify buttons out of this card. */
  showInlineCertifyActions?: boolean;
}) {
  const { capitalApprove } = useModuleScope();
  const { effectiveAuthority, isLoading: authorityLoading } = useUserProfile();
  /**
   * Whether to OFFER the checker act — never whether to permit it. The server
   * decides that, and decides it differently per family
   * (`attestation_api._ensure_certify_authority`).
   *
   * For the prudential returns the scalar ladder is the right and unchanged
   * question: a bank's approvers approve its returns. An ICAAP report is a
   * scoped surface, so the server asks for an exact Capital/confidential
   * APPROVE binding for this institution and refuses a scalar approver who
   * holds none. Asking the scalar question here got both halves wrong: a board
   * member whose whole authority is over capital was never SHOWN the
   * approve-and-sign control, and a reporting approver with no capital
   * standing was shown one the server would refuse.
   *
   * The projection is the active institution's, which is the institution every
   * surface that mounts this panel is already looking at.
   */
  const filingAuthority = filingAuthorityFor(effectiveAuthority, bankId, {
    resolved: !authorityLoading,
  });
  const isApprover =
    returnFamily === 'icaap'
      ? capitalApprove === true
      : filingAuthority.mayApprove;

  const statusQuery = usePackageAttestation(bankId, packageId);
  const voidAttestation = useVoidAttestation(bankId);

  const [ownCertifyRole, setOwnCertifyRole] = useState<SigningRole | null>(null);
  const certifyRole =
    controlledCertifyRole !== undefined ? controlledCertifyRole : ownCertifyRole;
  const setCertifyRole = onCertifyRoleChange ?? setOwnCertifyRole;
  // The deep-link effect below runs once, on mount, and must not re-run when a
  // parent re-renders with a fresh callback identity — reopening a signing
  // ceremony because a prop changed is exactly the surprise a ceremony must not
  // spring on a signer.
  const setCertifyRoleRef = useRef(setCertifyRole);
  setCertifyRoleRef.current = setCertifyRole;
  const [ssoOutcome, setSsoOutcome] = useState<string | null>(null);
  const [voidOpen, setVoidOpen] = useState(false);
  const [voidReason, setVoidReason] = useState('');
  const [verifyRequested, setVerifyRequested] = useState(false);

  /**
   * Two things arrive by URL, and both are consumed on read.
   *
   * `?sign=<role>` is the signature queue's deep link — a colleague was asked to
   * sign this return and followed the request straight into the ceremony.
   * `?certify=<role>&stepUp=<outcome>` is the SSO round trip: step-up is a full
   * redirect to the bank's IdP, so the signer comes back to a fresh page and the
   * ceremony has to reopen where it left off (`sign` for the workspace,
   * `certify` for the dialog — the two are not interchangeable).
   *
   * Read from `location` in an effect rather than `useSearchParams` so the panel
   * stays renderable without a Suspense boundary, and the params are stripped
   * immediately: a reload must not silently reopen a signing ceremony, and an
   * outcome marker must not outlive its round trip.
   */
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const role = params.get('sign') ?? params.get('certify');
    const outcome = params.get('stepUp');
    if (!role && !outcome) return;
    if (role && role in SIGNING_ROLE_ACTIONS) {
      setCertifyRoleRef.current(role as SigningRole);
      setSsoOutcome(outcome);
    }
    params.delete('sign');
    params.delete('certify');
    params.delete('stepUp');
    const query = params.toString();
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}`
    );
  }, []);

  const verifyQuery = useVerifyPackageAttestation(bankId, packageId, verifyRequested);
  const status = statusQuery.data;

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2.5">
          Attestation
          {status && <AttestationStatePill state={status.attestationState} />}
          {status && <SubmissionClearancePill status={status} />}
        </span>
      }
      subtitle="Who signed, who must still sign, and the frozen figures they committed to"
      actions={
        <button
          type="button"
          onClick={() => {
            setVerifyRequested(true);
            if (verifyRequested) void verifyQuery.refetch();
          }}
          disabled={verifyQuery.isFetching}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60"
        >
          {verifyQuery.isFetching ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <FileSearch size={13} aria-hidden />
          )}
          Verify
        </button>
      }
    >
      {statusQuery.isLoading ? (
        <SkeletonCard />
      ) : statusQuery.error ? (
        <ErrorPanel
          error={statusQuery.error}
          onRetry={() => statusQuery.refetch()}
          title="Could not load the attestation status"
        />
      ) : status ? (
        <div className="space-y-5">
          <StateSummary status={status} />

          {status.policy.requireSignature ? (
            <>
              <Signatures signatures={status.signatures} />
              <Routing status={status} />
              <Actions
                status={status}
                packageStatus={packageStatus}
                validationClean={validationClean}
                isApprover={isApprover}
                returnFamily={returnFamily}
                showCertifyButtons={showInlineCertifyActions}
                onCertify={setCertifyRole}
                onVoid={() => setVoidOpen(true)}
              />
            </>
          ) : (
            <p className="rounded border border-border-light bg-surface px-3.5 py-2.5 text-caption text-navy/85 leading-relaxed">
              {/*
                Which of the three reasons applies is not cosmetic. Two of them
                are deployment switches that no policy can overrule, and sending
                an officer to Settings to fix one of those would send them to a
                screen that cannot.
              */}
              {status.policy.source === 'icaap_signing_disabled'
                ? 'The assessment is not signed in this installation. Nothing is certified here and submission is not gated on a signature: the assessment is prepared and approved in the usual way, and the Board resolution filed with it is the evidence of the Board’s approval. A signing policy cannot change this — ask your AequorOS administrator to switch ICAAP signing on.'
                : status.policy.source === 'esign_disabled'
                  ? 'Signing is switched off for this installation, so no return is certified here and submission is not gated on a signature. A signing policy cannot change this — ask your AequorOS administrator.'
                  : 'The signing policy in force for this return does not require a signature. Nothing is certified, and submission is not gated on attestation. Configure a policy under Regulatory Reporting → Settings if that is wrong.'}
            </p>
          )}

          {voidOpen && (
            <VoidForm
              reason={voidReason}
              onReasonChange={setVoidReason}
              pending={voidAttestation.isPending}
              error={voidAttestation.error}
              onCancel={() => {
                setVoidOpen(false);
                setVoidReason('');
                voidAttestation.reset();
              }}
              onConfirm={() =>
                voidAttestation.mutate(
                  { packageId, reason: voidReason.trim() },
                  {
                    onSuccess: () => {
                      setVoidOpen(false);
                      setVoidReason('');
                    },
                  }
                )
              }
            />
          )}

          {verifyRequested && (
            <div className="pt-4 border-t border-border-light">
              <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2">
                Verification — five independent checks
              </p>
              {verifyQuery.isLoading ? (
                <SkeletonCard />
              ) : verifyQuery.error ? (
                <ErrorPanel
                  error={verifyQuery.error}
                  onRetry={() => verifyQuery.refetch()}
                  title="Verification could not run"
                />
              ) : verifyQuery.data ? (
                <VerificationPanel report={verifyQuery.data} />
              ) : null}
            </div>
          )}
        </div>
      ) : null}

      {/*
        The workspace is the ceremony for every role the return artifact has a
        field for; `board` and `witness` have none, so there is nothing to show
        them on the page and they keep the dialog. Both surfaces render the same
        figures, the same statement and the same step-up form (./review).
      */}
      {certifyRole &&
        (isPlaceableRole(certifyRole) ? (
          <SigningWorkspace
            bankId={bankId}
            packageId={packageId}
            signingRole={certifyRole}
            returnLabel={returnLabel}
            ssoOutcome={ssoOutcome}
            onClose={() => {
              setCertifyRole(null);
              setSsoOutcome(null);
            }}
            onCertified={() => void statusQuery.refetch()}
            onRequestVoid={() => setVoidOpen(true)}
          />
        ) : (
          <CertifyDialog
            bankId={bankId}
            packageId={packageId}
            signingRole={certifyRole}
            returnLabel={returnLabel}
            ssoOutcome={ssoOutcome}
            onClose={() => {
              setCertifyRole(null);
              setSsoOutcome(null);
            }}
            onRequestVoid={() => setVoidOpen(true)}
          />
        ))}
    </SectionCard>
  );
}

/** The state, its consequence, and the frozen digest both signers commit to. */
function StateSummary({ status }: { status: AttestationStatusRead }) {
  return (
    <div className="space-y-2.5">
      <p className="text-caption text-navy/85 leading-relaxed">
        {ATTESTATION_STATE_BLURBS[status.attestationState]}
      </p>

      {status.certificationDigest && (
        <div className="flex items-start gap-2.5 rounded border border-action/20 bg-action-light/40 px-3.5 py-2.5">
          <Snowflake size={15} className="text-action shrink-0 mt-0.5" aria-hidden />
          <div className="min-w-0">
            <p className="text-body font-medium text-navy">Frozen figures digest</p>
            <p className="mt-1">
              <DigestChip
                digest={status.certificationDigest}
                label="frozen figures digest"
              />
            </p>
            <p className="mt-1 text-caption text-navy/80 leading-relaxed">
              Every signature in this cycle is over this one value. The server
              recomputes it at each later signature and refuses on any difference,
              so a change between signatures is provable forever after — by
              anyone, offline.
            </p>
          </div>
        </div>
      )}

      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-x-4 gap-y-2">
        {status.certifiedAt && (
          <Meta label="Preparer certified">{fmtTimestamp(new Date(status.certifiedAt))}</Meta>
        )}
        {status.fullyCertifiedAt && (
          <Meta label="Fully certified">
            {fmtTimestamp(new Date(status.fullyCertifiedAt))}
          </Meta>
        )}
        {status.voidedAt && (
          <Meta label="Last voided">{fmtTimestamp(new Date(status.voidedAt))}</Meta>
        )}
        <Meta label="Attestation cycle">
          <span className="tnum">#{status.attestationCycle}</span>
        </Meta>
      </dl>

      {status.voidReason && (
        <p className="rounded border border-warning/25 bg-warning-light/40 px-3.5 py-2.5 text-caption text-navy/85 leading-relaxed">
          <span className="font-medium text-navy">Last void reason: </span>
          {status.voidReason}
          {status.attestationState === 'unsigned' && (
            <span className="block mt-1 text-slate">
              A previous attestation cycle was withdrawn. Its signatures are
              retained in the append-only trail — the list below shows cycle #
              {status.attestationCycle} only.
            </span>
          )}
        </p>
      )}
    </div>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-caption text-navy/85 tnum">{children}</dd>
    </div>
  );
}

function Signatures({ signatures }: { signatures: SignatureRead[] }) {
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2">
        Signatures on record ({signatures.length})
      </p>
      {signatures.length === 0 ? (
        <p className="text-caption text-slate">
          None yet in this attestation cycle.
        </p>
      ) : (
        <ul className="space-y-3">
          {signatures.map((signature) => (
            <li key={signature.id}>
              <SignatureBlock signature={signature} />
              <details className="mt-1.5">
                <summary className="cursor-pointer text-caption font-medium text-action hover:text-action-hover">
                  Cryptographic detail
                </summary>
                <SignatureEvidence signature={signature} />
              </details>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Who must still sign, per the policy resolved as at the reporting date. */
function Routing({ status }: { status: AttestationStatusRead }) {
  const { policy, outstanding } = status;
  const outstandingByRole = new Map(outstanding.map((slot) => [slot.role, slot.count]));

  return (
    <div>
      <div className="flex items-center gap-2 flex-wrap">
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Required signatures
        </p>
        <StatusPill tone={policy.source === 'configured' ? 'action' : 'slate'}>
          {policy.source === 'configured'
            ? 'Configured policy'
            : policy.source === 'esign_disabled'
              ? 'Signing disabled (deployment)'
              : 'Platform default'}
        </StatusPill>
        {policy.requireSignedPdf && <StatusPill tone="amber">Signed PDF required</StatusPill>}
        {!policy.distinctSigners && (
          <StatusPill tone="amber">Distinct signers not enforced</StatusPill>
        )}
      </div>

      <ul className="mt-2 space-y-1.5">
        {policy.requiredSignatures.map((slot) => {
          const still = outstandingByRole.get(slot.role) ?? 0;
          const satisfied = still === 0;
          return (
            <li
              key={slot.role}
              className="flex items-center gap-2 flex-wrap text-caption"
            >
              {satisfied ? (
                <BadgeCheck size={13} className="text-success shrink-0" aria-hidden />
              ) : (
                <UserCheck size={13} className="text-warning shrink-0" aria-hidden />
              )}
              <span className="font-medium text-navy">{roleNoun(slot.role)}</span>
              <span className="text-slate tnum">×{slot.minCount}</span>
              {slot.officerTitles.length > 0 ? (
                <span className="text-navy/80">
                  — {slot.officerTitles.join(' or ')}
                </span>
              ) : (
                <span className="text-slate">— any officer holding the role</span>
              )}
              <span className="ml-auto">
                {satisfied ? (
                  <StatusPill tone="success">Signed</StatusPill>
                ) : (
                  <StatusPill tone="amber">{still} outstanding</StatusPill>
                )}
              </span>
            </li>
          );
        })}
      </ul>

      <p className="mt-2 text-caption text-slate leading-relaxed">
        Outstanding: {outstandingSummary(outstanding)}. The submission gate reads
        the policy in force at the reporting date, so a later policy change never
        retroactively invalidates a filed return.
      </p>

      {policy.requiredAttachments.length > 0 && (
        <p className="mt-1.5 text-caption text-navy/85">
          Required attachments:{' '}
          <span className="text-navy">{policy.requiredAttachments.join(', ')}</span>
        </p>
      )}
    </div>
  );
}

/** The certify / void affordances, with the reason each is unavailable. */
function Actions({
  status,
  packageStatus,
  validationClean,
  isApprover,
  returnFamily,
  showCertifyButtons,
  onCertify,
  onVoid,
}: {
  status: AttestationStatusRead;
  packageStatus: PackageStatus;
  validationClean: boolean;
  isApprover: boolean;
  returnFamily: string;
  /**
   * False when the surface has lifted the certify act to its own command bar.
   * The sentence below stays either way — it is what tells an officer why the
   * act is not theirs, or not yet.
   */
  showCertifyButtons: boolean;
  onCertify: (role: SigningRole) => void;
  onVoid: () => void;
}) {
  const state = status.attestationState;
  const outstandingRoles = status.outstanding.map((slot) => slot.role);
  // The preparer slot comes first in every policy; anything else is a checker act.
  const preparerOutstanding = outstandingRoles.includes('preparer');
  const checkerRole = outstandingRoles.find((role) => role !== 'preparer');

  const canCertifyAsPreparer =
    (state === 'unsigned' || state === 'void') &&
    preparerOutstanding &&
    CERTIFIABLE_STATUSES.includes(packageStatus) &&
    validationClean;
  const canCertifyAsChecker =
    state === 'preparer_certified' && checkerRole != null && isApprover;
  const canVoid =
    (state === 'preparer_certified' || state === 'fully_certified') && isApprover;

  return (
    <div className="pt-4 border-t border-border-light space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        {showCertifyButtons &&
          (state === 'unsigned' || state === 'void') &&
          preparerOutstanding && (
          <button
            type="button"
            disabled={!canCertifyAsPreparer}
            onClick={() => onCertify('preparer')}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            <PenLine size={13} aria-hidden />
            {SIGNING_ROLE_ACTIONS.preparer}
          </button>
        )}
        {/* ABSENT, not disabled, for an officer who does not hold the checker
            authority at all: a greyed-out "Approve and sign" invites someone to
            infer that the act is theirs and the moment is wrong, when neither
            is true (docs/filing_workflow_redesign.md §4b.1). The sentence below
            names the authority the server asks for. */}
        {showCertifyButtons &&
          isApprover &&
          state === 'preparer_certified' &&
          checkerRole && (
          <button
            type="button"
            disabled={!canCertifyAsChecker}
            onClick={() => onCertify(checkerRole)}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            <BadgeCheck size={13} aria-hidden />
            {SIGNING_ROLE_ACTIONS[checkerRole] ?? 'Approve and certify'}
          </button>
        )}
        {canVoid && (
          <button
            type="button"
            onClick={onVoid}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light"
          >
            <Undo2 size={13} aria-hidden />
            Void attestation
          </button>
        )}
      </div>

      <p className="text-caption text-slate leading-relaxed">
        {state === 'fully_certified'
          ? 'Fully certified — nothing further is required before submission.'
          : state === 'preparer_certified' && !isApprover
            ? returnFamily === 'icaap'
              ? 'Awaiting a checker signature. Approving this assessment requires capital approval authority for this institution — maker-checker cannot be satisfied by a preparer.'
              : 'Awaiting a checker signature. Approving requires the approver role — maker-checker cannot be satisfied by a preparer.'
            : state === 'preparer_certified'
              ? 'You are certifying the identical frozen figures shown above; the server refuses the signature on any difference.'
              : !CERTIFIABLE_STATUSES.includes(packageStatus)
                ? 'The checks have not run cleanly against this version yet, so there is nothing here to certify.'
                : !validationClean
                  ? 'Some checks are still failing — figures with failing checks cannot be attested to.'
                  : 'Certifying freezes the figures: regeneration is refused for this return and reporting date until the attestation is completed or voided.'}
      </p>
    </div>
  );
}

/** Voiding is an explicit, audited, reason-required act — never a silent undo. */
function VoidForm({
  reason,
  onReasonChange,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  reason: string;
  onReasonChange: (value: string) => void;
  pending: boolean;
  error: unknown;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const help = attestationErrorHelp(isApiError(error) ? error.errorCode : null);
  return (
    <div className="rounded border border-critical/30 bg-critical-light/30 px-3.5 py-3 space-y-2">
      <p className="text-body font-medium text-navy">Void this attestation</p>
      <p className="text-caption text-navy/85 leading-relaxed">
        Every signature is retained as history and marked superseded with this
        reason — nothing is deleted. The package returns to &apos;generated&apos; so
        the corrected figures can be re-certified from the preparer onwards.
        Correcting a certified return is always an audited void, never a silent
        supersession.
      </p>
      <label className="block">
        <span className="block text-caption font-medium text-navy mb-1.5">
          Reason <span className="font-normal text-slate">(required)</span>
        </span>
        <textarea
          value={reason}
          onChange={(event) => onReasonChange(event.target.value)}
          rows={2}
          placeholder="e.g. HQLA misclassification found after certification; re-certifying corrected figures."
          className="w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={pending || reason.trim().length === 0}
          onClick={onConfirm}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light disabled:opacity-60"
        >
          {pending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <Undo2 size={13} aria-hidden />
          )}
          Void attestation
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={pending}
          className="inline-flex items-center px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-60"
        >
          Cancel
        </button>
      </div>
      {Boolean(error) && (
        <div role="alert" className="text-caption text-navy/85 leading-relaxed">
          <p className="font-medium text-critical">
            {help?.title ?? 'Void was refused'}
          </p>
          <p className="mt-0.5">
            {error instanceof Error ? error.message : String(error)}
          </p>
          {help && <p className="mt-1">{help.guidance}</p>}
        </div>
      )}
    </div>
  );
}
