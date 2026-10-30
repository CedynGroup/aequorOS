import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { readSqliteForPoll, SQLITE_READ_BUSY } from "./sqlite-poll";

const directory = mkdtempSync(path.join(tmpdir(), "aequoros-worker-poll-"));
const database = path.join(directory, "fixture.db");
const writer = new DatabaseSync(database);

function readStatus(): string | undefined {
  const reader = new DatabaseSync(database, { readOnly: true });
  try {
    return reader.prepare("SELECT status FROM jobs WHERE id=1").get()
      ?.status as string | undefined;
  } finally {
    reader.close();
  }
}

try {
  writer.exec(
    "CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT NOT NULL)",
  );
  writer.exec("INSERT INTO jobs VALUES (1, 'queued')");
  assert.equal(readSqliteForPoll(readStatus), "queued");

  writer.exec("BEGIN EXCLUSIVE");
  writer.exec("UPDATE jobs SET status='succeeded' WHERE id=1");
  assert.throws(readStatus, /database is locked/);
  assert.equal(readSqliteForPoll(readStatus), SQLITE_READ_BUSY);
  writer.exec("COMMIT");
  assert.equal(readSqliteForPoll(readStatus), "succeeded");
  assert.equal(
    readSqliteForPoll(() => undefined),
    undefined,
  );

  assert.throws(
    () =>
      readSqliteForPoll(() =>
        writer.prepare("SELECT status FROM missing").get(),
      ),
    /no such table/,
  );
  const failure = new TypeError("invalid worker evidence");
  assert.throws(
    () =>
      readSqliteForPoll(() => {
        throw failure;
      }),
    (error) => error === failure,
  );
} finally {
  writer.close();
  rmSync(directory, { recursive: true });
}

console.log(
  "sqlite-poll.test.ts: live-writer lock recovery and fatal errors verified",
);
