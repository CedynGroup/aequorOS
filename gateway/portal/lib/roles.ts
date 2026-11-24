/**
 * Role-aware UI. These decide what the portal OFFERS; the gateway API decides
 * what is ALLOWED (separation of duties is enforced in the service, not here).
 * Per-record decisions on filings come from the record's `allowed_actions`.
 */

import { ROLE_TITLE } from "./labels";
import type { Role, SubmissionAction } from "./api/types";

function hasAny(roles: readonly Role[], ...wanted: Role[]): boolean {
  return wanted.some((role) => roles.includes(role));
}

export const capabilities = {
  /** Raise and answer queries, close them, write supervisory notes. */
  supervise: (roles: readonly Role[]) => hasAny(roles, "supervisor", "senior_supervisor"),
  /** Draft a new return-definition version: Returns Policy only. */
  draftReturns: (roles: readonly Role[]) => hasAny(roles, "returns_policy"),
  /**
   * Approve or return a draft: Returns Policy, or a Senior Supervisor as the
   * design's "Second approver, Supervision" (the API's APPROVER_ROLES).
   */
  decideReturns: (roles: readonly Role[]) => hasAny(roles, "returns_policy", "senior_supervisor"),
  /** Approve or deny operator support sessions. */
  approveAccess: (roles: readonly Role[]) => hasAny(roles, "access_approver"),
  /** Verifying the chain and exporting the log decide nothing; every role may ask. */
  verifyAudit: (roles: readonly Role[]) => roles.length > 0,
  exportAudit: (roles: readonly Role[]) => roles.length > 0,
};

/** True when the person can read but take no decision anywhere (auditors). */
export function isReadOnly(roles: readonly Role[]): boolean {
  return roles.every((role) => role === "auditor");
}

export function submissionActions(allowed: readonly SubmissionAction[]) {
  return {
    accept: allowed.includes("accept"),
    query: allowed.includes("query"),
    resubmission: allowed.includes("request_resubmission"),
    assign: allowed.includes("assign"),
    any: allowed.length > 0,
  };
}

const TITLE_ORDER: Role[] = ["senior_supervisor", "supervisor", "returns_policy", "access_approver", "auditor"];

/** The sidebar's role line, e.g. "Senior Supervisor" or "Returns Policy Officer · Access Approver". */
export function roleTitle(roles: readonly Role[]): string {
  const ordered = TITLE_ORDER.filter((role) => roles.includes(role));
  if (ordered.length === 0) return "No role assigned";
  return ordered.map((role) => ROLE_TITLE[role]).join(" · ");
}
