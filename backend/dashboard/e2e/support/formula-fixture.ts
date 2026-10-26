import { expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { biApi, SAMPLE_BANK_ID } from "./bi";

export function digestOf(source: string): string {
  return createHash("sha256").update(Buffer.from(source, "utf8")).digest("hex");
}

export function formulaFixture(expression: string) {
  /** Prepare only this story's prerequisite through supported governance routes. */
  return async function prepareFormula(
    request: Parameters<typeof biApi>[0],
    objects: { create(route: string, data: object): Promise<string> },
    key: string,
    label: string,
    state: "draft" | "proposed" | "returned" | "reproposed" | "certified",
  ): Promise<string> {
    const route = `/banks/${SAMPLE_BANK_ID}/bi/measures`;
    const id = await objects.create(route, {
      measure_key: key,
      label,
      expression: expression,
      value_type: "pct",
      description: "Independent synthetic formula",
      favourable_direction: "lower_better",
    });
    const propose = async () => {
      const result = await biApi(request, "admin", `${route}/${id}/proposal`, {
        method: "POST",
        data: { reason: "Review this story's exact formula" },
      });
      expect(result.status, JSON.stringify(result.body)).toBe(200);
    };
    const decide = async (decision: "approve" | "reject") => {
      const result = await biApi(
        request,
        "approver",
        `${route}/${id}/decision`,
        {
          method: "POST",
          data: {
            decision,
            reason: "Independent prerequisite review",
            expression_digest: digestOf(expression),
          },
        },
      );
      expect(result.status, JSON.stringify(result.body)).toBe(200);
    };
    if (state !== "draft") await propose();
    if (state === "returned" || state === "reproposed") await decide("reject");
    if (state === "reproposed") await propose();
    if (state === "certified") await decide("approve");
    return id;
  };
}
