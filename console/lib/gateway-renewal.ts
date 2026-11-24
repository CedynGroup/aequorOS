/**
 * What the console says about a bank's gateway client certificate and its renewal.
 *
 * Pure: every decision the renewal panel shows is made here, from the staff API's
 * enrolment record and the moment it is read, so it can be tested without a DOM.
 */

import type {
  GatewayCertificateRenewal,
  GatewayRenewalStatus,
  ReportingGatewayEnrolment,
} from "./api";

export type RenewalTone = "ok" | "warn" | "crit" | "accent" | "neutral";

const MS_PER_DAY = 86_400_000;
/** A scheduled check older than this is not evidence that it is running. */
export const CHECK_FRESH_DAYS = 3;

/** The last renewal's outcome, in words. */
export const RENEWAL_STATUS: Record<
  GatewayRenewalStatus,
  { label: string; tone: RenewalTone }
> = {
  complete: { label: "Completed", tone: "ok" },
  in_progress: { label: "In progress", tone: "accent" },
  failed: {
    label: "Failed — bank still on its current certificate",
    tone: "warn",
  },
  revocation_pending: {
    label: "Switched — old client not yet revoked",
    tone: "warn",
  },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** The certificate's standing now: expiry first, then an unfinished renewal, then the window. */
export function certificateState(
  enrolment: ReportingGatewayEnrolment,
  now: number,
): { label: string; tone: RenewalTone } {
  const renewal = enrolment.certificate_renewal;
  if (
    enrolment.certificate_not_after &&
    Date.parse(enrolment.certificate_not_after) <= now
  ) {
    return { label: "Expired", tone: "crit" };
  }
  if (renewal?.status === "failed")
    return { label: "Renewal failed", tone: "warn" };
  if (renewal?.status === "revocation_pending") {
    return { label: "Old client not yet revoked", tone: "warn" };
  }
  if (renewal?.status === "in_progress")
    return { label: "Renewing", tone: "accent" };
  if (
    enrolment.renewal_due_from &&
    Date.parse(enrolment.renewal_due_from) <= now
  ) {
    return { label: "Due for renewal", tone: "warn" };
  }
  return { label: "Valid", tone: "ok" };
}

/** A renewal the next action resumes rather than starts afresh. */
export function isUnfinished(
  renewal: GatewayCertificateRenewal | null | undefined,
): boolean {
  return (
    renewal?.status === "failed" ||
    renewal?.status === "in_progress" ||
    renewal?.status === "revocation_pending"
  );
}

/** Only a renewal that has not switched the bank over can be abandoned. */
export function isCancellable(
  renewal: GatewayCertificateRenewal | null | undefined,
): boolean {
  return (
    (renewal?.status === "failed" || renewal?.status === "in_progress") &&
    (renewal.stage === "key_prepared" || renewal.stage === "client_issued")
  );
}

/** Whether the staff plane's scheduled check has looked at this bank recently. */
export function scheduledCheckRunning(
  enrolment: ReportingGatewayEnrolment,
  now: number,
): boolean {
  if (!enrolment.last_renewal_check_at) return false;
  return (
    now - Date.parse(enrolment.last_renewal_check_at) <=
    CHECK_FRESH_DAYS * MS_PER_DAY
  );
}
