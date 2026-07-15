import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_ORG_ID,
  DEFAULT_TENANT_OPTIONS,
  type TenantDirectory,
  activeTenantOption,
  tenantConfiguration,
} from "./constants";

describe("tenantConfiguration", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses seeded tenants when runtime configuration is absent", () => {
    vi.stubEnv("VITE_RISK_TENANTS", undefined);

    expect(tenantConfiguration()).toEqual({
      status: "ready",
      tenants: DEFAULT_TENANT_OPTIONS,
    });
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
    const configuration = tenantConfiguration();

    expect(configuration).toEqual({
      status: "ready",
      tenants: configured,
    });
    expect(
      configuration.status === "ready" &&
        configuration.tenants.some((tenant) => tenant.orgId === DEFAULT_ORG_ID),
    ).toBe(false);
  });

  it.each([
    ["", "must contain valid JSON"],
    ["not-json", "must contain valid JSON"],
    ["[]", "must be a non-empty JSON array"],
    ['[{"name":"Configured Bank","orgId":"org-1"}]', "entry 1"],
  ])("rejects explicitly supplied invalid tenant JSON", (value, reason) => {
    vi.stubEnv("VITE_RISK_TENANTS", value);

    expect(tenantConfiguration()).toEqual({
      status: "error",
      error: expect.stringContaining(reason),
    });
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
