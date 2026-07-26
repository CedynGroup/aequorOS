'use client';

/**
 * The return, rendered. One page at a time onto a canvas, with an absolutely
 * positioned overlay slot the field boxes live in.
 *
 * The signer is about to put their name and permanent signer ID on this exact
 * document, so it is the real artifact bytes — the archived unsigned PDF export
 * — not a re-render of the snapshot. Anything else would make
 * what-you-see-is-what-you-sign a claim about two different documents.
 *
 * Rendering is at `devicePixelRatio` and scaled back down in CSS, because a
 * blurred figure on the page a signer is certifying is not a cosmetic problem.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { loadPdfDocument } from '@/lib/attestation/pdfjs';
import { pageSpace, type PageSpace } from '@/lib/attestation/geometry';

export default function DocumentCanvas({
  bytes,
  pageIndex,
  scale,
  onDocumentLoaded,
  onPageSpace,
  children,
}: {
  bytes: ArrayBuffer | null;
  /** 0-based, matching the placement contract. */
  pageIndex: number;
  scale: number;
  onDocumentLoaded: (pageCount: number) => void;
  /** Fires whenever the page or the zoom changes the pixel↔point transform. */
  onPageSpace: (space: PageSpace) => void;
  /** The placement overlay, positioned over the rendered page. */
  children?: ReactNode;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  // The callbacks come from a parent that re-renders on every drag frame;
  // holding them in refs keeps the (expensive) render effect keyed on the page
  // and the zoom alone.
  const loadedRef = useRef(onDocumentLoaded);
  const spaceRef = useRef(onPageSpace);
  loadedRef.current = onDocumentLoaded;
  spaceRef.current = onPageSpace;

  useEffect(() => {
    if (!bytes) return;
    let cancelled = false;
    let opened: PDFDocumentProxy | null = null;
    setError(null);
    // pdf.js takes ownership of the buffer it is handed and detaches it, so a
    // re-render (or React's development double-effect) must not be given the
    // same one twice.
    loadPdfDocument(bytes.slice(0))
      .then((doc) => {
        if (cancelled) {
          void doc.destroy();
          return;
        }
        opened = doc;
        setDocument(doc);
        loadedRef.current(doc.numPages);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof Error
            ? `The document could not be opened: ${cause.message}`
            : 'The document could not be opened.'
        );
      });
    return () => {
      cancelled = true;
      void opened?.destroy();
      setDocument(null);
    };
  }, [bytes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!document || !canvas) return;
    let cancelled = false;
    let task: { cancel: () => void } | null = null;
    setRendering(true);

    void (async () => {
      try {
        const page = await document.getPage(pageIndex + 1);
        if (cancelled) return;
        const viewport = page.getViewport({ scale });
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        const context = canvas.getContext('2d');
        if (!context) return;
        setSize({ width: viewport.width, height: viewport.height });
        spaceRef.current(pageSpace(pageIndex, viewport));
        const render = page.render({
          canvasContext: context,
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        task = render;
        await render.promise;
      } catch (cause: unknown) {
        // A cancelled render is the expected outcome of paging or zooming
        // quickly; it is not a failure to report to a signer.
        if (!cancelled && (cause as { name?: string })?.name !== 'RenderingCancelledException') {
          setError(
            cause instanceof Error
              ? `Page ${pageIndex + 1} could not be drawn: ${cause.message}`
              : `Page ${pageIndex + 1} could not be drawn.`
          );
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [document, pageIndex, scale]);

  const retry = useCallback(() => setError(null), []);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3"
      >
        <p className="inline-flex items-center gap-2 text-body font-medium text-navy">
          <AlertTriangle size={14} className="text-critical" aria-hidden />
          The return could not be displayed
        </p>
        <p className="mt-1 text-caption text-navy/85 leading-relaxed">{error}</p>
        <p className="mt-1.5 text-caption text-navy/85 leading-relaxed">
          Signing is blocked rather than allowed against a document nobody could
          see. Re-export the PDF artifact from the Export card and reopen this
          workspace.
        </p>
        <button
          type="button"
          onClick={retry}
          className="mt-2.5 inline-flex items-center px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    // `self-start` is load-bearing, not spacing: this sits in a centring flex
    // container, where the default `stretch` would size it to the SCROLL PORT
    // and not to the page. The overlay is positioned against this box, so a
    // stretched one silently stopped covering the bottom of a page taller than
    // the window — fields below the fold could not be dropped, and the failure
    // looked like a dead click rather than a layout bug.
    <div className="relative inline-block align-top self-start">
      <canvas
        ref={canvasRef}
        aria-label={`Return document, page ${pageIndex + 1}`}
        className="block bg-white shadow-pop rounded-sm"
        style={size ? { width: size.width, height: size.height } : undefined}
      />
      {size && children}
      {(rendering || !document) && (
        <div className="absolute inset-0 flex items-center justify-center bg-surface-raised/60">
          <Loader2 size={20} className="animate-spin text-action" aria-label="Rendering" />
        </div>
      )}
    </div>
  );
}
