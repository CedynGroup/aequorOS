"use client";

/**
 * Where a reader puts a question into words, and what they see while it is worked
 * out.
 *
 * Three things this box states rather than implies, because a reader who does not
 * know them cannot use the surface honestly:
 *
 * * NOTHING RUNS FROM HERE. Asking produces a proposal to check, never an
 *   answer — so the button says so, and the caption says so.
 * * THE DATE IS THE READER'S. The platform never lets a model name a reporting
 *   date, so the date control is part of the question and not a detail: a real
 *   figure for a date nobody asked for is the most convincing wrong answer
 *   available here.
 * * THE WORDS LEAVE THE PLATFORM. They are what makes the feature possible, so
 *   the reader is told once, plainly, before they type — not buried in a
 *   settings page.
 *
 * The length bound mirrors the route's own (`ASK_QUESTION_MAX_CHARS`). It stops
 * a reader at the bound instead of letting them finish a paragraph the server
 * will refuse; the server remains the authority and refuses independently.
 */

import { useState, type FormEvent } from "react";
import { Loader2, MessageSquare, Send } from "lucide-react";
import { ASK_QUESTION_MAX_CHARS } from "@/lib/api/ask";

export default function AskQuestionForm({
  asOf,
  onAsOfChange,
  onAsk,
  /** True while the question is being submitted or is still being worked out. */
  busy,
  /** Shown while the platform is working: the server's own sentence. */
  waitingMessage,
  onStopWaiting,
  disabled = false,
}: {
  asOf: string;
  onAsOfChange: (asOf: string) => void;
  onAsk: (question: string) => void;
  busy: boolean;
  waitingMessage?: string | null;
  onStopWaiting?: () => void;
  disabled?: boolean;
}) {
  const [question, setQuestion] = useState("");
  const trimmed = question.trim();
  const canAsk = !busy && !disabled && trimmed.length > 0 && asOf !== "";

  function submit(event: FormEvent): void {
    event.preventDefault();
    if (!canAsk) return;
    onAsk(trimmed);
  }

  return (
    <form onSubmit={submit} className="card space-y-4 p-5">
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex min-w-[18rem] flex-1 flex-col gap-1">
          <span className="text-micro font-medium uppercase tracking-wider text-slate">
            Your question
          </span>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={ASK_QUESTION_MAX_CHARS}
            rows={2}
            disabled={busy || disabled}
            placeholder="Gross loans by branch, largest first"
            className="resize-y rounded-md border border-border bg-surface-raised px-3 py-2 text-body text-navy disabled:opacity-60"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-micro font-medium uppercase tracking-wider text-slate">
            Reporting date
          </span>
          <input
            type="date"
            value={asOf}
            onChange={(event) => onAsOfChange(event.target.value)}
            disabled={busy || disabled}
            className="rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-body text-navy disabled:opacity-60"
          />
        </label>
        <button
          type="submit"
          disabled={!canAsk}
          className="inline-flex items-center gap-2 rounded-md bg-action px-4 py-2 text-body font-medium text-white disabled:opacity-50"
        >
          {busy ? (
            <Loader2 size={15} className="animate-spin" aria-hidden />
          ) : (
            <Send size={15} aria-hidden />
          )}
          Work out my question
        </button>
      </div>

      <p className="flex items-start gap-2 text-caption leading-relaxed text-slate">
        <MessageSquare size={14} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          Your words are sent to the assistant so it can pick the figures you
          meant. Your institution&apos;s figures are not: it is shown the names
          of the measures your access covers and nothing more. It proposes a
          question, you check it, and only then does anything read your data.
          Please do not type a customer&apos;s name.
          <span className="ml-1 text-slate-light">
            {question.length} of {ASK_QUESTION_MAX_CHARS} characters.
          </span>
        </span>
      </p>

      {busy && waitingMessage && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-surface px-4 py-3"
          aria-live="polite"
        >
          <Loader2 size={15} className="animate-spin text-action" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-body text-navy">{waitingMessage}</p>
            {onStopWaiting && (
              <p className="mt-0.5 text-caption text-slate">
                You can stop waiting and ask something else. Nothing has been
                read from your figures.
              </p>
            )}
          </div>
          {onStopWaiting && (
            <button
              type="button"
              onClick={onStopWaiting}
              className="rounded-md border border-border px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface-raised"
            >
              Stop waiting
            </button>
          )}
        </div>
      )}
    </form>
  );
}
