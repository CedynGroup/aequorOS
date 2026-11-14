import { describe, expect, it } from "vitest";
import type { EffectiveAuthorityRead } from "@aequoros/risk-service-api";
import { filingAuthorityFor } from "../../lib/submissions/filingAuthority";

const EXPORTER_AUTHORITY: EffectiveAuthorityRead = {
  authv: 1,
  organizationCapabilities: [],
  institutionCapabilities: [
    {
      institutionId: "BK-A",
      capabilities: (
        ["view", "export"] as const
      ).map((permission) => ({
        module: "reg",
        sensitivity: "restricted",
        permission,
        dataScope: { kind: "all", branches: [], regions: [] },
        requiresContextualAuthorization: false,
      })),
    },
  ],
};

const VIEWER_AUTHORITY: EffectiveAuthorityRead = {
  authv: 1,
  organizationCapabilities: [],
  institutionCapabilities: [
    {
      institutionId: "BK-A",
      capabilities: [
        {
          module: "reg",
          sensitivity: "restricted",
          permission: "view",
          dataScope: { kind: "all", branches: [], regions: [] },
          requiresContextualAuthorization: false,
        },
      ],
    },
  ],
};

describe("exporter filing authority", () => {
  it("offers export without any preparation authority", () => {
    const authority = filingAuthorityFor(EXPORTER_AUTHORITY, "BK-A", {
      resolved: true,
    });
    expect(authority.isResolved).toBe(true);
    expect(authority.isReadOnly).toBe(false);
    expect(authority.mayExport).toBe(true);
    expect(authority.mayPrepare).toBe(false);
    expect(authority.mayRunChecks).toBe(false);
    expect(authority.mayApprove).toBe(false);
    expect(authority.mayTransmit).toBe(false);
  });

  it("keeps a view-only reader out of the export surface", () => {
    const authority = filingAuthorityFor(VIEWER_AUTHORITY, "BK-A", {
      resolved: true,
    });
    expect(authority.mayExport).toBe(false);
    expect(authority.mayPrepare).toBe(false);
  });

  it("never offers the exporter another bank's or an unresolved projection", () => {
    expect(
      filingAuthorityFor(EXPORTER_AUTHORITY, "BK-B", { resolved: true }).mayExport,
    ).toBe(false);
    expect(
      filingAuthorityFor(EXPORTER_AUTHORITY, "BK-A", { resolved: false }).mayExport,
    ).toBe(false);
  });
});
