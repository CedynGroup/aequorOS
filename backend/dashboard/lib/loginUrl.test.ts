import assert from "node:assert/strict";

async function checkLoginUrl() {
  const previous = process.env.NEXT_PUBLIC_LOGIN_URL;
  process.env.NEXT_PUBLIC_LOGIN_URL =
    "https://aequoros.com/login?source=dashboard#form";
  const { LOGIN_URL, loginUrlWithReason } = await import("./loginUrl");
  if (previous === undefined) delete process.env.NEXT_PUBLIC_LOGIN_URL;
  else process.env.NEXT_PUBLIC_LOGIN_URL = previous;
  assert.equal(LOGIN_URL, "/login?source=dashboard#form");
  assert.equal(
    new URL(LOGIN_URL, "http://127.0.0.1:3001").href,
    "http://127.0.0.1:3001/login?source=dashboard#form",
  );
  assert.equal(
    loginUrlWithReason("session_ended", "http://127.0.0.1:3001"),
    "http://127.0.0.1:3001/login?source=dashboard&reason=session_ended#form",
  );
  assert.equal(
    loginUrlWithReason("access_changed"),
    "/login?source=dashboard&reason=access_changed#form",
  );
  console.log(
    "loginUrl.test.ts: configured login destinations retain caller host",
  );
}
void checkLoginUrl().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
