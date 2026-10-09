/**
 * Pins the live engine's headline-metric map so its two mirrors cannot drift:
 * `_PRIMARY_METRIC_KEY` in `backend/app/services/window_analytics.py` (its
 * parity test reads THIS file's source) and the labels rendered by
 * `components/home/WindowAnalysis.tsx`. Also pins the D-013
 * direction rule: the IRR headline is stored signed, judged on magnitude —
 * and (A1-02) that the pulse badge's glyph and figure follow the SIGNED move
 * while only its colour follows the magnitude judgement.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import NodeModule from "node:module";
import { join } from "node:path";
import { createElement } from "react";
import { act, create, type ReactTestInstance } from "react-test-renderer";
import type {
  LiveModule,
  WindowAnalyticsRead,
} from "@aequoros/risk-service-api";
import { SemanticDelta } from "../ui/DeltaBadge";
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

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function renderedText(node: ReactTestInstance): string {
  return node.children
    .map((child) => (typeof child === "string" ? child : renderedText(child)))
    .join("");
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

/** What the pulse card badge shows: glyph + text, and the tone it wears. */
function renderedBadge(
  module: LiveModule,
  next: number,
  prev: number,
): { text: string; tone: string } {
  const change = livePrimaryMetricChange(module, next, prev);
  let renderer!: ReturnType<typeof create>;
  act(() => {
    renderer = create(
      createElement(
        SemanticDelta,
        { direction: change.direction, favorability: change.favorability },
        liveMetricChangeText(change.change),
      ),
    );
  });
  try {
    const glyphNode = renderer.root.findByProps({ "aria-hidden": true });
    const badge = glyphNode.parent;
    assert.ok(badge);
    const glyph = renderedText(glyphNode);
    const tones: Record<string, string> = {
      "text-success": "favorable",
      "text-critical": "adverse",
      "text-slate": "neutral",
    };
    const tone = badge.props.className
      .split(" ")
      .find((name: string) => name in tones);
    assert.ok(tone, "badge must render a semantic tone");
    return {
      text: `${glyph} ${badge.children.filter((child) => typeof child === "string").join("")}`,
      tone: tones[tone],
    };
  } finally {
    act(() => renderer.unmount());
  }
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
  assert.deepEqual(renderedBadge("capital", 12, 12), {
    text: "– 0.00 pts",
    tone: "neutral",
  });
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

test("WindowAnalysis renders each daily headline with the live card's label", () => {
  const data: WindowAnalyticsRead = {
    bankId: "BK-AAAAAAAA",
    startDate: new Date("2026-01-01"),
    endDate: new Date("2026-01-31"),
    periodCount: 1,
    ratios: [],
    daily: MODULES.map((module) => ({
      module,
      metricKey: livePrimaryMetricKey(module),
      dayCount: 2,
      avg: "12",
      min: "11",
      max: "13",
    })),
  };
  const loader = NodeModule as typeof NodeModule & {
    _load: (request: string, parent: unknown, isMain: boolean) => unknown;
  };
  const originalLoad = loader._load;
  loader._load = (request, parent, isMain) => {
    if (request === "@/lib/api/hooks") {
      return {
        useWindowAnalytics: () => ({ data, isFetching: false, error: null }),
      };
    }
    if (request === "@/lib/api/client") {
      return { isApiError: () => false, isModuleUnavailable: () => false };
    }
    const resolved = request.startsWith("@/")
      ? join(__dirname, "../..", request.slice(2))
      : request;
    return originalLoad(resolved, parent, isMain);
  };
  let renderer: ReturnType<typeof create> | undefined;
  try {
    const WindowAnalysis: typeof import("../home/WindowAnalysis").default =
      require("../home/WindowAnalysis").default;
    act(() => {
      renderer = create(createElement(WindowAnalysis, { bankId: data.bankId }));
    });
    assert.ok(renderer);
    for (const [label, value] of [
      ["Window start date", "2026-01-01"],
      ["Window end date", "2026-01-31"],
    ]) {
      const input = renderer.root.findByProps({ "aria-label": label });
      act(() => input.props.onChange({ target: { value } }));
    }
    const compute = renderer.root
      .findAllByType("button")
      .find((button) => renderedText(button) === "Compute");
    assert.ok(compute);
    assert.equal(compute.props.disabled, false);
    act(() => compute.props.onClick());
    const rows = renderer.root
      .findAllByType("p")
      .filter((row) => renderedText(row).includes("daily closes"));
    assert.equal(rows.length, MODULES.length);
    for (const [index, module] of MODULES.entries()) {
      const metric = livePrimaryMetric(module, {
        [livePrimaryMetricKey(module)]: 12,
      });
      assert.ok(metric);
      const advisory = livePrimaryMetricIsAdvisory(module) ? " (advisory)" : "";
      assert.equal(
        renderedText(rows[index]),
        `${LIVE_MODULE_LABELS[module]}: 2 daily closes · ${metric.label}${advisory} avg 12.0% · min 11.0%`,
      );
    }
  } finally {
    if (renderer) act(() => renderer!.unmount());
    loader._load = originalLoad;
  }
});

if (failures > 0) {
  console.error(`\n${failures} moduleDisplay assertion group(s) failed.`);
  process.exit(1);
}
console.log(
  "moduleDisplay.test.ts: headline keys, magnitude rule, signed badge direction and WindowAnalysis mirror pinned.",
);
