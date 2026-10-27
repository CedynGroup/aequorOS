/** HTTP query authority and refusal without partial data disclosure. */
import { expect, test } from "@playwright/test";
import { biApi, fixtureAsOf, SAMPLE_BANK_ID } from "../e2e/support/bi";

test.describe("bi-authorization HTTP contracts", () => {
  test("is refused a query it could not have built, and gets no partial answer", async ({
    request,
  }) => {
    const asOf = await fixtureAsOf(request);
    // The catalogue never offered this member to this reader, so the only way
    // to ask is to construct the request — which is exactly what an attacker
    // would do, and the route decides again rather than trusting the surface.
    const { status, body } = await biApi(
      request,
      "liquidity_viewer",
      `/banks/${SAMPLE_BANK_ID}/bi/query`,
      {
        method: "POST",
        data: {
          measures: ["loans.balance_rc"],
          dimensions: ["counterparty.name"],
          time: { as_of: asOf },
          filters: [],
        },
      },
    );
    expect(status).toBe(403);
    const text = JSON.stringify(body);
    expect(text).toContain("bi_authorization_denied");
    // No rows, no total, no partial column set.
    expect(text).not.toContain("84850000");
    expect(text).not.toContain('"rows"');
  });
});
