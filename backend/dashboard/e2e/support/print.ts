import { expect, type Locator, type Page } from "@playwright/test";

/** A printed report, read back the way its reader would read the PDF. */
export type PrintedPdf = {
  bytes: Buffer;
  /** Extracted text of each page, whitespace-normalised. */
  pages: string[];
  /** Every page's text, joined. */
  text: string;
};

/**
 * Press a report's print control and return the PDF the reader would save.
 *
 * The control calls `window.print()`, whose native dialog Playwright cannot
 * drive, so the call is captured to prove the control asked for it. The file
 * is then produced by `page.pdf()`: Chromium's own print-to-PDF pipeline, under
 * print media and the report's `@page` rules — the same rendering the dialog's
 * "Save as PDF" destination writes.
 */
export async function printToPdf(
  page: Page,
  printControl: Locator,
): Promise<PrintedPdf> {
  await page.evaluate(() => {
    const target = window as unknown as { __printCalls: number };
    target.__printCalls = 0;
    window.print = () => {
      target.__printCalls += 1;
    };
  });
  await printControl.click();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as unknown as { __printCalls: number }).__printCalls,
      ),
    )
    .toBe(1);

  const bytes = await page.pdf({
    preferCSSPageSize: true,
    printBackground: true,
  });
  return { bytes, ...(await readPdfText(bytes)) };
}

/** Parse a PDF and extract its text, failing on anything pdf.js rejects. */
export async function readPdfText(
  bytes: Buffer,
): Promise<{ pages: string[]; text: string }> {
  expect(bytes.subarray(0, 5).toString("latin1")).toBe("%PDF-");
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const document = await pdfjs.getDocument({
    data: new Uint8Array(bytes),
    isEvalSupported: false,
  }).promise;
  try {
    const pages: string[] = [];
    for (let number = 1; number <= document.numPages; number += 1) {
      const content = await (await document.getPage(number)).getTextContent();
      const text = content.items
        .map((item) =>
          "str" in item ? `${item.str}${item.hasEOL ? "\n" : ""}` : "",
        )
        .join("");
      pages.push(text.replace(/\s+/g, " ").trim());
    }
    return { pages, text: pages.join(" ") };
  } finally {
    await document.destroy();
  }
}
