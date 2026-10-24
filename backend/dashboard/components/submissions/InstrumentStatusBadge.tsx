"use client";

import { useCallback, useEffect, useRef } from "react";
import {
  instrumentDisclosure,
  type InstrumentDate,
  type InstrumentStatus,
} from "@/lib/regulatoryInstruments";

/** The shared legal-status disclosure, with keyboard-accessible source details. */
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
  const tooltipRef = useRef<HTMLDivElement>(null);
  const positionTooltip = useCallback(() => {
    const tooltip = tooltipRef.current;
    if (!tooltip || tooltip.getClientRects().length === 0) return;
    tooltip.style.transform = "";
    const bounds = tooltip.getBoundingClientRect();
    const offset = Math.max(
      8 - bounds.left,
      Math.min(0, window.innerWidth - 8 - bounds.right),
    );
    tooltip.style.transform = `translateX(${offset}px)`;
  }, []);
  useEffect(() => {
    window.addEventListener("resize", positionTooltip);
    return () => window.removeEventListener("resize", positionTooltip);
  }, [positionTooltip]);
  return (
    <details
      className="relative inline-block text-caption"
      onClick={(event) => event.stopPropagation()}
      onToggle={positionTooltip}
    >
      <summary
        title={disclosure.tooltip}
        className="cursor-pointer rounded-sm border border-border bg-surface px-2 py-0.5 font-medium text-navy"
      >
        {disclosure.label}
      </summary>
      <div
        ref={tooltipRef}
        role="tooltip"
        className="absolute z-30 mt-1 w-72 max-w-[80vw] rounded-sm border border-border bg-surface p-3 shadow-lg text-navy"
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
