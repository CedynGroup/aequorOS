import assert from "node:assert/strict";
import NodeModule from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as rehearsalLabels from "../icaap/p3/labels";
import StatusPill from "../ui/StatusPill";

// Relative, not aliased: this file is compiled to plain CommonJS and run by
// node, which does not resolve the `@/` path alias.
import {
  REHEARSAL_BODY,
  REHEARSAL_HEADLINE,
  REHEARSAL_SHORT,
} from "../icaap/p3/labels";

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

test("the short marker is the same sentence, shortened — not a new one", () => {
  assert.match(REHEARSAL_SHORT, /practice run/i);
  assert.match(REHEARSAL_HEADLINE, /practice run/i);
  assert.match(REHEARSAL_HEADLINE, /never be filed/i);
  assert.match(REHEARSAL_BODY, /nothing here satisfies a filing obligation/i);
});

test("no reader is ever shown the flag's own name", () => {
  // `is_rehearsal` / `isRehearsal` / `cycle_kind` are the wire's vocabulary.
  for (const copy of [REHEARSAL_SHORT, REHEARSAL_HEADLINE, REHEARSAL_BODY]) {
    assert.ok(!/is_?[Rr]ehearsal/.test(copy), `raw flag name in: ${copy}`);
    assert.ok(
      !/cycle_kind|snake_case|_id\b/.test(copy),
      `raw token in: ${copy}`,
    );
    assert.ok(!/\d/.test(copy), `a digit in display copy (D-024): ${copy}`);
  }
});

test("the shared pill and notice render the rehearsal vocabulary", () => {
  const loader = NodeModule as typeof NodeModule & {
    _load: (request: string, parent: unknown, isMain: boolean) => unknown;
  };
  const originalLoad = loader._load;
  loader._load = (request, parent, isMain) => {
    if (request === "@/components/icaap/p3/labels") return rehearsalLabels;
    if (request === "@/components/ui/StatusPill")
      return { default: StatusPill, __esModule: true };
    // Downloads are unrelated to these presentational components.
    if (request === "@/lib/api/client" || request === "@/lib/api/token")
      return {};
    return originalLoad(request, parent, isMain);
  };
  try {
    const { RehearsalPill, RehearsalNotice } =
      require("./shared") as typeof import("./shared");
    const pill = renderToStaticMarkup(createElement(RehearsalPill));
    assert.ok(pill.includes(REHEARSAL_SHORT));
    assert.ok(pill.includes(`title="${REHEARSAL_HEADLINE}"`));
    const notice = renderToStaticMarkup(createElement(RehearsalNotice));
    assert.ok(notice.includes(REHEARSAL_HEADLINE));
    assert.ok(notice.includes(REHEARSAL_BODY));
    const detail = "Review this practice return before continuing.";
    const custom = renderToStaticMarkup(
      createElement(RehearsalNotice, { detail }),
    );
    assert.ok(custom.includes(REHEARSAL_HEADLINE));
    assert.ok(custom.includes(detail));
    assert.ok(!custom.includes(REHEARSAL_BODY));
  } finally {
    loader._load = originalLoad;
  }
});

if (failures > 0) {
  console.error(`${failures} rehearsal labelling test(s) failed`);
  process.exit(1);
}
console.log("Rehearsal labelling: all checks passed");
