import { expect, type Page } from "@playwright/test";

/**
 * Best-effort readiness for reviewer-facing captures: every data-loading
 * skeleton in the app renders with the `animate-pulse` shimmer and clears when
 * its query lands, and charts render with `animation: false`
 * (lib/echartsOptions.ts), so a capture with no shimmer left shows the page's
 * settled state. Evidence quality only — a residual skeleton must never fail
 * the journey the capture documents, so the wait is tolerated when it times
 * out (the same tolerance the `waitForLoadState("networkidle")` it replaced
 * carried).
 */
export async function settleForCapture(
  page: Page,
  timeout = 15_000,
): Promise<void> {
  try {
    await expect(page.locator(".animate-pulse")).toHaveCount(0, { timeout });
  } catch {
    // Evidence-only: capture whatever has rendered rather than fail the journey.
  }
}
