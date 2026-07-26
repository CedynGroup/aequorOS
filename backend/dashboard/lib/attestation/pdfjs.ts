'use client';

/**
 * pdf.js, loaded in the browser and only when a document is actually opened.
 *
 * Three decisions here are deliberate rather than incidental:
 *
 * 1. **Dynamic import.** The library is over a megabyte and touches DOM globals
 *    (`DOMMatrix`, `Path2D`) that do not exist in the Next server runtime, so a
 *    static import would both bloat every route and break server rendering of
 *    any page that merely links to the signing workspace.
 * 2. **The worker is bundled from this package, never fetched from a CDN.** The
 *    document being parsed is an unfiled regulatory return; routing it — or the
 *    code that reads it — through a third-party origin is not acceptable, and a
 *    CDN outage would take signing down. It is handed to pdf.js as a live
 *    `workerPort` built from webpack's `new Worker(new URL(…), {type:'module'})`
 *    form rather than as a `workerSrc` path: emitting the worker as a raw asset
 *    puts an ES module through Next's script-mode minifier, and the production
 *    build fails on it ("'import' and 'export' cannot be used outside of module
 *    code"). A port makes webpack compile it as the module it is.
 * 3. **The `legacy` build.** The default build uses `Promise.withResolvers`,
 *    which is absent from browsers a bank's standard desktop image may still be
 *    on. A signer who cannot open the document cannot sign it.
 * 4. **Standard font data is bundled, for the same reason as the worker.** A
 *    return is rendered by reportlab against the PDF standard-14 faces and
 *    embeds none of them, so pdf.js has to load its substitutes — and without
 *    them it throws `UnknownErrorException: Ensure that the
 *    'standardFontDataUrl' API parameter is provided` and falls back to
 *    whatever the browser has. A signer reading a return with substituted
 *    glyph metrics is reading something other than the filed document.
 *    `standardFontDataUrl` is a base URL pdf.js appends filenames to, which a
 *    bundler that content-hashes assets cannot provide, so the fonts are
 *    resolved through a factory over an explicit name→asset map instead.
 */

import type { PDFDocumentProxy } from 'pdfjs-dist';

type PdfJs = typeof import('pdfjs-dist/legacy/build/pdf.mjs');

/**
 * Every file pdf.js can ask :class:`BundledStandardFontDataFactory` for, named
 * exactly as pdf.js names it. `new URL(…, import.meta.url)` makes webpack emit
 * each one as a same-origin asset — the whole set is ~1 MB and only loads for a
 * face a document actually uses, because each entry is fetched on demand.
 */
const STANDARD_FONT_DATA: Record<string, URL> = {
  'FoxitDingbats.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitDingbats.pfb',
    import.meta.url
  ),
  'FoxitFixed.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitFixed.pfb',
    import.meta.url
  ),
  'FoxitFixedBold.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitFixedBold.pfb',
    import.meta.url
  ),
  'FoxitFixedBoldItalic.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitFixedBoldItalic.pfb',
    import.meta.url
  ),
  'FoxitFixedItalic.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitFixedItalic.pfb',
    import.meta.url
  ),
  'FoxitSerif.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitSerif.pfb',
    import.meta.url
  ),
  'FoxitSerifBold.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitSerifBold.pfb',
    import.meta.url
  ),
  'FoxitSerifBoldItalic.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitSerifBoldItalic.pfb',
    import.meta.url
  ),
  'FoxitSerifItalic.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitSerifItalic.pfb',
    import.meta.url
  ),
  'FoxitSymbol.pfb': new URL(
    'pdfjs-dist/standard_fonts/FoxitSymbol.pfb',
    import.meta.url
  ),
  'LiberationSans-Bold.ttf': new URL(
    'pdfjs-dist/standard_fonts/LiberationSans-Bold.ttf',
    import.meta.url
  ),
  'LiberationSans-BoldItalic.ttf': new URL(
    'pdfjs-dist/standard_fonts/LiberationSans-BoldItalic.ttf',
    import.meta.url
  ),
  'LiberationSans-Italic.ttf': new URL(
    'pdfjs-dist/standard_fonts/LiberationSans-Italic.ttf',
    import.meta.url
  ),
  'LiberationSans-Regular.ttf': new URL(
    'pdfjs-dist/standard_fonts/LiberationSans-Regular.ttf',
    import.meta.url
  ),
};

/**
 * pdf.js' `StandardFontDataFactory` port, satisfied from the bundle.
 *
 * pdf.js constructs this itself with a `{ baseUrl }` it takes from
 * `standardFontDataUrl`; the argument is ignored here because the map, not a
 * directory, is the source of truth.
 */
class BundledStandardFontDataFactory {
  async fetch({ filename }: { filename: string }): Promise<Uint8Array> {
    const asset = STANDARD_FONT_DATA[filename];
    if (asset === undefined) {
      throw new Error(
        `pdf.js requested standard font data (${filename}) that is not bundled.`
      );
    }
    const response = await fetch(asset.href);
    if (!response.ok) {
      throw new Error(
        `Standard font data ${filename} failed to load (${response.status}).`
      );
    }
    return new Uint8Array(await response.arrayBuffer());
  }
}

let library: Promise<PdfJs> | null = null;

function pdfjs(): Promise<PdfJs> {
  library ??= import('pdfjs-dist/legacy/build/pdf.mjs').then((lib) => {
    lib.GlobalWorkerOptions.workerPort = new Worker(
      new URL('pdfjs-dist/legacy/build/pdf.worker.min.mjs', import.meta.url),
      { type: 'module' }
    );
    return lib;
  });
  return library;
}

/**
 * Parse PDF bytes into a document handle.
 *
 * `isEvalSupported: false` turns off pdf.js' font-compilation eval path: the
 * bytes come from our own artifact store, but a viewer that evaluates code out
 * of a document is not something to run beside a signing key.
 */
export async function loadPdfDocument(
  data: ArrayBuffer
): Promise<PDFDocumentProxy> {
  const lib = await pdfjs();
  return lib.getDocument({
    data,
    isEvalSupported: false,
    StandardFontDataFactory: BundledStandardFontDataFactory,
  }).promise;
}
