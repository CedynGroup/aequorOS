/**
 * Signature-field geometry: the viewer's pixels ↔ the PDF's points.
 *
 * The two spaces disagree about where the origin is and which way y grows. A
 * browser measures from the TOP-LEFT of the rendered page in CSS pixels; a PDF
 * measures from the BOTTOM-LEFT of the media box in points. Getting the flip
 * wrong does not fail loudly — it silently stamps an officer's name and
 * permanent signer ID somewhere else on a document filed with the regulator —
 * so the conversion lives here, in one place, and is derived from pdf.js' own
 * page viewport rather than re-implemented from the page dimensions.
 *
 * `PageViewport.convertToPdfPoint` is the authority for the transform because it
 * already accounts for a non-zero media-box origin and for page rotation, both
 * of which a hand-rolled `height - y` gets wrong. A rotated page can also swap
 * which corner is the minimum, so both corners are converted and then
 * normalised — never assumed.
 */

import type { PageViewport } from 'pdfjs-dist';
import type {
  PlacementFieldType,
  PlacementFieldTypeRead,
  SignatureFieldPlacement,
} from '@aequoros/risk-service-api';
import { FIELD_TYPE_LABELS } from './fields';

/** A field box as the browser lays it out: CSS pixels from the page's top-left. */
export interface ViewerRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** A field box in PDF user space: points, origin bottom-left, x1<x2 and y1<y2. */
export interface PdfBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * One rendered page and the transform between the two spaces. Rebuilt whenever
 * the page or the zoom changes, because the viewport it wraps encodes both.
 */
export interface PageSpace {
  /** 0-based, matching `SignatureFieldPlacement.pageIndex`. */
  pageIndex: number;
  /** Rendered size in CSS pixels — what the overlay is positioned against. */
  cssWidth: number;
  cssHeight: number;
  /** Media-box size in points — what a bounds check is measured against. */
  widthPt: number;
  heightPt: number;
  toPdf: (rect: ViewerRect) => PdfBox;
  toViewer: (box: PdfBox) => ViewerRect;
}

export function pageSpace(pageIndex: number, viewport: PageViewport): PageSpace {
  const unscaled = viewport.clone({ scale: 1 }) as PageViewport;

  const toPdf = (rect: ViewerRect): PdfBox => {
    const [ax, ay] = viewport.convertToPdfPoint(rect.left, rect.top);
    const [bx, by] = viewport.convertToPdfPoint(
      rect.left + rect.width,
      rect.top + rect.height
    );
    // Whole points on the wire: the service rounds anyway, and rounding here is
    // what lets the minimum-size check below agree with the server's.
    return {
      x1: Math.round(Math.min(ax, bx)),
      y1: Math.round(Math.min(ay, by)),
      x2: Math.round(Math.max(ax, bx)),
      y2: Math.round(Math.max(ay, by)),
    };
  };

  const toViewer = (box: PdfBox): ViewerRect => {
    const [ax, ay] = viewport.convertToViewportPoint(box.x1, box.y1);
    const [bx, by] = viewport.convertToViewportPoint(box.x2, box.y2);
    return {
      left: Math.min(ax, bx),
      top: Math.min(ay, by),
      width: Math.abs(bx - ax),
      height: Math.abs(by - ay),
    };
  };

  return {
    pageIndex,
    cssWidth: viewport.width,
    cssHeight: viewport.height,
    widthPt: unscaled.width,
    heightPt: unscaled.height,
    toPdf,
    toViewer,
  };
}

/**
 * The floor for each kind of box, keyed by `fieldType`.
 *
 * Served by `GET …/attestation/placements` rather than written down here,
 * because the numbers are DERIVED backend-side from what each kind has to print
 * at a legibility floor. A copy in the browser would drift from the one that
 * actually refuses a placement, and the drift would show up as a box the signer
 * was allowed to draw and the certification then rejected.
 */
export type PlacementLimits = Record<
  string,
  { minBoxWidth: number; minBoxHeight: number }
>;

export function limitsByFieldType(
  fieldTypes: readonly PlacementFieldTypeRead[]
): PlacementLimits {
  return Object.fromEntries(
    fieldTypes.map((entry) => [
      entry.fieldType,
      { minBoxWidth: entry.minBoxWidth, minBoxHeight: entry.minBoxHeight },
    ])
  );
}

/** What a box prints. Defaulted because the generated field carries a default. */
export function fieldTypeOf(placement: SignatureFieldPlacement): PlacementFieldType {
  return placement.fieldType ?? 'signature';
}

/**
 * Why this box cannot be filed, or `null` if it can.
 *
 * The service refuses the same things (`attestation.placements.
 * _require_complete` plus the media-box check in `pdf_signing`), and it is the
 * enforcement. This is here so the refusal arrives while the signer still has
 * their hand on the box, with the number that is wrong — not two minutes later
 * as a rejected certification on a filing deadline.
 *
 * A kind with no entry in `limits` is NOT reported as too small: that state
 * means the floors have not been fetched yet, and inventing one locally is
 * exactly the duplication this indirection exists to avoid.
 */
export function placementViolation(
  placement: SignatureFieldPlacement,
  space: PageSpace,
  limits: PlacementLimits
): string | null {
  const width = placement.x2 - placement.x1;
  const height = placement.y2 - placement.y1;
  const floor = limits[fieldTypeOf(placement)];
  if (floor && (width < floor.minBoxWidth || height < floor.minBoxHeight)) {
    return (
      `${width}×${height} pt is below the ${floor.minBoxWidth}×${floor.minBoxHeight} pt ` +
      `minimum for a ${FIELD_TYPE_LABELS[fieldTypeOf(placement)].toLowerCase()} field — ` +
      'below that it does not print legibly on a filed return.'
    );
  }
  if (
    placement.x1 < 0 ||
    placement.y1 < 0 ||
    placement.x2 > space.widthPt ||
    placement.y2 > space.heightPt
  ) {
    return (
      'The box runs off page ' +
      `${space.pageIndex + 1} (${Math.round(space.widthPt)}×${Math.round(space.heightPt)} pt). ` +
      'A field outside the page cannot be created, so the certification would be refused.'
    );
  }
  return null;
}

/** Smallest box that still leaves a grabbable resize handle, in CSS pixels. */
const HANDLE_FLOOR_PX = 12;

/**
 * Slide a rect back inside the page it belongs to.
 *
 * Position IS clamped: a box dragged off the page would be refused by the
 * service, and there is no reading of "off the page" that a signer meant. Size
 * is NOT clamped to `minBoxWidth`/`minBoxHeight` — only to something still
 * grabbable — because silently refusing to shrink is indistinguishable from a
 * broken handle. Below the floor the box is flagged by
 * :func:`placementViolation`, which names the number that is wrong, and the
 * ceremony is blocked until it is fixed. Stopped and told why, rather than
 * stopped.
 */
export function clampRect(rect: ViewerRect, space: PageSpace): ViewerRect {
  const width = Math.min(Math.max(rect.width, HANDLE_FLOOR_PX), space.cssWidth);
  const height = Math.min(Math.max(rect.height, HANDLE_FLOOR_PX), space.cssHeight);
  return {
    width,
    height,
    left: Math.min(Math.max(rect.left, 0), space.cssWidth - width),
    top: Math.min(Math.max(rect.top, 0), space.cssHeight - height),
  };
}

/**
 * A new box of `fieldType`, centred on where the signer dropped it.
 *
 * Centred rather than anchored top-left because a drop lands under the cursor,
 * and a field that jumps down-right of the pointer reads as a miss.
 */
export function newPlacement(
  fieldType: PlacementFieldType,
  signingRole: SignatureFieldPlacement['signingRole'],
  drop: { x: number; y: number },
  size: { width: number; height: number },
  space: PageSpace
): SignatureFieldPlacement {
  const scale = space.cssHeight / space.heightPt;
  const rect = clampRect(
    {
      left: drop.x - (size.width * scale) / 2,
      top: drop.y - (size.height * scale) / 2,
      width: size.width * scale,
      height: size.height * scale,
    },
    space
  );
  return {
    signingRole,
    fieldType,
    pageIndex: space.pageIndex,
    ...space.toPdf(rect),
  };
}

/** Whether both roles have the one signature field the document must carry. */
export function signatureRolesPlaced(placements: SignatureFieldPlacement[]): Set<string> {
  return new Set(
    placements
      .filter((placement) => fieldTypeOf(placement) === 'signature')
      .map((placement) => placement.signingRole)
  );
}
