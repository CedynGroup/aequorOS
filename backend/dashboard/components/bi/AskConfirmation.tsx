"use client";

/**
 * What the platform understood, shown back before it reads anything.
 *
 * This is the surface `docs/bi.md` §Phase 5 means by "it's shown to the user for
 * confirmation", and it is the one component here where a shortcut would make the
 * feature WORSE than not having it: a dialog a reader cannot evaluate would
 * launder a model's guess as a person's decision. So:
 *
 * * EVERY WORD IS THE SERVER'S. The sentence and its clauses are rendered by
 *   `app/services/bi/nlq/reading.py` from the catalogue's own labels. Nothing
 *   here composes a phrase out of member ids, and nothing here shows the model's
 *   words — the model contributes the SHAPE of the question and no text at all.
 * * IT IS A CHECKLIST, NOT A PARAGRAPH. A reader checks a figure, a breakdown, a
 *   date and a filter one at a time; the clauses arrive already separated for
 *   exactly that, and a clause whose kind this build cannot head is still shown,
 *   under its text alone. Dropping one would hide part of what is being agreed
 *   to, and printing its wire token would put an enum on a bank analyst's screen.
 * * THE DATES ARE RE-FORMATTED, NOT RE-DERIVED. The clause text spells them
 *   ISO-8601 (the one form that names no country); the structured window is
 *   carried beside it so the period and comparison lines can read in the
 *   institution's own locale. When the structured value is absent the server's
 *   text stands as written.
 * * THE QUERY IS NEVER SHOWN AS A QUERY. There is no JSON on screen and no field
 *   editor: the only two actions are to confirm what is written or to discard it
 *   and ask again. A reader who wants a different question builds it in Explore,
 *   where it is authorized and logged in its own right.
 */

import { Check, ListChecks, X } from "lucide-react";
import {
  askClauseHeading,
  type AskProposal,
  type AskClause,
} from "@/lib/api/ask";
import { fmtLocale } from "@/lib/format";

/** An ISO day in the institution's own locale, or `null` if it is not one. */
function localDay(iso: string | null): string | null {
  if (iso === null) return null;
  const parsed = new Date(`${iso}T00:00:00.000Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString(fmtLocale(), {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * The text of one clause, with dates localised where the server gave us the
 * machine value to localise. Falls back to the server's own phrase — never to a
 * phrase of this component's own making.
 */
function clauseText(clause: AskClause, proposal: AskProposal): string {
  const reading = proposal.reading;
  if (reading === null) return clause.text;
  if (clause.kind === "period") {
    const asOf = localDay(reading.asOf);
    if (asOf !== null) return `as at ${asOf}`;
    const start = localDay(reading.rangeStart);
    const end = localDay(reading.rangeEnd);
    if (start !== null && end !== null) {
      return `for each period from ${start} to ${end}`;
    }
  }
  if (clause.kind === "comparison") {
    const compareTo = localDay(reading.compareTo);
    if (compareTo !== null) return `compared with ${compareTo}`;
  }
  return clause.text;
}

export default function AskConfirmation({
  proposal,
  onConfirm,
  onDiscard,
  confirming,
}: {
  proposal: AskProposal;
  onConfirm: () => void;
  onDiscard: () => void;
  confirming: boolean;
}) {
  const clauses = proposal.reading?.clauses ?? [];
  return (
    <section className="card overflow-hidden">
      <header className="flex items-start gap-3 border-b border-border-light px-5 py-4">
        <ListChecks
          size={18}
          className="mt-0.5 shrink-0 text-action"
          aria-hidden
        />
        <div className="min-w-0">
          <h2 className="text-h3 text-navy">{proposal.message}</h2>
          <p className="mt-1 text-caption leading-relaxed text-slate">
            You asked: &ldquo;{proposal.question}&rdquo;. Nothing has been read
            yet. This is what the platform will measure if you confirm it.
          </p>
        </div>
      </header>

      <dl className="divide-y divide-border-light">
        {clauses.map((clause, index) => {
          const heading = askClauseHeading(clause.kind);
          return (
            <div
              key={`${clause.kind ?? "clause"}-${index}`}
              className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3"
            >
              <dt className="w-40 shrink-0 text-micro font-medium uppercase tracking-wider text-slate">
                {heading ?? "Also"}
              </dt>
              <dd className="min-w-0 flex-1 text-body text-navy">
                {clauseText(clause, proposal)}
              </dd>
            </div>
          );
        })}
      </dl>

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border-light bg-surface px-5 py-4">
        <p className="min-w-0 flex-1 text-caption leading-relaxed text-slate">
          Confirming runs exactly the question written above, under your own
          access. If it is not what you meant, discard it and ask again — or
          build it yourself in Explore.
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onDiscard}
            disabled={confirming}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-body font-medium text-navy hover:bg-surface-raised disabled:opacity-50"
          >
            <X size={14} aria-hidden />
            Discard
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirming}
            className="inline-flex items-center gap-1.5 rounded-md bg-action px-4 py-2 text-body font-medium text-white disabled:opacity-50"
          >
            <Check size={15} aria-hidden />
            {confirming ? "Running this question" : "Yes, run this question"}
          </button>
        </div>
      </footer>
    </section>
  );
}
