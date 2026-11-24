import assert from "node:assert/strict";
import test from "node:test";
import {
  daysBetween,
  detailValue,
  formatDate,
  formatDateTime,
  formatDayMonth,
  formatDigest,
  formatDurationMs,
  formatGatewayKey,
  formatMonthYear,
  formatFigure,
  formatNumber,
  formatPercent,
  formatRelativeDay,
  formatSeconds,
  formatShare,
  formatTime,
  formatWeekdayDate,
  humanize,
  initials,
  paragraphs,
  plural,
  shortHash,
  shortPersonName,
  unitPhrase,
} from "../../lib/format";

test("dates and times are GMT and written as the design writes them", () => {
  assert.equal(formatDate("2026-10-31"), "31 Oct 2026");
  assert.equal(formatDate("2026-09-30"), "30 Sep 2026", "never 'Sept'");
  assert.equal(formatDate("2026-11-21T23:30:00+02:00"), "21 Nov 2026", "converted to GMT");
  assert.equal(formatDate(null), "—");
  assert.equal(formatDate("nonsense"), "—");
  assert.equal(formatDayMonth("2026-11-24T17:00:00Z"), "24 Nov");
  assert.equal(formatWeekdayDate("2026-11-23"), "Mon 23 Nov");
  assert.equal(formatTime("2026-11-21T16:38:04Z"), "16:38");
  assert.equal(formatTime("2026-11-21T16:38:04Z", { seconds: true }), "16:38:04");
  assert.equal(formatDateTime("2026-11-21T15:57:12.418Z", { seconds: true, zone: true }), "21 Nov 2026, 15:57:12 GMT");
  assert.equal(formatDateTime("2026-11-20T17:55:03Z"), "20 Nov 2026, 17:55");
  assert.equal(formatDateTime("2026-11-21T16:38:00Z", { year: false }), "21 Nov, 16:38");
  assert.equal(formatMonthYear("2026-10-31"), "October 2026");
});

test("relative days: time today, 'Yesterday', then the date", () => {
  const now = new Date("2026-11-21T16:40:00Z");
  assert.equal(formatRelativeDay("2026-11-21T11:30:00Z", now), "11:30");
  assert.equal(formatRelativeDay("2026-11-20T23:59:00Z", now), "Yesterday");
  assert.equal(formatRelativeDay("2026-11-18T09:00:00Z", now), "18 Nov");
  assert.equal(formatRelativeDay("2025-11-18T09:00:00Z", now), "18 Nov 2025");
  assert.equal(daysBetween("2026-11-19T17:00:00Z", now), 2);
  assert.equal(daysBetween("2026-12-01", "2026-11-30"), -1);
});

test("numbers, shares and durations", () => {
  assert.equal(formatNumber(1148206), "1,148,206");
  assert.equal(formatNumber(null), "—");
  assert.equal(formatPercent(99.97, 2), "99.97%");
  assert.equal(formatPercent(71), "71%");
  assert.equal(formatShare(571, 612), "93.3%");
  assert.equal(formatShare(538, 571), "94.2%");
  assert.equal(formatShare(1, 0), "—");
  assert.equal(formatSeconds(1.8), "1.8 s");
  assert.equal(formatDurationMs(2900), "2.9 s");
  assert.equal(plural(1, "return"), "1 return");
  assert.equal(plural(6, "return"), "6 returns");
  assert.equal(plural(48, "institution"), "48 institutions");
});

test("digests, keys, names and labels", () => {
  assert.equal(formatDigest("9f2c41ab77e0d5c318be6a904f2d7c11a3e95b0c6d42f8e7205b9ad17c3e6f04"), "9f2c41ab77e0d5c3 18be6a904f2d7c11 a3e95b0c6d42f8e7 205b9ad17c3e6f04");
  assert.equal(shortHash("7c1e9a04d2b85f3e6a10c47b9e2d5f8a1c3b07e6d4a9f2c58e1b6d3a7f90b3f2"), "7c1e9a04…b3f2");
  assert.equal(formatGatewayKey("gateway-receipt-2026-q4"), "2026-Q4");
  assert.equal(formatGatewayKey("gateway-audit-2026-q4"), "2026-Q4");
  assert.equal(formatGatewayKey("pgb-sign-2026-01"), "pgb-sign-2026-01");
  assert.equal(initials("Kofi Owusu"), "KO");
  assert.equal(initials("Riverbend Rural Bank"), "RB");
  assert.equal(initials("Northgate Savings & Loans"), "NL");
  assert.equal(initials(""), "?");
  assert.equal(shortPersonName("Akosua Boateng"), "A. Boateng");
  assert.equal(humanize("scope_and_limit"), "Scope and limit");
  assert.equal(detailValue(1148371), "1,148,371");
  assert.equal(detailValue(null), "—");
  assert.equal(detailValue({ a: 1 }), '{"a":1}');
  assert.deepEqual(paragraphs("First.\n\nSecond.\n  \nThird."), ["First.", "Second.", "Third."]);
});

test("filed figures: exact decimal text grouped to two places, text as filed, blank as a dash", () => {
  assert.equal(formatFigure("35172763.04"), "35,172,763.04");
  assert.equal(formatFigure("-8793190.7612"), "-8,793,190.76");
  assert.equal(formatFigure("1184900"), "1,184,900");
  assert.equal(formatFigure(null), "—");
  assert.equal(formatFigure("Kumasi Branch"), "Kumasi Branch");
  assert.equal(unitPhrase("millions", "GHS"), "GHS millions");
  assert.equal(unitPhrase("units", "GHS"), "GHS");
  assert.equal(unitPhrase("percent", "GHS"), "%");
});
