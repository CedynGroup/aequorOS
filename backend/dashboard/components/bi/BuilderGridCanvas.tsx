"use client";

/**
 * The twelve-column grid a reader arranges a saved dashboard on.
 *
 * react-grid-layout owns the dragging and the resizing; this file owns nothing
 * about what a tile MEANS. The layout it reports back is geometry only —
 * `i/x/y/w/h` — and `components/bi/builder.ts` decides what to do with it, so a
 * bug here can move a view and can never change what it reads.
 *
 * DRAGGING IS RESTRICTED TO AN EXPLICIT GRIP. Without `dragConfig.handle`, the
 * whole tile is a drag surface and the controls inside it — remove, resize, edit
 * — become unreliable to click, because a three-pixel movement during a click
 * becomes a drag. The grip is also what makes the tile's own buttons reachable
 * from a keyboard, which the width and height controls beside them depend on.
 *
 * THE LIBRARY'S OWN STYLESHEET IS IMPORTED, NOT REIMPLEMENTED. Its class names
 * (`react-grid-item`, `react-resizable-handle`) are its runtime contract: the
 * placement transform is written inline by the library, and everything else —
 * the transition, the placeholder, the handle's hit area — comes from that file.
 * Two rules are overridden below: the placeholder's stock `background: red`,
 * which is not a colour this product uses for "the tile will land here", and the
 * handle's stock `opacity: 0`, which hides the one affordance a builder must
 * advertise.
 */

import type { ReactNode } from "react";
import { GridLayout, useContainerWidth } from "react-grid-layout";
import type { Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { BUILDER_COLUMNS, BUILDER_DRAG_HANDLE_CLASS } from "./builder";
import styles from "./builderGrid.module.css";
import type { BiGridItem } from "./types";

export type BuilderGridItem = Readonly<{ id: string; node: ReactNode }>;

export type BuilderGridCanvasProps = Readonly<{
  layout: readonly BiGridItem[];
  /** Pixel height of one grid row, matching the read-only canvas. */
  rowHeight: number;
  items: readonly BuilderGridItem[];
  /** Called with the geometry the reader dragged or resized it into. */
  onLayoutChange: (layout: readonly BiGridItem[]) => void;
}>;

function geometry(layout: Layout): readonly BiGridItem[] {
  return layout.map((item) => ({
    i: item.i,
    x: item.x,
    y: item.y,
    w: item.w,
    h: item.h,
  }));
}

export default function BuilderGridCanvas({
  layout,
  rowHeight,
  items,
  onLayoutChange,
}: BuilderGridCanvasProps) {
  const { width, containerRef } = useContainerWidth({ initialWidth: 1080 });

  return (
    <div ref={containerRef} className={styles.canvas}>
      <GridLayout
        width={width}
        layout={[...layout]}
        gridConfig={{
          cols: BUILDER_COLUMNS,
          rowHeight,
          margin: [16, 16],
          containerPadding: [0, 0],
        }}
        dragConfig={{ handle: `.${BUILDER_DRAG_HANDLE_CLASS}` }}
        onLayoutChange={(next) => onLayoutChange(geometry(next))}
      >
        {items.map((item) => (
          <div key={item.id} data-widget={item.id}>
            {item.node}
          </div>
        ))}
      </GridLayout>
    </div>
  );
}
