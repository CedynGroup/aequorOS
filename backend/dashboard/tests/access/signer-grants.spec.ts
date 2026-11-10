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
  roleBundle: "signer",
  institutionScope: "institution",
  institutionId: "BK-SIGNU001",
  moduleScope: "reg",
  sensitivityScope: "restricted",
  dataScope: WHOLE_INSTITUTION_BOOK,
  reasonCategory: "other",
  reasonDetail: "Named filing signatory",
  reference: "",
  validUntil: "",
};

describe("Signer grants", () => {
  it("offers the named role and labels stored grants", () => {
    expect(ROLE_OPTIONS).toContainEqual(["signer", "Signer"]);
    expect(roleBundleLabel("signer")).toBe("Signer");
  });

  it("serializes one exact bank, module and sensitivity with the server's sentence", () => {
    const sentence =
      "Officer is a Signer in Regulatory Reporting for Validation Bank, covering Restricted data.";
    expect(grantPreviewRequest(draft, "officer")).toMatchObject({
      roleBundle: "signer",
      institutionScope: "institution",
      institutionId: "BK-SIGNU001",
      moduleScope: "reg",
      sensitivityScope: "restricted",
    });
    expect(
      BindingCreateRequestToJSON(
        grantCreateRequest(draft, "officer", sentence),
      ),
    ).toMatchObject({
      role_bundle: "signer",
      institution_scope: "institution",
      institution_id: "BK-SIGNU001",
      module_scope: "reg",
      sensitivity_scope: "restricted",
      expected_authority_sentence: sentence,
    });
  });

  it("warns when signing's Restricted sensitivity is missing", () => {
    for (const sensitivityScope of [
      "published",
      "aggregated",
      "confidential",
    ] as const) {
      expect(grantShortfall({ ...draft, sensitivityScope })).toBe(
        "This grant won't let them sign filings or enterprise stress results: that needs Restricted data.",
      );
    }
  });

  it("accepts REG or RISK at exact Restricted or explicit all sensitivity", () => {
    for (const moduleScope of ["reg", "risk", "all"] as const) {
      expect(grantShortfall({ ...draft, moduleScope })).toBeNull();
      expect(
        grantShortfall({ ...draft, moduleScope, sensitivityScope: "all" }),
      ).toBeNull();
    }
  });

  it("warns when organization-wide coverage cannot satisfy exact-bank signing", () => {
    expect(
      grantShortfall({
        ...draft,
        institutionScope: "organization",
        institutionId: undefined,
      }),
    ).toBe(
      "This grant won't let them sign filings or enterprise stress results: that needs an exact institution.",
    );
  });

  it("warns when the module cannot carry signing authority", () => {
    expect(grantShortfall({ ...draft, moduleScope: "liq" })).toBe(
      "This grant won't let them sign filings or enterprise stress results: that needs Regulatory Reporting or Risk & Limits.",
    );
    expect(
      grantShortfall({
        ...draft,
        moduleScope: "liq",
        sensitivityScope: "confidential",
      }),
    ).toBe(
      "This grant won't let them sign filings or enterprise stress results: that needs Regulatory Reporting or Risk & Limits and Restricted data.",
    );
  });

  it("explains that a second Signer grant does not widen another row", () => {
    expect(
      overlappingGrantNotice(draft, [
        {
          roleBundle: "signer",
          institutionId: draft.institutionId,
          moduleScope: "risk",
          sensitivityScope: "confidential",
          status: "active",
        },
      ]),
    ).toBe(
      "They already have Signer here; this adds a separate grant rather than widening it.",
    );
  });
});
