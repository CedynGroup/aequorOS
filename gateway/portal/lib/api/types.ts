/**
 * Supervisor API (`/portal/api`) response and request shapes.
 *
 * Copied from `gateway/contract/SPEC.md` Part 3 (and the Part 1/2 types it
 * references). Field names are exact. SPEC writes several shapes inline; they
 * are given names here, and nothing else is added. If SPEC changes, change
 * this file to match it — never the other way round.
 */

// ---------------------------------------------------------------------------
// Conventions
// ---------------------------------------------------------------------------

export type ErrorCode =
  | "invalid_request"
  | "unauthenticated"
  | "certificate_required"
  | "certificate_mismatch"
  | "invalid_client_assertion"
  | "insufficient_scope"
  | "idempotency_key_required"
  | "idempotency_conflict"
  | "signing_key_unknown"
  | "signing_key_revoked"
  | "signature_invalid"
  | "manifest_mismatch"
  | "payload_digest_mismatch"
  | "declaration_digest_mismatch"
  | "institution_mismatch"
  | "return_unknown"
  | "return_version_not_applicable"
  | "schema_invalid"
  | "duplicate_submission"
  | "supersedes_not_current"
  | "amendment_reason_required"
  | "not_found"
  | "forbidden"
  | "rate_limited"
  | "conflict";

export const ERROR_CODES: readonly ErrorCode[] = [
  "invalid_request",
  "unauthenticated",
  "certificate_required",
  "certificate_mismatch",
  "invalid_client_assertion",
  "insufficient_scope",
  "idempotency_key_required",
  "idempotency_conflict",
  "signing_key_unknown",
  "signing_key_revoked",
  "signature_invalid",
  "manifest_mismatch",
  "payload_digest_mismatch",
  "declaration_digest_mismatch",
  "institution_mismatch",
  "return_unknown",
  "return_version_not_applicable",
  "schema_invalid",
  "duplicate_submission",
  "supersedes_not_current",
  "amendment_reason_required",
  "not_found",
  "forbidden",
  "rate_limited",
  "conflict",
];

export type Issue = {
  rule_id: string;
  severity: "error" | "warning";
  pointer: string | null;
  message: string;
};

/** RFC 9457 problem details, as SPEC "Conventions" defines them. */
export type ProblemDetails = {
  type: string;
  title: string;
  status: number;
  code: ErrorCode;
  detail?: string;
  instance?: string;
  issues?: Issue[];
};

// ---------------------------------------------------------------------------
// Part 1 / Part 2 types referenced by Part 3
// ---------------------------------------------------------------------------

export type Cadence =
  | "daily"
  | "weekly"
  | "monthly"
  | "quarterly"
  | "semiannual"
  | "annual";

export type ReturnSummary = {
  code: string;
  name: string;
  cadence: Cadence;
  current_version: string;
  applies_to: string[];
};

export type ReturnVersion = {
  version: string;
  status: "draft" | "published" | "retired";
  first_reporting_date: string;
  last_reporting_date: string | null;
  schema_sha256: string;
  rule_count: number;
  published_at: string | null;
};

export type ReturnDetail = ReturnSummary & { versions: ReturnVersion[] };

export type Basis = "solo" | "consolidated";

export type Obligation = {
  return_code: string;
  return_version: string;
  reporting_date: string;
  basis: Basis;
  due_at: string;
  status: "not_due" | "due" | "filed" | "filed_late" | "overdue";
  receipt_id: string | null;
};

export type SubmissionState =
  | "received"
  | "validating"
  | "rejected"
  | "in_review"
  | "accepted"
  | "queried"
  | "superseded";

export const SUBMISSION_STATES: readonly SubmissionState[] = [
  "received",
  "validating",
  "rejected",
  "in_review",
  "accepted",
  "queried",
  "superseded",
];

export type QueryMessage = {
  id: string;
  author: "supervisor" | "institution" | "system";
  author_label: string;
  body: string;
  created_at: string;
};

export type Rule =
  | { id: string; severity: "error" | "warning"; kind: "sum"; target: string; terms: string[]; tolerance: number; description: string }
  | { id: string; severity: "error" | "warning"; kind: "non_negative"; lines: string[]; description: string }
  | { id: string; severity: "error" | "warning"; kind: "compare"; left: string; op: ">=" | "<=" | "=="; right: string; description: string }
  | { id: string; severity: "error" | "warning"; kind: "cross_equal"; line: string; other_return: string; other_line: string; tolerance: number; description: string }
  | { id: string; severity: "warning"; kind: "variance"; line: string; max_change_pct: number; description: string };

// ---------------------------------------------------------------------------
// Part 3 — Supervisor API
// ---------------------------------------------------------------------------

export type Role = "supervisor" | "senior_supervisor" | "returns_policy" | "access_approver" | "auditor";

export const ROLES: readonly Role[] = ["supervisor", "senior_supervisor", "returns_policy", "access_approver", "auditor"];

export type Me = { subject: string; name: string; email: string; roles: Role[] };

export type Channel = "api" | "portal_upload";

export type SubmissionRow = {
  receipt_id: string;
  received_at: string;
  institution_code: string;
  institution_name: string;
  return_code: string;
  return_name: string;
  reporting_date: string;
  version: number;
  channel: Channel;
  software_declared: string | null;
  validation: { passed: number; total: number; failed: number } | null;
  status: SubmissionState;
  reviewer_name: string | null;
};

// GET /portal/api/overview

export type HeatState = "on_time" | "late" | "outstanding" | "not_due";

export type OverviewTotals = {
  due: number;
  received: number;
  accepted: number;
  outstanding_past_due: number;
  filed_late: number;
  open_queries: number;
  institutions: number;
  returns: number;
};

export type HeatmapCell = { return_code: string; state: HeatState; receipt_id: string | null };

export type HeatmapRow = {
  institution_code: string;
  institution_name: string;
  cells: HeatmapCell[];
  received: number;
  due: number;
};

export type AttentionItem = {
  kind: "outstanding" | "rejected" | "queried" | "late" | "credential";
  when: string;
  title: string;
  detail: string;
  link: { type: "submission" | "institution" | "query" | "onboarding"; ref: string };
};

export type ChannelStats = {
  api_availability_30d: number | null;
  median_receipt_seconds: number | null;
  filings_today: number;
  certificates_expiring_30d: number;
};

export type Overview = {
  reporting_date: string;
  totals: OverviewTotals;
  heatmap: { return_codes: string[]; rows: HeatmapRow[] };
  attention: AttentionItem[];
  channel: ChannelStats;
  latest: SubmissionRow[];
};

// GET /portal/api/submissions

export type SubmissionCountKey =
  | "all"
  | "needs_review"
  | "validating"
  | "rejected"
  | "queried"
  | "accepted";

export type SubmissionList = {
  items: SubmissionRow[];
  next_cursor: string | null;
  counts: Record<SubmissionCountKey, number>;
};

export type SubmissionListParams = {
  status?: string;
  return_code?: string;
  institution_code?: string;
  reporting_date?: string;
  channel?: string;
  q?: string;
  cursor?: string;
  limit?: number;
};

// GET /portal/api/submissions/{receipt_id}

export type SubmissionAction = "accept" | "query" | "request_resubmission" | "assign";

/** One filed line. Figures are exact decimal strings; `null` is a blank cell. */
export type ContentLine = {
  code: string;
  label: string;
  value: string | null;
  previous_version_value: string | null;
  prior_period_value: string | null;
  is_total: boolean;
  changed: boolean;
  /** The section (official sheet or table) the line belongs to. */
  section?: string | null;
};

export type ContentSection = { code: string; title: string; unit: string };

export type SubmissionEvidence = {
  receipt_id: string;
  payload_sha256: string;
  signature: { valid: boolean; alg: string; kid: string; key_registered_at: string; custody_note: string };
  certificate: { valid: boolean; not_after: string };
  receipt_signature: { valid: boolean; kid: string };
  received_at: string;
  ledger_seq: number;
  chain_intact: boolean;
};

export type SubmissionDetail = {
  row: SubmissionRow;
  return_version: string;
  basis: Basis;
  amendment: { supersedes_receipt_id: string; supersedes_received_at: string; reason: string } | null;
  validation: {
    rule_set: string;
    ran_at: string;
    duration_ms: number;
    groups: { label: string; passed: number; total: number; note: string }[];
    issues: Issue[];
  };
  content: { currency: string; unit: string; sections: ContentSection[]; lines: ContentLine[] };
  evidence: SubmissionEvidence;
  declaration: { prepared_by_role: string; approved_by_role: string; approved_at: string; software: string | null };
  institution: {
    code: string;
    name: string;
    type: string;
    supervisor_name: string | null;
    cycle_received: number;
    cycle_due: number;
    outstanding: string[];
  };
  versions: { version: number; receipt_id: string; received_at: string; status: SubmissionState; current: boolean }[];
  activity: { at: string; title: string; detail: string }[];
  allowed_actions: SubmissionAction[];
  /** The Excel, CSV and PDF filed with the return; each digest is in the signed manifest. */
  documents: FiledDocument[];
};

export type FiledDocument = { name: string; media_type: string; sha256: string; bytes: number };

export type AcceptRequest = { note?: string };
export type ResubmissionRequest = { reason: string; due_at: string };
export type AssignRequest = { reviewer_subject: string };
export type CreatedNumber = { number: string };

// GET /portal/api/queries

export type QueryListStatus = "awaiting_institution" | "awaiting_supervisor" | "overdue" | "closed";
export type QueryKind = "query" | "resubmission";
export type QueryFilter = "open" | "awaiting_institution" | "awaiting_supervisor" | "closed";

export type QueryListItem = {
  number: string;
  institution_name: string;
  subject: string;
  status: QueryListStatus;
  kind: QueryKind;
  return_code: string;
  due_at: string | null;
  last_activity_at: string;
};

export type QueryList = {
  items: QueryListItem[];
  counts: Record<"open" | "awaiting_institution" | "awaiting_supervisor" | "closed", number>;
  next_cursor: string | null;
};

export type QueryThreadMessage = QueryMessage & { author_role: string | null };

/**
 * GET /portal/api/queries/{number}. SPEC lists the untyped fields by name
 * (`{ number; subject; kind; status; … }`); their types are taken from the
 * list item and `InstitutionQuery` shapes that carry the same names.
 */
export type QueryDetail = {
  number: string;
  subject: string;
  kind: QueryKind;
  status: QueryListStatus;
  institution_code: string;
  institution_name: string;
  receipt_id: string;
  return_code: string;
  reporting_date: string;
  opened_by: string;
  opened_at: string;
  due_at: string | null;
  delivered_at: string | null;
  acknowledged_at: string | null;
  messages: QueryThreadMessage[];
};

export type CreateQueryRequest = {
  receipt_id: string;
  subject: string;
  body: string;
  kind: QueryKind;
  due_at: string;
};

export type PostQueryMessageRequest = {
  body: string;
  require_resubmission: boolean;
  due_at: string | null;
};

// GET /portal/api/institutions

export type InstitutionStatus = "requested" | "sandbox" | "certified" | "live" | "suspended";

export type InstitutionListItem = {
  code: string;
  name: string;
  type: string;
  status: InstitutionStatus;
  channel: Channel | null;
  software_declared: string | null;
  on_time_12m: number | null;
  outstanding: number;
};

export type InstitutionList = { items: InstitutionListItem[] };

export type InstitutionDetail = {
  code: string;
  name: string;
  type: string;
  channel: Channel | null;
  flags: string[];
  stats: {
    on_time_12m: number;
    peer_median_on_time_12m: number;
    outstanding: number;
    late_this_year: number;
    filings_this_year: number;
    open_queries: number;
    last_filing: { at: string; label: string } | null;
  };
  history: {
    months: string[];
    rows: { return_code: string; cells: { reporting_date: string; state: HeatState; receipt_id: string | null }[] }[];
  };
  obligations: Obligation[];
  contacts: { role: string; name: string }[];
  credentials: {
    portal_users: number;
    mfa: boolean;
    api_client: { client_id: string; certificate_not_after: string } | null;
    signing_keys: { kid: string; status: string }[];
  };
  supervisor_name: string | null;
  notes: { id: string; author_name: string; body: string; created_at: string }[];
};

export type CreateNoteRequest = { body: string };

// GET /portal/api/calendar

export type CalendarTone = "past" | "warning" | "future";

export type CalendarEvent = {
  title: string;
  detail: string;
  tone: CalendarTone;
  received: number | null;
  due: number;
};

export type CalendarMonth = {
  month: string;
  days: { date: string; events: CalendarEvent[] }[];
  upcoming: { label: string; count_label: string; detail: string }[];
  cadences: { cadence: string; returns: number }[];
};

// Return definitions

export type ReturnListItem = ReturnSummary & { rule_count: number; status: "published" | "draft_pending" };
export type ReturnList = { items: ReturnListItem[] };

export type ReturnDraftApproval = {
  step: "drafted" | "approved" | "pending";
  by: string | null;
  role_label: string;
  at: string | null;
};

export type ReturnDraft = {
  version: string;
  change_summary: string;
  first_reporting_date: string;
  notice_days: number;
  breaking: boolean;
  approvals: ReturnDraftApproval[];
};

export type ReturnDefinitionDetail = ReturnDetail & { draft: ReturnDraft | null };

export type CreateDraftRequest = {
  version: string;
  change_summary: string;
  first_reporting_date: string;
  notice_days: number;
  schema: object;
  rules: Rule[];
};

export type ReturnVersionReturnRequest = { reason: string };

// The channel register (read-only: enrolment belongs to the channel operator, SPEC Part 5)

export type OnboardingStage = "requested" | "sandbox" | "certified" | "live";

export type OnboardingRegisterRow = {
  code: string;
  name: string;
  type: string;
  channel: Channel | null;
  software_declared: string | null;
  enrolled_at: string;
  client_id: string | null;
  certificate_not_after: string | null;
  certificate_expiring: boolean;
  portal_users: number;
  status: string;
};

export type OnboardingOverview = {
  stages: { stage: OnboardingStage; count: number }[];
  register: OnboardingRegisterRow[];
};


// Audit

export type AuditListParams = {
  actor_type?: string;
  category?: string;
  institution_code?: string;
  from?: string;
  to?: string;
  cursor?: string;
};

export type AuditListItem = {
  seq: number;
  occurred_at: string;
  actor_label: string;
  actor_via: string;
  category: string;
  action: string;
  object_ref: string;
};

export type AuditList = {
  integrity: { intact: boolean; entries: number; verified_at: string; head_hash: string };
  siem: { streaming: boolean; lag_seconds: number | null };
  retention_label: string;
  items: AuditListItem[];
  next_cursor: string | null;
};

export type AuditEntry = AuditListItem & {
  detail: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
  signature_kid: string;
  signature_valid: boolean;
};

export type AuditVerification = {
  intact: boolean;
  entries: number;
  verified_at: string;
  first_bad_seq: number | null;
};

/** The signed export bundle (`application/zip`) streamed back to the browser. */
export type AuditExport = { body: ArrayBuffer; filename: string; content_type: string };

/** A file the gateway serves for download, passed through unchanged. */
export type FileDownload = AuditExport;

// Access

export type AccessOverview = {
  roles: { group: string; role: Role; can: string; members: number | null }[];
  pending: {
    number: string;
    operator_name: string;
    operator_title: string;
    scope: string;
    excludes: string;
    reason: string;
    incident_ref: string | null;
    requested_at: string;
    expires_at: string;
  }[];
  sessions: { number: string; operator_name: string; status: "active" | "ended" | "denied" | "expired"; detail: string }[];
};

export type AccessDuration = 30 | 60 | 120;
export type ApproveAccessRequest = { duration_minutes: AccessDuration };
export type DenyAccessRequest = { reason: string };

// ---------------------------------------------------------------------------
// The client surface both the real client and the mock implement.
// ---------------------------------------------------------------------------

export interface GatewayApi {
  me(): Promise<Me>;
  overview(params?: { reporting_date?: string }): Promise<Overview>;
  listSubmissions(params?: SubmissionListParams): Promise<SubmissionList>;
  getSubmission(receiptId: string): Promise<SubmissionDetail>;
  acceptSubmission(receiptId: string, body: AcceptRequest): Promise<void>;
  requestResubmission(receiptId: string, body: ResubmissionRequest): Promise<CreatedNumber>;
  assignSubmission(receiptId: string, body: AssignRequest): Promise<void>;
  /** The filing's signed receipt, as the institution's own API returns it. */
  downloadReceipt(receiptId: string): Promise<FileDownload>;
  /** The filing as received, verifiable without the gateway (zip). */
  downloadFiledPackage(receiptId: string): Promise<FileDownload>;
  /** One document filed with the return, byte for byte. */
  downloadDocument(receiptId: string, name: string): Promise<FileDownload>;
  listQueries(params?: { status?: QueryFilter; cursor?: string }): Promise<QueryList>;
  getQuery(number: string): Promise<QueryDetail>;
  createQuery(body: CreateQueryRequest): Promise<CreatedNumber>;
  postQueryMessage(number: string, body: PostQueryMessageRequest): Promise<QueryMessage>;
  closeQuery(number: string): Promise<void>;
  listInstitutions(params?: { q?: string }): Promise<InstitutionList>;
  getInstitution(code: string): Promise<InstitutionDetail>;
  addInstitutionNote(code: string, body: CreateNoteRequest): Promise<void>;
  calendar(params?: { month?: string }): Promise<CalendarMonth>;
  listReturns(): Promise<ReturnList>;
  getReturn(code: string): Promise<ReturnDefinitionDetail>;
  createReturnDraft(code: string, body: CreateDraftRequest): Promise<void>;
  approveReturnVersion(code: string, version: string): Promise<void>;
  returnReturnVersion(code: string, version: string, body: ReturnVersionReturnRequest): Promise<void>;
  /** Read-only. BoG staff never enrol institutions or manage credentials; the channel operator does (`/ops/api`). */
  onboarding(): Promise<OnboardingOverview>;
  listAudit(params?: AuditListParams): Promise<AuditList>;
  getAuditEntry(seq: number): Promise<AuditEntry>;
  verifyAudit(): Promise<AuditVerification>;
  exportAudit(params?: { from?: string; to?: string }): Promise<AuditExport>;
  access(): Promise<AccessOverview>;
  approveAccess(number: string, body: ApproveAccessRequest): Promise<void>;
  denyAccess(number: string, body: DenyAccessRequest): Promise<void>;
}
