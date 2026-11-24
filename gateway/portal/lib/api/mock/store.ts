/**
 * Mock mode (`GATEWAY_PORTAL_MOCK=1`): an in-memory gateway that serves the
 * design's fixtures through the same `GatewayApi` interface as the HTTP
 * client, so every screen renders without the API. It emulates the API's
 * role scoping (`allowed_actions`, refusals for roles that may not decide)
 * and the separation-of-duties refusals, so the portal's error paths can be
 * exercised too. State lives for the life of the server process.
 */

import { GatewayApiError } from "../errors";
import { formatTime, shortPersonName } from "../../format";
import { createZip } from "../../zip";
import type {
  AccessOverview,
  AuditEntry,
  AuditList,
  AuditListItem,
  GatewayApi,
  InstitutionDetail,
  InstitutionListItem,
  Me,
  OnboardingOverview,
  Overview,
  QueryMessage,
  ReturnDefinitionDetail,
  ReturnListItem,
  Role,
  SubmissionAction,
  SubmissionDetail,
  SubmissionList,
  SubmissionRow,
} from "../types";
import {
  AUDIT_ACTOR_TYPE,
  AUDIT_INSTITUTION,
  INBOX_COUNTS,
  QUERY_COUNTS,
  buildAccess,
  buildAuditEntry,
  buildAuditList,
  buildCalendar,
  buildHeatmap,
  buildInboxRows,
  buildInstitutionDetail,
  buildInstitutionList,
  buildOnboarding,
  buildOverview,
  buildQueries,
  buildReturnDetail,
  buildReturns,
  buildSubmissionDetail,
  sha256Hex,
  type MockQuery,
} from "./fixtures";

type MockAuditItem = AuditListItem & { actor_type: string; institution_code: string | null };

export type MockState = {
  rows: SubmissionRow[];
  details: Map<string, SubmissionDetail>;
  inboxCounts: SubmissionList["counts"];
  overview: Overview;
  queries: MockQuery[];
  queryCounts: typeof QUERY_COUNTS;
  institutions: InstitutionListItem[];
  institutionDetails: Map<string, InstitutionDetail>;
  returns: ReturnListItem[];
  returnDetails: Map<string, ReturnDefinitionDetail>;
  onboarding: OnboardingOverview;
  audit: Omit<AuditList, "items"> & { items: MockAuditItem[] };
  auditDetails: Map<number, AuditEntry>;
  access: AccessOverview;
  nextQuery: number;
};

export function createMockState(): MockState {
  const inbox = buildInboxRows();
  const { heatmap, generated } = buildHeatmap(inbox);
  const rows = [...inbox, ...generated];
  const audit = buildAuditList();
  return {
    rows,
    details: new Map(),
    inboxCounts: { ...INBOX_COUNTS },
    overview: buildOverview(inbox, heatmap),
    queries: buildQueries(heatmap),
    queryCounts: { ...QUERY_COUNTS },
    institutions: buildInstitutionList(heatmap),
    institutionDetails: new Map(),
    returns: buildReturns(),
    returnDetails: new Map(),
    onboarding: buildOnboarding(),
    audit: {
      ...audit,
      items: audit.items.map((item) => ({
        ...item,
        actor_type: AUDIT_ACTOR_TYPE[item.seq] ?? "staff",
        institution_code: AUDIT_INSTITUTION[item.seq] ?? null,
      })),
    },
    auditDetails: new Map(),
    access: buildAccess(),
    nextQuery: 319,
  };
}

const STATE_KEY = Symbol.for("srg.portal.mockState");

/** One state per server process (survives dev-server module reloads). */
export function sharedMockState(): MockState {
  const holder = globalThis as unknown as Record<symbol, MockState | undefined>;
  holder[STATE_KEY] ??= createMockState();
  return holder[STATE_KEY];
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function forbidden(detail: string): GatewayApiError {
  return new GatewayApiError({ status: 403, code: "forbidden", title: "Forbidden", detail });
}
function notFound(): GatewayApiError {
  return new GatewayApiError({ status: 404, code: "not_found", title: "Not found" });
}
function conflict(detail: string): GatewayApiError {
  return new GatewayApiError({ status: 409, code: "conflict", title: "Conflict", detail });
}
function invalid(detail: string): GatewayApiError {
  return new GatewayApiError({ status: 400, code: "invalid_request", title: "Invalid request", detail });
}

const OPEN_STATES = new Set(["received", "in_review", "queried"]);

export function createMockGateway(viewer: Me, state: MockState = sharedMockState(), now: () => Date = () => new Date()): GatewayApi {
  const has = (...roles: Role[]) => roles.some((role) => viewer.roles.includes(role));
  const short = shortPersonName(viewer.name);
  const supervises = () => has("supervisor", "senior_supervisor");

  function requireSupervisor() {
    if (!supervises()) throw forbidden("Only supervisors can take decisions on filings and queries.");
  }

  function record(category: string, action: string, object_ref: string, institution_code: string | null) {
    const seq = state.audit.integrity.entries + 1;
    state.audit.integrity.entries = seq;
    const head = sha256Hex(`entry:${seq}`);
    state.audit.integrity.head_hash = head;
    state.audit.items.unshift({
      seq,
      occurred_at: now().toISOString(),
      actor_label: short,
      actor_via: "Portal · BoG SSO",
      category,
      action,
      object_ref,
      actor_type: "staff",
      institution_code,
    });
  }

  function findRow(receiptId: string): SubmissionRow {
    const found = state.rows.find((r) => r.receipt_id === receiptId);
    if (!found) throw notFound();
    return found;
  }

  function detailFor(receiptId: string): SubmissionDetail {
    const r = findRow(receiptId);
    let detail = state.details.get(receiptId);
    if (!detail) {
      detail = buildSubmissionDetail(r, state.rows, state.institutions.find((i) => i.code === r.institution_code));
      state.details.set(receiptId, detail);
    }
    detail.row = r;
    detail.versions = detail.versions.map((ver) => {
      const current = state.rows.find((x) => x.receipt_id === ver.receipt_id);
      return current ? { ...ver, status: current.status } : ver;
    });
    return detail;
  }

  function allowedActions(detail: SubmissionDetail): SubmissionAction[] {
    if (!supervises()) return [];
    const senior = has("senior_supervisor");
    if (!senior && detail.institution.supervisor_name !== short) return [];
    const status = detail.row.status;
    const actions: SubmissionAction[] = [];
    if (OPEN_STATES.has(status)) actions.push("accept", "query", "request_resubmission");
    else if (status === "rejected") actions.push("query");
    if (senior && status !== "superseded" && status !== "accepted") actions.push("assign");
    return actions;
  }

  function moveCount(from: string, to: keyof SubmissionList["counts"] | null) {
    const counts = state.inboxCounts;
    if (from === "in_review" || from === "received") counts.needs_review = Math.max(0, counts.needs_review - 1);
    if (from === "queried") counts.queried = Math.max(0, counts.queried - 1);
    if (to) counts[to] += 1;
  }

  function setStatus(r: SubmissionRow, status: SubmissionRow["status"]) {
    const to = status === "accepted" ? "accepted" : status === "queried" ? "queried" : null;
    if (r.status !== status) moveCount(r.status, to);
    r.status = status;
  }

  function findQuery(number: string): MockQuery {
    const q = state.queries.find((x) => x.number === number);
    if (!q) throw notFound();
    return q;
  }

  function newQuery(r: SubmissionRow, input: { subject: string; body: string; kind: MockQuery["kind"]; due_at: string }): string {
    const number = `QRY-2026-${String(state.nextQuery).padStart(4, "0")}`;
    state.nextQuery += 1;
    const at = now().toISOString();
    state.queries.unshift({
      number,
      subject: input.subject,
      kind: input.kind,
      status: "awaiting_institution",
      institution_code: r.institution_code,
      institution_name: r.institution_name,
      receipt_id: r.receipt_id,
      return_code: r.return_code,
      reporting_date: r.reporting_date,
      opened_by: short,
      opened_at: at,
      due_at: input.due_at,
      delivered_at: at,
      acknowledged_at: null,
      messages: [
        { id: `${number}-m1`, author: "supervisor", author_label: viewer.name, author_role: "Supervisor", body: input.body, created_at: at },
        { id: `${number}-m2`, author: "system", author_label: "Gateway", author_role: null, body: "Delivered to the institution through the API and its portal inbox", created_at: at },
      ],
      last_activity_at: at,
    });
    state.queryCounts.open += 1;
    state.queryCounts.awaiting_institution += 1;
    if (OPEN_STATES.has(r.status) && r.status !== "queried") setStatus(r, "queried");
    const detail = state.details.get(r.receipt_id);
    detail?.activity.unshift({ at, title: `${input.kind === "resubmission" ? "Resubmission requested" : "Query raised"} by ${short}`, detail: `${number} · ${input.subject}` });
    record("query", input.kind === "resubmission" ? "Requested resubmission" : "Opened query", number, r.institution_code);
    return number;
  }

  function returnItem(code: string): ReturnListItem {
    const item = state.returns.find((x) => x.code === code);
    if (!item) throw notFound();
    return item;
  }

  function returnDetail(code: string): ReturnDefinitionDetail {
    const item = returnItem(code);
    let detail = state.returnDetails.get(code);
    if (!detail) {
      detail = buildReturnDetail(item);
      state.returnDetails.set(code, detail);
    }
    return detail;
  }

  function institutionItem(code: string): InstitutionListItem {
    const item = state.institutions.find((i) => i.code === code);
    if (!item) throw notFound();
    return item;
  }

  function institutionDetail(code: string): InstitutionDetail {
    const item = institutionItem(code);
    let detail = state.institutionDetails.get(code);
    if (!detail) {
      const open = state.queries.filter((q) => q.institution_code === code && q.status !== "closed").length;
      detail = buildInstitutionDetail(item, state.overview.heatmap, state.rows, open);
      state.institutionDetails.set(code, detail);
    }
    return detail;
  }

  const api: GatewayApi = {
    async me() {
      return clone(viewer);
    },

    async overview() {
      const o = state.overview;
      o.latest = o.latest.map((l) => state.rows.find((r) => r.receipt_id === l.receipt_id) ?? l);
      o.totals.open_queries = state.queryCounts.open;
      return clone(o);
    },

    async listSubmissions(params = {}) {
      const q = params.q?.toLowerCase();
      const filtered = state.rows.filter(
        (r) =>
          (!q || r.institution_name.toLowerCase().includes(q) || r.receipt_id.toLowerCase().includes(q) || r.return_code.toLowerCase().includes(q)) &&
          (!params.return_code || r.return_code === params.return_code) &&
          (!params.institution_code || r.institution_code === params.institution_code) &&
          (!params.reporting_date || r.reporting_date === params.reporting_date) &&
          (!params.channel || r.channel === params.channel),
      );
      const byStatus = (status: string | undefined) =>
        filtered.filter((r) => {
          if (!status || status === "all") return true;
          if (status === "needs_review") return r.status === "in_review" || r.status === "received";
          return r.status === status;
        });
      const narrowed = Boolean(q || params.return_code || params.institution_code || params.reporting_date || params.channel);
      const counts: SubmissionList["counts"] = narrowed
        ? {
            all: filtered.length,
            needs_review: byStatus("needs_review").length,
            validating: byStatus("validating").length,
            rejected: byStatus("rejected").length,
            queried: byStatus("queried").length,
            accepted: byStatus("accepted").length,
          }
        : { ...state.inboxCounts };
      const items = byStatus(params.status).sort((a, b) => b.received_at.localeCompare(a.received_at));
      const limit = Math.min(Math.max(params.limit ?? 50, 1), 200);
      const offset = params.cursor?.startsWith("o:") ? Number.parseInt(params.cursor.slice(2), 10) || 0 : 0;
      const page = items.slice(offset, offset + limit);
      return { items: clone(page), next_cursor: offset + limit < items.length ? `o:${offset + limit}` : null, counts };
    },

    async getSubmission(receiptId) {
      const detail = detailFor(receiptId);
      return { ...clone(detail), allowed_actions: allowedActions(detail) };
    },

    async downloadReceipt(receiptId) {
      const detail = detailFor(receiptId);
      const ev = detail.evidence;
      const receipt = {
        receipt: { receipt_id: ev.receipt_id, institution_code: detail.row.institution_code, return_code: detail.row.return_code, reporting_date: detail.row.reporting_date, version: detail.row.version, payload_sha256: ev.payload_sha256, signing_kid: ev.signature.kid, received_at: ev.received_at, ledger_seq: ev.ledger_seq },
        receipt_jws: null,
        status: detail.row.status,
        note: "Mock mode: no gateway receipt key is held, so this receipt is unsigned.",
      };
      record("view", "Downloaded receipt", receiptId, detail.row.institution_code);
      const body = new TextEncoder().encode(JSON.stringify(receipt, null, 2) + "\n");
      return { body: body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer, filename: `${receiptId}-receipt.json`, content_type: "application/json" };
    },

    async downloadFiledPackage(receiptId) {
      const detail = detailFor(receiptId);
      const zip = createZip([
        { name: "payload.json", content: JSON.stringify({ lines: detail.content.lines.map((l) => ({ code: l.code, value: l.value })) }, null, 2) + "\n" },
        { name: "index.json", content: JSON.stringify({ receipt_id: receiptId, signed: false, mode: "mock" }, null, 2) + "\n" },
        { name: "VERIFY.txt", content: "Mock mode: synthetic data, nothing here is signed.\n" },
      ]);
      record("view", "Downloaded filed package", receiptId, detail.row.institution_code);
      const body = zip.buffer.slice(zip.byteOffset, zip.byteOffset + zip.byteLength) as ArrayBuffer;
      return { body, filename: `${receiptId}-filed-package.zip`, content_type: "application/zip" };
    },

    async downloadDocument(receiptId, name) {
      detailFor(receiptId);
      throw new GatewayApiError({ status: 404, code: "not_found", title: "Not found", detail: `${receiptId} has no filed document ${name}.` });
    },

    async acceptSubmission(receiptId, body) {
      requireSupervisor();
      const detail = detailFor(receiptId);
      if (!allowedActions(detail).includes("accept")) throw conflict("This filing cannot be accepted in its current state.");
      setStatus(detail.row, "accepted");
      detail.row.reviewer_name = short;
      const at = now().toISOString();
      detail.activity.unshift({ at, title: `Accepted by ${short}`, detail: body.note ? `Note: ${body.note}` : "No note added" });
      record("decision", "Accepted filing", receiptId, detail.row.institution_code);
    },

    async requestResubmission(receiptId, body) {
      requireSupervisor();
      const detail = detailFor(receiptId);
      if (!allowedActions(detail).includes("request_resubmission")) throw conflict("A resubmission cannot be requested for this filing now.");
      if (!body.reason.trim()) throw invalid("Give the institution a reason.");
      const number = newQuery(detail.row, { subject: `Corrected ${detail.row.return_code} needed`, body: body.reason, kind: "resubmission", due_at: body.due_at });
      return { number };
    },

    async assignSubmission(receiptId, body) {
      if (!has("senior_supervisor")) throw forbidden("Only senior supervisors can reassign filings.");
      const detail = detailFor(receiptId);
      const name = body.reviewer_subject === viewer.subject ? short : body.reviewer_subject;
      detail.row.reviewer_name = name;
      detail.activity.unshift({ at: now().toISOString(), title: `Assigned to ${name}`, detail: `By ${short}` });
      record("decision", "Assigned reviewer", receiptId, detail.row.institution_code);
    },

    async listQueries(params = {}) {
      const status = params.status ?? "open";
      const items = state.queries
        .filter((q) => {
          if (status === "open") return q.status !== "closed";
          if (status === "awaiting_institution") return q.status === "awaiting_institution" || q.status === "overdue";
          return q.status === status;
        })
        .sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))
        .map((q) => ({
          number: q.number,
          institution_name: q.institution_name,
          subject: q.subject,
          status: q.status,
          kind: q.kind,
          return_code: q.return_code,
          due_at: q.due_at,
          last_activity_at: q.last_activity_at,
        }));
      return { items, counts: { ...state.queryCounts }, next_cursor: null };
    },

    async getQuery(number) {
      const { last_activity_at, ...detail } = findQuery(number);
      void last_activity_at;
      return clone(detail);
    },

    async createQuery(body) {
      requireSupervisor();
      const r = findRow(body.receipt_id);
      if (!body.subject.trim() || !body.body.trim()) throw invalid("A subject and a message are both needed.");
      return { number: newQuery(r, body) };
    },

    async postQueryMessage(number, body) {
      requireSupervisor();
      const q = findQuery(number);
      if (q.status === "closed") throw conflict("This query is closed.");
      if (!body.body.trim()) throw invalid("Write a message first.");
      const at = now().toISOString();
      const message: QueryMessage & { author_role: string | null } = {
        id: `${number}-m${q.messages.length + 1}`,
        author: "supervisor",
        author_label: viewer.name,
        author_role: "Supervisor",
        body: body.body,
        created_at: at,
      };
      q.messages.push(message);
      if (body.require_resubmission) q.kind = "resubmission";
      if (body.due_at) q.due_at = body.due_at;
      if (q.status === "awaiting_supervisor") {
        state.queryCounts.awaiting_supervisor = Math.max(0, state.queryCounts.awaiting_supervisor - 1);
        state.queryCounts.awaiting_institution += 1;
      }
      q.status = "awaiting_institution";
      q.last_activity_at = at;
      record("query", "Sent query message", number, q.institution_code);
      const { author_role, ...plain } = message;
      void author_role;
      return plain;
    },

    async closeQuery(number) {
      requireSupervisor();
      const q = findQuery(number);
      if (q.status === "closed") throw conflict("This query is already closed.");
      if (q.status === "awaiting_supervisor") state.queryCounts.awaiting_supervisor = Math.max(0, state.queryCounts.awaiting_supervisor - 1);
      else state.queryCounts.awaiting_institution = Math.max(0, state.queryCounts.awaiting_institution - 1);
      state.queryCounts.open = Math.max(0, state.queryCounts.open - 1);
      state.queryCounts.closed += 1;
      const at = now().toISOString();
      q.status = "closed";
      q.last_activity_at = at;
      q.messages.push({ id: `${number}-m${q.messages.length + 1}`, author: "system", author_label: "Gateway", author_role: null, body: `Closed by ${short}`, created_at: at });
      record("query", "Closed query", number, q.institution_code);
    },

    async listInstitutions(params = {}) {
      const q = params.q?.toLowerCase();
      const items = state.institutions.filter((i) => !q || i.name.toLowerCase().includes(q) || i.code.includes(q));
      return { items: clone(items) };
    },

    async getInstitution(code) {
      return clone(institutionDetail(code));
    },

    async addInstitutionNote(code, body) {
      requireSupervisor();
      if (!body.body.trim()) throw invalid("Write the note first.");
      const detail = institutionDetail(code);
      detail.notes.unshift({ id: `note-${code}-${detail.notes.length + 1}`, author_name: short, body: body.body.trim(), created_at: now().toISOString() });
      record("configuration", "Added supervisory note", code, code);
    },

    async calendar(params = {}) {
      const today = now().toISOString().slice(0, 10);
      return buildCalendar(params.month ?? today.slice(0, 7), today);
    },

    async listReturns() {
      return { items: clone(state.returns) };
    },

    async getReturn(code) {
      return clone(returnDetail(code));
    },

    async createReturnDraft(code, body) {
      if (!has("returns_policy")) throw forbidden("Only returns policy officers can draft return definitions.");
      const detail = returnDetail(code);
      if (detail.draft) throw conflict(`Version ${detail.draft.version} is already awaiting approval.`);
      if (detail.versions.some((x) => x.version === body.version)) throw conflict(`Version ${body.version} already exists.`);
      detail.draft = {
        version: body.version,
        change_summary: body.change_summary,
        first_reporting_date: body.first_reporting_date,
        notice_days: body.notice_days,
        breaking: false,
        approvals: [
          { step: "drafted", by: short, role_label: "Policy", at: now().toISOString() },
          { step: "pending", by: null, role_label: "Policy", at: null },
          { step: "pending", by: null, role_label: "Supervision", at: null },
        ],
      };
      returnItem(code).status = "draft_pending";
      record("configuration", `Drafted ${code} ${body.version}`, code, null);
    },

    async approveReturnVersion(code, version) {
      if (!has("returns_policy")) throw forbidden("Only returns policy officers can approve return definitions.");
      const detail = returnDetail(code);
      const draft = detail.draft;
      if (!draft || draft.version !== version) throw notFound();
      if (draft.approvals.some((a) => a.step === "drafted" && a.by === short)) {
        throw forbidden("You drafted this version, so a different person must approve it.");
      }
      if (draft.approvals.some((a) => a.step === "approved" && a.by === short)) {
        throw forbidden("You have already approved this version. The other approval must come from someone else.");
      }
      const pending = draft.approvals.find((a) => a.step === "pending");
      if (!pending) throw conflict("This version has no approval outstanding.");
      pending.step = "approved";
      pending.by = short;
      pending.at = now().toISOString();
      record("configuration", `Approved ${code} ${version}`, code, null);
      if (!draft.approvals.some((a) => a.step === "pending")) {
        detail.versions.unshift({
          version,
          status: "published",
          first_reporting_date: draft.first_reporting_date,
          last_reporting_date: null,
          schema_sha256: sha256Hex(`schema:${code}:${version}`),
          rule_count: detail.versions[0]?.rule_count ?? 0,
          published_at: now().toISOString(),
        });
        detail.current_version = version;
        detail.draft = null;
        const item = returnItem(code);
        item.current_version = version;
        item.status = "published";
        record("configuration", `Published ${code} ${version}`, code, null);
      }
    },

    async returnReturnVersion(code, version, body) {
      if (!has("returns_policy")) throw forbidden("Only returns policy officers can return a draft.");
      if (!body.reason.trim()) throw invalid("Say why the draft is being returned.");
      const detail = returnDetail(code);
      if (!detail.draft || detail.draft.version !== version) throw notFound();
      detail.draft = null;
      returnItem(code).status = "published";
      record("configuration", `Returned ${code} ${version} to the drafter`, code, null);
    },

    async onboarding() {
      return clone(state.onboarding);
    },

    async listAudit(params = {}) {
      const items = state.audit.items
        .filter(
          (e) =>
            (!params.actor_type || e.actor_type === params.actor_type) &&
            (!params.category || e.category === params.category) &&
            (!params.institution_code || e.institution_code === params.institution_code) &&
            (!params.from || e.occurred_at.slice(0, 10) >= params.from) &&
            (!params.to || e.occurred_at.slice(0, 10) <= params.to),
        )
        .map(({ actor_type, institution_code, ...item }) => {
          void actor_type;
          void institution_code;
          return item;
        });
      return { integrity: { ...state.audit.integrity }, siem: { ...state.audit.siem }, retention_label: state.audit.retention_label, items, next_cursor: null };
    },

    async getAuditEntry(seq) {
      const found = state.audit.items.find((e) => e.seq === seq);
      if (!found) throw notFound();
      let entry = state.auditDetails.get(seq);
      if (!entry) {
        const { actor_type, institution_code, ...item } = found;
        void actor_type;
        void institution_code;
        entry = buildAuditEntry(item);
        state.auditDetails.set(seq, entry);
      }
      return clone(entry);
    },

    async verifyAudit() {
      const verified_at = now().toISOString();
      state.audit.integrity.verified_at = verified_at;
      return { intact: true, entries: state.audit.integrity.entries, verified_at, first_bad_seq: null };
    },

    async exportAudit(params = {}) {
      const list = await api.listAudit({ from: params.from, to: params.to });
      const entries = list.items.map((item) => JSON.stringify({ ...item, entry_hash: sha256Hex(`entry:${item.seq}`) })).join("\n");
      const zip = createZip([
        { name: "entries.jsonl", content: entries + "\n" },
        { name: "jwks.json", content: JSON.stringify({ keys: [], note: "Mock mode: no audit key is held, so this bundle is unsigned." }, null, 2) },
        {
          name: "manifest.json",
          content: JSON.stringify({ generated_at: now().toISOString(), from: params.from ?? null, to: params.to ?? null, entries: list.items.length, signed: false, mode: "mock" }, null, 2),
        },
      ]);
      const span = [params.from, params.to].filter(Boolean).join("-to-") || "all";
      const body = zip.buffer.slice(zip.byteOffset, zip.byteOffset + zip.byteLength) as ArrayBuffer;
      return { body, filename: `audit-export-${span}.zip`, content_type: "application/zip" };
    },

    async access() {
      return clone(state.access);
    },

    async approveAccess(number, body) {
      if (!has("access_approver")) throw forbidden("Only access approvers can decide operator sessions.");
      const index = state.access.pending.findIndex((p) => p.number === number);
      const request = state.access.pending[index];
      if (!request) throw notFound();
      if (![30, 60, 120].includes(body.duration_minutes)) throw invalid("Choose 30, 60 or 120 minutes.");
      state.access.pending.splice(index, 1);
      state.access.sessions.unshift({ number, operator_name: request.operator_name, status: "active", detail: `Approved by ${short} · ${body.duration_minutes} min from ${formatTime(now())}` });
      record("operator_access", "Approved support session", number, null);
    },

    async denyAccess(number, body) {
      if (!has("access_approver")) throw forbidden("Only access approvers can decide operator sessions.");
      if (!body.reason.trim()) throw invalid("Give a reason for denying the request.");
      const index = state.access.pending.findIndex((p) => p.number === number);
      const request = state.access.pending[index];
      if (!request) throw notFound();
      state.access.pending.splice(index, 1);
      state.access.sessions.unshift({ number, operator_name: request.operator_name, status: "denied", detail: `Denied by ${short} · ${body.reason.trim()}` });
      record("operator_access", "Denied support session", number, null);
    },
  };
  return api;
}
