import assert from "node:assert/strict";
import test from "node:test";
import { createHttpClient, queryString, type FetchLike } from "../../lib/api/client";
import { GatewayApiError, codeForStatus, friendlyMessage, parseProblem } from "../../lib/api/errors";

type Call = { url: string; init: RequestInit | undefined };

function stub(responder: (call: Call) => Response | Promise<Response>): { fetch: FetchLike; calls: Call[] } {
  const calls: Call[] = [];
  return {
    calls,
    fetch: async (url, init) => {
      const call = { url, init };
      calls.push(call);
      return responder(call);
    },
  };
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

const problem = (status: number, body: Record<string, unknown>) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/problem+json" } });

test("GET calls send the ID token as Bearer to /portal/api and return the body", async () => {
  const s = stub(() => json({ subject: "s", name: "Kofi Owusu", email: "k@x", roles: ["senior_supervisor"] }));
  const api = createHttpClient({ baseUrl: "http://gateway.test/", token: "id-token-123", fetch: s.fetch });
  const me = await api.me();
  assert.equal(me.name, "Kofi Owusu");
  assert.equal(s.calls[0]?.url, "http://gateway.test/portal/api/me");
  const headers = (s.calls[0]?.init?.headers ?? {}) as Record<string, string>;
  assert.equal(headers.Authorization, "Bearer id-token-123");
  assert.match(headers.Accept ?? "", /application\/problem\+json/);
  assert.equal(s.calls[0]?.init?.method, "GET");
  assert.equal(s.calls[0]?.init?.body, undefined);
});

test("query strings drop empty values and path segments are encoded", async () => {
  assert.equal(queryString({ a: "1", b: "", c: undefined, d: null, e: 0 }), "?a=1&e=0");
  assert.equal(queryString({}), "");
  const s = stub(() => json({ items: [], next_cursor: null, counts: { all: 0, needs_review: 0, validating: 0, rejected: 0, queried: 0, accepted: 0 } }));
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: s.fetch });
  await api.listSubmissions({ status: "needs_review", q: "Harbour line", cursor: undefined, limit: 25 });
  assert.equal(s.calls[0]?.url, "http://g/portal/api/submissions?status=needs_review&q=Harbour+line&limit=25");
  await api.getSubmission("SRG-26-0114806/../../x");
  assert.equal(s.calls[1]?.url, "http://g/portal/api/submissions/SRG-26-0114806%2F..%2F..%2Fx");
});

test("POST calls send JSON and accept an empty 204", async () => {
  const s = stub(() => new Response(null, { status: 204 }));
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: s.fetch });
  await api.approveAccess("SUP-2026-0043", { duration_minutes: 60 });
  const call = s.calls[0];
  assert.equal(call?.url, "http://g/portal/api/access/requests/SUP-2026-0043/approve");
  assert.equal(call?.init?.method, "POST");
  assert.equal(((call?.init?.headers ?? {}) as Record<string, string>)["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(String(call?.init?.body)), { duration_minutes: 60 });
});

test("problem+json errors become typed errors with code, detail and issues", async () => {
  const s = stub(() =>
    problem(409, {
      type: "https://gateway.test/errors/conflict",
      title: "Conflict",
      status: 409,
      code: "conflict",
      detail: "The filing was already accepted.",
      issues: [{ rule_id: "BSD2-ARI-001", severity: "error", pointer: "/lines/1", message: "Sum mismatch" }, { bogus: true }],
    }),
  );
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: s.fetch });
  await assert.rejects(api.acceptSubmission("SRG-26-0114806", {}), (error: unknown) => {
    assert.ok(error instanceof GatewayApiError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "conflict");
    assert.equal(error.detail, "The filing was already accepted.");
    assert.equal(error.type, "https://gateway.test/errors/conflict");
    assert.equal(error.issues.length, 1, "malformed issues are dropped");
    return true;
  });
});

test("errors without a usable body fall back to the HTTP status", async () => {
  const cases: [Response, string][] = [
    [new Response("<html>oops</html>", { status: 500 }), "server_error"],
    [new Response(null, { status: 404 }), "not_found"],
    [new Response(null, { status: 401 }), "unauthenticated"],
    [new Response(null, { status: 403 }), "forbidden"],
    [new Response(null, { status: 429 }), "rate_limited"],
    [problem(400, { code: "not_a_real_code", title: "Bad" }), "invalid_request"],
  ];
  for (const [response, code] of cases) {
    const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: async () => response });
    await assert.rejects(api.me(), (error: unknown) => error instanceof GatewayApiError && error.code === code, `expected ${code}`);
  }
  assert.equal(codeForStatus(422), "invalid_request");
  assert.equal(codeForStatus(418), "unexpected_response");
});

test("a network failure is a network_error with status 0", async () => {
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: async () => { throw new TypeError("fetch failed"); } });
  await assert.rejects(api.overview(), (error: unknown) => error instanceof GatewayApiError && error.code === "network_error" && error.status === 0);
});

test("an unreadable success body is unexpected_response", async () => {
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: async () => new Response("not json", { status: 200 }) });
  await assert.rejects(api.access(), (error: unknown) => error instanceof GatewayApiError && error.code === "unexpected_response");
});

test("the audit export streams bytes and keeps the server's file name", async () => {
  const bytes = new Uint8Array([0x50, 0x4b, 3, 4, 9, 9]);
  const s = stub(() => new Response(bytes, { status: 200, headers: { "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="srg-audit-2026-11.zip"' } }));
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: s.fetch });
  const bundle = await api.exportAudit({ from: "2026-11-01", to: "2026-11-21" });
  assert.equal(s.calls[0]?.url, "http://g/portal/api/audit/export?from=2026-11-01&to=2026-11-21");
  assert.match(((s.calls[0]?.init?.headers ?? {}) as Record<string, string>).Accept ?? "", /application\/zip/);
  assert.equal(bundle.filename, "srg-audit-2026-11.zip");
  assert.deepEqual(new Uint8Array(bundle.body), bytes);
});

test("the portal has no way to call enrolment or credential endpoints", () => {
  const api = createHttpClient({ baseUrl: "http://g", token: "t", fetch: async () => json({}) }) as unknown as Record<string, unknown>;
  for (const name of ["createInstitution", "certifyInstitution", "createClient", "registerSigningKey", "certification"]) {
    assert.equal(api[name], undefined, `${name} must not exist`);
  }
});

test("parseProblem ignores non-objects and friendlyMessage stays plain", () => {
  assert.equal(parseProblem("x"), null);
  assert.equal(parseProblem([1]), null);
  const error = new GatewayApiError({ status: 403, code: "forbidden", title: "Forbidden", detail: "You drafted this version." });
  assert.equal(friendlyMessage(error), "This action is not open to you. You drafted this version.");
  assert.equal(friendlyMessage(new Error("boom")), "Something went wrong. Try again in a moment.");
  assert.doesNotMatch(friendlyMessage(new GatewayApiError({ status: 0, code: "network_error", title: "x" })), /network_error/);
});
