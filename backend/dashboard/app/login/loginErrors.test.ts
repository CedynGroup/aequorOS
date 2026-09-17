import assert from "node:assert/strict";

import {
  INVALID_CREDENTIALS_MESSAGE,
  SERVICE_UNAVAILABLE_MESSAGE,
  SIGN_IN_FAILED_MESSAGE,
  loginErrorMessage,
} from "./loginErrors";
import { loginUrlWithReason } from "../../lib/loginUrl";
import { requestOrigin } from "../../lib/requestOrigin";

assert.equal(
  loginErrorMessage({ error: "CredentialsSignin", code: "credentials" }),
  INVALID_CREDENTIALS_MESSAGE,
);
assert.equal(
  loginErrorMessage({
    error: "CredentialsSignin",
    code: "service_unavailable",
  }),
  SERVICE_UNAVAILABLE_MESSAGE,
);
assert.equal(
  loginErrorMessage({ error: "Configuration", code: undefined }),
  SIGN_IN_FAILED_MESSAGE,
);
assert.equal(loginErrorMessage({ error: undefined, code: undefined }), null);
assert.equal(
  loginUrlWithReason("session_ended", "http://127.0.0.1:3001"),
  "http://127.0.0.1:3001/login?reason=session_ended",
);
const previousNodeEnv = process.env.NODE_ENV;
try {
  (process.env as Record<string, string>).NODE_ENV = "development";
  assert.equal(
    requestOrigin({
      url: "http://localhost:3001/",
      headers: new Headers({
        host: "127.0.0.1:3001",
        "x-forwarded-host": "localhost:3001",
        "x-forwarded-proto": "http",
      }),
    }),
    "http://127.0.0.1:3001",
  );
} finally {
  if (previousNodeEnv === undefined) {
    delete (process.env as Record<string, string | undefined>).NODE_ENV;
  } else {
    (process.env as Record<string, string>).NODE_ENV = previousNodeEnv;
  }
}

console.log("loginErrors.test.ts: truthful sign-in messages passed");
