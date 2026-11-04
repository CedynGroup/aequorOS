import { expect, type Page } from "@playwright/test";

export async function expectAlignedHeaderAndCards(page: Page) {
  const heading = page.getByRole("heading", { level: 1 });
  const header = heading.locator("../../../..");
  const firstCard = page.locator("main .card").first();
  await expect(firstCard).toBeVisible();
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect
      .poll(async () => {
        const titleBox = await heading.boundingBox();
        const headerContent = await header
          .locator(":scope > div")
          .boundingBox();
        // The first card may sit in a grid or in a row of cards (the BI
        // insight strip every landing page opens with). Either way the
        // container is the content edge; a row's cards are capped in width
        // and scroll, so the card's own box would never reach the right edge.
        const grid = await firstCard.evaluate((card) => {
          const parent = card.parentElement;
          const display = parent ? getComputedStyle(parent).display : "";
          const body =
            parent && (display === "grid" || display === "flex")
              ? parent
              : card;
          const box = body.getBoundingClientRect();
          return { left: box.left, right: box.right };
        });
        return {
          left: Math.round((titleBox?.x ?? -1) - grid.left),
          right: Math.round(
            (headerContent ? headerContent.x + headerContent.width : -1) -
              grid.right,
          ),
        };
      })
      .toEqual({ left: 0, right: 0 });
    await expect(header).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(header).toHaveCSS("border-bottom-width", "0px");
    await expect(header).toHaveCSS("box-shadow", "none");
  }
}
