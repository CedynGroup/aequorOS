/**
 * Plain-language labels and status-chip tones for API enums. UI copy never
 * shows a raw enum value; unknown values are humanized rather than dropped.
 */

import { humanize } from "./format";
import type {
  AttentionItem,
  CalendarTone,
  Cadence,
  Channel,
  HeatState,
  InstitutionStatus,
  Obligation,
  OnboardingStage,
  QueryKind,
  QueryListStatus,
  Role,
  SubmissionState,
  AccessOverview,
} from "./api/types";

/** Status chip palette from the design. */
export type Tone = "green" | "blue" | "grey" | "red" | "purple" | "amber" | "muted" | "struck";

export const SUBMISSION_STATUS: Record<SubmissionState, { label: string; tone: Tone }> = {
  received: { label: "Received", tone: "blue" },
  validating: { label: "Validating", tone: "blue" },
  rejected: { label: "Rejected", tone: "red" },
  in_review: { label: "In review", tone: "grey" },
  accepted: { label: "Accepted", tone: "green" },
  queried: { label: "Queried", tone: "purple" },
  superseded: { label: "Superseded", tone: "struck" },
};

export function submissionStatus(state: SubmissionState): { label: string; tone: Tone } {
  return SUBMISSION_STATUS[state] ?? { label: humanize(state), tone: "grey" };
}

export const ATTENTION_KIND: Record<AttentionItem["kind"], { label: string; tone: Tone }> = {
  outstanding: { label: "Outstanding", tone: "red" },
  rejected: { label: "Rejected", tone: "red" },
  queried: { label: "Queried", tone: "purple" },
  late: { label: "Late", tone: "amber" },
  credential: { label: "Credential", tone: "grey" },
};

export const HEAT_STATE: Record<HeatState, { label: string }> = {
  on_time: { label: "Received on time" },
  late: { label: "Received late" },
  outstanding: { label: "Outstanding or rejected" },
  not_due: { label: "Not yet due" },
};

/** The institution profile's legend words them more briefly. */
export const HISTORY_STATE: Record<HeatState, { label: string }> = {
  on_time: { label: "On time" },
  late: { label: "Late" },
  outstanding: { label: "Outstanding or rejected" },
  not_due: { label: "Not yet due" },
};

export const QUERY_STATUS: Record<QueryListStatus, { label: string; tone: Tone }> = {
  awaiting_institution: { label: "Awaiting institution", tone: "purple" },
  awaiting_supervisor: { label: "Awaiting supervisor", tone: "blue" },
  overdue: { label: "Overdue", tone: "red" },
  closed: { label: "Closed", tone: "grey" },
};

export function queryStatus(status: QueryListStatus): { label: string; tone: Tone } {
  return QUERY_STATUS[status] ?? { label: humanize(status), tone: "grey" };
}

export const QUERY_KIND: Record<QueryKind, string> = { query: "Query", resubmission: "Resubmission" };

export const OBLIGATION_STATUS: Record<Obligation["status"], { label: string; tone: Tone }> = {
  overdue: { label: "Overdue", tone: "red" },
  due: { label: "Due soon", tone: "amber" },
  not_due: { label: "Upcoming", tone: "grey" },
  filed: { label: "Filed", tone: "green" },
  filed_late: { label: "Filed late", tone: "amber" },
};

export const CHANNEL: Record<Channel, string> = { api: "API", portal_upload: "Portal upload" };

export function channelLabel(channel: Channel | null): string {
  return channel ? CHANNEL[channel] ?? humanize(channel) : "To be decided";
}

/** "AequorOS 2026.11 · declared", or the design's wording when nothing was declared. */
export function softwareLabel(software: string | null, channel: Channel | null = "api"): string {
  if (software) return `${software} · declared`;
  return channel === null ? "Not yet declared" : "No software declared";
}

export function filesByLabel(channel: Channel | null): string {
  if (channel === "api") return "Files by API";
  if (channel === "portal_upload") return "Files by portal upload";
  return "No filing channel yet";
}

export const CADENCE: Record<Cadence, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  semiannual: "Semi-annual",
  annual: "Annual",
};

export function cadenceLabel(cadence: string): string {
  return (CADENCE as Record<string, string>)[cadence] ?? humanize(cadence);
}

const INSTITUTION_TYPES: Record<string, { long: string; short: string }> = {
  universal_bank: { long: "Universal bank", short: "Universal bank" },
  rural_bank: { long: "Rural bank", short: "Rural bank" },
  savings_and_loans: { long: "Savings and loans company", short: "Savings and loans" },
  microfinance: { long: "Microfinance institution", short: "Microfinance" },
  finance_house: { long: "Finance house", short: "Finance house" },
};

export function institutionTypeLabel(type: string, form: "long" | "short" = "short"): string {
  return INSTITUTION_TYPES[type]?.[form] ?? humanize(type);
}

export const INSTITUTION_TYPE_OPTIONS = Object.entries(INSTITUTION_TYPES).map(([value, l]) => ({ value, label: l.long }));

export const INSTITUTION_STATUS: Record<InstitutionStatus, { label: string; tone: Tone }> = {
  requested: { label: "Under review", tone: "grey" },
  sandbox: { label: "In sandbox", tone: "blue" },
  certified: { label: "Certified", tone: "purple" },
  live: { label: "Live", tone: "green" },
  suspended: { label: "Suspended", tone: "red" },
};

/** Onboarding register `status` is a free string in SPEC; known values get the design's words. */
export function registerStatus(status: string): { label: string; tone: Tone } {
  const known: Record<string, { label: string; tone: Tone }> = {
    ...INSTITUTION_STATUS,
    enrolled: { label: "Enrolled", tone: "blue" },
    requested: { label: "Enrolled", tone: "blue" },
    sandbox: { label: "Enrolled", tone: "blue" },
    certified: { label: "Enrolled", tone: "blue" },
    renewal_due: { label: "Renewal due", tone: "amber" },
  };
  return known[status] ?? { label: humanize(status), tone: "grey" };
}

/**
 * The channel register's counts, as plain words. SPEC's stage values still
 * carry the retired enrolment steps (requested, sandbox, certified); those
 * all mean "enrolled, not yet live" now that the channel operator enrols.
 */
export const REGISTER_COUNTS = ["Enrolled", "Live", "Suspended"] as const;

export function registerCountKey(stage: OnboardingStage | string): (typeof REGISTER_COUNTS)[number] {
  if (stage === "live") return "Live";
  if (stage === "suspended") return "Suspended";
  return "Enrolled";
}

export const FLAG_LABEL: Record<string, string> = { heightened_monitoring: "Heightened monitoring" };

export function flagLabel(flag: string): string {
  return FLAG_LABEL[flag] ?? humanize(flag);
}

export const SESSION_STATUS: Record<AccessOverview["sessions"][number]["status"], { label: string; tone: Tone }> = {
  active: { label: "Active", tone: "blue" },
  ended: { label: "Ended", tone: "grey" },
  denied: { label: "Denied", tone: "red" },
  expired: { label: "Expired", tone: "muted" },
};

/** Audit categories (ARCHITECTURE.md "Evidence: the audit chain"). */
export const AUDIT_CATEGORY: Record<string, { label: string; tone: Tone; option: string }> = {
  sign_in: { label: "Sign-in", tone: "grey", option: "Sign-in" },
  filing: { label: "Filing", tone: "blue", option: "Filing" },
  decision: { label: "Decision", tone: "green", option: "Decision" },
  view: { label: "View", tone: "grey", option: "View" },
  query: { label: "Query", tone: "purple", option: "Query" },
  configuration: { label: "Configuration", tone: "grey", option: "Configuration" },
  credential: { label: "Credential", tone: "grey", option: "Credentials" },
  operator_access: { label: "Operator", tone: "amber", option: "Operator access" },
  system: { label: "System", tone: "muted", option: "System" },
};

export function auditCategory(category: string): { label: string; tone: Tone } {
  return AUDIT_CATEGORY[category] ?? { label: humanize(category), tone: "grey" };
}

export const AUDIT_ACTORS: { value: string; label: string }[] = [
  { value: "", label: "Everyone" },
  { value: "staff", label: "Bank staff" },
  { value: "institution", label: "Institutions" },
  { value: "system", label: "System" },
  { value: "operator", label: "Operator support" },
];

/** Role names as the Access screen words them. */
export const ROLE_LABEL: Record<Role, string> = {
  supervisor: "Supervisor",
  senior_supervisor: "Senior supervisor",
  returns_policy: "Returns policy officer",
  access_approver: "Access approver",
  auditor: "Auditor",
};

/** Role titles as the sidebar's user card words them. */
export const ROLE_TITLE: Record<Role, string> = {
  supervisor: "Supervisor",
  senior_supervisor: "Senior Supervisor",
  returns_policy: "Returns Policy Officer",
  access_approver: "Access Approver",
  auditor: "Internal Auditor",
};

export function roleLabel(role: Role): string {
  return ROLE_LABEL[role] ?? humanize(role);
}

export const CALENDAR_TONE: Record<CalendarTone, { srLabel: string }> = {
  past: { srLabel: "Closed" },
  warning: { srLabel: "Needs attention" },
  future: { srLabel: "Upcoming" },
};

export const BASIS: Record<"solo" | "consolidated", string> = { solo: "Solo", consolidated: "Consolidated" };
