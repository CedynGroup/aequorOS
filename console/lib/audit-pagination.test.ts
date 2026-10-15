import assert from "node:assert/strict";
import test from "node:test";

import { createAuditBrowser, getAudit, type AuditLogItem } from "./api";

test("server bounds keep audited pages stable with the browser clock ahead", async (t) => {
  let serverTime = Date.parse("2026-10-14T12:00:00.000Z");
  t.mock.timers.enable({ apis: ["Date"], now: serverTime + 120_000 });
  const initial = Array.from({ length: 6 }, (_, index): AuditLogItem => ({
    id: String(index),
    operator_email: "staff@example.test",
    auth_mode: "dev",
    action: "tenants.list",
    target_org: "OR-DEMO0001",
    detail: "{}",
    created_at: new Date(serverTime - (index + 1) * 1000).toISOString(),
  }));
  const rows = [...initial];
  const requests: URLSearchParams[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    const params = new URL(url, "https://console.example").searchParams;
    requests.push(params);
    const bound = Math.min(
      serverTime,
      params.has("as_of") ? Date.parse(params.get("as_of")!) : Infinity,
    );
    const matches = rows.filter((row) => Date.parse(row.created_at) < bound);
    const offset = Number(params.get("offset") ?? 0);
    const limit = Number(params.get("limit") ?? 100);
    const page = {
      items: matches.slice(offset, offset + limit),
      total: matches.length,
      as_of: new Date(bound).toISOString(),
    };
    rows.unshift({
      ...initial[0],
      id: `read-${requests.length}`,
      action: "audit.read",
      created_at: new Date(serverTime).toISOString(),
    });
    serverTime += 1000;
    return Response.json(page);
  });
  const browse = createAuditBrowser({ targetOrg: "OR-DEMO0001" });
  const first = await browse({ offset: 0, limit: 3 });
  assert.equal(Date.now() - Date.parse(first.as_of), 120_000);
  const second = await browse({ offset: 3, limit: 3 });
  const previous = await browse({ offset: 0, limit: 3 });
  const resized = await browse({ offset: 0, limit: 2 });
  const retry = await browse({ offset: 0, limit: 2 });
  assert.deepEqual([...first.items, ...second.items], initial);
  assert.equal(second.total, first.total);
  assert.deepEqual(previous, first);
  assert.deepEqual(resized.items, initial.slice(0, 2));
  assert.deepEqual(retry, resized);
  assert.equal(rows.filter((row) => row.action === "audit.read").length, 5);
  assert.ok(
    requests.every((params) => params.get("target_org") === "OR-DEMO0001"),
  );
  assert.deepEqual(
    requests.map((params) => params.get("as_of")),
    [null, first.as_of, first.as_of, first.as_of, first.as_of],
  );
  const refreshed = await createAuditBrowser()({ offset: 0, limit: 3 });
  assert.ok(Date.parse(refreshed.as_of) > Date.parse(first.as_of));
  assert.equal(refreshed.total, initial.length + 5);
  assert.equal(requests.at(-1)?.get("as_of"), null);
});

test("audit date filters and the server browsing bound are independent", async (t) => {
  const requests: URLSearchParams[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    requests.push(new URL(url, "https://console.example").searchParams);
    return Response.json({
      items: [],
      total: 0,
      as_of: "2026-10-14T12:00:00.000Z",
    });
  });
  const asOf = "2026-10-14T12:00:00.000Z";
  for (const to of ["2026-10-10", "2026-10-15"]) {
    await getAudit({
      asOf,
      from: "2026-10-01",
      to,
      action: "audit.",
      targetOrg: "OR-DEMO0001",
    });
  }
  assert.deepEqual(
    requests.map((params) => params.get("to")),
    ["2026-10-10T00:00:00.000Z", "2026-10-15T00:00:00.000Z"],
  );
  assert.ok(requests.every((params) => params.get("as_of") === asOf));
  assert.ok(requests.every((params) => params.get("from") === "2026-10-01"));
  assert.equal(requests[0].get("action"), "audit.");
  assert.equal(requests[0].get("target_org"), "OR-DEMO0001");
});

test("an unsuccessful first audit read can be retried", async (t) => {
  let fail = true;
  t.mock.method(globalThis, "fetch", async () => {
    if (fail) throw new Error("offline");
    return Response.json({
      items: [],
      total: 0,
      as_of: "2026-10-14T12:00:00.000Z",
    });
  });
  const browse = createAuditBrowser();
  await assert.rejects(browse({}), /offline/);
  fail = false;
  assert.deepEqual(await browse({}), {
    items: [],
    total: 0,
    as_of: "2026-10-14T12:00:00.000Z",
  });
});
