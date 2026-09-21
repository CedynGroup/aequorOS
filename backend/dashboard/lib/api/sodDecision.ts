/**
 * The separation-of-duties findings the server sends with a refused grant.
 *
 * `POST /authorization/bindings` returns 409 with
 * `details.sod_decision.findings`, each one a code and a sentence saying which
 * rule fired. The Members dialog rendered only the exception's own text —
 * "The scoped grant conflicts with separation-of-duties policy." — which names
 * no rule, no bundle and no remedy.
 *
 * An Org Owner who tries to grant themselves Validator is blocked by C9:
 * account administration and operational maker/checker authority must stay on
 * different identities. That is a correct, deliberate refusal. Shown as the
 * generic line it reads as a malfunction, and the Owner's next move is to try
 * the same thing again with different scopes — which cannot work, because the
 * conflict is about WHO they are, not how narrow the grant is.
 *
 * Untrusted shape on purpose: `details` is `unknown` on ApiError, and a
 * malformed payload must produce no findings rather than a crash or a blank
 * bullet. Pure — no app imports, so the node harness can reach it.
 */

export type SodFinding = Readonly<{ code: string; message: string }>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The findings carried by a refused grant, or [] when there are none to show. */
export function sodFindings(details: unknown): SodFinding[] {
  if (!isRecord(details)) return [];
  const decision = details.sod_decision;
  if (!isRecord(decision)) return [];
  const raw = decision.findings;
  if (!Array.isArray(raw)) return [];

  const findings: SodFinding[] = [];
  for (const candidate of raw) {
    if (!isRecord(candidate)) continue;
    const message = candidate.message;
    if (typeof message !== "string" || message.length === 0) continue;
    const code = typeof candidate.code === "string" ? candidate.code : "";
    findings.push({ code, message });
  }
  return findings;
}

/**
 * What the Owner should do next, when the rule that fired has a known remedy.
 *
 * Only for rules whose remedy is NOT "narrow the scope" — those are the ones
 * where an Owner will otherwise keep re-composing the same grant. Returns null
 * when there is nothing specific to add; a generic hint is worse than none.
 */
export function sodRemedy(findings: readonly SodFinding[]): string | null {
  const codes = new Set(findings.map((finding) => finding.code));
  if (codes.has("c9_account_administration_operational_conflict")) {
    return (
      "This identity administers the account, so it cannot also carry " +
      "operational authority — no scope will allow it. Grant this bundle to a " +
      "different person, or revoke this identity's account administration first."
    );
  }
  return null;
}
