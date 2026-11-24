"use server";

/**
 * Server actions for every supervisor decision. Each one is an untrusted
 * entry point: it re-reads the session (through `getApi`), validates its
 * input and leaves authorization to the gateway API, whose refusals come
 * back as plain-language messages.
 */

import { revalidatePath } from "next/cache";
import { redirect, unstable_rethrow } from "next/navigation";
import type { ActionState } from "@/lib/action-state";
import { friendlyMessage } from "@/lib/api/errors";
import type { AccessDuration, QueryKind, Rule } from "@/lib/api/types";
import { formatNumber, formatTime } from "@/lib/format";
import { ACCESS_NUMBER, DATE, INSTITUTION_CODE, QUERY_NUMBER, RECEIPT_ID } from "@/lib/search-params";
import { getApi, getMe } from "@/lib/server/api";

class InputError extends Error {}

function error(message: string): ActionState {
  return { status: "error", message, at: Date.now() };
}

function success(message: string, reset = false): ActionState {
  return { status: "success", message, at: Date.now(), reset };
}

function text(form: FormData, name: string, label: string, options: { required?: boolean; max?: number } = {}): string {
  const raw = form.get(name);
  const value = typeof raw === "string" ? raw.trim() : "";
  if (options.required && !value) throw new InputError(`${label} is needed.`);
  if (value.length > (options.max ?? 4000)) throw new InputError(`${label} is too long (${formatNumber(options.max ?? 4000)} characters at most).`);
  return value;
}

/** A reply-due date from a date input, as 17:00 GMT that day; never in the past. */
function dueAt(form: FormData, name: string, label: string): string {
  const value = text(form, name, label, { required: true, max: 10 });
  if (!DATE.test(value)) throw new InputError(`${label} must be a date.`);
  const today = new Date().toISOString().slice(0, 10);
  if (value < today) throw new InputError(`${label} cannot be in the past.`);
  return `${value}T17:00:00.000Z`;
}

function check(pattern: RegExp, value: string) {
  if (!pattern.test(value)) throw new InputError("That record reference is not valid.");
}

/** Runs the API call; input problems and API refusals become an error state. */
async function attempt(work: () => Promise<void>): Promise<ActionState | null> {
  try {
    await work();
    return null;
  } catch (cause) {
    // Next's own control flow (a redirect for an ended session) must propagate.
    unstable_rethrow(cause);
    if (cause instanceof InputError) return error(cause.message);
    return error(friendlyMessage(cause));
  }
}

function refresh() {
  revalidatePath("/", "layout");
}

// ---------------------------------------------------------------------------
// Filings
// ---------------------------------------------------------------------------

export async function acceptFiling(receiptId: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const failed = await attempt(async () => {
    check(RECEIPT_ID, receiptId);
    const note = text(form, "note", "The note", { max: 2000 });
    await (await getApi()).acceptSubmission(receiptId, note ? { note } : {});
  });
  if (failed) return failed;
  refresh();
  redirect(`/submissions/${receiptId}?notice=accepted`);
}

export async function requestResubmission(receiptId: string, _state: ActionState, form: FormData): Promise<ActionState> {
  let number = "";
  const failed = await attempt(async () => {
    check(RECEIPT_ID, receiptId);
    const reason = text(form, "reason", "A reason for the institution", { required: true });
    const due = dueAt(form, "due", "The due date");
    number = (await (await getApi()).requestResubmission(receiptId, { reason, due_at: due })).number;
  });
  if (failed) return failed;
  refresh();
  redirect(`/submissions/${receiptId}?notice=resubmission_requested${QUERY_NUMBER.test(number) ? `&ref=${number}` : ""}`);
}

export async function assignToMe(receiptId: string, _state: ActionState): Promise<ActionState> {
  void _state;
  const failed = await attempt(async () => {
    check(RECEIPT_ID, receiptId);
    const me = await getMe();
    await (await getApi()).assignSubmission(receiptId, { reviewer_subject: me.subject });
  });
  if (failed) return failed;
  refresh();
  redirect(`/submissions/${receiptId}?notice=assigned`);
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export async function raiseQuery(_state: ActionState, form: FormData): Promise<ActionState> {
  let number = "";
  const failed = await attempt(async () => {
    const receiptId = text(form, "receipt_id", "The receipt", { required: true, max: 20 }).toUpperCase();
    if (!RECEIPT_ID.test(receiptId)) throw new InputError("Enter a receipt like SRG-26-0114806.");
    const kind: QueryKind = form.get("kind") === "resubmission" ? "resubmission" : "query";
    const subject = text(form, "subject", "A subject", { required: true, max: 200 });
    const body = text(form, "body", "A message to the institution", { required: true });
    const due = dueAt(form, "due", "The reply-due date");
    number = (await (await getApi()).createQuery({ receipt_id: receiptId, subject, body, kind, due_at: due })).number;
  });
  if (failed) return failed;
  refresh();
  redirect(QUERY_NUMBER.test(number) ? `/queries/${number}?notice=query_sent` : "/queries?notice=query_sent");
}

/** One form, two outcomes: send a message, or close the query. */
export async function queryReply(queryNumber: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const intent = form.get("intent") === "close" ? "close" : "send";
  const failed = await attempt(async () => {
    check(QUERY_NUMBER, queryNumber);
    const api = await getApi();
    if (intent === "close") {
      await api.closeQuery(queryNumber);
      return;
    }
    const body = text(form, "body", "A message to the institution", { required: true });
    const requireResubmission = form.get("require_resubmission") === "on";
    const due = form.get("due") ? dueAt(form, "due", "The reply-due date") : null;
    await api.postQueryMessage(queryNumber, { body, require_resubmission: requireResubmission, due_at: due });
  });
  if (failed) return failed;
  refresh();
  if (intent === "close") redirect(`/queries/${queryNumber}?notice=query_closed`);
  return success("Message sent to the institution.", true);
}

// ---------------------------------------------------------------------------
// Institutions
// ---------------------------------------------------------------------------

export async function addNote(code: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const failed = await attempt(async () => {
    check(INSTITUTION_CODE, code);
    const body = text(form, "body", "The note", { required: true, max: 4000 });
    await (await getApi()).addInstitutionNote(code, { body });
  });
  if (failed) return failed;
  refresh();
  return success("Supervisory note added.", true);
}

// ---------------------------------------------------------------------------
// Return definitions
// ---------------------------------------------------------------------------

const VERSION = /^\d{4}\.\d{1,3}$/;
const CODE = /^[A-Z0-9][A-Z0-9-]{0,31}$/;

/** One form, two outcomes: approve the draft version, or return it to the drafter. */
export async function decideReturnVersion(code: string, version: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const intent = form.get("intent") === "return" ? "return" : "approve";
  let published = false;
  const failed = await attempt(async () => {
    check(CODE, code);
    check(VERSION, version);
    const api = await getApi();
    if (intent === "return") {
      const reason = text(form, "reason", "A reason for returning the draft", { required: true });
      await api.returnReturnVersion(code, version, { reason });
      return;
    }
    await api.approveReturnVersion(code, version);
    const after = await api.getReturn(code);
    published = after.draft?.version !== version;
  });
  if (failed) return failed;
  refresh();
  redirect(`/returns/${code}?notice=${intent === "return" ? "return_returned" : published ? "return_published" : "return_approved"}`);
}

export async function draftReturnVersion(code: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const failed = await attempt(async () => {
    check(CODE, code);
    const version = text(form, "version", "The version", { required: true, max: 12 });
    if (!VERSION.test(version)) throw new InputError("The version must look like 2026.2.");
    const changeSummary = text(form, "change_summary", "A summary of the change", { required: true, max: 500 });
    const firstReportingDate = text(form, "first_reporting_date", "The first reporting date", { required: true, max: 10 });
    if (!DATE.test(firstReportingDate)) throw new InputError("The first reporting date must be a date.");
    const noticeDays = Number(text(form, "notice_days", "The notice period", { required: true, max: 4 }));
    if (!Number.isInteger(noticeDays) || noticeDays < 0 || noticeDays > 365) throw new InputError("The notice period must be a whole number of days, up to 365.");
    let schema: unknown;
    let rules: unknown;
    try {
      schema = JSON.parse(text(form, "schema", "The schema", { required: true, max: 500_000 }));
    } catch (cause) {
      if (cause instanceof InputError) throw cause;
      throw new InputError("The schema is not valid JSON.");
    }
    try {
      rules = JSON.parse(text(form, "rules", "The rules", { required: true, max: 500_000 }));
    } catch (cause) {
      if (cause instanceof InputError) throw cause;
      throw new InputError("The rules are not valid JSON.");
    }
    if (typeof schema !== "object" || schema === null || Array.isArray(schema)) throw new InputError("The schema must be a JSON object.");
    if (!Array.isArray(rules)) throw new InputError("The rules must be a JSON list.");
    await (await getApi()).createReturnDraft(code, {
      version,
      change_summary: changeSummary,
      first_reporting_date: firstReportingDate,
      notice_days: noticeDays,
      schema,
      rules: rules as Rule[],
    });
  });
  if (failed) return failed;
  refresh();
  redirect(`/returns/${code}?notice=draft_created`);
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export async function verifyChain(_state: ActionState): Promise<ActionState> {
  void _state;
  let message = "";
  let intact = true;
  const failed = await attempt(async () => {
    const result = await (await getApi()).verifyAudit();
    intact = result.intact;
    message = result.intact
      ? `Chain intact · ${formatNumber(result.entries)} entries checked at ${formatTime(result.verified_at, { seconds: true })}.`
      : `Chain broken at entry ${formatNumber(result.first_bad_seq ?? 0)}. Report this to internal audit now.`;
  });
  if (failed) return failed;
  refresh();
  return intact ? success(message) : error(message);
}

// ---------------------------------------------------------------------------
// Operator access
// ---------------------------------------------------------------------------

/** One form, two outcomes: approve the operator session for a duration, or deny it. */
export async function decideAccess(requestNumber: string, _state: ActionState, form: FormData): Promise<ActionState> {
  const intent = form.get("intent") === "deny" ? "deny" : "approve";
  const failed = await attempt(async () => {
    check(ACCESS_NUMBER, requestNumber);
    const api = await getApi();
    if (intent === "deny") {
      const reason = text(form, "reason", "A reason for denying", { required: true, max: 1000 });
      await api.denyAccess(requestNumber, { reason });
      return;
    }
    const minutes = Number(form.get("duration"));
    if (![30, 60, 120].includes(minutes)) throw new InputError("Choose how long to approve the session for.");
    await api.approveAccess(requestNumber, { duration_minutes: minutes as AccessDuration });
  });
  if (failed) return failed;
  refresh();
  redirect(`/access?notice=${intent === "deny" ? "access_denied" : "access_approved"}`);
}
