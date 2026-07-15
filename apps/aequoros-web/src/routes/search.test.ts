import { CaseSort, CaseStatus, RiskLevel } from "@aequoros/risk-service-api";
import { describe, expect, it } from "vitest";

import { parseSearchState } from "./search";

describe("parseSearchState", () => {
  it("parses typed console search params", () => {
    expect(
      parseSearchState({
        tab: "report",
        report: "html",
        q: "northstar",
        status: CaseStatus.InReview,
        risk: RiskLevel.High,
        archived: "true",
        sort: CaseSort.RiskScoreDesc,
        page: "3",
      }),
    ).toEqual({
      tab: "report",
      report: "html",
      q: "northstar",
      status: CaseStatus.InReview,
      risk: RiskLevel.High,
      archived: true,
      sort: CaseSort.RiskScoreDesc,
      page: 3,
      mode: "cases",
      almTab: "overview",
      bankId: undefined,
      periodId: undefined,
    });
  });

  it("defaults report mode to json and archived to false", () => {
    expect(parseSearchState({ report: "xml", archived: "false" })).toMatchObject({
      report: "json",
      archived: false,
    });
  });

  it("parses ALM regulatory mode params", () => {
    expect(
      parseSearchState({
        mode: "alm",
        almTab: "capital-stress",
        bankId: "33333333-3333-4333-8333-333333333333",
        periodId: "44444444-4444-4444-8444-444444444444",
      }),
    ).toMatchObject({
      mode: "alm",
      almTab: "capital-stress",
      bankId: "33333333-3333-4333-8333-333333333333",
      periodId: "44444444-4444-4444-8444-444444444444",
    });
  });

  it("keeps existing URLs on the cases mode with the overview ALM tab", () => {
    expect(parseSearchState({ tab: "report", almTab: "not-a-tab" })).toMatchObject({
      tab: "report",
      mode: "cases",
      almTab: "overview",
    });
  });
});
