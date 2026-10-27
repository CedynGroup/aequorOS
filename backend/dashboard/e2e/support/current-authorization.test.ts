import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { currentAuthorizationVersion } from "./current-authorization";

const directory = mkdtempSync(path.join(tmpdir(), "aequoros-current-auth-"));
const database = path.join(directory, "fixture.db");
const user = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const org = "OR-TEST0001";
const writer = new DatabaseSync(database);
try {
  writer.exec(
    "CREATE TABLE users (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, authorization_version INTEGER NOT NULL)",
  );
  writer
    .prepare("INSERT INTO users VALUES (?, ?, ?)")
    .run(user.replaceAll("-", ""), org, 2);
  assert.equal(currentAuthorizationVersion(user, org, database), 2);
  writer
    .prepare("UPDATE users SET authorization_version=? WHERE id=?")
    .run(7, user.replaceAll("-", ""));
  assert.equal(currentAuthorizationVersion(user, org, database), 7);
  assert.throws(
    () => currentAuthorizationVersion(user, "OR-OTHER001", database),
    /Missing valid authorization version/,
  );
  assert.throws(
    () =>
      currentAuthorizationVersion(
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        org,
        database,
      ),
    /Missing valid authorization version/,
  );
  writer.exec("UPDATE users SET authorization_version=0");
  assert.throws(
    () => currentAuthorizationVersion(user, org, database),
    /Missing valid authorization version/,
  );
} finally {
  writer.close();
  rmSync(directory, { recursive: true });
}
console.log(
  "current-authorization.test.ts: current generations and tenant isolation verified",
);
