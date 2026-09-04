'use client';

/**
 * The signing workspace: the document, the fields, the mark, the recipient and
 * the commitment, on one surface.
 *
 * Placement has to happen HERE, before the signature, and not as a separate
 * administrative screen: the preparer's certification applies a DocMDP policy of
 * `FILL_FORMS`, so a signature field added after it would itself be tampering.
 * Every box therefore exists by the time the first signature lands or it never
 * exists at all — which is why "place, then sign" is one act
 * (`attestation_api.certify_and_send`) rather than two calls this component
 * could get half-way through, and why the preparer places the APPROVER's fields
 * too.
 *
 * A regulator's attestation block asks each officer for four things — BSD3 reads
 * "Prepared by (name / designation / signature / date)" — so the palette hands
 * out a field per line and the signer drops each one where the form printed it.
 * Nothing in the palette is an input: every value except the mark is derived
 * server-side from the signature record when it is signed, and the previews here
 * are computed the same way so what is seen is what will be stamped.
 *
 * The workspace opens EMPTY unless somebody has saved a layout for this return.
 * The platform's built-in default puts two boxes in a clear band near the
 * attestation block rather than on its lines, which is a guess — and a wrong
 * default that has to be dragged off the wording costs more than a palette
 * inviting the first placement.
 *
 * What-you-see-is-what-you-sign is carried by the rail, not by the page: the
 * signature covers the certification digest and the statement, which are
 * rendered in full and unabbreviated beside the document (`../review`). The
 * document is what those figures look like printed; the digest is what is
 * cryptographically bound.
 *
 * Two roles sign here — the ones `pdf_signing.ROLE_FIELD_NAMES` gives a field
 * to. `board` and `witness` have no field on a return, so they keep the dialog.
 *
 * For a CHECKER this is the whole of their act, and it has two exits. Approving
 * and signing are one press (`attestation_api.certify_and_send` writes the
 * signature and the approval decision in one transaction), and sending the
 * return back for corrections is the other press, with the note required. There
 * is deliberately no third surface offering a bare approve: reviewing the figures
 * and deciding on them have to happen on the same screen, or the reviewer is
 * committing to something they read somewhere else.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookmarkPlus,
  ChevronLeft,
  ChevronRight,
  FileWarning,
  Loader2,
  Minus,
  Plus,
  Undo2,
  X,
} from 'lucide-react';
import type {
  PlacementFieldType,
  ResolvedSignaturePlacementsRead,
  SignatureFieldPlacement,
  SignatureRead,
  SigningRole,
} from '@aequoros/risk-service-api';
import StatusPill from '@/components/ui/StatusPill';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useGrantAdministrationAccess } from '@/lib/api/grantAdministration';
import {
  useCertifyAndSend,
  useCertifyAndSendWithHeldAuthorization,
  useCertificationPreview,
  useExportRegulatoryPackage,
  useMyAdoptedSignature,
  useMySignerIdentity,
  useOrganizationUsers,
  usePackageArtifactVersions,
  usePackageArtifacts,
  usePackageAttestation,
  usePackageSignaturePlacements,
  useSendBackForCorrections,
  useSetPackageSignaturePlacements,
  useStepUpForSigning,
  useUpsertSignaturePlacementTemplate,
} from '@/lib/api/hooks';
import {
  fetchArtifactBytes,
  fetchArtifactVersionBytes,
} from '@/components/submissions/shared';
import {
  fieldTypeOf,
  limitsByFieldType,
  newPlacement,
  placementViolation,
  signatureRolesPlaced,
  type PageSpace,
  type ViewerRect,
} from '@/lib/attestation/geometry';
import { DEFAULT_FIELD_SIZES, FIELD_TYPE_LABELS } from '@/lib/attestation/fields';
import { recipientStashKey, startSsoStepUp } from '@/lib/attestation/ssoStepUp';
import { SIGNING_ROLE_ACTIONS, SignatureBlock, roleNoun } from '../shared';
import {
  CertificationFailure,
  FiguresSection,
  PreviewError,
  SsoOutcomeNotice,
  StatementSection,
  StepUpSection,
  isTerminalFailure,
} from '../review';
import DocumentCanvas from './DocumentCanvas';
import PlacementLayer, { type FieldSlot } from './PlacementLayer';
import FieldPalette, { FIELD_DRAG_TYPE, type PaletteRecipient } from './FieldPalette';
import AdoptSignaturePanel from './AdoptSignaturePanel';
import RecipientPicker, { type Nomination } from './RecipientPicker';

/** The roles the return artifact actually has a field for. */
const PLACEABLE_ROLES = ['preparer', 'approver'] as const;

export function isPlaceableRole(role: SigningRole): boolean {
  return (PLACEABLE_ROLES as readonly string[]).includes(role);
}

const ZOOM_STEPS = [0.75, 1, 1.25, 1.5, 2];

/** Rail tints, one per recipient, matching the box drawn on the page. */
const RECIPIENT_TONES: Record<string, string> = {
  preparer: 'border-action bg-action/10',
  approver: 'border-slate bg-slate/10',
};

export default function SigningWorkspace({
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
  /** e.g. "BSD3 · 31 Mar 2026 v2". */
  returnLabel: string;
  onClose: () => void;
  onCertified?: () => void;
  onRequestVoid?: () => void;
  /** The `stepUp` marker the SSO callback redirected back with, if any. */
  ssoOutcome?: string | null;
}) {
  const previewQuery = useCertificationPreview(bankId, packageId, signingRole);
  const placementsQuery = usePackageSignaturePlacements(bankId, packageId);
  const appearanceQuery = useMyAdoptedSignature();
  const identityQuery = useMySignerIdentity();
  const usersQuery = useOrganizationUsers();
  const statusQuery = usePackageAttestation(bankId, packageId);

  const stepUp = useStepUpForSigning(bankId);
  const certifyAndSend = useCertifyAndSend(bankId);
  const certifyWithHeld = useCertifyAndSendWithHeldAuthorization(bankId);
  const savePlacements = useSetPackageSignaturePlacements(bankId);
  const saveTemplate = useUpsertSignaturePlacementTemplate();
  const sendBack = useSendBackForCorrections(bankId);
  const canAdministerGrants = useGrantAdministrationAccess();

  const [placements, setPlacements] = useState<SignatureFieldPlacement[] | null>(null);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [activeRole, setActiveRole] = useState<string>(signingRole);
  const [armed, setArmed] = useState<PlacementFieldType | null>(null);
  const [templateSaved, setTemplateSaved] = useState(false);
  const [password, setPassword] = useState('');
  const [reason, setReason] = useState('');
  const [correctionsNote, setCorrectionsNote] = useState('');
  const [pageIndex, setPageIndex] = useState(0);
  const [pageCount, setPageCount] = useState(1);
  const [scale, setScale] = useState(1);
  const [space, setSpace] = useState<PageSpace | null>(null);
  const [pageSizes, setPageSizes] = useState<Record<number, PageSpace>>({});
  /** The rendered page's own box, so a drop can be resolved to page pixels. */
  const pageRef = useRef<HTMLDivElement>(null);

  const resolved = placementsQuery.data;
  const preview = previewQuery.data;
  const status = statusQuery.data;
  // The floors the service enforces, per kind, echoed so a box is refused under
  // the signer's hand rather than at certification time. Never duplicated here:
  // they are derived backend-side and a local copy would drift from the number
  // that actually refuses the placement.
  const limits = useMemo(
    () => limitsByFieldType(resolved?.fieldTypes ?? []),
    [resolved]
  );
  const editable = resolved?.editable ?? false;

  // Seed ONLY from a layout somebody saved. The platform default is two boxes in
  // a clear band near the attestation block, not on its ruled lines — seeding
  // from it opens the workspace with fields in the wrong place that the signer
  // then has to drag off the wording, which is worse than starting empty with
  // the palette inviting the first placement.
  //
  // Once the fields are no longer editable that reasoning inverts: whatever
  // resolves IS the layout on the filed document, default included, and hiding
  // it from a later signer would show them a page that does not match the one
  // they are signing.
  useEffect(() => {
    if (!resolved || placements) return;
    const seeded =
      resolved.source === 'default' && resolved.editable ? [] : resolved.placements;
    setPlacements(seeded);
    const mine = seeded.find((p) => p.signingRole === signingRole);
    setPageIndex(mine?.pageIndex ?? seeded[0]?.pageIndex ?? 0);
  }, [resolved, placements, signingRole]);

  useEffect(() => {
    if (reason) return;
    setReason(
      signingRole === 'preparer'
        ? 'Signature fields placed; certified as preparer and sent for approval.'
        : `Certified as ${roleNoun(signingRole).toLowerCase()} on the frozen figures.`
    );
  }, [reason, signingRole]);

  // A nomination parked before the IdP round trip. Nothing downstream trusts it
  // — the server re-validates every nominee — so a miss is an annoyance, not a
  // correctness problem.
  useEffect(() => {
    if (ssoOutcome !== 'ready') return;
    try {
      const stashed = window.sessionStorage.getItem(
        recipientStashKey(packageId, signingRole)
      );
      if (stashed) setNominations(JSON.parse(stashed) as Nomination[]);
    } catch {
      // A malformed or unavailable stash simply means picking again.
    }
  }, [ssoOutcome, packageId, signingRole]);

  const onPageSpace = useCallback((next: PageSpace) => {
    setSpace(next);
    setPageSizes((sizes) => ({ ...sizes, [next.pageIndex]: next }));
  }, []);

  const bytes = usePackageDocument(bankId, packageId);

  const violations = useMemo(
    () =>
      (placements ?? []).map((placement) => {
        // A page nobody has rendered yet has no known media box; the size check
        // is page-independent, so it still runs, and bounds are checked once
        // the page has been seen.
        const known = pageSizes[placement.pageIndex];
        return known ? placementViolation(placement, known, limits) : null;
      }),
    [placements, pageSizes, limits]
  );

  const nominationSlots = useMemo<SigningRole[]>(() => {
    if (!preview) return [];
    const slots: SigningRole[] = [];
    for (const slot of preview.outstanding) {
      // The count the server will compute is the one AFTER this signature lands.
      const remaining = slot.role === signingRole ? slot.count - 1 : slot.count;
      for (let index = 0; index < remaining; index += 1) slots.push(slot.role);
    }
    return slots;
  }, [preview, signingRole]);

  /**
   * Who each role's boxes belong to, and the identity they will print.
   *
   * Resolved most-certain-first: a signature that already exists, then a named
   * recipient, then — for the caller's own role — themselves. An approver
   * nobody has nominated yet has no name, and the boxes say so rather than
   * inventing one.
   */
  const recipients: PaletteRecipient[] = useMemo(() => {
    const signed = new Map(
      (status?.signatures ?? []).map((signature) => [signature.signingRole, signature])
    );
    const routed = new Map(
      (status?.recipients ?? []).map((recipient) => [recipient.signingRole, recipient])
    );
    const nominated = new Map(
      nominations.map((nomination) => [nomination.signingRole as string, nomination.userId])
    );
    const users = usersQuery.data?.users ?? [];
    return PLACEABLE_ROLES.map((role) => {
      const signature = signed.get(role);
      const recipient = routed.get(role);
      const picked = users.find((user) => user.id === nominated.get(role));
      const mine = role === signingRole && signature == null;
      const signer = signature
        ? { displayName: signature.signerDisplayName, jobTitle: signature.officerTitle }
        : recipient
          ? {
              displayName: recipient.recipientDisplayName,
              jobTitle: recipient.recipientJobTitle,
            }
          : picked
            ? { displayName: picked.displayName ?? picked.email, jobTitle: picked.jobTitle }
            : mine
              ? {
                  displayName: identityQuery.data?.displayName ?? null,
                  jobTitle: identityQuery.data?.jobTitle ?? null,
                }
              : null;
      return {
        signingRole: role,
        label: signer?.displayName
          ? `${roleNoun(role)} — ${signer.displayName}`
          : `${roleNoun(role)} — to be named`,
        signer,
        tone: RECIPIENT_TONES[role] ?? 'border-slate bg-slate/10',
      };
    });
  }, [status, nominations, usersQuery.data, signingRole, identityQuery.data]);

  const slots: FieldSlot[] = useMemo(() => {
    const signedRoles = new Set(
      (status?.signatures ?? []).map((signature) => signature.signingRole as string)
    );
    const byRole = new Map(recipients.map((entry) => [entry.signingRole, entry]));
    return (placements ?? []).map((placement, index) => {
      const owner = byRole.get(placement.signingRole);
      return {
        index,
        placement,
        ownerLabel: owner?.label ?? roleNoun(placement.signingRole as SigningRole),
        mine: placement.signingRole === signingRole && !signedRoles.has(signingRole),
        // The document on screen is the signed revision, so this role's mark and
        // values are already printed on the page beneath the box. Previewing them
        // again would draw a second copy over the real one.
        signed: signedRoles.has(placement.signingRole),
        signer: owner?.signer ?? null,
        violation: violations[index] ?? null,
      };
    });
  }, [placements, recipients, violations, signingRole, status]);

  const moveField = (index: number, rect: ViewerRect) => {
    if (!space || !editable) return;
    const box = space.toPdf(rect);
    setPlacements((current) =>
      (current ?? []).map((placement, at) =>
        at === index ? { ...placement, pageIndex: space.pageIndex, ...box } : placement
      )
    );
  };

  const removeField = (index: number) => {
    if (!editable) return;
    setPlacements((current) => (current ?? []).filter((_, at) => at !== index));
  };

  /** Drop a new box of the armed (or dragged) kind where the pointer landed. */
  const placeField = (fieldType: PlacementFieldType, clientX: number, clientY: number) => {
    if (!space || !editable) return;
    const surface = pageRef.current?.getBoundingClientRect();
    if (!surface) return;
    setPlacements((current) => [
      ...(current ?? []),
      newPlacement(
        fieldType,
        activeRole as SignatureFieldPlacement['signingRole'],
        { x: clientX - surface.left, y: clientY - surface.top },
        DEFAULT_FIELD_SIZES[fieldType],
        space
      ),
    ]);
    setArmed(null);
  };

  const failure = certifyWithHeld.error ?? certifyAndSend.error ?? stepUp.error ?? null;
  const terminal = isTerminalFailure(failure);
  const authorizationHeld = ssoOutcome === 'ready';
  const pending =
    stepUp.isPending ||
    certifyAndSend.isPending ||
    certifyWithHeld.isPending ||
    savePlacements.isPending;

  const offending = slots.find((slot) => slot.violation);
  const missingNominee = nominationSlots.length !== nominations.length;
  // Both signature fields, or none: the workspace starts empty on purpose, and
  // certifying with nothing placed would silently fall back to the platform
  // default layout — the guess this screen exists to replace.
  const unplaced = editable
    ? PLACEABLE_ROLES.filter((role) => !signatureRolesPlaced(placements ?? []).has(role))
    : [];
  const blockedReason = offending
    ? `The ${offending.ownerLabel.toLowerCase()} ${FIELD_TYPE_LABELS[
        fieldTypeOf(offending.placement)
      ].toLowerCase()} field cannot be filed: ${offending.violation}`
    : unplaced.length > 0
      ? `Place a signature field for ${unplaced.map(roleNoun).join(' and ')}. ` +
        'Every field has to exist before the first signature — the certification ' +
        'permits only form filling afterwards, so one left unplaced can never be added.'
      : missingNominee
        ? 'Name who signs next before certifying. The signature and the routing land in ' +
          'one transaction, so a return cannot be certified into nobody’s queue.'
        : reason.trim().length === 0
          ? 'A reason is recorded against the placement and the routing. Say why in a line.'
          : null;

  /** The placement to send: only when it is still ours to set. */
  const placementPayload = editable ? (placements ?? []) : [];

  /** Both exits end the same way: nothing held locally, the caller re-reads. */
  const finish = () => {
    setPassword('');
    window.sessionStorage.removeItem(recipientStashKey(packageId, signingRole));
    onCertified?.();
    onClose();
  };

  const onPasswordSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!preview) return;
    void (async () => {
      const granted = await stepUp.mutateAsync({ packageId, signingRole, password });
      await certifyAndSend.mutateAsync({
        packageId,
        signingRole,
        authorizationToken: granted.authorizationToken,
        // The digest this ceremony was built around — not the one the server
        // just recomputed, so a change between preview and submit is caught.
        expectedCertificationDigest: preview.certificationDigest,
        recipients: nominations,
        placements: placementPayload,
        reason: reason.trim(),
      });
      finish();
    })().catch(() => undefined);
  };

  const onHeldAuthorizationSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!preview) return;
    void certifyWithHeld
      .mutateAsync({
        packageId,
        signingRole,
        expectedCertificationDigest: preview.certificationDigest,
        recipients: nominations,
        placements: placementPayload,
        reason: reason.trim(),
      })
      .then(finish)
      .catch(() => undefined);
  };

  /**
   * Leaving for the IdP navigates this page away, so the boxes are written to
   * the server FIRST. They are audited data the signature depends on; losing
   * them to a redirect and silently falling back to the template would mean
   * signing a layout nobody chose.
   */
  const onSsoClick = () => {
    void (async () => {
      try {
        window.sessionStorage.setItem(
          recipientStashKey(packageId, signingRole),
          JSON.stringify(nominations)
        );
      } catch {
        // Private-mode storage refusal: the nominee is simply picked again.
      }
      if (editable && placements) {
        await savePlacements.mutateAsync({
          packageId,
          placements,
          reason: reason.trim() || 'Placement saved before single sign-on re-authentication.',
        });
      }
      startSsoStepUp({ bankId, packageId, signingRole, resume: 'sign' });
    })().catch(() => undefined);
  };

  /**
   * Save this layout as the return's template, so month two opens with the
   * boxes already on the form's lines. This is the whole reason the placement
   * work is worth doing once: a monthly return re-placed by hand every cycle is
   * a chore, and a chore is where a field ends up in the wrong place.
   */
  const onSaveTemplate = () => {
    if (!resolved || !placements?.length) return;
    setTemplateSaved(false);
    void saveTemplate
      .mutateAsync({
        bankId,
        returnCode: resolved.returnCode,
        placements,
        reason: `Signature field layout saved from the ${resolved.returnCode} signing workspace.`,
      })
      .then(() => setTemplateSaved(true))
      .catch(() => undefined);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Escape disarms the palette first: closing the whole ceremony because
      // somebody cancelled a field drop would lose every box they had placed.
      if (armed) {
        setArmed(null);
        return;
      }
      onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, armed]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Sign ${returnLabel}`}
      data-testid="signing-workspace"
      className="fixed inset-0 z-50 flex flex-col bg-surface-alt"
    >
      <header className="flex items-start justify-between gap-4 px-5 py-3 border-b border-border bg-surface-raised">
        <div className="min-w-0">
          <h2 className="text-h3 text-navy">
            {SIGNING_ROLE_ACTIONS[signingRole] ?? 'Certify'} — {returnLabel}
          </h2>
          <p className="mt-0.5 text-caption text-slate">
            Signing as {roleNoun(signingRole).toLowerCase()} ·{' '}
            {editable
              ? 'position the fields, adopt your mark, name who signs next, then certify'
              : 'the fields are part of the certified revision and can no longer be moved'}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          {resolved && <PlacementSourcePill source={resolved} />}
          <button
            type="button"
            aria-label="Close the signing workspace"
            onClick={onClose}
            className="w-9 h-9 rounded text-slate hover:bg-surface inline-flex items-center justify-center"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
      </header>

      <div className="flex-1 min-h-0 flex flex-col lg:flex-row">
        {editable && (
          <aside
            aria-label="Field palette"
            className="w-full lg:w-64 shrink-0 overflow-y-auto p-4 border-b lg:border-b-0 lg:border-r border-border bg-surface-raised"
          >
            <FieldPalette
              recipients={recipients}
              activeRole={activeRole}
              onActiveRole={setActiveRole}
              armed={armed}
              onArm={setArmed}
              limits={limits}
              disabled={!editable}
            />
            <TemplateSaver
              returnCode={resolved?.returnCode ?? ''}
              canAdministerGrants={canAdministerGrants}
              canSave={(placements?.length ?? 0) > 0 && !offending}
              saved={templateSaved}
              pending={saveTemplate.isPending}
              error={saveTemplate.error}
              onSave={onSaveTemplate}
            />
          </aside>
        )}

        <section
          aria-label="Return document"
          className="flex-1 min-w-0 flex flex-col border-b lg:border-b-0 lg:border-r border-border"
        >
          <Toolbar
            pageIndex={pageIndex}
            pageCount={pageCount}
            scale={scale}
            onPage={setPageIndex}
            onScale={setScale}
          />
          <div className="flex-1 min-h-0 overflow-auto p-6 flex justify-center">
            {bytes.error ? (
              <div className="max-w-xl">
                <ErrorPanel error={bytes.error} title="The return document is not available" />
              </div>
            ) : bytes.data ? (
              <DocumentCanvas
                bytes={bytes.data}
                pageIndex={pageIndex}
                scale={scale}
                onDocumentLoaded={setPageCount}
                onPageSpace={onPageSpace}
              >
                {space && (
                  // The drop surface, over the rendered page. `onClick` is the
                  // keyboard-reachable half of the same gesture: the palette
                  // arms a kind, this places it.
                  <div
                    ref={pageRef}
                    data-testid="placement-surface"
                    className={`absolute inset-0 ${armed ? 'cursor-copy' : ''}`}
                    onDragOver={(event) => {
                      if (!armed && !event.dataTransfer.types.includes(FIELD_DRAG_TYPE)) return;
                      event.preventDefault();
                      event.dataTransfer.dropEffect = 'copy';
                    }}
                    onDrop={(event) => {
                      const dropped = event.dataTransfer.getData(FIELD_DRAG_TYPE);
                      if (!dropped) return;
                      event.preventDefault();
                      placeField(dropped as PlacementFieldType, event.clientX, event.clientY);
                    }}
                    onClick={(event) => {
                      if (!armed) return;
                      placeField(armed, event.clientX, event.clientY);
                    }}
                  >
                    <PlacementLayer
                      space={space}
                      slots={slots}
                      editable={editable}
                      mark={appearanceQuery.data}
                      onMove={moveField}
                      onRemove={removeField}
                    />
                  </div>
                )}
              </DocumentCanvas>
            ) : (
              <DocumentPending exporting={bytes.exporting} />
            )}
          </div>
        </section>

        <aside
          aria-label="What you are signing"
          className="w-full lg:w-[440px] shrink-0 overflow-y-auto p-5 space-y-5 bg-surface-raised"
        >
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

          {status && signingRole !== 'preparer' && (
            <PriorSignatures signatures={status.signatures} />
          )}

          <div className="pt-4 border-t border-border-light">
            <AdoptSignaturePanel
              adopted={appearanceQuery.data}
              signerName={identityQuery.data?.displayName ?? ''}
            />
          </div>

          <div className="pt-4 border-t border-border-light space-y-2.5">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Send to
            </p>
            <RecipientPicker
              slots={nominationSlots}
              users={usersQuery.data?.users ?? []}
              nominations={nominations}
              onChange={setNominations}
            />
          </div>

          <label className="block">
            <span className="block text-caption font-medium text-navy mb-1.5">
              Reason <span className="font-normal text-slate">(recorded, required)</span>
            </span>
            <textarea
              value={reason}
              rows={2}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
              className="w-full rounded border border-border bg-surface px-2.5 py-2 text-body text-navy"
            />
          </label>

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
              onSsoClick={onSsoClick}
              pending={pending}
              authorizationHeld={authorizationHeld}
              actionLabel={
                nominationSlots.length > 0
                  ? 'Certify and send'
                  : (SIGNING_ROLE_ACTIONS[signingRole] ?? 'Certify')
              }
              blockedReason={blockedReason}
              stage={
                certifyAndSend.isPending || certifyWithHeld.isPending
                  ? 'signing'
                  : stepUp.isPending
                    ? 'proving'
                    : 'idle'
              }
              onCancel={onClose}
            />
          )}

          {/* The reviewer's other exit. Offered to checkers only: a preparer who
              disagrees with their own figures regenerates them. */}
          {signingRole !== 'preparer' && (
            <SendBackPanel
              note={correctionsNote}
              onNoteChange={setCorrectionsNote}
              pending={sendBack.isPending}
              error={sendBack.error}
              onSend={() => {
                void sendBack
                  .mutateAsync({ packageId, reason: correctionsNote.trim() })
                  .then(finish)
                  .catch(() => undefined);
              }}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

/**
 * Send it back, with the note that says what to fix.
 *
 * The note is required because it IS the instruction: a return handed back with
 * no reason leaves the preparer guessing at what a reviewer saw. The panel states
 * the consequence plainly — the preparer's certification is withdrawn and the
 * figures unfreeze — because that is what makes the return correctable, and a
 * reviewer should not discover it afterwards.
 */
function SendBackPanel({
  note,
  onNoteChange,
  pending,
  error,
  onSend,
}: {
  note: string;
  onNoteChange: (value: string) => void;
  pending: boolean;
  error: unknown;
  onSend: () => void;
}) {
  const empty = note.trim().length === 0;
  return (
    <div
      data-testid="send-back-panel"
      className="pt-4 border-t border-border-light space-y-2.5"
    >
      <div className="flex items-center gap-2">
        <Undo2 size={13} className="text-slate" aria-hidden />
        <p className="text-body font-medium text-navy">Or send it back for corrections</p>
      </div>
      <p className="text-caption text-slate leading-relaxed">
        Nothing is signed. The return goes back to the preparer with your note,
        and the preparer&apos;s certification is withdrawn so the figures can be
        corrected — their signature is kept as history, never deleted.
      </p>
      <label className="block">
        <span className="block text-caption font-medium text-navy mb-1.5">
          What needs correcting <span className="font-normal text-slate">(required)</span>
        </span>
        <textarea
          value={note}
          rows={2}
          maxLength={2000}
          onChange={(event) => onNoteChange(event.target.value)}
          placeholder="e.g. Line 12 double-counts the placement maturing 2 April."
          className="w-full rounded border border-border bg-surface px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
        />
      </label>
      <button
        type="button"
        disabled={pending || empty}
        title={empty ? 'A note is required — it is the instruction to the preparer.' : undefined}
        onClick={onSend}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light disabled:opacity-60"
      >
        {pending ? (
          <Loader2 size={13} className="animate-spin" aria-hidden />
        ) : (
          <Undo2 size={13} aria-hidden />
        )}
        Send back for corrections
      </button>
      {error != null && (
        <p role="alert" className="text-caption text-critical leading-relaxed">
          {error instanceof Error ? error.message : 'The return could not be sent back.'}
        </p>
      )}
    </div>
  );
}

/**
 * The bytes on screen — the document as the LAST signer left it.
 *
 * An approver has to see the preparer's signature. Until the version chain was
 * readable this component could only fetch the canonical export, so a second
 * officer countersigned while looking at an empty attestation block and the rail
 * apologised for it. The archived revision each signature pinned is now
 * fetchable, and a signed revision contains its predecessor as a literal prefix,
 * so loading the latest one shows every signature made so far.
 *
 * The unsigned export is still the right document while nothing is signed — it
 * is the exact render the fields are created on
 * (`artifact_signing._unsigned_base` re-exports and prepares it), so what the
 * preparer places boxes on is what gets signed. When no PDF exists at all one is
 * minted — once per mount, tracked by a ref rather than by query state, because
 * an export is a write and a re-render must not repeat it.
 */
function usePackageDocument(bankId: string, packageId: string) {
  const artifactsQuery = usePackageArtifacts(bankId, packageId);
  const versionsQuery = usePackageArtifactVersions(bankId, packageId);
  const exportPackage = useExportRegulatoryPackage(bankId);
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState<unknown>(null);
  const requestedExport = useRef(false);
  const loadedDocument = useRef<string | null>(null);

  const signed = useMemo(
    () => versionsQuery.data?.versions.find((version) => version.isFiled) ?? null,
    [versionsQuery.data]
  );

  const pdf = useMemo(() => {
    const artifacts = artifactsQuery.data?.artifacts ?? [];
    return artifacts
      .filter((artifact) => artifact.kind === 'pdf')
      .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime())[0];
  }, [artifactsQuery.data]);

  useEffect(() => {
    // The versions query decides which document this is, so wait for it rather
    // than showing the unsigned base for a beat and swapping it underneath a
    // signer mid-review.
    if (!versionsQuery.isSuccess) return;
    const source = signed
      ? { id: signed.id, fetch: fetchArtifactVersionBytes }
      : pdf
        ? { id: pdf.id, fetch: fetchArtifactBytes }
        : null;
    if (!source || loadedDocument.current === source.id) return;
    loadedDocument.current = source.id;
    setError(null);
    source
      .fetch(bankId, source.id)
      .then(setData)
      .catch((cause: unknown) => setError(cause));
  }, [bankId, pdf, signed, versionsQuery.isSuccess]);

  useEffect(() => {
    if (pdf || signed || !artifactsQuery.isSuccess || requestedExport.current) return;
    requestedExport.current = true;
    exportPackage.mutate(
      { packageId, kind: 'pdf' },
      { onError: (cause) => setError(cause) }
    );
  }, [pdf, signed, artifactsQuery.isSuccess, exportPackage, packageId]);

  return {
    data,
    error: error ?? artifactsQuery.error ?? versionsQuery.error,
    exporting: exportPackage.isPending,
  };
}

function DocumentPending({ exporting }: { exporting: boolean }) {
  return (
    <div className="self-center text-center max-w-sm">
      {exporting ? (
        <>
          <Loader2 size={22} className="mx-auto animate-spin text-action" aria-hidden />
          <p className="mt-2 text-body text-navy">Exporting the return as a PDF…</p>
          <p className="mt-1 text-caption text-slate leading-relaxed">
            The signature fields are created on this document, so it has to exist
            before anything can be placed on it.
          </p>
        </>
      ) : (
        <>
          <FileWarning size={22} className="mx-auto text-slate" aria-hidden />
          <p className="mt-2 text-body text-navy">Loading the return document…</p>
        </>
      )}
    </div>
  );
}

function Toolbar({
  pageIndex,
  pageCount,
  scale,
  onPage,
  onScale,
}: {
  pageIndex: number;
  pageCount: number;
  scale: number;
  onPage: (index: number) => void;
  onScale: (scale: number) => void;
}) {
  const step = (delta: number) => {
    const index = ZOOM_STEPS.indexOf(scale);
    const next = ZOOM_STEPS[Math.min(Math.max((index < 0 ? 1 : index) + delta, 0), ZOOM_STEPS.length - 1)];
    onScale(next);
  };
  return (
    <div className="flex items-center gap-2 px-4 py-2 border-b border-border-light bg-surface-raised">
      <button
        type="button"
        aria-label="Previous page"
        disabled={pageIndex === 0}
        onClick={() => onPage(pageIndex - 1)}
        className="w-8 h-8 rounded border border-border text-slate hover:bg-surface disabled:opacity-40 inline-flex items-center justify-center"
      >
        <ChevronLeft size={14} aria-hidden />
      </button>
      <span className="font-mono text-caption text-navy tnum">
        Page {pageIndex + 1} / {pageCount}
      </span>
      <button
        type="button"
        aria-label="Next page"
        disabled={pageIndex >= pageCount - 1}
        onClick={() => onPage(pageIndex + 1)}
        className="w-8 h-8 rounded border border-border text-slate hover:bg-surface disabled:opacity-40 inline-flex items-center justify-center"
      >
        <ChevronRight size={14} aria-hidden />
      </button>

      <span className="ml-auto flex items-center gap-2">
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => step(-1)}
          className="w-8 h-8 rounded border border-border text-slate hover:bg-surface inline-flex items-center justify-center"
        >
          <Minus size={14} aria-hidden />
        </button>
        <span className="font-mono text-caption text-slate tnum w-12 text-center">
          {Math.round(scale * 100)}%
        </span>
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => step(1)}
          className="w-8 h-8 rounded border border-border text-slate hover:bg-surface inline-flex items-center justify-center"
        >
          <Plus size={14} aria-hidden />
        </button>
      </span>
    </div>
  );
}

const PLACEMENT_SOURCE_LABELS: Record<string, string> = {
  package: 'Placed on this return',
  bank_template: 'From this bank’s template',
  organization_template: 'From the institution template',
  // "default" is never seeded into the workspace, so what the signer is looking
  // at when it resolves is an empty page — say that, not "platform default".
  default: 'Nothing placed yet',
};

function PlacementSourcePill({ source }: { source: ResolvedSignaturePlacementsRead }) {
  return (
    <StatusPill tone={source.source === 'default' ? 'slate' : 'action'}>
      {PLACEMENT_SOURCE_LABELS[source.source] ?? source.source}
    </StatusPill>
  );
}

/**
 * One press to make next month effortless.
 *
 * These returns are monthly, and the layout is the part that is fiddly to get
 * right once and pointless to redo. Org Owner-only, because the template governs
 * every future filing of the return code rather than this one package — a
 * non-owner is told that rather than shown a button that 403s.
 */
function TemplateSaver({
  returnCode,
  canAdministerGrants,
  canSave,
  saved,
  pending,
  error,
  onSave,
}: {
  returnCode: string;
  canAdministerGrants: boolean;
  canSave: boolean;
  saved: boolean;
  pending: boolean;
  error: unknown;
  onSave: () => void;
}) {
  if (!canAdministerGrants) {
    return (
      <p className="mt-4 pt-4 border-t border-border-light text-caption text-slate leading-relaxed">
        These boxes apply to this return only. An Organization Owner can save the
        layout as the {returnCode || 'return'} template so next month opens with
        the fields already on the form.
      </p>
    );
  }
  return (
    <div className="mt-4 pt-4 border-t border-border-light">
      <button
        type="button"
        disabled={!canSave || pending}
        onClick={onSave}
        className="w-full inline-flex items-center justify-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-navy hover:bg-surface disabled:opacity-50"
      >
        {pending ? (
          <Loader2 size={13} className="animate-spin" aria-hidden />
        ) : (
          <BookmarkPlus size={13} aria-hidden />
        )}
        Save as the {returnCode || 'return'} template
      </button>
      {saved && (
        <p role="status" className="mt-1.5 text-caption text-positive leading-relaxed">
          Saved. Every future {returnCode} filing for this bank opens with these
          boxes already on the form.
        </p>
      )}
      {error != null && (
        <p role="alert" className="mt-1.5 text-caption text-critical leading-relaxed">
          {error instanceof Error ? error.message : 'The template could not be saved.'}
        </p>
      )}
    </div>
  );
}

/**
 * What the earlier signers committed to, as recorded.
 *
 * The four evidential lines of §2.5 are the signature an examiner reads, and
 * they are shown here rather than only on the page because the signed revision
 * of the PDF is not retrievable through the artifact API — only its unsigned
 * base is. Rendering the recorded lines is the honest maximum: it is the same
 * content, from the authoritative source, and it is not dressed up as a picture
 * of a document this screen cannot fetch.
 */
function PriorSignatures({ signatures }: { signatures: SignatureRead[] }) {
  if (signatures.length === 0) return null;
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        Already signed on this document
      </p>
      <ul className="mt-1.5 space-y-2">
        {signatures.map((signature) => (
          <li key={signature.id}>
            <SignatureBlock signature={signature} />
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-caption text-slate leading-relaxed">
        The document above is the signed revision each of these pinned — the same
        bytes an examiner would receive.
      </p>
    </div>
  );
}
