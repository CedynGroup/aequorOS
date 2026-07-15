import {
  BulkCaseActionFailureCode,
  CaseStatus,
  type CaseBulkActionRead,
} from "@aequoros/risk-service-api";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID } from "../../lib/constants";
import { mockCase } from "../demo-data/demo-data";
import { BulkResult } from "./bulk-actions";
import { RiskConsoleRoute } from "./risk-console";

describe("BulkResult", () => {
  it("separates bulk action successes and failures", () => {
    const result = {
      succeeded: [
        {
          caseId: "11111111-1111-4111-8111-111111111111",
          status: CaseStatus.InReview,
          _case: mockCase(
            DEFAULT_ORG_ID,
            "11111111-1111-4111-8111-111111111111",
          ),
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

afterEach(() => {
  vi.unstubAllEnvs();
});

it("renders invalid tenant configuration without mounting the console", () => {
  vi.stubEnv("VITE_RISK_TENANTS", "[]");

  render(<RiskConsoleRoute />);

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Tenant configuration error",
  );
  expect(screen.getByRole("alert")).toHaveTextContent("VITE_RISK_TENANTS");
  expect(screen.queryByText("Risk operations")).not.toBeInTheDocument();
});
