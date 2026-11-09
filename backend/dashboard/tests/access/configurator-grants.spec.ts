import { describe, expect, it } from "vitest";
import { BindingCreateRequestToJSON } from "@aequoros/risk-service-api";
import {
  ROLE_OPTIONS,
  WHOLE_INSTITUTION_BOOK,
  grantCreateRequest,
  grantPreviewRequest,
  roleBundleLabel,
  type GrantDraft,
} from "../../lib/api/grants";
import {
  grantShortfall,
  overlappingGrantNotice,
} from "../../lib/api/grantRequirements";

const draft: GrantDraft = {
  roleBundle: "configurator",
  institutionScope: "institution",
  institutionId: "BK-CFGU0001",
  moduleScope: "liq",
  sensitivityScope: "restricted",
  dataScope: WHOLE_INSTITUTION_BOOK,
  reasonCategory: "other",
  reasonDetail: "Treasury configuration responsibility",
  reference: "",
  validUntil: "",
};

describe("Configurator grants", () => {
  it("offers the named role and labels stored grants", () => {
    expect(ROLE_OPTIONS).toContainEqual(["configurator", "Configurator"]);
    expect(roleBundleLabel("configurator")).toBe("Configurator");
  });

  it("serializes one exact bank, module and sensitivity with the server's sentence", () => {
    const sentence =
      "Officer is a Configurator in Liquidity Monitoring for Validation Bank, covering Restricted data.";
    expect(grantPreviewRequest(draft, "officer")).toMatchObject({
      roleBundle: "configurator",
      institutionScope: "institution",
      institutionId: "BK-CFGU0001",
      moduleScope: "liq",
      sensitivityScope: "restricted",
    });
    expect(
      BindingCreateRequestToJSON(
        grantCreateRequest(draft, "officer", sentence),
      ),
    ).toMatchObject({
      role_bundle: "configurator",
      institution_scope: "institution",
      institution_id: "BK-CFGU0001",
      module_scope: "liq",
      sensitivity_scope: "restricted",
      expected_authority_sentence: sentence,
    });
  });

  it("warns when configuration's Restricted sensitivity is missing", () => {
    for (const sensitivityScope of [
      "published",
      "aggregated",
      "confidential",
    ] as const) {
      expect(grantShortfall({ ...draft, sensitivityScope })).toBe(
        "This grant won't let them configure settings: that needs Restricted data.",
      );
    }
  });

  it("accepts exact Restricted or explicit all sensitivity without a filing-module requirement", () => {
    for (const moduleScope of [
      "liq",
      "credit",
      "data",
      "markets",
      "reg",
      "beh",
    ] as const) {
      expect(grantShortfall({ ...draft, moduleScope })).toBeNull();
      expect(
        grantShortfall({ ...draft, moduleScope, sensitivityScope: "all" }),
      ).toBeNull();
    }
  });

  it("explains that a second Configurator grant does not widen another row", () => {
    expect(
      overlappingGrantNotice(draft, [
        {
          roleBundle: "configurator",
          institutionId: draft.institutionId,
          moduleScope: "credit",
          sensitivityScope: "confidential",
          status: "active",
        },
      ]),
    ).toBe(
      "They already have Configurator here; this adds a separate grant rather than widening it.",
    );
  });
});
