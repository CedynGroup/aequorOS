/**
 * Oxlint JS plugin: acts — the ACTS test-hygiene rules for AequorOS.
 *
 * Exposes the three user-mandated ACTS-coded rules under one plugin so rule
 * ids read `acts/<rule-name>` and every message starts with its ACTS code:
 *
 *   ACTS001 acts/require-frozen-time   — unfrozen wall-clock reads in tests
 *   ACTS002 acts/no-weak-test-awaits   — sleep-style waits / microtask flushes
 *   ACTS003 acts/prefer-e2e-stability  — waitForTimeout / networkidle in E2E
 *
 * See README.md for the config snippet and the suppression syntax.
 */

import requireFrozenTime from "./rules/require-frozen-time.js";
import noWeakTestAwaits from "./rules/no-weak-test-awaits.js";
import preferE2EStability from "./rules/prefer-e2e-stability.js";

const plugin = {
  meta: { name: "acts" },
  rules: {
    "require-frozen-time": requireFrozenTime,
    "no-weak-test-awaits": noWeakTestAwaits,
    "prefer-e2e-stability": preferE2EStability,
  },
};

export default plugin;
