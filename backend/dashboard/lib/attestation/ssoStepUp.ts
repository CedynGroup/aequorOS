'use client';

/**
 * Leave for the identity provider to prove presence.
 *
 * A full-page navigation, not a popup: the IdP decides how it authenticates the
 * signer, and some refuse to render in a frame or a popup at all. `returnTo`
 * brings the ceremony back open on this package, and the callback route rejects
 * anything that is not a path inside this app — an open redirect on a signing
 * action is a phishing primitive.
 *
 * `resume` records WHICH surface to reopen. The two are not interchangeable: a
 * signer sent back to the dialog after placing fields in the workspace would
 * find their placement gone, and one sent to the workspace for a role that has
 * no field on the document would find nothing to place.
 */

export type CeremonySurface = 'certify' | 'sign';

export function startSsoStepUp({
  bankId,
  packageId,
  signingRole,
  resume,
}: {
  bankId: string;
  packageId: string;
  signingRole: string;
  resume: CeremonySurface;
}): void {
  const here = new URL(window.location.href);
  here.searchParams.delete('certify');
  here.searchParams.delete('sign');
  here.searchParams.delete('stepUp');
  here.searchParams.set(resume, signingRole);

  const start = new URL('/api/attestation/step-up/start', window.location.origin);
  start.searchParams.set('bankId', bankId);
  start.searchParams.set('packageId', packageId);
  start.searchParams.set('signingRole', signingRole);
  start.searchParams.set('returnTo', `${here.pathname}${here.search}`);
  window.location.assign(start.toString());
}

/**
 * Where the workspace parks a nomination across the IdP round trip.
 *
 * Session-scoped and package-scoped, and deliberately NOT the placement: the
 * boxes are written to the server before leaving (they are audited data the
 * signature depends on), whereas a nomination is a choice that is cheap to make
 * again. Losing this is a minor annoyance, not a correctness problem — nothing
 * downstream trusts it, and the server re-validates every nominee.
 */
export function recipientStashKey(packageId: string, signingRole: string): string {
  return `aeq-signing-recipients:${packageId}:${signingRole}`;
}
