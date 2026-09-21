"use client";

/**
 * "This is a practice run, and it will never be filed."
 *
 * D-068 lets a rehearsal cycle do everything a real filing does — freeze, seal
 * a package, take every signature, record a submission — because those are the
 * riskiest steps in the regime and a bank should be able to practise them.
 * Five independent mechanisms stop a rehearsal reaching a regulator (a column
 * on the package, three database constraints, the non-transmitting channel
 * rule, and the calendar's own exclusion).
 *
 * None of them stops a PERSON confusing one for the other, and that is the
 * failure this component exists to prevent. It is therefore repeated on every
 * surface where a rehearsal can appear rather than shown once at the top: the
 * cycle header, the freeze confirmation, the signature card, the documents
 * card and the submission card. A reader who scrolls to the submission button
 * without passing a warning is the case that matters.
 *
 * It is deliberately loud, deliberately not dismissible, and deliberately says
 * what to do instead.
 */

import { FlaskConical } from "lucide-react";
import { ICON_SM } from "@/components/icaap/p2/display";
import { REHEARSAL_BODY, REHEARSAL_HEADLINE } from "./labels";

export default function RehearsalNotice({
  detail,
  compact = false,
}: {
  /** An extra sentence for the surface this is shown on. */
  detail?: string;
  /** One line, for a card footer rather than the top of a page. */
  compact?: boolean;
}) {
  if (compact) {
    return (
      <p
        className="mt-3 flex items-start gap-2 rounded border border-warning/30 bg-warning-light/40 px-3 py-2 text-caption text-navy/80"
        data-testid="rehearsal-notice"
      >
        <FlaskConical size={ICON_SM} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          <strong className="font-medium">{REHEARSAL_HEADLINE}.</strong>{" "}
          {detail ?? REHEARSAL_BODY}
        </span>
      </p>
    );
  }

  return (
    <section
      className="card border-l-4 border-l-warning bg-warning-light/40 p-4"
      aria-label={REHEARSAL_HEADLINE}
      data-testid="rehearsal-notice"
    >
      <p className="flex items-center gap-2 text-body font-semibold text-navy">
        <FlaskConical size={ICON_SM} aria-hidden />
        {REHEARSAL_HEADLINE}
      </p>
      <p className="mt-1 text-body leading-relaxed text-navy/80">
        {REHEARSAL_BODY}
      </p>
      {detail && (
        <p className="mt-2 text-body leading-relaxed text-navy/80">{detail}</p>
      )}
    </section>
  );
}

/** True when this cycle is a rehearsal. One reading of the rule, in one place. */
export function isRehearsal(cycleKind: string | null | undefined): boolean {
  return cycleKind === "rehearsal";
}
