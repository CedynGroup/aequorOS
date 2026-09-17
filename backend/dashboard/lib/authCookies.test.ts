import assert from "node:assert/strict";

import {
  authSessionCookieNamesToClear,
  expiredAuthSessionCookieHeaders,
  presentAuthSessionCookieNames,
  sessionCookieGroups,
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

const groups = sessionCookieGroups(cookieHeader);
assert.equal(groups.length, 6);
assert.equal(
  groups.find((group) => group.baseName === "__Secure-authjs.session-token")
    ?.value,
  "secure-authjs-chunked",
);

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
