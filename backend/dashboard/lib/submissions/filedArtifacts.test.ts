/**
 * Reading back what was filed, from a record nothing constrains.
 *
 * `detail` is an open dict on the wire. This table is shown as the bank's
 * evidence of what it sent a regulator, so the failure that matters is not a
 * crash — it is a confident-looking table that is subtly wrong: a file listed
 * as the signed record when it was not, or a row of blanks that reads as "we
 * sent an empty file".
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  FILED_ROLE_LABEL,
  filedArtifacts,
  roleFor,
  shortChecksum,
} from "./filedArtifacts";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

const artifact = (over: Record<string, unknown> = {}) => ({
  kind: "pdf",
  object_path: "banks/BK-SAMP0001/outputs/LMT_2026-06-30_v1.pdf",
  checksum_sha256: "8f3a11bc22de33ff44aa55bb66cc77dd88ee99ff00112233445566778899c210",
  size_bytes: 421_888,
  signed: true,
  ...over,
});

test("the filename is the leaf, not the storage path", () => {
  const [row] = filedArtifacts({ filed_artifacts: [artifact()] });
  assert.equal(row.filename, "LMT_2026-06-30_v1.pdf");
});

test("the server's `signed` flag decides the record, not the file type", () => {
  // If a return's signed revision is ever not a PDF, a kind lookup would
  // mislabel it. The record's own statement wins.
  assert.equal(roleFor("xlsx", true), "signed_record");
  // And an UNSIGNED pdf is not the record of truth just because it is a PDF.
  assert.equal(roleFor("pdf", false), "official_copy");
});

test("the formula copy is labelled as not the signed record", () => {
  assert.equal(roleFor("xlsx_working", false), "formula_copy");
  assert.match(FILED_ROLE_LABEL.formula_copy, /not the signed record/i);
});

test("an unknown kind is data, never the record", () => {
  // A new artifact type must earn the word "signed" rather than inherit it.
  assert.equal(roleFor("parquet", false), "data");
});

test("a row that cannot be identified is dropped, not half-rendered", () => {
  const rows = filedArtifacts({
    filed_artifacts: [
      artifact(),
      { kind: "csv" }, // no path
      { object_path: "a/b.csv" }, // no kind
      "not an object",
      null,
    ],
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, "pdf");
});

test("missing checksum or size is null, never zero or empty string", () => {
  // A rendered "0 B" would say the bank filed an empty file.
  const [row] = filedArtifacts({
    filed_artifacts: [artifact({ checksum_sha256: null, size_bytes: "88kb" })],
  });
  assert.equal(row.checksum, null);
  assert.equal(row.sizeBytes, null);
});

test("an unreadable detail yields nothing rather than a false record", () => {
  for (const detail of [null, undefined, "x", 42, [], {}, { filed_artifacts: {} }]) {
    assert.deepEqual(filedArtifacts(detail), []);
  }
});

test("`signed` only counts when it is literally true", () => {
  // A truthy string from a loosely-typed producer must not promote a copy to
  // the record of truth.
  const [row] = filedArtifacts({
    filed_artifacts: [artifact({ kind: "xlsx", signed: "yes" })],
  });
  assert.equal(row.signed, false);
  assert.equal(row.role, "official_copy");
});

test("checksums shorten to something comparable by eye", () => {
  assert.equal(shortChecksum(null), null);
  assert.equal(shortChecksum("abcd"), "abcd");
  assert.equal(
    shortChecksum("8f3a11bc22de33ff44aa55bb66cc77dd88ee99ff00112233445566778899c210"),
    "8f3a…c210",
  );
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("filedArtifacts: all tests passed");
