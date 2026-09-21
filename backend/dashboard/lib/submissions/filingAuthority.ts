/**
 * Who the signed-in officer is on a regulatory return, read from the SERVER'S
 * OWN projection of their authority.
 *
 * The Returns workspace used to render Generate, Validate, Approve and
 * Submit-to-the-regulator for everybody, each with a disabled state, and asked a
 * preparer to infer from greyed-out controls that none of it was theirs
 * (docs/filing_workflow_redesign.md §4b). The surface is now decided here, and
 * it is decided from `/auth/me`'s effective capabilities for the ACTIVE
 * institution — never from a scalar session role. A panel that computed
 * `roles.includes('approver')` is how a board member holding the correct
 * binding was never offered the control they held.
 *
 * Two rules this module exists to keep:
 *
 *  1. **Hidden until resolved.** Before the projection settles every answer is
 *     `false`, so nothing actionable is rendered. A flash of the wrong role's
 *     controls is the same defect in miniature.
 *  2. **Contextual capabilities are an INVITATION, not authority.** `approve`
 *     and `submit` are projected with `requires_contextual_authorization`,
 *     because the server's final answer needs the object in hand (it refuses the
 *     officer who prepared the very return they are trying to file). They are
 *     read here to decide whether to OFFER a control; the server re-decides
 *     every act, and the UI must state the gate rather than claim the outcome.
 *     `hasEffectiveCapability` deliberately drops them, so it is not the helper
 *     for this question.
 */

import type {
  EffectiveAuthorityRead,
  EffectiveCapabilityRead,
} from "@aequoros/risk-service-api";

type CapabilityModule = EffectiveCapabilityRead["module"];
type CapabilitySensitivity = EffectiveCapabilityRead["sensitivity"];
type CapabilityPermission = EffectiveCapabilityRead["permission"];

/** Regulatory Reporting. The module every filing authority is scoped to. */
const REPORTING: CapabilityModule = "reg";

/**
 * Transmission is gated on Regulatory Reporting / RESTRICTED / submit, exactly —
 * `backend/docs/filing_submit_authority_rollout.md`. Reading it loosely here
 * would offer a channel to someone the server refuses, which is the failure this
 * whole redesign is correcting.
 */
const TRANSMISSION_SENSITIVITY: CapabilitySensitivity = "restricted";

export type FilingAuthority = {
  /** The projection has answered. Everything below is meaningless until true. */
  isResolved: boolean;
  /**
   * The session is an examiner inspection (or another read-only principal).
   * Reading is the whole of it: no act on this screen is ever offered.
   */
  isReadOnly: boolean;
  /** Generate, and regenerate onto a new version. */
  mayPrepare: boolean;
  /** Re-run the machine checks against the generated figures. */
  mayRunChecks: boolean;
  /** Export the artifacts. */
  mayExport: boolean;
  /** Review the return and either approve it or send it back with a comment. */
  mayApprove: boolean;
  /** Transmit the return to the regulator — the Validator, and nobody else. */
  mayTransmit: boolean;
};

export const UNRESOLVED_FILING_AUTHORITY: FilingAuthority = {
  isResolved: false,
  isReadOnly: false,
  mayPrepare: false,
  mayRunChecks: false,
  mayExport: false,
  mayApprove: false,
  mayTransmit: false,
};

/**
 * Does the projection carry this capability?
 *
 * `sensitivity: null` means "at any classification": the preparation verbs are
 * still gated on the scalar analyst ladder server-side for the returns that are
 * not family-gated, so demanding one exact classification here would hide
 * Generate from a preparer the server would happily accept. Transmission, which
 * IS exactly gated, always names its classification.
 */
function holds(
  capabilities: readonly EffectiveCapabilityRead[],
  sensitivity: CapabilitySensitivity | null,
  permission: CapabilityPermission,
): boolean {
  return capabilities.some(
    (capability) =>
      capability.module === REPORTING &&
      capability.permission === permission &&
      (sensitivity === null || capability.sensitivity === sensitivity),
  );
}

/**
 * The signed-in officer's filing authority over one institution.
 *
 * `institutionId` is the institution on screen. A capability list is projected
 * per institution, so covering one bank never covers its sibling — passing the
 * wrong one would be the cross-institution leak the foundation exists to stop.
 */
export function filingAuthorityFor(
  authority: EffectiveAuthorityRead | undefined,
  institutionId: string | null | undefined,
  options: { resolved: boolean; readOnly?: boolean },
): FilingAuthority {
  if (!options.resolved || !authority || !institutionId) {
    return UNRESOLVED_FILING_AUTHORITY;
  }
  const capabilities =
    authority.institutionCapabilities.find(
      (entry) => entry.institutionId === institutionId,
    )?.capabilities ?? [];

  if (options.readOnly) {
    return { ...UNRESOLVED_FILING_AUTHORITY, isResolved: true, isReadOnly: true };
  }

  return {
    isResolved: true,
    isReadOnly: false,
    mayPrepare: holds(capabilities, null, "create"),
    mayRunChecks: holds(capabilities, null, "validate"),
    mayExport: holds(capabilities, null, "export"),
    // Exactly gated, like transmission. `null` here was the fail-open half of
    // the live 403: the server evaluates a chain decision against Regulatory
    // Reporting / RESTRICTED, so an Approver grant at a narrower sensitivity
    // had the button offered to them and was then refused. A screen must not
    // offer an act the server will not accept.
    mayApprove: holds(capabilities, TRANSMISSION_SENSITIVITY, "approve"),
    mayTransmit: holds(
      capabilities,
      TRANSMISSION_SENSITIVITY,
      "submit",
    ),
  };
}

/**
 * The permission sentence an Org Owner would have to write, named the way
 * Settings → Members names it. Used to explain an absence when an officer asks
 * why a surface is not theirs — never to imply they can grant it themselves.
 */
export const TRANSMISSION_SENTENCE =
  "Regulatory Reporting · Restricted · Transmit to the regulator";
