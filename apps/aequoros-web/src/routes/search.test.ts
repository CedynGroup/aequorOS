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
    });
  });

  it("defaults report mode to html and archived to false", () => {
    expect(parseSearchState({ report: "xml", archived: "false" })).toMatchObject({
      report: "html",
      archived: false,
    });
  });
});
