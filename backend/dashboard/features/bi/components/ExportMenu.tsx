"use client";

/**
 * The governed way out of a BI view.
 *
 * Every export is a read, and it is authorized by the SAME decision the screen
 * was: a summary export needs view authority over every member it carries, a
 * record-level or confidential one needs export authority, and each one is
 * audited and watermarked server-side. That is why a chart library's own
 * "download data" button is never wired up here — it would copy rows out of the
 * browser with no decision and no audit record behind it.
 *
 * The menu itself decides nothing. It renders the options its caller was able
 * to offer, and an option the caller cannot offer is shown disabled with the
 * reason, never hidden — a reader who cannot export should know that is the
 * reason they cannot, not wonder where the button went.
 */

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Download, Printer } from "lucide-react";

export type BiExportOption = {
  id: string;
  label: string;
  description?: string;
  onSelect: () => void;
  disabled?: boolean;
  /** Shown in place of the description when the option is disabled. */
  disabledReason?: string;
};

/**
 * Print, which the browser turns into a PDF. It is styled by `app/print.css`
 * and carries whatever is on screen, so it is available wherever a view is.
 */
export function printExportOption(): BiExportOption {
  return {
    id: "print",
    label: "Print or save as PDF",
    description:
      "Uses your browser's print dialog and this view's print layout.",
    onSelect: () => window.print(),
  };
}

export default function ExportMenu({
  options,
  label = "Export",
}: {
  options: readonly BiExportOption[];
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (options.length === 0) return null;

  return (
    <div ref={containerRef} className="relative print:hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-caption font-medium text-slate hover:bg-surface"
      >
        <Download size={13} aria-hidden />
        {label}
        <ChevronDown size={13} aria-hidden />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-72 rounded-md border border-border bg-surface-raised p-1 shadow-pop"
        >
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              role="menuitem"
              disabled={option.disabled}
              onClick={() => {
                setOpen(false);
                option.onSelect();
              }}
              className="flex w-full flex-col items-start gap-0.5 rounded-sm px-3 py-2 text-left hover:bg-surface disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
            >
              <span className="flex items-center gap-1.5 text-body text-navy">
                {option.id === "print" && <Printer size={13} aria-hidden />}
                {option.label}
              </span>
              {(option.disabled
                ? option.disabledReason
                : option.description) && (
                <span className="text-caption leading-relaxed text-slate">
                  {option.disabled ? option.disabledReason : option.description}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
