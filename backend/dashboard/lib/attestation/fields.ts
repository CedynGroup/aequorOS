/**
 * The kinds of box a signer can place, and what each one will actually print.
 *
 * A regulator's attestation block asks for four things per officer — BSD3's
 * "Prepared by (name / designation / signature / date)" — so the workspace hands
 * out four kinds of field plus initials, and the signer drops each one on the
 * line the form printed for it.
 *
 * **The values here are previews, never inputs.** The backend derives every
 * non-signature value from the signature record at signing time
 * (`pdf_signing.SignatureAppearance.derived_values`), and nothing typed in a
 * browser can reach a filed document. These functions exist so what the signer
 * sees in the box is what will be stamped in it — computed the same way, from
 * the same facts — and they must be kept in step with that method.
 */

import type { PlacementFieldType } from '@aequoros/risk-service-api';

/** The palette, in the order a form asks for them. */
export const PLACEABLE_FIELD_TYPES: readonly PlacementFieldType[] = [
  'signature',
  'name',
  'title',
  'date_signed',
  'initials',
];

export const FIELD_TYPE_LABELS: Record<PlacementFieldType, string> = {
  signature: 'Signature',
  name: 'Name',
  title: 'Designation',
  date_signed: 'Date',
  initials: 'Initials',
};

/**
 * What each kind is drawn at when it is first dropped, in PDF points.
 *
 * Chosen to sit on a BoG attestation block's ruled lines rather than to be
 * safely large: the signature default is the ~144×35 line the form prints. Every
 * default is comfortably above its own kind's minimum — which the backend serves
 * (`field_types[].minBoxWidth/Height`) rather than this file restating — so a
 * freshly dropped field is never already in violation. The signature floor is
 * the whole stamp (role label, adopted mark, permanent signer ID), not the mark
 * alone, so it is the one to re-check when this changes.
 */
export const DEFAULT_FIELD_SIZES: Record<PlacementFieldType, { width: number; height: number }> = {
  signature: { width: 144, height: 35 },
  name: { width: 160, height: 16 },
  title: { width: 160, height: 16 },
  date_signed: { width: 90, height: 16 },
  initials: { width: 44, height: 16 },
};

/** The identity a set of boxes will print, as far as this browser can know it. */
export interface SignerPreview {
  displayName: string | null;
  jobTitle: string | null;
}

/** `"Ama Mensah"` → `"A.M."` — mirrors `pdf_signing._initials`. */
export function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .filter((part) => /^\p{L}/u.test(part))
    .map((part) => `${part[0]!.toUpperCase()}.`)
    .join('');
}

/** The signing date as the PDF prints it — ISO, matching `_DATE_FORMAT`. */
export function signingDatePreview(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/**
 * The text this box will carry, or `null` when the signer is not known yet.
 *
 * `null` is a real answer and is rendered as such: before an approver has been
 * nominated, nobody — including the server — knows whose name goes on their
 * line, and showing a plausible placeholder would be showing a value that is
 * not going to be printed.
 */
export function derivedPreview(
  fieldType: PlacementFieldType,
  signer: SignerPreview | null
): string | null {
  if (fieldType === 'signature') return null;
  if (fieldType === 'date_signed') return signingDatePreview();
  if (!signer?.displayName) return null;
  if (fieldType === 'name') return signer.displayName;
  if (fieldType === 'initials') return initialsOf(signer.displayName);
  return signer.jobTitle ?? null;
}
