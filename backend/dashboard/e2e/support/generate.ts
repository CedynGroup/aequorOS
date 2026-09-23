import { expect, type Page } from "@playwright/test";

/**
 * Generate (or regenerate) the return for the reporting date the workspace is
 * on, and return once the minted version is the one on screen.
 *
 * A visible "Checks passed" badge is not that signal. Regeneration supersedes
 * the version already on screen, whose badge stays visible until the package
 * list has refetched. An action taken inside that window — producing a file,
 * opening a row under Earlier versions — lands on the version about to be
 * superseded: the export is refused as immutable history and the opened row
 * is pushed down the list by its successor. That is the race the workspace's
 * own version-bound controls lock against; waiting until the header names the
 * minted version closes it for the journeys.
 */
export async function generateCurrentVersion(page: Page): Promise<number> {
  const generate = page
    .getByRole("button", { name: /generate the return|^regenerate$/i })
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
    page.getByText(new RegExp(`\\bVersion ${version} ·`)),
  ).toBeVisible({ timeout: 30_000 });
  return version;
}
