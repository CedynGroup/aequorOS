/**
 * A role lens permutes the Command Center pulse wall; it never hides a live
 * engine. Treasurer, ALM and CFO once listed six modules and silently dropped
 * `credit` and `rating` while the wall built both cards.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";
import { ROLE_CONFIG, type RoleLens } from "./RoleLens";
import { DEFAULT_MODULE_ORDER } from "../live/moduleDisplay";

const lenses = Object.keys(ROLE_CONFIG) as RoleLens[];
assert.deepEqual(lenses.sort(), ["alm", "cfo", "risk", "treasurer"]);

let explicitOrders = 0;
for (const lens of lenses) {
  const order = ROLE_CONFIG[lens].moduleOrder;
  if (order === "severity") continue; // sorts DEFAULT_MODULE_ORDER by status
  explicitOrders += 1;

  assert.equal(
    new Set(order).size,
    order.length,
    `${lens} lens lists a module twice`,
  );
  for (const liveModule of order) {
    assert.ok(
      DEFAULT_MODULE_ORDER.includes(liveModule),
      `${lens} lens names ${liveModule}, which is not a live module`,
    );
  }
  assert.ok(order.includes("credit"), `${lens} lens hides the credit card`);
  if (DEFAULT_MODULE_ORDER.includes("rating")) {
    assert.ok(order.includes("rating"), `${lens} lens hides the rating card`);
  }
  // Current state: every explicit lens is a full permutation of the wall.
  assert.deepEqual(
    [...order].sort(),
    [...DEFAULT_MODULE_ORDER].sort(),
    `${lens} lens must show every module the wall builds`,
  );
}
assert.equal(explicitOrders, 3, "treasurer, alm and cfo carry explicit orders");

console.log(
  "RoleLens.test.ts: every lens is a permutation of the pulse wall including credit.",
);
