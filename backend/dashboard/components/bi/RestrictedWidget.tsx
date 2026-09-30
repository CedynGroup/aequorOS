"use client";

/**
 * What a reader sees where the server refused a view.
 *
 * A DENIAL THAT NAMES WHAT WAS HIDDEN IS A DISCLOSURE. "You may not see
 * Exposure to Acme Ltd" tells the reader that Acme Ltd is a borrower; "You may
 * not see Largest single-name share" tells them the institution tracks one, and
 * on a filtered view the filter is often the sensitive half. So this component
 * takes NO measure, NO dimension, NO filter and NO figure — it cannot render
 * one, because it is never given one — and says only that access is restricted
 * and who can change that.
 *
 * `components/bi/disclosure.test.ts` pins that property against this file's
 * source, because the pressure to "just show which field" arrives with every
 * support ticket.
 */

import { Lock } from "lucide-react";

export default function RestrictedWidget({
  /** Pixel height, so a refused widget keeps the layout the pack laid out. */
  height,
  className = "",
}: {
  height?: number;
  className?: string;
}) {
  return (
    <section
      className={`card flex flex-col items-center justify-center gap-2 p-6 text-center ${className}`}
      style={height ? { minHeight: height } : undefined}
    >
      <div className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface text-slate">
        <Lock size={16} aria-hidden />
      </div>
      <p className="text-body font-medium text-navy">Access restricted</p>
      <p className="max-w-xs text-caption leading-relaxed text-slate">
        Your access does not cover everything this view needs. An organization
        owner can grant it.
      </p>
    </section>
  );
}
