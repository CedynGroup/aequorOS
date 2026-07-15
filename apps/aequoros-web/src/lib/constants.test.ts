import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_ORG_ID,
  type TenantDirectory,
  activeTenantOption,
  tenantOptions,
} from "./constants";

describe("tenantOptions", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses runtime-configured tenants instead of demo defaults", () => {
    const configured: TenantDirectory = [
      {
        name: "Configured Bank",
        orgId: "33333333-3333-4333-8333-333333333333",
        userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      },
    ];
    vi.stubEnv("VITE_RISK_TENANTS", JSON.stringify(configured));

    expect(tenantOptions()).toEqual(configured);
    expect(
      tenantOptions().some((tenant) => tenant.orgId === DEFAULT_ORG_ID),
    ).toBe(false);
  });

  it("falls back from a stale persisted organization to configured context", () => {
    const configured: TenantDirectory = [
      {
        name: "Configured Bank",
        orgId: "33333333-3333-4333-8333-333333333333",
        userId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      },
    ];

    expect(activeTenantOption(configured, DEFAULT_ORG_ID)).toEqual(
      configured[0],
    );
  });
});
