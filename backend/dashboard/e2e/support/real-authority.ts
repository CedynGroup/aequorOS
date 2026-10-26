import { expect } from "@playwright/test";
import { BankReportingPeriodListReadFromJSON } from "@aequoros/risk-service-api";
import { E2E_API_ORIGIN } from "../../playwright.config";
import { mintBackendToken } from "./mint";

/** Bypass the UI guard with the same actor's real current credential. */
export async function expectBankStatus(
  route: string,
  status: number,
  options: {
    role?: string;
    bankId?: string;
    method?: string;
    data?: unknown;
  } = {},
): Promise<void> {
  const response = await fetch(
    `${E2E_API_ORIGIN}/api/v1/banks/${options.bankId ?? "BK-SAMP0001"}${route}`,
    {
      method: options.method ?? "GET",
      headers: {
        Authorization: `Bearer ${await mintBackendToken(options.role ?? "viewer")}`,
        ...(options.data === undefined
          ? {}
          : { "Content-Type": "application/json" }),
      },
      body:
        options.data === undefined ? undefined : JSON.stringify(options.data),
    },
  );
  expect(response.status, await response.text()).toBe(status);
}

/** Use an existing bank period so a refused mutation has a valid request body. */
export async function currentBankPeriod(role = "viewer"): Promise<string> {
  const response = await fetch(
    `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001/reporting-periods`,
    { headers: { Authorization: `Bearer ${await mintBackendToken(role)}` } },
  );
  expect(response.status).toBe(200);
  const periods = BankReportingPeriodListReadFromJSON(
    await response.json(),
  ).periods;
  expect(periods.length).toBeGreaterThan(0);
  return periods[0].id;
}
