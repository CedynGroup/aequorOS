import assert from "node:assert/strict";
import test from "node:test";

import type { OperatorTenant } from "../../lib/api";
import {
  examinableBanks,
  examinedOptionOf,
  splitExaminedOption,
} from "../../lib/inspector-bank";

function tenant(organizationId: string, bankId: string | null): OperatorTenant {
  return {
    organization_id: organizationId,
    organization_name: `Org ${organizationId}`,
    organization_created_at: "2026-01-01T00:00:00Z",
    bank_id: bankId,
    bank_name: bankId === null ? null : `Bank ${bankId}`,
    jurisdiction_code: "GH",
    currency: "GHS",
    license_type: "universal_bank",
    bank_created_at: "2026-01-01T00:00:00Z",
    period_count: 0,
    latest_period_end: null,
  } as OperatorTenant;
}

test("two banks of one organization are separately selectable", () => {
  // `GET /operator/v1/tenants` returns one row per bank, so an organization id
  // alone would make both rows the same option and bind the session to whichever
  // `.find()` reached first — leaving the sibling bank unreachable.
  const first = examinedOptionOf("OR-DEM00001", "BK-AAA00001");
  const second = examinedOptionOf("OR-DEM00001", "BK-BBB00002");
  assert.notEqual(first, second);
  assert.deepEqual(splitExaminedOption(second), {
    organizationId: "OR-DEM00001",
    bankId: "BK-BBB00002",
  });
});

test("an option value round-trips every pair it is given", () => {
  for (const [organizationId, bankId] of [
    ["OR-DEM00001", "BK-SAMP0001"],
    ["OR-1S000002", "BK-EXAM0002"],
    ["OR-Z6190S83", "BK-1FJ6Y8JC"],
  ]) {
    assert.deepEqual(
      splitExaminedOption(examinedOptionOf(organizationId, bankId)),
      {
        organizationId,
        bankId,
      },
    );
  }
});

test("the unselected option and a malformed value both read as no choice", () => {
  assert.deepEqual(splitExaminedOption(""), { organizationId: "", bankId: "" });
  assert.deepEqual(splitExaminedOption("OR-ONLY"), {
    organizationId: "OR-ONLY",
    bankId: "",
  });
});

test("an organization with no bank yields no examinable institution", () => {
  assert.deepEqual(examinableBanks([tenant("OR-A", null)], "OR-A"), []);
});

test("only this organization's banks are offered, in board order", () => {
  const board = [
    tenant("OR-A", "BK-A1"),
    tenant("OR-B", "BK-B1"),
    tenant("OR-A", "BK-A2"),
  ];
  assert.deepEqual(
    examinableBanks(board, "OR-A").map((row) => row.bank_id),
    ["BK-A1", "BK-A2"],
  );
  assert.deepEqual(
    examinableBanks(board, "OR-NOBODY").map((row) => row.bank_id),
    [],
  );
});
