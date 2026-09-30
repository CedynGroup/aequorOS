/**
 * The rules in `lib/echartsOptions.ts` that are about a bank's numbers rather
 * than about how a chart looks.
 *
 * Two of them exist because the Recharts-to-ECharts migration could have quietly
 * broken them: a gap has to stay a gap (and yet a lone reading still has to be
 * visible), and a tooltip is now HTML rather than React, so tenant text has to be
 * escaped. Each is asserted here, and each assertion is shown to be capable of
 * failing by feeding it the input the rule exists to reject.
 */

import assert from "node:assert/strict";
import {
  axisTooltip,
  BAR_SERIES_BASE,
  escapeHtml,
  gapAwareData,
  hasAnyValue,
  LINE_SERIES_BASE,
  thresholdMarkLine,
  tooltipHtml,
  waterfallBase,
} from "./echartsOptions";

// --- 1. an absent figure is a hole, never a zero -----------------------------

const DOT = { value: 0, symbol: "circle", symbolSize: 5 };
const dot = (value: number) => ({ ...DOT, value });

assert.deepEqual(
  gapAwareData([1, 2, null, 4, 5]),
  [1, 2, null, 4, 5],
  "an absent middle figure must stay null so ECharts draws a break",
);
assert.deepEqual(
  gapAwareData([1, 2, undefined, 4, 5]),
  [1, 2, null, 4, 5],
  "undefined is an absence too — it must not become 0",
);
// A three-point series with the middle absent is TWO isolated readings, not a
// line with a hole: neither end has a neighbour to be drawn by.
assert.deepEqual(gapAwareData([1, null, 3]), [dot(1), null, dot(3)]);
assert.equal(
  gapAwareData([null, undefined]).every((datum) => datum === null),
  true,
  "a series with no readings must be all holes, not a flat line at zero",
);

// The inverse hazard: an honest gap must not swallow the one figure that exists.
assert.deepEqual(
  gapAwareData([null, 7, null]),
  [null, dot(7), null],
  "an isolated reading must carry its own symbol, or a symbol-less line draws nothing for it",
);
assert.deepEqual(
  gapAwareData([5]),
  [dot(5)],
  "a single-point series is entirely isolated points",
);
// ...and a point with a neighbour is drawn by the line itself, so it must NOT
// acquire a symbol — otherwise every dense series sprouts dots.
assert.deepEqual(
  gapAwareData([1, 2, 3]),
  [1, 2, 3],
  "a point with a neighbour must stay a bare number",
);
assert.deepEqual(
  gapAwareData([1, 2, null, 4]),
  [1, 2, null, dot(4)],
  "the point after a gap is isolated when nothing follows it",
);

assert.equal(hasAnyValue([null, undefined]), false);
assert.equal(
  hasAnyValue([null, 0]),
  true,
  "0 IS a reading — only null is absence",
);

// The gap rule is stated at the series, not inherited.
assert.equal(
  LINE_SERIES_BASE.connectNulls,
  false,
  "connectNulls must be stated false: a line across an unmeasured period is a claim the platform did not make",
);
assert.equal(LINE_SERIES_BASE.animation, false);
assert.equal(BAR_SERIES_BASE.animation, false);

// --- 2. a tooltip is HTML now, so tenant text must be escaped ---------------

assert.equal(
  escapeHtml("<img src=x onerror=alert(1)>"),
  "&lt;img src=x onerror=alert(1)&gt;",
  "angle brackets must not survive into tooltip markup",
);
assert.equal(escapeHtml("A & B"), "A &amp; B");
assert.equal(escapeHtml(`"q" 'r'`), "&quot;q&quot; &#39;r&#39;");

const hostile = tooltipHtml("<b>Period</b>", [
  {
    label: "<script>x</script>",
    value: "<i>1</i>",
    color: '"/><b>',
    note: "<u>n</u>",
  },
]);
for (const raw of [
  "<b>Period</b>",
  "<script>",
  "<i>1</i>",
  '"/><b>',
  "<u>n</u>",
]) {
  assert.equal(
    hostile.includes(raw),
    false,
    `tooltipHtml let the raw substring ${raw} through — a label, a figure, a colour and a note are all tenant-reachable`,
  );
}
// It still renders the text, escaped, rather than dropping it.
assert.equal(hostile.includes("&lt;script&gt;x&lt;/script&gt;"), true);
assert.equal(hostile.includes("&lt;b&gt;Period&lt;/b&gt;"), true);

// A titleless tooltip emits no heading block at all.
assert.equal(
  tooltipHtml(null, [{ label: "x", value: "1" }]).includes("margin-bottom"),
  false,
);

// --- 3. a tooltip says "absent", never "-" ---------------------------------

const tip = axisTooltip(["Mar", "Apr"], (value) => `${value.toFixed(1)}%`);
const bothPresent = tip([
  { dataIndex: 0, seriesName: "LCR", color: "#111", value: 142 },
  { dataIndex: 0, seriesName: "NSFR", color: "#222", value: 118 },
]);
assert.equal(bothPresent.includes("Mar"), true);
assert.equal(bothPresent.includes("142.0%"), true);
assert.equal(bothPresent.includes("118.0%"), true);

const oneAbsent = tip([
  { dataIndex: 1, seriesName: "LCR", color: "#111", value: 142 },
  { dataIndex: 1, seriesName: "NSFR", color: "#222", value: null },
]);
assert.equal(
  oneAbsent.includes("not computed"),
  true,
  'a series with no figure must SAY so — ECharts renders a null as "-", which reads as a measured nothing',
);
assert.equal(
  /NSFR<\/?b?>?: <b>0/.test(oneAbsent),
  false,
  "an absent figure must never be rendered as zero",
);
assert.equal(
  tip([{ dataIndex: 9, seriesName: "LCR", color: "#111", value: 1 }]).includes(
    "undefined",
  ),
  false,
  "a category index past the label list must not print the word undefined",
);

// --- 4. shapes that stand in for Recharts constructs ------------------------

const marks = thresholdMarkLine([
  { axis: "y", value: 100, label: "Regulatory minimum", color: "#f00" },
  { axis: "x", value: 0, color: "#888", solid: true },
]);
const markData = marks.data as unknown as ReadonlyArray<
  Record<string, unknown>
>;
assert.equal(markData.length, 2);
assert.equal(
  markData[0].yAxis,
  100,
  "a y-axis threshold must be fixed on the y axis",
);
assert.equal(
  markData[1].xAxis,
  0,
  "an x-axis baseline must be fixed on the x axis",
);
assert.equal(
  "xAxis" in markData[0],
  false,
  "a threshold must not be fixed on both axes",
);
assert.equal(
  (markData[1].lineStyle as { type: string }).type,
  "solid",
  "a zero baseline is solid, a threshold dashed",
);
assert.equal((markData[0].lineStyle as { type: string }).type, "dashed");
assert.equal(
  marks.symbol,
  "none",
  "a reference line must not sprout arrowheads",
);

// A waterfall's pedestal must put every bar back where its delta belongs, and
// the closing level must be the sum of the deltas — the bridge cannot invent a leg.
const { base, bars } = waterfallBase([10, -4, 2], 100);
assert.deepEqual(base, [100, 106, 106]);
assert.deepEqual(bars, [10, 4, 2]);
assert.equal(
  base[2] + bars[2],
  108,
  "the top of the last bar must equal the closing level 100 + 10 - 4 + 2",
);

console.log("lib/echartsOptions.test.ts: all checks passed");
