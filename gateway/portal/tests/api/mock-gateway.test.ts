/** Mock mode emulates the API's role scoping and separation-of-duties refusals. */

import assert from "node:assert/strict";
import test from "node:test";
import { GatewayApiError } from "../../lib/api/errors";
import { createMockGateway, createMockState } from "../../lib/api/mock/store";
import type { Me } from "../../lib/api/types";

const NOW = new Date("2026-11-21T16:45:00.000Z");
const staff = {
  kofi: { subject: "kofi", name: "Kofi Owusu", email: "k@x", roles: ["senior_supervisor"] },
  akosua: { subject: "akosua", name: "Akosua Boateng", email: "a@x", roles: ["supervisor"] },
  esi: { subject: "esi", name: "Esi Asante", email: "e@x", roles: ["returns_policy"] },
  michael: { subject: "michael", name: "Michael Adjei", email: "m@x", roles: ["returns_policy", "access_approver"] },
  audit: { subject: "audit", name: "Internal Audit", email: "i@x", roles: ["auditor"] },
} satisfies Record<string, Me>;

const isCode = (code: string) => (error: unknown) => error instanceof GatewayApiError && error.code === code;

test("auditors see no decisions and are refused if they try", async () => {
  const api = createMockGateway(staff.audit, createMockState(), () => NOW);
  const d = await api.getSubmission("SRG-26-0114806");
  assert.deepEqual(d.allowed_actions, []);
  await assert.rejects(api.acceptSubmission("SRG-26-0114806", {}), isCode("forbidden"));
  await assert.rejects(api.createQuery({ receipt_id: "SRG-26-0114806", subject: "s", body: "b", kind: "query", due_at: "2026-11-24T17:00:00.000Z" }), isCode("forbidden"));
  await assert.rejects(api.approveAccess("SUP-2026-0043", { duration_minutes: 30 }), isCode("forbidden"));
});

test("decisions come from roles, never from a name: an auditor named like a supervisor still decides nothing", async () => {
  const lookalike: Me = { subject: "x", name: "Akosua Boateng", email: "x@x", roles: ["auditor"] };
  const api = createMockGateway(lookalike, createMockState(), () => NOW);
  assert.deepEqual((await api.getSubmission("SRG-26-0114806")).allowed_actions, []);
  await assert.rejects(api.acceptSubmission("SRG-26-0114806", {}), isCode("forbidden"));
});

test("a supervisor decides only for assigned institutions; a senior supervisor for all, and may reassign", async () => {
  const state = createMockState();
  const akosua = createMockGateway(staff.akosua, state, () => NOW);
  assert.deepEqual((await akosua.getSubmission("SRG-26-0114806")).allowed_actions, ["accept", "query", "request_resubmission"], "Palmgrove is hers");
  assert.deepEqual((await akosua.getSubmission("SRG-26-0114702")).allowed_actions, [], "Riverbend is not");
  const kofi = createMockGateway(staff.kofi, state, () => NOW);
  assert.ok((await kofi.getSubmission("SRG-26-0114702")).allowed_actions.includes("assign"));
  assert.deepEqual((await kofi.getSubmission("SRG-26-0114819")).allowed_actions, [], "accepted filings have nothing to decide");
});

test("accepting a filing changes its status, reviewer, activity and the inbox counts", async () => {
  const state = createMockState();
  const api = createMockGateway(staff.kofi, state, () => NOW);
  await api.acceptSubmission("SRG-26-0114806", { note: "Explained in the amendment." });
  const d = await api.getSubmission("SRG-26-0114806");
  assert.equal(d.row.status, "accepted");
  assert.equal(d.row.reviewer_name, "K. Owusu");
  assert.equal(d.activity[0]?.title, "Accepted by K. Owusu");
  const counts = (await api.listSubmissions()).counts;
  assert.equal(counts.needs_review, 20);
  assert.equal(counts.accepted, 539);
  await assert.rejects(api.acceptSubmission("SRG-26-0114806", {}), isCode("conflict"), "cannot accept twice");
  assert.equal((await api.listAudit()).items[0]?.action, "Accepted filing");
});

test("queries: raise, reply and close", async () => {
  const state = createMockState();
  const api = createMockGateway(staff.kofi, state, () => NOW);
  const { number } = await api.createQuery({ receipt_id: "SRG-26-0114577", subject: "NPL write-offs", body: "Explain the write-offs.", kind: "query", due_at: "2026-11-25T17:00:00.000Z" });
  assert.match(number, /^QRY-2026-\d{4}$/);
  assert.equal((await api.getSubmission("SRG-26-0114577")).row.status, "queried");
  const message = await api.postQueryMessage(number, { body: "A reminder.", require_resubmission: true, due_at: null });
  assert.equal(message.author, "supervisor");
  assert.equal("author_role" in message, false, "POST returns a plain QueryMessage");
  assert.equal((await api.getQuery(number)).kind, "resubmission");
  await api.closeQuery(number);
  assert.equal((await api.getQuery(number)).status, "closed");
  await assert.rejects(api.postQueryMessage(number, { body: "x", require_resubmission: false, due_at: null }), isCode("conflict"));
  const { number: resub } = await api.requestResubmission("SRG-26-0114806", { reason: "Correct line 1.3.", due_at: "2026-11-26T17:00:00.000Z" });
  assert.equal((await api.getQuery(resub)).kind, "resubmission");
});

test("return versions need a drafter and two other approvers", async () => {
  const state = createMockState();
  await assert.rejects(createMockGateway(staff.esi, state, () => NOW).approveReturnVersion("BSD5A", "2026.2"), (e: unknown) => isCode("forbidden")(e) && /drafted/.test((e as GatewayApiError).detail ?? ""));
  await assert.rejects(createMockGateway(staff.michael, state, () => NOW).approveReturnVersion("BSD5A", "2026.2"), (e: unknown) => isCode("forbidden")(e) && /already approved/.test((e as GatewayApiError).detail ?? ""));
  await assert.rejects(createMockGateway(staff.kofi, state, () => NOW).approveReturnVersion("BSD5A", "2026.2"), isCode("forbidden"), "not a returns policy officer");
  const michael = createMockGateway(staff.michael, state, () => NOW);
  await assert.rejects(michael.returnReturnVersion("BSD5A", "2026.2", { reason: " " }), isCode("invalid_request"));
  await michael.returnReturnVersion("BSD5A", "2026.2", { reason: "Line 4.7 needs a definition." });
  assert.equal((await michael.getReturn("BSD5A")).draft, null);

  const esi = createMockGateway(staff.esi, state, () => NOW);
  await esi.createReturnDraft("LMT", { version: "2026.2", change_summary: "Tightens Table 4", first_reporting_date: "2027-03-31", notice_days: 60, schema: {}, rules: [] });
  await michael.approveReturnVersion("LMT", "2026.2");
  assert.equal((await michael.getReturn("LMT")).draft?.approvals.filter((a) => a.step === "approved").length, 1);
});

test("operator sessions: only access approvers decide, and a denial needs a reason", async () => {
  const state = createMockState();
  await assert.rejects(createMockGateway(staff.kofi, state, () => NOW).approveAccess("SUP-2026-0043", { duration_minutes: 30 }), isCode("forbidden"));
  const michael = createMockGateway(staff.michael, state, () => NOW);
  await assert.rejects(michael.denyAccess("SUP-2026-0043", { reason: "" }), isCode("invalid_request"));
  await michael.approveAccess("SUP-2026-0043", { duration_minutes: 60 });
  const access = await michael.access();
  assert.equal(access.pending.length, 0);
  assert.deepEqual(access.sessions[0], { number: "SUP-2026-0043", operator_name: "R. Quaye", status: "active", detail: "Approved by M. Adjei · 60 min from 16:45" });
});

test("supervisory notes and chain verification", async () => {
  const state = createMockState();
  const kofi = createMockGateway(staff.kofi, state, () => NOW);
  await kofi.addInstitutionNote("0231", { body: "Remediation plan received." });
  assert.equal((await kofi.getInstitution("0231")).notes[0]?.body, "Remediation plan received.");
  await assert.rejects(createMockGateway(staff.audit, state, () => NOW).addInstitutionNote("0231", { body: "x" }), isCode("forbidden"));
  const result = await createMockGateway(staff.audit, state, () => NOW).verifyAudit();
  assert.equal(result.intact, true);
  assert.equal(result.first_bad_seq, null);
  assert.equal(result.verified_at, NOW.toISOString());
});

test("the mock audit export is a real zip with entries, keys and a manifest", async () => {
  const api = createMockGateway(staff.audit, createMockState(), () => NOW);
  const bundle = await api.exportAudit({ from: "2026-11-21" });
  const bytes = new Uint8Array(bundle.body);
  assert.deepEqual(Array.from(bytes.subarray(0, 4)), [0x50, 0x4b, 0x03, 0x04]);
  const text = Buffer.from(bytes).toString("latin1");
  for (const name of ["entries.jsonl", "jwks.json", "manifest.json"]) assert.ok(text.includes(name), name);
  assert.equal(bundle.content_type, "application/zip");
  assert.equal(bundle.filename, "audit-export-2026-11-21.zip");
});

test("receipt and filed-package downloads are named for the filing and leave a view entry", async () => {
  const api = createMockGateway(staff.audit, createMockState(), () => NOW);
  const receipt = await api.downloadReceipt("SRG-26-0114806");
  assert.equal(receipt.filename, "SRG-26-0114806-receipt.json");
  assert.equal(receipt.content_type, "application/json");
  const body = JSON.parse(new TextDecoder().decode(receipt.body)) as { receipt: { receipt_id: string } };
  assert.equal(body.receipt.receipt_id, "SRG-26-0114806");
  const pack = await api.downloadFiledPackage("SRG-26-0114806");
  assert.equal(pack.filename, "SRG-26-0114806-filed-package.zip");
  assert.equal(pack.content_type, "application/zip");
  assert.deepEqual([...new Uint8Array(pack.body).slice(0, 2)], [0x50, 0x4b]); // "PK": a zip
  const views = (await api.listAudit({ category: "view" })).items.map((e) => e.action);
  assert.ok(views.includes("Downloaded receipt"));
  assert.ok(views.includes("Downloaded filed package"));
});
