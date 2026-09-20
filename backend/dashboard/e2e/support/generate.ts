import { expect, type Page } from "@playwright/test";

/**
 * Generate (or regenerate) the package for the return and reporting date the
 * workspace is on, and return once the minted version is the one on screen.
 *
 * A visible "Generated" badge is not that signal. Regeneration supersedes the
 * version already on screen, which reads Generated (or Validated) the whole
 * time, so the badge is visible before the package list has refetched. An
 * action taken inside that window — an export, a disclosure click in Prior
 * versions — lands on the version about to be superseded: the export is
 * refused as immutable history and the disclosure is pushed down the list by
 * its successor. That is the race the workspace's own Validate control locks
 * against; waiting until the workspace names the minted version as current
 * closes it for the journeys.
 */
export async function generateCurrentVersion(page: Page): Promise<number> {
  const generate = page
    .getByRole("button", { name: /generate package|regenerate/i })
    .first();
  await expect(generate).toBeVisible({ timeout: 5_000 });
  const minted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/regulatory-packages$/.test(new URL(response.url()).pathname) &&
      response.ok(),
  );
  await generate.click();
  const { version } = (await (await minted).json()) as { version: number };
  await expect(
    page.getByText(
      new RegExp(`^v${version} (\\(current\\)|is the only version)`),
    ),
  ).toBeVisible({ timeout: 30_000 });
  return version;
}
