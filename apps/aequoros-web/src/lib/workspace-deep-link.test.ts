import { afterEach, describe, expect, it } from "vitest";

import { workspaceHash } from "./workspace-deep-link";

describe("workspaceHash", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "#");
  });

  it("decodes a valid workspace target", () => {
    window.history.replaceState(null, "", "#financial-balances-record%201");

    expect(workspaceHash()).toBe("financial-balances-record 1");
  });

  it("fails closed for a malformed encoded target", () => {
    window.history.replaceState(null, "", "#financial-balances-%E0%A4%A");

    expect(workspaceHash()).toBe("");
  });
});
