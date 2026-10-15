import assert from "node:assert/strict";
import test from "node:test";

import { getAudit } from "./api";

test("audit paging, page sizes and retries retain the browsing time bound", async (t) => {
  const requests: URLSearchParams[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    requests.push(new URL(url, "https://console.example").searchParams);
    return Response.json({ items: [], total: 0 });
  });
  const asOf = "2026-10-14T12:00:00.000Z";
  for (const [offset, limit] of [
    [0, 50],
    [50, 50],
    [0, 50],
    [0, 25],
    [0, 25],
  ]) {
    await getAudit({ asOf, offset, limit });
  }
  assert.deepEqual(
    requests.map((params) => [
      params.get("to"),
      params.get("offset"),
      params.get("limit"),
    ]),
    [
      [asOf, "0", "50"],
      [asOf, "50", "50"],
      [asOf, "0", "50"],
      [asOf, "0", "25"],
      [asOf, "0", "25"],
    ],
  );
});

test("audit date filters cannot extend beyond the browsing time bound", async (t) => {
  const requests: URLSearchParams[] = [];
  t.mock.method(globalThis, "fetch", async (url: string) => {
    requests.push(new URL(url, "https://console.example").searchParams);
    return Response.json({ items: [], total: 0 });
  });
  const asOf = "2026-10-14T12:00:00.000Z";
  const from = "2026-10-01";
  for (const to of ["2026-10-10", "2026-10-15"]) {
    await getAudit({
      asOf,
      from,
      to,
      action: "audit.",
      targetOrg: "OR-DEMO0001",
    });
  }
  await getAudit({ asOf: "2026-10-14T13:00:00.000Z", from });
  assert.deepEqual(
    requests.map((params) => params.get("to")),
    ["2026-10-10T00:00:00.000Z", asOf, "2026-10-14T13:00:00.000Z"],
  );
  assert.ok(requests.every((params) => params.get("from") === from));
  assert.equal(requests[0].get("action"), "audit.");
  assert.equal(requests[0].get("target_org"), "OR-DEMO0001");
});
