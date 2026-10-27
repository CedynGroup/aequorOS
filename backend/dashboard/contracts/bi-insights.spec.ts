/** HTTP certified insight filtering for wide and narrowed readers. */
import { expect, test } from "@playwright/test";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "../e2e/support/bi";

test.describe("bi-insights HTTP contracts", () => {
  test("the statements the platform will make, and the ones it withholds from a narrower reader", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    const route = `/banks/${SAMPLE_BANK_ID}/bi/insights?as_of=${asOf}`;

    const wide = await biApi(request, "admin", route);
    expect(wide.status).toBe(200);
    const wideBody = wide.body as {
      as_of: string;
      compare_to: string;
      insights: {
        rule_id: string;
        headline: string;
        detail: string;
        qualifiers: string[];
      }[];
    };

    // A statement is composed server-side from typed facts, and it is compared
    // against a prior period the caller did not have to name.
    expect(wideBody.as_of).toBe(asOf);
    expect(wideBody.compare_to).not.toBe(asOf);
    expect(wideBody.insights.length).toBeGreaterThan(0);

    // A figure that was not computed is SAID to be absent, in words, and is
    // explicitly not reported as zero or as flat.
    const gap = wideBody.insights.find(
      (insight) => insight.rule_id === "data_gap",
    );
    expect(gap).toBeDefined();
    expect(gap!.detail).toContain(
      "It is not zero, and it has not stayed flat.",
    );

    // Liquidity-only access covers advisory metrics, not certified headline
    // candidates. The refusal discloses neither unavailable figures nor labels.
    const narrow = await biApi(request, "liquidity_viewer", route);
    expect(narrow.status).toBe(403);
    expect(narrow.body).toMatchObject({
      error: { details: { error_code: "bi_insights_authorization_denied" } },
    });
    const narrowText = JSON.stringify(narrow.body);
    for (const withheld of [
      "Liquidity coverage ratio (LCR)",
      "loans.npl_ratio_pct",
      "engine.car_pct",
      "Gross loans",
      "Non-performing exposure",
      "Capital adequacy ratio",
      "Tier 1 ratio",
      "Net open position",
    ]) {
      expect(
        narrowText.includes(withheld),
        `a Liquidity-only reader's insights must not name ${withheld}`,
      ).toBe(false);
    }
  });
});
