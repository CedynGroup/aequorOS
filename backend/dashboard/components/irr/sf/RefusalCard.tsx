"use client";

/**
 * The framework was run for this reporting date, and it refused to measure.
 *
 * THE RULE THIS CARD EXISTS FOR (DV-010 / D-061): an options refusal is a
 * REFUSAL, never a zero and never a blank. The framework refuses because it
 * cannot value the book's interest-rate option positions, and a measure that
 * silently omitted them would understate the loss while looking exactly like a
 * measurement. So this card replaces the figures entirely rather than sitting
 * beside them, says what could not be valued, and gives the preparer a next
 * step instead of a reassurance.
 *
 * The refusal keeps ONE name end to end. The sentence is chosen from that name
 * and matches the one the ICAAP register prints, so an examiner reading a run
 * row against the ICAAP report meets one account of one condition.
 */

import { AlertOctagon } from "lucide-react";
import type { SfRun } from "@/lib/api/irrbbSfNormalize";
import { ICON_SM } from "./display";
import { REFUSAL_HEADING, REFUSAL_NEXT_STEP, refusalSentence } from "./labels";

export default function RefusalCard({ run }: { run: SfRun }) {
  return (
    <div
      className="card border-l-4 border-l-critical bg-critical-light/40 p-5"
      role="alert"
    >
      <p className="flex items-center gap-2 text-body font-medium text-critical">
        <AlertOctagon size={ICON_SM} aria-hidden />
        {REFUSAL_HEADING}
      </p>
      <p className="mt-2 text-body leading-relaxed text-navy">
        {/*
          The SERVER's sentence when the payload carries one, and only then the
          one composed here from the code. Both say the same thing today — the
          backend's `regulatory_irr_sf.refusal_sentence` is the original and
          this is its mirror — and preferring the server's is what stops them
          drifting the day a new refusal is added to one and not the other.
        */}
        {run.statement ?? refusalSentence(run.errorCode, run.errorMessage)}
      </p>
      <p className="mt-2 text-body leading-relaxed text-navy/80">
        {REFUSAL_NEXT_STEP}
      </p>
    </div>
  );
}
