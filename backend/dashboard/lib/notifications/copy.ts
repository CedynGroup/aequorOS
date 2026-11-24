/**
 * Plain-language copy for the notification settings screens.
 *
 * Pure functions only, so the wording the person reads is pinned by unit tests
 * and never leaks an API enum ("digest", "suppressed", "recipient_lost_access").
 */

import type {
  DeliveryStatus,
  EmailMode,
  NotificationEventPreferenceRead,
} from "@aequoros/risk-service-api";

export const EMAIL_MODES: readonly EmailMode[] = ["immediate", "digest", "off"];

export const MODE_LABELS: Record<EmailMode, string> = {
  immediate: "Email right away",
  digest: "Daily summary",
  off: "In the app only",
};

/** Short form for tables where the full label does not fit. */
export const MODE_SHORT_LABELS: Record<EmailMode, string> = {
  immediate: "Right away",
  digest: "Daily summary",
  off: "App only",
};

export type PreferenceState = {
  /** The mode the control shows as selected. */
  selected: EmailMode;
  /** True when the person follows the default instead of a saved choice. */
  followsDefault: boolean;
  /** Whether the person can change it. */
  editable: boolean;
  /** Why it cannot be changed, or a note about the default; null when nothing to say. */
  note: string | null;
};

/** What the preference control for one event should show. */
export function preferenceState(
  event: NotificationEventPreferenceRead,
): PreferenceState {
  if (event.lockedBy === "platform") {
    return {
      selected: event.emailMode,
      followsDefault: false,
      editable: false,
      note: "Always emailed — this is too important to turn off.",
    };
  }
  if (event.lockedBy === "organisation") {
    return {
      selected: event.emailMode,
      followsDefault: false,
      editable: false,
      note: "Set by your organisation for everyone.",
    };
  }
  if (event.choice == null) {
    return {
      selected: event.emailMode,
      followsDefault: true,
      editable: true,
      note: `Organisation default: ${MODE_LABELS[event.defaultEmail].toLowerCase()}.`,
    };
  }
  return {
    selected: event.choice,
    followsDefault: false,
    editable: true,
    note: null,
  };
}

export const DELIVERY_STATUS_LABELS: Record<DeliveryStatus, string> = {
  sent: "Sent",
  pending: "Waiting",
  dead: "Failed",
  suppressed: "Not sent",
};

const SUPPRESSION_REASONS: Record<string, string> = {
  recipient_inactive: "Person is deactivated",
  no_address: "Person has no email address",
  not_on_allowlist: "Blocked by the test-mode allowlist",
  recipient_lost_access: "Person no longer has access",
  expired: "Expired before it could be sent",
};

/**
 * Explain why a delivery did not go out. Suppression reasons are codes the
 * dispatcher records; relay errors arrive as "SMTPRecipientsRefused: …" and
 * are summarised, keeping the relay's own words for the administrator.
 */
export function deliveryReason(lastError: string | null | undefined): string | null {
  if (!lastError) return null;
  const known = SUPPRESSION_REASONS[lastError];
  if (known) return known;
  const [kind, ...rest] = lastError.split(": ");
  const detail = rest.join(": ").trim();
  const summary: Record<string, string> = {
    SMTPRecipientsRefused: "The mail server refused this address",
    SMTPSenderRefused: "The mail server refused the sender address",
    SMTPDataError: "The mail server rejected the message",
    SMTPAuthenticationError: "AequorOS could not sign in to the mail server",
    SMTPServerDisconnected: "The mail server disconnected",
    SMTPConnectError: "Could not connect to the mail server",
    TimeoutError: "The mail server did not answer in time",
    ConnectionRefusedError: "Could not connect to the mail server",
  };
  const lead = summary[kind];
  if (lead) return detail ? `${lead} (${detail})` : lead;
  return lastError;
}

/** "07:00" for an hour of the day. */
export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** Plain explanation of a time-zone identifier for the summary line. */
export function zoneLabel(zone: string): string {
  return zone === "UTC" ? "UTC" : zone.replaceAll("_", " ");
}

export type OrgWindows = {
  reminderAfterHours: number;
  escalateAfterHours: number;
};

/** The reason the organisation form cannot be saved, or null when it can. */
export function orgSettingsProblem(form: OrgWindows & { digestHour: number }): string | null {
  if (!Number.isInteger(form.digestHour) || form.digestHour < 0 || form.digestHour > 23) {
    return "Choose a summary hour between 00:00 and 23:00.";
  }
  if (!Number.isInteger(form.reminderAfterHours) || form.reminderAfterHours < 1) {
    return "Reminders must wait at least one hour.";
  }
  if (
    !Number.isInteger(form.escalateAfterHours) ||
    form.escalateAfterHours <= form.reminderAfterHours
  ) {
    return "Escalation must come after the first reminder.";
  }
  return null;
}

/** "in 4 minutes" / "in 2 hours" / "now" for a retry time, relative to ``now``. */
export function untilLabel(when: Date, now: Date = new Date()): string {
  const minutes = Math.round((when.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  return `in ${hours} hour${hours === 1 ? "" : "s"}`;
}
