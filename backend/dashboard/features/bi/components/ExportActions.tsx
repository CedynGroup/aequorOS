"use client";

/**
 * The governed way out of a BI view, bound to a control.
 *
 * `POST …/bi/export` is the ONLY path figures take out of the analytics surfaces.
 * It re-makes the same authorization decision the screen was served under,
 * classifies the disclosure from the catalogue members the query touches — never
 * from a flag on the request — records the release in `bi_query_log` and
 * `audit_events`, and stamps every artifact with who took it, from which
 * institution, on which catalogue version and under which analytics build. None
 * of that can be produced by a browser's own "download data", which is why
 * neither the chart library's nor the grid's is wired to anything.
 *
 * WHAT A READER WITHOUT THE AUTHORITY SEES. The three governed items are always
 * offered, because whether this reader may export THIS answer is the server's
 * decision and asking it in advance would be a second, weaker copy of it. A
 * refusal therefore arrives as a refusal: the request is answered 403, no bytes
 * are produced, and this component states — in the same words `RestrictedWidget`
 * uses, naming no field — that the access does not cover it. A broken download,
 * or a disabled button with a guess for a reason, would both be worse.
 */

import { useEffect, useRef, useState } from "react";
import type { BiQuery } from "@aequoros/risk-service-api";
import ExportMenu, {
  printExportOption,
  type BiExportOption,
} from "./ExportMenu";
import {
  isBiAccessDenied,
  useBiExport,
  type BiExportFormat,
} from "@/lib/api/bi";

/**
 * How each artifact reads on the menu.
 *
 * The same words the scheduled-reports composer uses for the same three formats,
 * so a reader who asks for a file once and schedules it the next week is choosing
 * between the same named things.
 */
const ARTIFACTS: readonly {
  format: BiExportFormat;
  label: string;
  description: string;
}[] = [
  {
    format: "csv",
    label: "Comma-separated values",
    description:
      "Every row of this answer at full precision, above a block stating where it came from.",
  },
  {
    format: "xlsx",
    label: "Excel workbook",
    description:
      "The answer on one sheet, with a second sheet recording the question and the analytics build behind it.",
  },
  {
    format: "pdf",
    // Named for the property that distinguishes it from the print option below,
    // which also produces a PDF: this one is the platform's own artifact, stamped
    // and recorded, rather than a picture of the screen. A menu with two items
    // both called "PDF" is ambiguous to a reader as well as to a test.
    label: "Watermarked PDF",
    description:
      "A paginated document of this answer, watermarked, with its provenance on the cover.",
  },
];

/** The refusal, in the same words a refused widget uses, naming no field. */
const REFUSED =
  "Your access does not cover everything this export needs. An organization owner can grant it.";

const NOTHING_TO_EXPORT =
  "Build a question first — an export carries the answer on screen.";

export default function ExportActions({
  bankId,
  /** The question AS SUBMITTED. Null while the reader has not built one. */
  query,
  label,
}: {
  bankId: string | undefined;
  query: BiQuery | null;
  label?: string;
}) {
  const [running, setRunning] = useState<BiExportFormat | null>(null);
  const exporting = useBiExport(bankId);
  // Object URLs minted for an inline artifact are revoked when the surface goes
  // away: a blob URL left behind keeps the whole file in memory.
  const minted = useRef<string[]>([]);

  useEffect(
    () => () => {
      for (const url of minted.current) URL.revokeObjectURL(url);
      minted.current = [];
    },
    [],
  );

  const run = (format: BiExportFormat) => {
    if (!query || !bankId) return;
    setRunning(format);
    exporting.mutate(
      { query, format },
      {
        onSuccess: ({ url, filename }) => {
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = filename;
          anchor.rel = "noopener";
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          if (url.startsWith("blob:")) minted.current.push(url);
        },
        onSettled: () => setRunning(null),
      },
    );
  };

  const unavailable = query === null || !bankId;
  const options: readonly BiExportOption[] = [
    ...ARTIFACTS.map((artifact) => ({
      id: artifact.format,
      label: artifact.label,
      description: artifact.description,
      disabled: unavailable || running !== null,
      disabledReason: unavailable
        ? NOTHING_TO_EXPORT
        : "Your last export is still being prepared.",
      onSelect: () => run(artifact.format),
    })),
    printExportOption(),
  ];

  const message = running
    ? "Preparing your export. The file downloads when the platform releases it."
    : isBiAccessDenied(exporting.error)
      ? REFUSED
      : exporting.error
        ? (exporting.error as { message?: string }).message ||
          "The export could not be produced. Try again in a moment."
        : null;

  return (
    <div className="flex flex-col items-end gap-1 print:hidden">
      <ExportMenu options={options} label={label} />
      {message && (
        <p
          role="status"
          className={`max-w-xs text-right text-caption leading-relaxed ${
            exporting.error && !running ? "text-critical" : "text-slate"
          }`}
        >
          {message}
        </p>
      )}
    </div>
  );
}
