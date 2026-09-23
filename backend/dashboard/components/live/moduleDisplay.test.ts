/**
 * Pins the live engine's headline-metric map so its two mirrors cannot drift:
 * `_PRIMARY_METRIC_KEY` in `backend/app/services/window_analytics.py` (its
 * parity test reads THIS file's source) and `METRIC_LABELS` in
 * `components/home/WindowAnalysis.tsx` (read here). Also pins the D-013
 * direction rule: the IRR headline is stored signed, judged on magnitude —
 * and (A1-02) that the pulse badge's glyph and figure follow the SIGNED move
 * while only its colour follows the magnitude judgement.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { LiveModule } from "@aequoros/risk-service-api";
import {
  DEFAULT_MODULE_ORDER,
  LIVE_MODULE_HREFS,
  LIVE_MODULE_LABELS,
  liveMetricChangeText,
  livePrimaryMetric,
  livePrimaryMetricChange,
  livePrimaryMetricDelta,
  livePrimaryMetricIsAdvisory,
  livePrimaryMetricKey,
} from "./moduleDisplay";

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

/** The dashboard root (holds tsconfig.test.json), found from the emitted test. */
function dashboardRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 10; i += 1) {
    if (existsSync(join(dir, "tsconfig.test.json"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("could not locate the dashboard root from " + __dirname);
}

const MODULES = Object.keys(LIVE_MODULE_LABELS) as LiveModule[];

/**
 * The stable partner for the backend parity test. Every key is a computed
 * metric the engine registers; `eve_limit_pct` is a parameter and reading it
 * as the IRR headline produced a flat sparkline with a zero delta.
 */
const EXPECTED_PRIMARY_KEYS: Record<LiveModule, string> = {
  liquidity: "lcr_pct",
  capital: "car_pct",
  credit: "npl_ratio_pct",
  irr: "worst_eve_change_pct_tier1",
  fx: "nop_pct_tier1",
  ftp: "portfolio_nim_pct",
  rating: "pit_pd_upper_pct",
  forecast: "year5_car_pct",
};

test("every live module has exactly the pinned headline metric key", () => {
  assert.equal(MODULES.length, 8);
  for (const liveModule of MODULES) {
    assert.equal(
      livePrimaryMetricKey(liveModule),
      EXPECTED_PRIMARY_KEYS[liveModule],
      `headline key for ${liveModule}`,
    );
    assert.ok(liveModule in LIVE_MODULE_HREFS, `href for ${liveModule}`);
  }
  assert.ok(
    !MODULES.some(
      (liveModule) => livePrimaryMetricKey(liveModule) === "eve_limit_pct",
    ),
    "a limit is a parameter, never a headline metric",
  );
});

test("the default card order is a permutation of the live modules", () => {
  assert.deepEqual([...DEFAULT_MODULE_ORDER].sort(), [...MODULES].sort());
  assert.equal(new Set(DEFAULT_MODULE_ORDER).size, DEFAULT_MODULE_ORDER.length);
  assert.ok(DEFAULT_MODULE_ORDER.includes("credit"));
});

test("the IRR headline renders signed, as the module page does", () => {
  assert.deepEqual(
    livePrimaryMetric("irr", { worst_eve_change_pct_tier1: -8.256 }),
    { label: "ΔEVE / Tier 1", value: "-8.26%" },
  );
  assert.deepEqual(
    livePrimaryMetric("irr", { worst_eve_change_pct_tier1: "3.5" }),
    { label: "ΔEVE / Tier 1", value: "3.50%" },
  );
  // The retired key no longer satisfies the lookup.
  assert.equal(livePrimaryMetric("irr", { eve_limit_pct: 15 }), null);
  assert.equal(
    livePrimaryMetric("irr", { worst_eve_change_pct_tier1: null }),
    null,
  );
  assert.equal(livePrimaryMetric("irr", undefined), null);
  assert.deepEqual(livePrimaryMetric("liquidity", { lcr_pct: 142.1 }), {
    label: "LCR",
    value: "142.10%",
  });
});

test("the IRR delta is judged on magnitude; other ratios stay signed", () => {
  // −8% → −12%: the signed number fell, the exposure grew — adverse.
  assert.equal(livePrimaryMetricDelta("irr", -12, -8), 4);
  // −8% → −4%: exposure shrank — favourable.
  assert.equal(livePrimaryMetricDelta("irr", -4, -8), -4);
  // The worst scenario flipping sign is a small move, not a 16-point swing.
  assert.ok(Math.abs(livePrimaryMetricDelta("irr", 8.1, -8) - 0.1) < 1e-9);
  assert.equal(livePrimaryMetricDelta("liquidity", 90, 100), -10);
  assert.equal(livePrimaryMetricDelta("capital", 14, 12), 2);
  assert.equal(livePrimaryMetricDelta("credit", 6, 4), 2);
});

/** `SemanticDelta`'s glyph per direction, read from the component source. */
function badgeGlyphs(): Record<string, string> {
  const source = readFileSync(
    join(dashboardRoot(), "components", "ui", "DeltaBadge.tsx"),
    "utf8",
  );
  const block = /const DIRECTION_GLYPH[^{]*\{([\s\S]*?)\};/.exec(source);
  assert.ok(block, "DIRECTION_GLYPH block not found in DeltaBadge.tsx");
  const glyphs: Record<string, string> = {};
  for (const match of block[1].matchAll(/^\s*(\w+):\s*'([^']*)'/gm)) {
    glyphs[match[1]] = match[2];
  }
  return glyphs;
}

/** What the pulse card badge shows: glyph + text, and the tone it wears. */
function renderedBadge(
  module: LiveModule,
  next: number,
  prev: number,
): { text: string; tone: string } {
  const change = livePrimaryMetricChange(module, next, prev);
  const glyph = badgeGlyphs()[change.direction];
  assert.ok(glyph !== undefined, `glyph for ${change.direction}`);
  return {
    text: `${glyph} ${liveMetricChangeText(change.change)}`,
    tone: change.favorability,
  };
}

test("A1-02: the IRR badge's arrow and figure are signed; only its colour is |ΔEVE|", () => {
  // −8% → −12%: headline "-12.00", falling spark — the badge must fall with
  // them, and be red because the exposure grew.
  assert.deepEqual(livePrimaryMetricChange("irr", -12, -8), {
    change: -4,
    direction: "down",
    favorability: "adverse",
  });
  assert.deepEqual(renderedBadge("irr", -12, -8), {
    text: "▼ -4.00 pts",
    tone: "adverse",
  });
  // −12% → −8%: rising series, green because the exposure shrank.
  assert.deepEqual(livePrimaryMetricChange("irr", -8, -12), {
    change: 4,
    direction: "up",
    favorability: "favorable",
  });
  assert.deepEqual(renderedBadge("irr", -8, -12), {
    text: "▲ +4.00 pts",
    tone: "favorable",
  });
  // The direction is NEVER the magnitude figure: −8 → −12 must not print +4.
  assert.notEqual(
    livePrimaryMetricChange("irr", -12, -8).change,
    livePrimaryMetricDelta("irr", -12, -8),
  );
  // −8% → +8.1%: the series jumps 16.1 points (and says so), coloured on the
  // 0.1-point growth in magnitude — adverse.
  const flip = livePrimaryMetricChange("irr", 8.1, -8);
  assert.ok(Math.abs(flip.change - 16.1) < 1e-9);
  assert.equal(flip.direction, "up");
  assert.equal(flip.favorability, "adverse");
  // A pure sign flip moves the series without moving the exposure: neutral.
  assert.deepEqual(livePrimaryMetricChange("irr", 8, -8), {
    change: 16,
    direction: "up",
    favorability: "neutral",
  });
  // −8% → −4%: rising series, shrinking exposure — green ▲.
  assert.deepEqual(renderedBadge("irr", -4, -8), {
    text: "▲ +4.00 pts",
    tone: "favorable",
  });
});

test("rise-is-adverse modules still colour a rise red; the rest colour it green", () => {
  // Credit (NPL ratio) and rating (PIT PD band) took `invertDelta: true` on
  // the card; the rule now lives beside the headline key.
  assert.deepEqual(renderedBadge("credit", 6, 4), {
    text: "▲ +2.00 pts",
    tone: "adverse",
  });
  assert.deepEqual(renderedBadge("credit", 4, 6), {
    text: "▼ -2.00 pts",
    tone: "favorable",
  });
  assert.deepEqual(renderedBadge("rating", 1.5, 1.2), {
    text: "▲ +0.30 pts",
    tone: "adverse",
  });
  assert.deepEqual(renderedBadge("fx", 7, 5), {
    text: "▲ +2.00 pts",
    tone: "adverse",
  });
  // Ratios where growth is good.
  assert.deepEqual(renderedBadge("capital", 14, 12), {
    text: "▲ +2.00 pts",
    tone: "favorable",
  });
  assert.deepEqual(renderedBadge("liquidity", 90, 100), {
    text: "▼ -10.00 pts",
    tone: "adverse",
  });
  assert.deepEqual(renderedBadge("ftp", 4.5, 4.25), {
    text: "▲ +0.25 pts",
    tone: "favorable",
  });
  assert.deepEqual(renderedBadge("forecast", 13, 12), {
    text: "▲ +1.00 pts",
    tone: "favorable",
  });
  // No move: flat glyph, neutral tone, never "-0.00".
  assert.deepEqual(livePrimaryMetricChange("capital", 12, 12), {
    change: 0,
    direction: "flat",
    favorability: "neutral",
  });
  assert.equal(liveMetricChangeText(-0), "0.00 pts");
  assert.equal(liveMetricChangeText(-0.001), "0.00 pts");
});

test("headlines that are never filed read as advisory, filed ones do not", () => {
  // FTP's portfolio NIM and the rating PD band are advisory_only in the
  // authority registry; the five-year projected CAR is supervisory_monitoring.
  // The registry is the authority — `test_primary_metric_parity.py` reads both
  // and fails when this set stops mirroring it (BI decision D-022).
  const advisory = MODULES.filter(livePrimaryMetricIsAdvisory);
  assert.deepEqual([...advisory].sort(), ["forecast", "ftp", "rating"]);
  for (const liveModule of [
    "liquidity",
    "capital",
    "credit",
    "irr",
    "fx",
  ] as const) {
    assert.equal(
      livePrimaryMetricIsAdvisory(liveModule),
      false,
      `${liveModule} files its headline — disclaiming it understates the figure`,
    );
  }
});

test("WindowAnalysis METRIC_LABELS mirrors the headline map exactly", () => {
  const source = readFileSync(
    join(dashboardRoot(), "components", "home", "WindowAnalysis.tsx"),
    "utf8",
  );
  const block = /const METRIC_LABELS[^{]*\{([\s\S]*?)\};/.exec(source);
  assert.ok(block, "METRIC_LABELS block not found in WindowAnalysis.tsx");
  const mirrored = new Map<string, string>();
  for (const match of block[1].matchAll(/^\s*([a-z0-9_]+):\s*'([^']*)'/gm)) {
    mirrored.set(match[1], match[2]);
  }
  const expected = new Map<string, string>();
  for (const liveModule of MODULES) {
    const metric = livePrimaryMetric(liveModule, {
      [livePrimaryMetricKey(liveModule)]: 0,
    });
    assert.ok(metric, `headline for ${liveModule}`);
    expected.set(livePrimaryMetricKey(liveModule), metric.label);
  }
  assert.deepEqual(
    [...mirrored.entries()].sort(),
    [...expected.entries()].sort(),
    "WindowAnalysis.tsx METRIC_LABELS has drifted from moduleDisplay.ts",
  );
});

if (failures > 0) {
  console.error(`\n${failures} moduleDisplay assertion group(s) failed.`);
  process.exit(1);
}
console.log(
  "moduleDisplay.test.ts: headline keys, magnitude rule, signed badge direction and WindowAnalysis mirror pinned.",
);
