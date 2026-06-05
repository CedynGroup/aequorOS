import type { APIRequestContext } from "playwright/test";

export const apiBaseUrl =
  process.env.VITE_RISK_API_BASE_URL ?? "http://127.0.0.1:8003/api/v1";

export const healthUrl = apiBaseUrl.replace(/\/api\/v1\/?$/, "/api/health/ready");

export async function isRiskServiceReady(request: APIRequestContext) {
  const response = await request.get(healthUrl);
  return response.ok();
}
