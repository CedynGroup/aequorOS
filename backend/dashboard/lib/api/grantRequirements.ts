/**
 * Whether the grant an Org Owner has composed will actually authorise the work
 * its role bundle names.
 *
 * The sentence composer lets every dimension be chosen independently, which is
 * the point — a grant is one indivisible statement and each part has to be
 * deliberate. But it means a sentence can read perfectly and authorise nothing:
 * "Approver · Regulatory Reporting · this bank · Confidential" looks complete
 * and is silently inert, because a filing-chain decision is evaluated against
 * RESTRICTED and sensitivity is matched exactly — `confidential` is not
 * `restricted` and there is no ladder between them.
 *
 * That cost a live debugging session. The Owner granted it, the grantee's
 * session was invalidated as designed, they signed in, pressed Approve, and got
 * "This action requires the 'analyst' role or higher" — a message about a
 * scalar role, pointing nowhere near a sensitivity mismatch on their binding.
 *
 * So the check belongs where the mistake is made, not where it surfaces.
 *
 * This module MIRRORS a backend constant and must not drift from it:
 * `CHAIN_DECISION_GATE` in
 * `backend/app/services/regulatory_reporting/family_access.py`.
 * `grantRequirements.parity.test.ts` reads that file and fails if the two
 * disagree — fix the mirror, never the test.
 */

import type { GrantDraft } from './grants';

/** The module a filing-chain decision is evaluated against. */
export const CHAIN_DECISION_MODULE = 'reg';

/** The sensitivity a filing-chain decision is evaluated against. */
export const CHAIN_DECISION_SENSITIVITY = 'restricted';

/**
 * Bundles whose whole purpose is deciding on a return in the filing chain.
 *
 * Deliberately not every operational bundle: an Analyst prepares through the
 * edit path, which is not a chain decision, so a narrower sensitivity is a
 * legitimate choice there rather than a mistake worth interrupting.
 */
const CHAIN_DECISION_BUNDLES: Record<string, string> = {
  approver: 'approve returns or send them back',
  validator: 'file returns with the regulator',
};

function coversModule(scope: string): boolean {
  return scope === CHAIN_DECISION_MODULE || scope === 'all';
}

function coversSensitivity(scope: string): boolean {
  return scope === CHAIN_DECISION_SENSITIVITY || scope === 'all';
}

/**
 * One sentence naming what this grant will NOT do, or null when it is sound.
 *
 * A warning, never a block: the server is the authority on what it accepts, and
 * a narrower grant may be exactly what the Owner intends for a role they are
 * scoping deliberately. What it must not do is let the Owner believe they have
 * granted something they have not.
 */
export function grantShortfall(draft: GrantDraft): string | null {
  const work = CHAIN_DECISION_BUNDLES[draft.roleBundle];
  if (work === undefined) return null;

  const moduleOk = coversModule(draft.moduleScope);
  const sensitivityOk = coversSensitivity(draft.sensitivityScope);
  if (moduleOk && sensitivityOk) return null;

  const missing: string[] = [];
  if (!moduleOk) missing.push('Regulatory Reporting (or all modules)');
  if (!sensitivityOk) missing.push('Restricted (or all sensitivity levels)');

  return (
    `This grant will not let them ${work}. Deciding on a return is evaluated ` +
    `against ${missing.join(' and ')}, and scopes are matched exactly — a ` +
    `narrower level does not include a wider one. They will be able to sign ` +
    `in and see the return, then be refused when they act on it.`
  );
}

/**
 * A grant the grantee already holds that this one will NOT extend.
 *
 * The composer evaluates one draft; an Org Owner thinks in terms of "expanding
 * someone's scope". Those are different models, and the gap between them is
 * expensive: a founder fixed a too-narrow sensitivity by issuing a SECOND
 * Approver grant with a wider one — and changed the module while doing it. The
 * result was two rows, each complete-looking, each missing a different
 * dimension, and neither authorising anything.
 *
 * That is the documented rule working as designed: every dimension ANDs WITHIN
 * a row, and rows only OR once each is sound on its own. Nothing in the
 * composer said so, because the composer could not see the other rows.
 *
 * Only same-bundle, same-institution rows are reported. A Viewer row beside an
 * Approver row is ordinary and says nothing about this draft.
 */
export type HeldGrant = Readonly<{
  roleBundle: string;
  institutionId: string | null | undefined;
  moduleScope: string;
  sensitivityScope: string;
  status: string;
}>;

export function overlappingGrantNotice(
  draft: GrantDraft,
  held: readonly HeldGrant[],
): string | null {
  const target =
    draft.institutionScope === 'institution' ? (draft.institutionId ?? null) : null;
  const same = held.filter(
    (grant) =>
      grant.status === 'active' &&
      grant.roleBundle === draft.roleBundle &&
      (grant.institutionId ?? null) === target,
  );
  if (same.length === 0) return null;

  return (
    `They already hold ${same.length === 1 ? 'an active' : `${same.length} active`} ` +
    `${draft.roleBundle} grant here. A new grant is a SEPARATE row — scopes do ` +
    `not merge across rows, so this one has to be complete on its own, and the ` +
    `existing one keeps whatever it already allows. If you meant to widen the ` +
    `existing grant, revoke it and issue one complete replacement.`
  );
}
