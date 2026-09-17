import assert from "node:assert/strict";

import {
  authSessionCookieNamesToClear,
  cleanAuthResponseCookies,
  expiredAuthSessionCookieHeaders,
  presentAuthSessionCookieNames,
} from "./authCookies";

const cookieHeader = [
  "unrelated=keep",
  "authjs.session-token=authjs",
  "__Secure-authjs.session-token.0=secure-authjs-",
  "__Secure-authjs.session-token.1=chunked",
  "__Host-authjs.session-token=host-authjs",
  "next-auth.session-token=legacy",
  "__Secure-next-auth.session-token.0=secure-legacy-",
  "__Secure-next-auth.session-token.1=chunked",
  "__Host-next-auth.session-token=host-legacy",
].join("; ");

const names = authSessionCookieNamesToClear(cookieHeader);
for (const expected of [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "__Host-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
  "__Host-next-auth.session-token",
  "__Secure-authjs.session-token.0",
  "__Secure-authjs.session-token.1",
  "__Secure-next-auth.session-token.0",
  "__Secure-next-auth.session-token.1",
]) {
  assert.ok(names.includes(expected), `missing ${expected}`);
}
assert.ok(!names.includes("unrelated"));
assert.deepEqual(
  presentAuthSessionCookieNames(cookieHeader).sort(),
  names.filter((name) => cookieHeader.includes(`${name}=`)).sort(),
);

const expired = expiredAuthSessionCookieHeaders(names);
assert.equal(expired.length, names.length);
for (const header of expired) {
  assert.match(header, /Max-Age=0/);
  assert.match(header, /Expires=Thu, 01 Jan 1970 00:00:00 GMT/);
  assert.match(header, /Path=\//);
}
assert.match(
  expired.find((header) => header.startsWith("__Host-authjs.session-token="))!,
  /; Secure$/,
);
assert.doesNotMatch(
  expired.find((header) => header.startsWith("authjs.session-token="))!,
  /; Secure$/,
);

console.log("authCookies.test.ts: all session cookie variants expire");

async function checkAuthResponses() {
  const request = (action: string, method = "GET") => new Request(
    `http://127.0.0.1:3001/api/auth/${action}`,
    { method, headers: { cookie: cookieHeader } },
  );
  const deleted = (response: Response) => response.headers.getSetCookie()
    .filter((header) => header.includes("Max-Age=0"))
    .map((header) => header.split("=")[0]);
  for (const response of [
    Response.json({ url: "http://127.0.0.1:3001/login" }),
    new Response(null, { status: 302, headers: { location: "/login" } }),
  ]) {
    const cleaned = await cleanAuthResponseCookies(request("signout", "POST"), response);
    assert.equal(cleaned, response);
    assert.deepEqual(deleted(cleaned).sort(), names.slice().sort());
  }
  const emptySession = await cleanAuthResponseCookies(request("session"), Response.json(null));
  assert.deepEqual(deleted(emptySession).sort(), names.slice().sort());
  assert.equal(await emptySession.json(), null);
  for (const action of ["session", "callback/credentials"]) {
    const response = Response.json({ user: { email: "new@example.com" } });
    response.headers.append("set-cookie", "authjs.session-token.0=new-; Path=/; HttpOnly");
    response.headers.append("set-cookie", "authjs.session-token.1=session; Path=/; HttpOnly");
    response.headers.append("set-cookie", "authjs.csrf-token=csrf; Path=/; HttpOnly");
    const renewalRequest = request(action);
    renewalRequest.headers.set("cookie", cookieHeader + "; authjs.session-token.0=old-; authjs.session-token.1=session; authjs.session-token.2=tail");
    await cleanAuthResponseCookies(renewalRequest, response);
    assert.deepEqual(deleted(response).sort(), [...names, "authjs.session-token.2"].sort());
    assert.ok(!deleted(response).includes("authjs.session-token.0"));
    assert.ok(!deleted(response).includes("authjs.session-token.1"));
    assert.ok(response.headers.getSetCookie().includes("authjs.csrf-token=csrf; Path=/; HttpOnly"));
    assert.deepEqual(await response.json(), { user: { email: "new@example.com" } });
  }
  for (const [action, method, response] of [
    ["signout", "GET", Response.json({ url: "/login" })],
    ["signout", "POST", Response.json({ url: "/api/auth/signin?error=MissingCSRF" })],
    ["session", "GET", Response.json({ error: "unavailable" }, { status: 500 })],
    ["providers", "GET", Response.json({})],
  ] as const) {
    await cleanAuthResponseCookies(request(action, method), response);
    assert.deepEqual(deleted(response), []);
  }
}
void checkAuthResponses().catch((error) => { console.error(error); process.exitCode = 1; });

assert.deepEqual(presentAuthSessionCookieNames(null), []);
assert.deepEqual(
  presentAuthSessionCookieNames([
    "authjs.session-token.9=tail",
    "authjs.session-token=",
    "authjs.session-token.0=head",
    "authjs.session-token.9=duplicate",
    "authjs.session-token.extra=unrelated",
    "authjs.session-token.=unrelated",
    "authjs.session-token-other=unrelated",
    "authjs.session-token.1",
  ].join("; ")),
  ["authjs.session-token.9", "authjs.session-token", "authjs.session-token.0"],
);
