"use client";

/**
 * A view the platform refused, in the PLATFORM's own words.
 *
 * `RestrictedWidget` is the paraphrase for one decision only: a grant denial,
 * whose 403 body names the refused members and which the disclosure rule
 * therefore forbids repeating. Every other refusal a BI route can make — an
 * impersonated staff session that may not read BI, a data scope this view
 * cannot narrow to, a dashboard only its owner may delete — arrives with the
 * server's own production sentence and names nothing sensitive. Those used to
 * be rendered as "Access restricted — an organization owner can grant it" as
 * well, because the client keyed on the status code alone: a decision the
 * server never made, and an instruction nobody could act on.
 *
 * So this tile renders the sentence it is given and composes none of its own.
 * It offers no retry: a refusal is not a fault.
 */

import { ShieldOff } from "lucide-react";

export default function RefusedWidget({
  /** The server's sentence, verbatim. */
  sentence,
  /** Pixel height, so a refused widget keeps the layout the pack laid out. */
  height,
  className = "",
}: {
  sentence: string;
  height?: number;
  className?: string;
}) {
  return (
    <section
      role="status"
      className={`card flex flex-col items-center justify-center gap-2 p-6 text-center ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface text-slate">
        <ShieldOff size={16} aria-hidden />
      </div>
      <p className="text-body font-medium text-navy">This view was refused</p>
      <p className="max-w-xs text-caption leading-relaxed text-slate">
        {sentence}
      </p>
    </section>
  );
}
