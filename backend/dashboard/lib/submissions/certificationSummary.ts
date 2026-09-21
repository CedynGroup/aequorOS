/**
 * The one line the collapsed Certification row shows.
 *
 * It exists because that line said **"Fully signed and cleared to be filed"**
 * for a return with ZERO signatures. The row keyed its summary off `canSubmit`
 * alone, and `canSubmit` answers "may this reach a channel?" — which is true
 * when no signature is REQUIRED (the deployment-wide e-sign switch, or the
 * ICAAP-scoped one). So the collapsed row claimed officers had signed while the
 * card underneath it read `UNSIGNED · Not certified`.
 *
 * `components/attestation/shared.tsx` already carries the rule this broke:
 * clearance is "driven by `canSubmit` and by nothing looser", and re-deriving
 * it elsewhere "is how 'CLEARED TO SUBMIT' once rendered beside 'UNSIGNED'".
 * The fix is not to loosen clearance — it is to stop conflating it with
 * signature. They are two facts and the summary states both:
 *
 *   signed?            from the signatures on record, never from policy
 *   cleared to file?   from `canSubmit`, never from the signature count
 *
 * A return can legitimately be cleared with nothing signed. It can never be
 * described as signed because it was cleared.
 *
 * Pure: no app imports, so the node test harness can reach it.
 */

export type CertificationSummaryInput = Readonly<{
  /** The service's answer to "may this reach a channel?". */
  canSubmit: boolean;
  /** Signatures actually on record for the current cycle. */
  signatureCount: number;
  /** Role nouns already formatted by the attestation vocabulary, in order. */
  signedRoles: readonly string[];
  /** "1 preparer · 1 approver", from `outstandingSummary`. */
  outstandingLabel: string;
}>;

/** "preparer and approver" / "preparer, approver and board". */
export function joinRoles(roles: readonly string[]): string {
  if (roles.length === 0) return '';
  if (roles.length === 1) return roles[0];
  return `${roles.slice(0, -1).join(', ')} and ${roles[roles.length - 1]}`;
}

export function certificationSummary(input: CertificationSummaryInput): string {
  if (!input.canSubmit) {
    return `Outstanding: ${input.outstandingLabel}.`;
  }
  if (input.signatureCount === 0) {
    // Cleared, but nobody signed. Saying so is the whole point of this module:
    // the reader must not infer a signature from the clearance.
    return 'No signature required here · cleared to be filed.';
  }
  const who = joinRoles(input.signedRoles);
  const signed = who.length > 0 ? `Signed by ${who}` : `${input.signatureCount} signed`;
  return `${signed} · cleared to be filed.`;
}
