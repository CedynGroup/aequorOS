"use client";

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
}: {
  status: InstrumentStatus | undefined;
  effectiveFrom?: InstrumentDate;
  citation?: string;
  sourceUrl?: string | null;
  code?: string;
}) {
  const disclosure = instrumentDisclosure(status, effectiveFrom);
  return (
    <details
      className="relative inline-block text-caption"
      onClick={(event) => event.stopPropagation()}
    >
      <summary
        title={disclosure.tooltip}
        className="cursor-pointer rounded-sm border border-border bg-surface px-2 py-0.5 font-medium text-navy"
      >
        {disclosure.label}
      </summary>
      <div
        role="tooltip"
        className="absolute z-30 mt-1 w-72 max-w-[80vw] rounded-sm border border-border bg-surface p-3 shadow-lg text-navy"
      >
        <p>{disclosure.tooltip}</p>
        {citation && <p className="mt-2 text-slate">{citation}</p>}
        <a
          className="mt-2 inline-block text-action underline"
          href={
            sourceUrl ??
            `/submissions/instruments${code ? `#${encodeURIComponent(code)}` : ""}`
          }
        >
          {sourceUrl
            ? "Read the source"
            : "Source details in the instruments register"}
        </a>
      </div>
    </details>
  );
}
