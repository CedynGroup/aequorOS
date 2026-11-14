import { expect, type Page } from "@playwright/test";

/**
 * Best-effort readiness for reviewer-facing captures: every data-loading
 * skeleton in the app renders with the `animate-pulse` shimmer and clears when
 * its query lands, and charts render with `animation: false`
 * (lib/echartsOptions.ts), so a capture with no shimmer left shows the page's
 * settled state. Only shimmer inside the viewport is awaited: a page can keep
 * permanent shimmering placeholders below the fold (the home page's deferred
 * trend-chart chips wait for scroll, for instance), and those are not a
 * loading state. Evidence quality only — a residual skeleton must never fail
 * the journey the capture documents, so the wait is tolerated when it times
 * out (the same tolerance the `waitForLoadState("networkidle")` it replaced
 * carried).
 */
export async function settleForCapture(
  page: Page,
  timeout = 15_000,
): Promise<void> {
  try {
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            Array.from(document.querySelectorAll(".animate-pulse")).filter(
              (element) => {
                const box = element.getBoundingClientRect();
                return box.bottom > 0 && box.top < window.innerHeight;
              },
            ).length,
          ),
        { timeout },
      )
      .toBe(0);
  } catch {
    // Evidence-only: capture whatever has rendered rather than fail the journey.
  }
}
