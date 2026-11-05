// @vitest-environment jsdom
/**
 * Parsing a PDF is not painting it.
 *
 * The signing workspace gates certification on the page the officer can see, so
 * the canvas has to report two separate moments: `onDocumentLoaded` when the
 * bytes have been parsed into pages, and `onPageDrawn` only once a page render
 * has actually resolved. This file drives the real `DocumentCanvas` with a mock
 * pdf.js boundary — a document that parses on demand and a render promise that
 * resolves, rejects, or is cancelled — because a mock of the canvas as a whole
 * cannot tell those two moments apart, which is exactly the defect that let a
 * parsed-but-unpainted return be certified.
 *
 * Cancelled renders stay silent: paging and zooming cancel constantly, and
 * reporting that as a failure would close the ceremony on every scroll.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Viewport = {
  width: number;
  height: number;
  clone: (options: { scale: number }) => Viewport;
  convertToPdfPoint: (x: number, y: number) => [number, number];
  convertToViewportPoint: (x: number, y: number) => [number, number];
};

const state = vi.hoisted(() => ({
  /** How the mocked render promise settles. */
  renderOutcome: "resolve" as "resolve" | "reject" | "cancel",
  load: vi.fn(),
}));

/** A viewport good enough for `pageSpace`, which only reads and converts. */
function viewport(): Viewport {
  const box: Viewport = {
    width: 612,
    height: 792,
    clone: () => ({ ...box, width: 612, height: 792 }),
    convertToPdfPoint: (x, y) => [x, 792 - y],
    convertToViewportPoint: (x, y) => [x, 792 - y],
  };
  return box;
}

vi.mock("@/lib/attestation/pdfjs", () => ({
  loadPdfDocument: state.load,
}));

import DocumentCanvas from "../components/attestation/signing/DocumentCanvas";

beforeEach(() => {
  state.renderOutcome = "resolve";
  state.load.mockReset();
  state.load.mockImplementation(() => {
    const render = () => {
      if (state.renderOutcome === "cancel") {
        const cancelled = new Error("cancelled");
        cancelled.name = "RenderingCancelledException";
        return { promise: Promise.reject(cancelled), cancel: () => undefined };
      }
      if (state.renderOutcome === "reject") {
        return {
          promise: Promise.reject(new Error("page could not be drawn")),
          cancel: () => undefined,
        };
      }
      return { promise: Promise.resolve(), cancel: () => undefined };
    };
    return Promise.resolve({
      numPages: 2,
      getPage: () => Promise.resolve({ getViewport: () => viewport(), render }),
      loadingTask: { destroy: () => undefined },
    });
  });
});

afterEach(cleanup);

function mount() {
  const drawn = vi.fn();
  const failed = vi.fn();
  const loaded = vi.fn();
  render(
    <DocumentCanvas
      bytes={new ArrayBuffer(8)}
      pageIndex={0}
      scale={1}
      onDocumentLoaded={loaded}
      onPageSpace={() => undefined}
      onPageDrawn={drawn}
      onPageFailed={failed}
    />,
  );
  return { drawn, failed, loaded };
}

describe("DocumentCanvas render proof", () => {
  it("reports a drawn page only after the render promise resolves", async () => {
    const { drawn, failed, loaded } = mount();

    // Parsing reports the page count, which is not a painted page.
    await waitFor(() => expect(loaded).toHaveBeenCalledWith(2));
    await waitFor(() => expect(drawn).toHaveBeenCalledTimes(1));
    expect(failed).not.toHaveBeenCalled();
  });

  it("reports a failure, and never a drawn page, when the render rejects", async () => {
    state.renderOutcome = "reject";
    const { drawn, failed, loaded } = mount();

    await waitFor(() => expect(loaded).toHaveBeenCalledWith(2));
    await waitFor(() => expect(failed).toHaveBeenCalledTimes(1));
    // The defect this pins: a parsed document must not count as a drawn one.
    expect(drawn).not.toHaveBeenCalled();
  });

  it("stays silent when a render is cancelled by paging or zooming", async () => {
    state.renderOutcome = "cancel";
    const { drawn, failed, loaded } = mount();

    await waitFor(() => expect(loaded).toHaveBeenCalledWith(2));
    // Give the rejection a turn to propagate before asserting the negative.
    await waitFor(() => expect(state.load).toHaveBeenCalled());
    expect(failed).not.toHaveBeenCalled();
    expect(drawn).not.toHaveBeenCalled();
  });
});
