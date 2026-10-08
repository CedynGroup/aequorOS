"use client";

/**
 * Write a formula, and let the server say whether it is one.
 *
 * THERE IS NO PARSER IN THIS FILE AND THERE MUST NEVER BE ONE. The language is
 * `app/domain/bi/expr.py` — a Pratt parser with a type system, three declared
 * comparison periods, a bound on `LAG` derived from how long the marts keep
 * history, and a dozen refusals each with its own wording and character offset. A
 * TypeScript copy of that would give faster feedback right up to the first
 * disagreement, and then it would tell a reader their formula is fine and the save
 * would refuse it. So the verdict on this screen is always the one
 * `POST …/bi/measures/validation` returned, rendered as the server worded it.
 *
 * What the browser DOES own is the offer: which figures may be named, how they are
 * spelled inside a formula, and what the language's words are. All of that comes
 * from `components/bi/measures.ts`, which mirrors `expr.py` and is pinned to it by
 * a parity test — so the palette cannot offer a function the language does not
 * have.
 *
 * ONLY FIGURES THIS READER ALREADY HOLDS ARE OFFERED. The palette is the catalogue
 * the server returned, which is filtered member by member through the same
 * decision the query path makes. A formula naming something outside it is refused
 * on the check with the figures listed — and those names are the reader's OWN
 * text, which is why naming them back is not a disclosure.
 */

import { useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Search } from "lucide-react";
import type {
  BiCatalogueMeasureRead,
  BiMeasureValidationRead,
} from "@aequoros/risk-service-api";
import { moduleLabel } from "@/components/bi/labels";
import {
  EXPRESSION_MAX_LENGTH,
  MAX_FIGURE_REFERENCES,
  MAX_LAG_PERIODS,
  MEASURE_FUNCTIONS,
  MEASURE_OPERATORS,
  PERIOD_GRAINS,
  figureReference,
  refusedFiguresSentence,
  validationRefusal,
} from "@/components/bi/measures";

export type ExpressionVerdict = Readonly<{
  /** The exact text the verdict was reached about. */
  expression: string;
  read: BiMeasureValidationRead;
}>;

export default function ExpressionEditor({
  value,
  onChange,
  figures,
  verdict,
  checking,
  checkError,
  onCheck,
  disabled = false,
}: {
  value: string;
  onChange: (next: string) => void;
  /** The catalogue measures this reader may name. Already filtered by the server. */
  figures: readonly BiCatalogueMeasureRead[];
  /** The server's last verdict, and the text it was about. */
  verdict: ExpressionVerdict | null;
  checking: boolean;
  /** A transport failure on the check itself, as the server worded it. */
  checkError: string | null;
  onCheck: () => void;
  disabled?: boolean;
}) {
  const [search, setSearch] = useState("");
  const area = useRef<HTMLTextAreaElement | null>(null);

  const matches = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const found = needle
      ? figures.filter(
          (figure) =>
            figure.label.toLowerCase().includes(needle) ||
            figure.id.toLowerCase().includes(needle),
        )
      : figures;
    return found.slice(0, 40);
  }, [figures, search]);

  /** Put a figure where the cursor is, which is where a person is writing. */
  function insert(memberId: string): void {
    const token = figureReference(memberId);
    const node = area.current;
    if (!node) {
      onChange(`${value}${token}`);
      return;
    }
    const start = node.selectionStart ?? value.length;
    const end = node.selectionEnd ?? start;
    const next = `${value.slice(0, start)}${token}${value.slice(end)}`;
    onChange(next);
    requestAnimationFrame(() => {
      node.focus();
      const caret = start + token.length;
      node.setSelectionRange(caret, caret);
    });
  }

  /**
   * Put the cursor on the character the server named. The offset is 1-based, the
   * way an editor counts — `expr.py` says so — and is clamped to the text on
   * screen so a stale verdict cannot select past its end.
   */
  function showPosition(position: number): void {
    const node = area.current;
    if (!node) return;
    const index = Math.min(Math.max(position - 1, 0), value.length);
    node.focus();
    node.setSelectionRange(index, Math.min(index + 1, value.length));
  }

  const current = verdict !== null && verdict.expression === value;
  const read = current ? verdict.read : null;
  const refusal = validationRefusal(read ?? undefined);
  const refusedSentence = refusedFiguresSentence(refusal);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <label
          htmlFor="bi-measure-expression"
          className="text-caption font-medium text-navy"
        >
          The formula
        </label>
        <textarea
          id="bi-measure-expression"
          ref={area}
          value={value}
          disabled={disabled}
          rows={4}
          spellCheck={false}
          maxLength={EXPRESSION_MAX_LENGTH}
          onChange={(event) => onChange(event.target.value)}
          placeholder={`SAFE_DIV(${figureReference("loans.non_performing_rc")}, ${figureReference("loans.balance_rc")})`}
          className="w-full rounded-md border border-border bg-white px-3 py-2 font-mono text-caption text-navy disabled:opacity-60"
        />
        <p className="text-caption text-slate">
          Name a figure as {figureReference("figure_id")}, and combine figures
          with arithmetic, brackets and the words below. Up to{" "}
          {MAX_FIGURE_REFERENCES} different figures in one formula.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onCheck}
          disabled={disabled || checking || value.trim().length === 0}
          className="rounded-md border border-border px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface disabled:opacity-50"
        >
          {checking ? "Checking…" : "Check this formula"}
        </button>
        {!current && verdict !== null && (
          <span className="text-caption text-slate">
            The formula has changed since it was last checked.
          </span>
        )}
      </div>

      {checkError !== null && (
        <p
          className="rounded-md border border-critical/30 bg-critical-light px-3 py-2 text-caption text-navy"
          role="alert"
        >
          {checkError}
        </p>
      )}

      {read !== null && read.valid && (
        <div className="rounded-md border border-success/30 bg-success-light px-3 py-2">
          <p className="flex items-center gap-1.5 text-caption font-medium text-success">
            <CheckCircle2 size={13} aria-hidden />
            {read.message}
          </p>
          {(read.referencedMemberLabels ?? []).length > 0 && (
            <p className="mt-1 text-caption text-navy">
              It reads {(read.referencedMemberLabels ?? []).join(", ")}.
            </p>
          )}
        </div>
      )}

      {read !== null && !read.valid && (
        <div
          className="rounded-md border border-critical/30 bg-critical-light px-3 py-2"
          role="alert"
        >
          <p className="flex items-start gap-1.5 text-caption text-navy">
            <AlertCircle size={13} aria-hidden className="mt-0.5 shrink-0" />
            <span>{refusedSentence ?? read.message}</span>
          </p>
          {read.position != null && (
            <button
              type="button"
              onClick={() => showPosition(read.position!)}
              className="mt-1 text-caption font-medium text-action"
            >
              Show me character {read.position}
            </button>
          )}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-md border border-border-light p-3">
          <h4 className="text-caption font-medium text-navy">
            Figures you can use
          </h4>
          <label className="mt-2 flex items-center gap-1.5 rounded-md border border-border px-2 py-1">
            <Search size={12} aria-hidden className="text-slate" />
            <span className="sr-only">Search the figures you can use</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search"
              className="w-full bg-transparent text-caption text-navy outline-hidden"
            />
          </label>
          {figures.length === 0 ? (
            <p className="mt-2 text-caption text-slate">
              Your access does not cover any figure for this institution yet, so
              there is nothing to write a formula over.
            </p>
          ) : (
            <ul className="mt-2 max-h-56 space-y-0.5 overflow-y-auto pr-1">
              {matches.map((figure) => (
                <li key={figure.id}>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => insert(figure.id)}
                    className="w-full rounded-sm px-1 py-1 text-left hover:bg-surface disabled:opacity-60"
                  >
                    <span className="block text-caption text-navy">
                      {figure.label}
                    </span>
                    <span className="block font-mono text-micro text-slate">
                      {figureReference(figure.id)} ·{" "}
                      {moduleLabel(figure.module)}
                    </span>
                  </button>
                </li>
              ))}
              {matches.length === 0 && (
                <li className="px-1 py-1 text-caption text-slate">
                  No figure you can use matches that.
                </li>
              )}
            </ul>
          )}
        </section>

        <section className="rounded-md border border-border-light p-3">
          <h4 className="text-caption font-medium text-navy">
            What you can write
          </h4>
          <dl className="mt-2 space-y-2">
            {MEASURE_FUNCTIONS.map((fn) => (
              <div key={fn.name}>
                <dt className="font-mono text-micro text-navy">
                  {fn.signature}
                </dt>
                <dd className="text-caption text-slate">{fn.purpose}</dd>
              </div>
            ))}
            {MEASURE_OPERATORS.map((operator) => (
              <div key={operator.symbol}>
                <dt className="font-mono text-micro text-navy">
                  {operator.symbol}
                </dt>
                <dd className="text-caption text-slate">{operator.reads}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-caption text-slate">
            Where a formula compares periods, write which period it compares
            over — {PERIOD_GRAINS.map((grain) => grain.word).join(", ")} — so
            the measure means the same comparison everywhere it is used. A
            formula can look back up to {MAX_LAG_PERIODS} periods, and no
            further than the history this deployment keeps.
          </p>
        </section>
      </div>
    </div>
  );
}
