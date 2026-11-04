"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  instrumentDisclosure,
  type InstrumentDate,
  type InstrumentStatus,
} from "@/lib/regulatoryInstruments";

/** The disclosure's intended width; it shrinks on viewports narrower than this. */
const PANEL_WIDTH = 288;
/** Keeps the floating panel clear of the viewport edge. */
const VIEWPORT_MARGIN = 16;
/** Gap between the badge and the panel. */
const PANEL_GAP = 8;
/** Room below this is not worth opening into; the panel goes above instead. */
const MIN_PANEL_HEIGHT = 96;

/**
 * A fixed panel is outside the document flow, so it cannot be scrolled into
 * view: it is anchored to the badge and either capped and scrolled internally
 * or flipped above the badge when the badge sits near the bottom edge.
 */
type PanelPlacement = { left: number; width: number; maxHeight: number } & (
  { top: number } | { bottom: number }
);

/** Identifies which edge the panel is anchored to, for change detection. */
function panelAnchor(placement: PanelPlacement): string {
  return "top" in placement
    ? `top:${Math.round(placement.top)}`
    : `bottom:${Math.round(placement.bottom)}`;
}

/**
 * The shared legal-status disclosure, with keyboard-accessible source details.
 *
 * The panel is viewport-positioned rather than laid out against the badge: the
 * badge sits in 160px register cells and in cards that clip overflow, so an
 * in-flow panel collapsed to the cell width and pushed every following row
 * down. Because a fixed panel is outside the document flow, it is also capped
 * to the viewport and scrolls internally (and flips above a badge near the
 * bottom edge), so its citation and source stay reachable without scrolling.
 * It stays a child of the `details` element, so the disclosure keeps its
 * native toggle, keyboard behaviour and document order.
 */
export default function InstrumentStatusBadge({
  status,
  effectiveFrom,
  citation,
  sourceUrl,
  code,
  regulator,
}: {
  status: InstrumentStatus | undefined;
  effectiveFrom?: InstrumentDate;
  citation?: string;
  sourceUrl?: string | null;
  code?: string;
  regulator?: string;
}) {
  const disclosure = instrumentDisclosure(status, effectiveFrom, regulator);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panel, setPanel] = useState<PanelPlacement | null>(null);
  const tones: Record<string, string> = {
    exposure_draft: "border-amber-300 bg-amber-50 text-amber-900",
    final_not_in_force: "border-blue-300 bg-blue-50 text-blue-900",
    in_force: "border-emerald-300 bg-emerald-50 text-emerald-900",
    unpublished: "border-slate-300 bg-slate-100 text-slate-800",
  };

  /**
   * `contentHeight` is unknown on the first pass, which only flips the panel
   * when the space below could not show anything useful. Once the panel is
   * open its real height decides between below, above and internal scrolling.
   */
  const measurePanel = useCallback(
    (contentHeight?: number): PanelPlacement | null => {
      const summary = detailsRef.current?.querySelector("summary");
      if (!summary) return null;
      const badge = summary.getBoundingClientRect();
      const viewportHeight = window.innerHeight;
      const width = Math.max(
        0,
        Math.min(PANEL_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2),
      );
      const below = viewportHeight - badge.bottom - PANEL_GAP - VIEWPORT_MARGIN;
      const above = badge.top - PANEL_GAP - VIEWPORT_MARGIN;
      // A zero height means the panel has not been laid out yet (a hidden
      // `details` or a test renderer), not that its content is empty.
      const known = contentHeight !== undefined && contentHeight > 0;
      const placeAbove =
        above > below &&
        (known ? contentHeight > below : below < MIN_PANEL_HEIGHT);
      // Never let the panel run past the viewport: the roomier side wins, and
      // a genuinely cramped viewport scrolls rather than overflowing unseen.
      const maxHeight = Math.max(
        0,
        Math.min(
          placeAbove ? above : below,
          viewportHeight - VIEWPORT_MARGIN * 2,
        ),
      );
      const left = Math.min(
        Math.max(badge.left, VIEWPORT_MARGIN),
        window.innerWidth - width - VIEWPORT_MARGIN,
      );
      return placeAbove
        ? {
            bottom: Math.max(
              VIEWPORT_MARGIN,
              viewportHeight - badge.top + PANEL_GAP,
            ),
            left,
            width,
            maxHeight,
          }
        : {
            top: Math.max(VIEWPORT_MARGIN, badge.bottom + PANEL_GAP),
            left,
            width,
            maxHeight,
          };
    },
    [],
  );

  // The closed `details` hides its content, so the panel's real height is only
  // measurable once it is open and positioned. The first open sizes it from the
  // room below (or above, when below is clearly too small); this pass refines
  // that with the panel's actual height, and every later scroll or resize
  // re-places it. Re-placing only on a real change keeps the effect from
  // looping, and both placements are pure functions of the badge and viewport.
  useEffect(() => {
    if (!panel) return;
    const reposition = () => {
      const next = measurePanel(panelRef.current?.scrollHeight);
      const unchanged =
        !next ||
        (Math.abs(next.maxHeight - panel.maxHeight) <= 1 &&
          Math.abs(next.left - panel.left) <= 1 &&
          Math.abs(next.width - panel.width) <= 1 &&
          panelAnchor(next) === panelAnchor(panel));
      if (!unchanged) setPanel(next);
    };
    // A fixed panel floats over the page, so it needs the escape hatches an
    // overlay gets: Escape, or a pointer press anywhere outside the disclosure.
    // Presses inside the panel stay, so its source link remains usable.
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent) {
        if (event.key === "Escape") setPanel(null);
        return;
      }
      if (!detailsRef.current?.contains(event.target as Node)) setPanel(null);
    };
    reposition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    document.addEventListener("keydown", dismiss);
    document.addEventListener("pointerdown", dismiss);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      document.removeEventListener("keydown", dismiss);
      document.removeEventListener("pointerdown", dismiss);
    };
  }, [panel, measurePanel]);

  return (
    <details
      ref={detailsRef}
      open={Boolean(panel)}
      className="inline-block max-w-full text-caption"
      onClick={(event) => event.stopPropagation()}
    >
      <summary
        title={disclosure.tooltip}
        onClick={(event) => {
          event.preventDefault();
          setPanel((current) => (current ? null : measurePanel()));
        }}
        className={`block w-max max-w-full truncate list-none cursor-help rounded-sm border px-2 py-0.5 font-medium [&::-webkit-details-marker]:hidden ${tones[status ?? ""] ?? "border-slate-300 bg-slate-100 text-slate-800"}`}
      >
        {disclosure.label}
      </summary>
      <div
        ref={panelRef}
        data-testid="instrument-status-disclosure"
        style={
          panel
            ? {
                position: "fixed",
                left: panel.left,
                width: panel.width,
                maxHeight: panel.maxHeight,
                overflowY: "auto",
                ...("top" in panel
                  ? { top: panel.top }
                  : { bottom: panel.bottom }),
              }
            : undefined
        }
        className="z-50 rounded-sm border border-border bg-surface p-3 text-navy shadow-pop break-words"
      >
        <p>{disclosure.tooltip}</p>
        {citation && <p className="mt-2 text-slate">{citation}</p>}
        {sourceUrl || code ? (
          <a
            className="mt-2 inline-block text-action underline"
            href={
              sourceUrl ??
              `/submissions/instruments#${encodeURIComponent(code!)}`
            }
          >
            {sourceUrl
              ? "Read the source"
              : "Source details in the instruments register"}
          </a>
        ) : (
          <p className="mt-2 text-slate">Source link not recorded.</p>
        )}
      </div>
    </details>
  );
}
