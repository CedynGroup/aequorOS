import assert from "node:assert/strict";
import test from "node:test";
import { addMonths, isMonth, monthGrid } from "../../lib/calendar";
import { gatewayEnv, readConfig } from "../../lib/config";
import { csvCell, toCsv } from "../../lib/csv";
import { REGISTER_COUNTS, registerCountKey, registerStatus, softwareLabel, submissionStatus } from "../../lib/labels";
import { noticeFrom } from "../../lib/notice";
import { hrefWith, objectHref } from "../../lib/search-params";
import { crc32, createZip } from "../../lib/zip";

test("the November 2026 grid starts on Monday 26 October and runs six weeks", () => {
  const grid = monthGrid("2026-11");
  assert.equal(grid.length, 42);
  assert.deepEqual(grid[0], { date: "2026-10-26", day: 26, inMonth: false, label: "Oct 26" });
  assert.equal(grid[6]?.date, "2026-11-01");
  assert.equal(grid[6]?.label, "Nov 1");
  assert.equal(grid[7]?.label, "2");
  assert.equal(grid[41]?.date, "2026-12-06");
  assert.equal(grid.filter((c) => c.inMonth).length, 30);
  assert.equal(addMonths("2026-12", 1), "2027-01");
  assert.equal(addMonths("2026-01", -1), "2025-12");
  assert.equal(isMonth("2026-13"), false);
  assert.equal(isMonth("2026-1"), false);
});

test("configuration: development conveniences never reach production", () => {
  assert.equal(gatewayEnv({ NODE_ENV: "development" }), "local");
  assert.equal(gatewayEnv({ NODE_ENV: "production" }), "production", "an unset GATEWAY_ENV is strict");
  assert.throws(() => gatewayEnv({ GATEWAY_ENV: "staging" }), /must be one of/);
  const local = readConfig({ NODE_ENV: "development" });
  assert.equal(local.apiUrl, "http://localhost:8200");
  assert.equal(local.oidc.issuer, "http://localhost:8200/idp");
  assert.equal(local.oidc.clientId, "srg-portal");
  assert.equal(local.mock, false);
  assert.equal(readConfig({ NODE_ENV: "development", GATEWAY_PORTAL_MOCK: "1" }).mock, true);
  assert.throws(() => readConfig({ GATEWAY_ENV: "production", GATEWAY_PORTAL_MOCK: "1" }), /refused/);
  assert.throws(() => readConfig({ GATEWAY_ENV: "production" }), /must be set/);
  assert.throws(
    () => readConfig({ GATEWAY_ENV: "production", GATEWAY_API_URL: "http://api", GATEWAY_OIDC_ISSUER: "https://idp", GATEWAY_OIDC_CLIENT_ID: "c" }),
    /https/,
  );
  const prod = readConfig({ GATEWAY_ENV: "production", GATEWAY_API_URL: "https://api/", GATEWAY_OIDC_ISSUER: "https://idp/", GATEWAY_OIDC_CLIENT_ID: "c" });
  assert.equal(prod.apiUrl, "https://api");
  assert.equal(prod.oidc.issuer, "https://idp");
});

test("CSV cells are escaped and spreadsheet formulas neutralised", () => {
  assert.equal(csvCell('He said "hi", then left'), '"He said ""hi"", then left"');
  assert.equal(csvCell("=HYPERLINK(1)"), "'=HYPERLINK(1)");
  assert.equal(csvCell(-5), "-5", "numbers stay numbers");
  assert.equal(csvCell(null), "");
  assert.equal(toCsv([["a", 1], ["b\nc", null]]), 'a,1\r\n"b\nc",\r\n');
});

test("zip: CRC-32 check value and a well-formed archive", () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
  const zip = createZip([{ name: "a.txt", content: "hello" }, { name: "b.json", content: "{}" }]);
  const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
  assert.equal(view.getUint32(0, true), 0x04034b50);
  const end = zip.byteLength - 22;
  assert.equal(view.getUint32(end, true), 0x06054b50);
  assert.equal(view.getUint16(end + 10, true), 2, "two entries");
});

test("labels: statuses read as words; the register's retired stages count as enrolled", () => {
  assert.equal(submissionStatus("in_review").label, "In review");
  assert.equal(submissionStatus("superseded").tone, "struck");
  assert.equal(registerStatus("live").label, "Live");
  assert.equal(registerStatus("sandbox").label, "Enrolled");
  assert.equal(registerStatus("mystery_state").label, "Mystery state");
  assert.deepEqual(REGISTER_COUNTS, ["Enrolled", "Live", "Suspended"]);
  assert.deepEqual(["requested", "sandbox", "certified", "live", "suspended"].map(registerCountKey), ["Enrolled", "Enrolled", "Enrolled", "Live", "Suspended"]);
  assert.equal(softwareLabel("AequorOS 2026.11"), "AequorOS 2026.11 · declared");
  assert.equal(softwareLabel(null, "portal_upload"), "No software declared");
  assert.equal(softwareLabel(null, null), "Not yet declared");
});

test("links: object references and filter-preserving hrefs", () => {
  assert.equal(objectHref("SRG-26-0114806"), "/submissions/SRG-26-0114806");
  assert.equal(objectHref("QRY-2026-0318"), "/queries/QRY-2026-0318");
  assert.equal(objectHref("SUP-2026-0042"), "/access");
  assert.equal(objectHref("0231"), "/institutions/0231");
  assert.equal(objectHref("session 4f1a…"), null);
  // The API's numbering is a minimum width: a busy year runs past it.
  assert.equal(objectHref("QRY-2026-10000"), "/queries/QRY-2026-10000");
  assert.equal(objectHref("SUP-2026-10000"), "/access");
  assert.equal(objectHref("SRG-26-10000000"), "/submissions/SRG-26-10000000");
  assert.equal(objectHref("QRY-2026-031"), null);
  assert.equal(hrefWith("/audit", { actor: "staff", cursor: "c1" }, { entry: "5", cursor: undefined }), "/audit?actor=staff&entry=5");
});

test("notices come only from the whitelist; the URL cannot inject text", () => {
  assert.equal(noticeFrom({ notice: "<script>" }, "/x"), null);
  assert.equal(noticeFrom({ notice: "toString" }, "/x"), null);
  const n = noticeFrom({ notice: "resubmission_requested", ref: "QRY-2026-0320", view: "full" }, "/submissions/SRG-26-0114806");
  assert.equal(n?.message, "Resubmission requested. The institution has been sent the request. Request QRY-2026-0320.");
  assert.equal(n?.dismissHref, "/submissions/SRG-26-0114806?view=full");
  assert.equal(noticeFrom({ notice: "resubmission_requested", ref: "evil text" }, "/x")?.message.includes("evil"), false);
});
