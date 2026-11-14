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
  roleBundle: "exporter",
  institutionScope: "institution",
  institutionId: "BK-EXPM0001",
  moduleScope: "reg",
  sensitivityScope: "restricted",
  dataScope: WHOLE_INSTITUTION_BOOK,
  reasonCategory: "other",
  reasonDetail: "Regulator liaison reads and collects finished returns",
  reference: "",
  validUntil: "",
};

describe("Exporter grants", () => {
  it("offers the named role and labels stored grants", () => {
    expect(ROLE_OPTIONS).toContainEqual(["exporter", "Exporter"]);
    expect(roleBundleLabel("exporter")).toBe("Exporter");
  });

  it("serializes one exact bank, module and sensitivity with the server's sentence", () => {
    const sentence =
      "Officer is an Exporter in Regulatory Reporting for Validation Bank, covering Restricted data.";
    expect(grantPreviewRequest(draft, "officer")).toMatchObject({
      roleBundle: "exporter",
      institutionScope: "institution",
      institutionId: "BK-EXPM0001",
      moduleScope: "reg",
      sensitivityScope: "restricted",
    });
    expect(
      BindingCreateRequestToJSON(
        grantCreateRequest(draft, "officer", sentence),
      ),
    ).toMatchObject({
      role_bundle: "exporter",
      institution_scope: "institution",
      institution_id: "BK-EXPM0001",
      module_scope: "reg",
      sensitivity_scope: "restricted",
      expected_authority_sentence: sentence,
    });
  });

  it("warns when the export surface's module or sensitivity is missing", () => {
    for (const moduleScope of ["liq", "credit", "data", "markets"] as const) {
      expect(grantShortfall({ ...draft, moduleScope })).toBe(
        "This grant won't let them download finished returns: that needs Regulatory Reporting.",
      );
    }
    expect(grantShortfall({ ...draft, moduleScope: "all" })).toBeNull();
    for (const sensitivityScope of [
      "published",
      "aggregated",
      "confidential",
    ] as const) {
      expect(grantShortfall({ ...draft, sensitivityScope })).toBe(
        "This grant won't let them download finished returns: that needs Restricted data.",
      );
    }
    expect(grantShortfall({ ...draft, sensitivityScope: "all" })).toBeNull();
  });

  it("explains that a second Exporter grant does not widen another row", () => {
    expect(
      overlappingGrantNotice(draft, [
        {
          roleBundle: "exporter",
          institutionId: draft.institutionId,
          moduleScope: "reg",
          sensitivityScope: "restricted",
          status: "active",
        },
      ]),
    ).toBe(
      "They already have Exporter here; this adds a separate grant rather than widening it.",
    );
  });
});
