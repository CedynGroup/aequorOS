import assert from "node:assert/strict";
import test from "node:test";

import { startInspectorSession } from "./api";
import { inspectorHours, inspectorMinutes } from "./inspector-duration";

test("short and multi-hour sessions retain useful hour precision", () => {
  assert.deepEqual([15, 30, 60, 120].map(inspectorHours), [0.25, 0.5, 1, 2]);
  assert.equal(inspectorHours(90), 1.5);
});

test("every existing five-minute choice survives an hours round trip", () => {
  for (let minutes = 15; minutes <= 120; minutes += 5) {
    assert.equal(inspectorMinutes(inspectorHours(minutes)), minutes);
  }
});

test("hour inputs submit the existing minute-based API contract", async (t) => {
  const submitted: unknown[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (_url: string, init: RequestInit) => {
      submitted.push(JSON.parse(String(init.body)));
      return Response.json({ session_id: "synthetic-session" });
    },
  );
  for (const hours of [0.25, 0.5, 1, 2]) {
    await startInspectorSession({
      organization_id: "OR-DEMO0001",
      reason: "Demo investigation",
      support_reference: "DEMO-123",
      mode: "consent",
      ttl_minutes: inspectorMinutes(hours),
    });
  }
  assert.deepEqual(
    submitted,
    [15, 30, 60, 120].map((ttl_minutes) => ({
      organization_id: "OR-DEMO0001",
      reason: "Demo investigation",
      support_reference: "DEMO-123",
      mode: "consent",
      ttl_minutes,
    })),
  );
});
