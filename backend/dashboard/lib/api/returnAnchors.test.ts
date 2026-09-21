import assert from "node:assert/strict";
import {
  defaultReportingDate,
  reportingDateOptionLabel,
  toReportingDateOptions,
} from "./returnAnchors";

const anchor = (
  date: string,
  dataStatus: "computed" | "awaiting_data",
  rag: "overdue" | "due_soon" | "on_track",
) => ({ reportingDate: new Date(`${date}T00:00:00Z`), dataStatus, rag });

// The founder's case, as the API returns it on 2026-09-19: the backend sorts
// newest first, and the only date with figures behind it is three months old.
const anchors = [
  anchor("2026-12-31", "awaiting_data", "on_track"),
  anchor("2026-11-30", "awaiting_data", "on_track"),
  anchor("2026-10-31", "awaiting_data", "on_track"),
  anchor("2026-09-30", "awaiting_data", "on_track"),
  anchor("2026-08-31", "awaiting_data", "overdue"),
  anchor("2026-07-31", "awaiting_data", "overdue"),
  anchor("2026-06-30", "computed", "overdue"),
  anchor("2026-05-31", "awaiting_data", "overdue"),
];
const options = toReportingDateOptions(anchors);

// --- the picker offers the elapsed dates, in the order the API sent them ----

assert.deepEqual(
  options.map((option) => option.date),
  [
    "2026-12-31",
    "2026-11-30",
    "2026-10-31",
    "2026-09-30",
    "2026-08-31",
    "2026-07-31",
    "2026-06-30",
    "2026-05-31",
  ],
);
// The date the bank can actually file is present and marked as having figures.
const june = options.find((option) => option.date === "2026-06-30");
assert.ok(june);
assert.equal(june.hasComputedPosition, true);
assert.equal(june.isOverdue, true);

// --- the label states both facts, in plain words ---------------------------

assert.equal(reportingDateOptionLabel(june), "2026-06-30 — past due");
assert.equal(
  reportingDateOptionLabel(
    toReportingDateOptions([anchor("2026-07-31", "awaiting_data", "overdue")])[0],
  ),
  "2026-07-31 — past due, no figures yet",
);
assert.equal(
  reportingDateOptionLabel(
    toReportingDateOptions([anchor("2026-09-30", "awaiting_data", "on_track")])[0],
  ),
  "2026-09-30 — no figures yet",
);
// An upcoming date with figures needs no explanation at all.
assert.equal(
  reportingDateOptionLabel(
    toReportingDateOptions([anchor("2026-09-30", "computed", "on_track")])[0],
  ),
  "2026-09-30",
);
// No raw enum value ever reaches the screen.
for (const option of options) {
  const label = reportingDateOptionLabel(option);
  assert.equal(label.includes("awaiting_data"), false);
  assert.equal(label.includes("overdue"), false);
  assert.equal(label.includes("computed"), false);
}

// --- the picker opens on the most recent date already owed -----------------

assert.equal(defaultReportingDate(options, "2026-09-19"), "2026-08-31");
// Not the newest date offered (a future period end nobody files yet), and not
// the newest date WITH figures (which would skip past an overdue period).
assert.notEqual(defaultReportingDate(options, "2026-09-19"), "2026-12-31");
assert.notEqual(defaultReportingDate(options, "2026-09-19"), "2026-06-30");
// Every date still ahead: fall back to the newest offered rather than nothing.
assert.equal(defaultReportingDate(options, "2026-01-01"), "2026-12-31");
assert.equal(defaultReportingDate(options, undefined), "2026-12-31");
assert.equal(defaultReportingDate([], "2026-09-19"), undefined);
// Order-independent: the same answer whatever order the API sends.
assert.equal(
  defaultReportingDate([...options].reverse(), "2026-09-19"),
  "2026-08-31",
);

console.log("returnAnchors.test.ts ok");
