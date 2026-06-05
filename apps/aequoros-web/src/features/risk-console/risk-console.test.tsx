import {
  BulkCaseActionFailureCode,
  CaseStatus,
  type CaseBulkActionRead,
} from "@aequoros/risk-service-api";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DEFAULT_ORG_ID } from "../../lib/constants";
import { mockCase } from "../demo-data/demo-data";
import { BulkResult } from "./bulk-actions";

describe("BulkResult", () => {
  it("separates bulk action successes and failures", () => {
    const result = {
      succeeded: [
        {
          caseId: "11111111-1111-4111-8111-111111111111",
          status: CaseStatus.InReview,
          _case: mockCase(DEFAULT_ORG_ID, "11111111-1111-4111-8111-111111111111"),
        },
      ],
      failed: [
        {
          caseId: "22222222-2222-4222-8222-222222222222",
          statusCode: 409,
          error: {
            code: BulkCaseActionFailureCode.Conflict,
            message: "Archived case cannot be updated",
          },
        },
      ],
    } satisfies CaseBulkActionRead;

    render(<BulkResult result={result} />);

    expect(screen.getByText("Succeeded (1)")).toBeInTheDocument();
    expect(screen.getByText("Failed (1)")).toBeInTheDocument();
    expect(screen.getByText(/conflict/)).toBeInTheDocument();
  });
});
