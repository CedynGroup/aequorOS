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
  const tones: Record<string, string> = {
    exposure_draft: "border-amber-300 bg-amber-50 text-amber-900",
    final_not_in_force: "border-blue-300 bg-blue-50 text-blue-900",
    in_force: "border-emerald-300 bg-emerald-50 text-emerald-900",
    unpublished: "border-slate-300 bg-slate-100 text-slate-800",
  };
  return (
    <details
      className="inline-block max-w-full text-caption"
      onClick={(event) => event.stopPropagation()}
    >
      <summary
        title={disclosure.tooltip}
        className={`block w-fit max-w-full whitespace-normal break-words list-none cursor-help rounded-sm border px-2 py-0.5 font-medium [&::-webkit-details-marker]:hidden ${tones[status ?? ""] ?? "border-slate-300 bg-slate-100 text-slate-800"}`}
      >
        {disclosure.label}
      </summary>
      <div
        role="tooltip"
        className="mt-2 w-72 max-w-full rounded-sm border border-border bg-surface p-3 text-navy break-words"
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
