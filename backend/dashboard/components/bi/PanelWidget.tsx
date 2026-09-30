"use client";

/**
 * A widget that embeds another part of the platform rather than a figure.
 *
 * A certified pack may reference a closed set of platform surfaces — the filing
 * calendar, the signature register, the ingestion history, the credit
 * migration and vintage views — and it references them BY KEY: a pack can express
 * no route, no parameter and no query, so it cannot widen what one of those
 * surfaces shows. The surface itself decides what its reader may see when they
 * arrive, which is why this tile is a door and not a copy: nothing is fetched
 * here, so nothing can be disclosed here.
 *
 * The words come from the pack file, which already states what the surface holds.
 */

import Link from "next/link";
import { ArrowRight, LayoutGrid } from "lucide-react";
import type { BiPanelSurface } from "./types";

export default function PanelWidget({
  title,
  caption,
  surface,
  /** Pixel height, so the widget keeps the place the pack laid out. */
  height,
  className = "",
}: {
  title: string;
  caption: string;
  surface: BiPanelSurface;
  height?: number;
  className?: string;
}) {
  return (
    <section
      className={`card flex flex-col gap-2 p-5 ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="flex items-start gap-2">
        <LayoutGrid
          size={15}
          className="mt-0.5 shrink-0 text-slate"
          aria-hidden
        />
        <h3 className="text-h3 text-navy">{title}</h3>
      </div>
      {caption.length > 0 && (
        <p className="text-caption leading-relaxed text-slate">{caption}</p>
      )}
      <Link
        href={surface.href}
        className="mt-auto inline-flex w-fit items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-action hover:bg-surface"
      >
        {surface.label}
        <ArrowRight size={13} aria-hidden />
      </Link>
    </section>
  );
}
