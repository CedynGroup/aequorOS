/**
 * Module tab navigation for the functional journeys.
 *
 * A client-side click returns before Next's route has finished loading, and a
 * route compiling for the first time can outlast an assertion's window. So a
 * journey moves between a module's tabs through `openTab`, which waits for the
 * clicked tab's exact destination before the caller asserts computed figures.
 */

import { expect, type Page } from "@playwright/test";

/** Click the module tab named `name` and wait until its route is the page. */
export async function openTab(page: Page, name: string): Promise<void> {
  const link = page.getByRole("link", { name, exact: true });
  const href = await link.getAttribute("href");
  expect(href).not.toBeNull();
  await Promise.all([
    page.waitForURL((url) => url.pathname === href),
    link.click(),
  ]);
}
