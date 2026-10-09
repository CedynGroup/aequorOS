/**
 * Read server findings from a refused grant without deriving policy locally.
 *
 * Untrusted shape on purpose: `details` is `unknown` on ApiError, and a
 * malformed payload must produce no findings rather than a crash or a blank
 * bullet. Pure — only sibling pure modules, so the node harness can reach it.
 */

import { canRevokeFromMembers } from "./grants";

export type SodFinding = Readonly<{
  code: string;
  message: string;
  /** The member's existing grants this finding fired on. */
  conflictingBindingIds?: readonly string[];
}>;

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
    const rawIds = candidate.conflicting_binding_ids;
    const conflictingBindingIds = Array.isArray(rawIds)
      ? rawIds.filter((id): id is string => typeof id === "string")
      : [];
    findings.push({ code, message, conflictingBindingIds });
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

type ConflictGrant = Readonly<{
  id: string;
  roleBundle: string;
  effective: boolean;
}>;

/**
 * What a notice offers for the grants its findings fired on.
 *
 * `reviewable` are the conflicting grants this viewer may open and revoke
 * through the normal Members flow, in finding order and without repeats.
 * `askAdministrator` is true when a revocable conflict exists that this viewer
 * cannot revoke, or a finding names a binding missing from the loaded member.
 * A loaded grant Members never revokes (ownership, baseline membership) gets
 * neither: nobody can change it from here.
 */
export function conflictGrantActions<G extends ConflictGrant>(
  findings: readonly SodFinding[],
  grants: readonly G[],
  canAdministerGrants: boolean,
): Readonly<{ reviewable: readonly G[]; askAdministrator: boolean }> {
  const byId = new Map(grants.map((grant) => [grant.id, grant]));
  const seen = new Set<string>();
  const conflicting: G[] = [];
  let missing = false;
  for (const finding of findings) {
    for (const id of finding.conflictingBindingIds ?? []) {
      const grant = byId.get(id);
      if (!grant) {
        missing = true;
        continue;
      }
      if (seen.has(id) || !canRevokeFromMembers(grant)) continue;
      seen.add(id);
      conflicting.push(grant);
    }
  }
  return canAdministerGrants
    ? { reviewable: conflicting, askAdministrator: missing }
    : { reviewable: [], askAdministrator: missing || conflicting.length > 0 };
}
