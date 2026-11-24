/**
 * Mock fixtures must (a) have the SPEC Part 3 shapes at runtime, not only at
 * compile time, (b) reproduce the design's data, and (c) resolve every link
 * the screens draw, so no mock-mode page leads to a 404.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { createMockGateway, createMockState } from "../../lib/api/mock/store";
import { ROLES, SUBMISSION_STATES, type Me, type SubmissionRow } from "../../lib/api/types";
import { ACCESS_NUMBER, INSTITUTION_CODE, QUERY_NUMBER, RECEIPT_ID } from "../../lib/search-params";

const NOW = new Date("2026-11-21T16:40:00.000Z");
const kofi: Me = { subject: "dev-staff-kofi-owusu", name: "Kofi Owusu", email: "k@staff.example", roles: ["senior_supervisor"] };
const fresh = () => createMockGateway(kofi, createMockState(), () => NOW);
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const SHA256 = /^[0-9a-f]{64}$/;

function assertRow(r: SubmissionRow) {
  assert.match(r.receipt_id, RECEIPT_ID);
  assert.match(r.received_at, RFC3339);
  assert.match(r.reporting_date, DATE);
  assert.match(r.institution_code, INSTITUTION_CODE);
  assert.ok(SUBMISSION_STATES.includes(r.status), r.status);
  assert.ok(r.channel === "api" || r.channel === "portal_upload");
  assert.ok(Number.isInteger(r.version) && r.version >= 1);
  if (r.validation) assert.equal(r.validation.passed + r.validation.failed, r.validation.total, r.receipt_id);
  if (r.channel === "portal_upload") assert.equal(r.software_declared, null);
}

test("the inbox's first page is the design's 14 filings, newest first, with its tab counts", async () => {
  const list = await fresh().listSubmissions({ limit: 14 });
  assert.deepEqual(
    list.items.map((r) => r.receipt_id),
    ["SRG-26-0114822", "SRG-26-0114819", "SRG-26-0114806", "SRG-26-0114771", "SRG-26-0114760", "SRG-26-0114702", "SRG-26-0114655", "SRG-26-0114649", "SRG-26-0114590", "SRG-26-0114577", "SRG-26-0114541", "SRG-26-0114512", "SRG-26-0114480", "SRG-26-0114455"],
  );
  assert.deepEqual(list.counts, { all: 571, needs_review: 21, validating: 4, rejected: 12, queried: 7, accepted: 538 });
  assert.ok(list.next_cursor, "more pages exist");
  list.items.forEach(assertRow);
  const palmgrove = list.items[2];
  assert.equal(palmgrove?.version, 2);
  assert.equal(palmgrove?.reviewer_name, "A. Boateng");
  assert.equal(list.items[3]?.validation?.failed, 3, "Harbourline LMT failed 3 rules");
});

test("every filing on every page has the SubmissionRow shape and pagination covers them once", async () => {
  const api = fresh();
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await api.listSubmissions({ limit: 40, cursor });
    page.items.forEach(assertRow);
    for (const r of page.items) {
      assert.ok(!seen.has(r.receipt_id), `duplicate ${r.receipt_id}`);
      seen.add(r.receipt_id);
    }
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  assert.ok(seen.size > 14);
});

test("the overview carries the design's totals, heatmap and attention items", async () => {
  const o = await fresh().overview();
  assert.equal(o.reporting_date, "2026-10-31");
  assert.deepEqual(o.totals, { due: 612, received: 571, accepted: 538, outstanding_past_due: 41, filed_late: 24, open_queries: 7, institutions: 48, returns: 14 });
  assert.deepEqual(o.heatmap.return_codes, ["BSD2", "BSD3A", "BSD5A", "BSD6", "BSD7A", "BSD8", "BSD13", "CAR-RWA", "LMT", "NPL-MONTHLY"]);
  assert.equal(o.heatmap.rows.length, 12);
  assert.equal(o.heatmap.rows[0]?.institution_name, "Northgate Savings & Loans");
  for (const row of o.heatmap.rows) {
    assert.deepEqual(row.cells.map((c) => c.return_code), o.heatmap.return_codes, "cells follow the column order");
    assert.equal(row.due, row.cells.filter((c) => c.state !== "not_due").length);
    assert.equal(row.received, row.cells.filter((c) => c.state === "on_time" || c.state === "late").length);
    for (const c of row.cells) if (c.state === "on_time" || c.state === "late") assert.match(c.receipt_id ?? "", RECEIPT_ID);
  }
  assert.equal(`${o.heatmap.rows[0]?.received} / ${o.heatmap.rows[0]?.due}`, "6 / 10");
  assert.deepEqual(o.attention.map((a) => a.kind), ["outstanding", "rejected", "queried", "late", "credential"]);
  assert.deepEqual(o.channel, { api_availability_30d: 99.97, median_receipt_seconds: 1.8, filings_today: 214, certificates_expiring_30d: 3 });
  assert.equal(o.latest.length, 8);
  o.latest.forEach(assertRow);
});

test("every link the overview draws resolves in mock mode", async () => {
  const api = fresh();
  const o = await api.overview();
  for (const row of o.heatmap.rows) {
    await api.getInstitution(row.institution_code);
    for (const c of row.cells) if (c.receipt_id) await api.getSubmission(c.receipt_id);
  }
  for (const a of o.attention) {
    if (a.link.type === "submission") await api.getSubmission(a.link.ref);
    if (a.link.type === "query") await api.getQuery(a.link.ref);
    if (a.link.type === "institution" || a.link.type === "onboarding") await api.getInstitution(a.link.ref);
  }
});

test("the designed submission detail matches the design", async () => {
  const d = await fresh().getSubmission("SRG-26-0114806");
  assert.equal(d.row.institution_name, "Palmgrove Bank");
  assert.equal(d.amendment?.supersedes_receipt_id, "SRG-26-0114590");
  assert.deepEqual(d.validation.groups.map((g) => `${g.label} ${g.passed}/${g.total}`), ["Structure 12/12", "Arithmetic 22/22", "Cross-return 14/14", "Period 10/10"]);
  assert.equal(d.validation.duration_ms, 2900);
  assert.equal(d.content.lines.length, 11);
  assert.deepEqual(d.content.lines.filter((l) => l.changed).map((l) => l.code), ["1.2", "1.3"]);
  assert.equal(d.evidence.payload_sha256, "9f2c41ab77e0d5c318be6a904f2d7c11a3e95b0c6d42f8e7205b9ad17c3e6f04");
  assert.equal(d.evidence.ledger_seq, 1_148_206);
  assert.equal(d.institution.code, "0117");
  assert.deepEqual(d.versions.map((v) => [v.version, v.status]), [[2, "in_review"], [1, "superseded"]]);
  assert.deepEqual(d.allowed_actions, ["accept", "query", "request_resubmission", "assign"]);
  for (const line of d.content.lines) assert.match(line.value ?? "", /^-?\d+(\.\d+)?$/);
});

test("queries list in the design's order with the design's counts, and every thread resolves", async () => {
  const api = fresh();
  const list = await api.listQueries({ status: "open" });
  assert.deepEqual(
    list.items.map((q) => q.institution_name),
    ["Riverbend Rural Bank", "Northgate Savings & Loans", "Harbourline Bank", "Kestrel Bank", "Cedarwood Bank", "Summit Mutual Bank", "Savanna Union Bank"],
  );
  assert.deepEqual(list.counts, { open: 7, awaiting_institution: 5, awaiting_supervisor: 2, closed: 64 });
  assert.equal((await api.listQueries({ status: "awaiting_institution" })).items.length, 5);
  for (const item of [...list.items, ...(await api.listQueries({ status: "closed" })).items]) {
    assert.match(item.number, QUERY_NUMBER);
    const q = await api.getQuery(item.number);
    assert.equal("last_activity_at" in q, false, "detail carries only SPEC fields");
    await api.getSubmission(q.receipt_id);
    assert.ok(q.messages.length >= 2);
  }
  const riverbend = await api.getQuery("QRY-2026-0318");
  assert.equal(riverbend.due_at, "2026-11-24T17:00:00.000Z");
  assert.deepEqual(riverbend.messages.map((m) => m.author), ["supervisor", "system", "institution"]);
});

test("institutions, returns, the register, the audit log and access have their designed content", async () => {
  const api = fresh();
  const northgate = await api.getInstitution("0231");
  assert.deepEqual(northgate.flags, ["heightened_monitoring"]);
  assert.equal(northgate.stats.on_time_12m, 71);
  assert.equal(northgate.history.months.length, 11);
  assert.equal(northgate.history.rows.length, 8);
  assert.equal(northgate.credentials.api_client, null);
  for (const i of (await api.listInstitutions()).items) await api.getInstitution(i.code);

  const returns = await api.listReturns();
  assert.equal(returns.items.length, 15);
  assert.deepEqual(returns.items.filter((r) => r.status === "draft_pending").map((r) => r.code), ["BSD5A"]);
  const bsd5a = await api.getReturn("BSD5A");
  assert.equal(bsd5a.draft?.version, "2026.2");
  assert.deepEqual(bsd5a.draft?.approvals.map((a) => a.step), ["drafted", "approved", "pending"]);

  const register = await api.onboarding();
  assert.equal(register.stages.reduce((sum, s) => sum + s.count, 0), 51);
  assert.equal(register.register.length, 9);
  assert.deepEqual(register.register.filter((r) => r.certificate_expiring).map((r) => r.name), ["Kestrel Bank"]);

  const audit = await api.listAudit();
  assert.equal(audit.items.length, 10);
  assert.match(audit.integrity.head_hash, SHA256);
  assert.equal(audit.retention_label, "[Retention period set by BoG]");
  for (const item of audit.items) {
    const entry = await api.getAuditEntry(item.seq);
    assert.match(entry.prev_hash, SHA256);
    assert.match(entry.entry_hash, SHA256);
  }

  const access = await api.access();
  for (const r of access.roles) assert.ok(ROLES.includes(r.role), r.role);
  assert.equal(access.roles.some((r) => r.group === "srg-onboarding"), false, "there is no onboarding role any more");
  for (const p of access.pending) assert.match(p.number, ACCESS_NUMBER);
  for (const s of access.sessions) assert.match(s.number, ACCESS_NUMBER);
});

test("the November 2026 calendar is the design's", async () => {
  const cal = await fresh().calendar({ month: "2026-11" });
  assert.deepEqual(cal.days.map((d) => d.date), ["2026-11-02", "2026-11-09", "2026-11-10", "2026-11-13", "2026-11-16", "2026-11-23", "2026-11-30"]);
  assert.deepEqual(cal.days.find((d) => d.date === "2026-11-13")?.events[0], { title: "Monthly · 31 Oct", detail: "14 returns", tone: "warning", received: 571, due: 612 });
  assert.equal(cal.upcoming.length, 4);
  const other = await fresh().calendar({ month: "2027-02" });
  assert.equal(other.month, "2027-02");
  for (const d of other.days) assert.match(d.date, /^2027-02-\d{2}$/);
});
