"use client";

/**
 * Define one of the institution's own figures: its id, its name, what kind of
 * number it is, which way is good, and the formula.
 *
 * FOUR THINGS THIS FORM WILL NOT DO.
 *
 * 1. It will not save a formula the server has not passed. `expressionReady` ties
 *    the verdict to the exact text it was reached about, so editing after a check
 *    makes the verdict stale and the save waits for a new one. A client-side
 *    guess about validity is the one shortcut that would make this screen worse.
 * 2. It will not guess the direction. `favourable_direction` is a required
 *    declaration because a surface that colours a movement has to know which way
 *    is good, and defaulting it would eventually tell a banker that a rising cost
 *    ratio is good news. The neutral option is offered and it means neutral.
 * 3. It will not change a measure's id. The id is how a formula, a chart and a
 *    saved dashboard name the figure; `PUT …/bi/measures/{id}` takes no new key,
 *    and a form that offered the field would imply a rename the product does not
 *    do.
 * 4. It will not open a certified measure as though the edit were free. The
 *    database will not hold a certified row whose text differs from the text that
 *    was approved (`ck_bi_measures_certified_matches_expression`), so the edit
 *    drops the certification — and that sentence is on screen before the first
 *    keystroke, not in a toast afterwards.
 */

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import type {
  BiCatalogueMeasureRead,
  BiMeasureRead,
} from "@aequoros/risk-service-api";
import ExpressionEditor, {
  type ExpressionVerdict,
} from "@/components/bi/ExpressionEditor";
import {
  EMPTY_MEASURE_DRAFT,
  MEASURE_DESCRIPTION_MAX_LENGTH,
  MEASURE_DIRECTIONS,
  MEASURE_KEY_MAX_LENGTH,
  MEASURE_LABEL_MAX_LENGTH,
  MEASURE_VALUE_TYPES,
  draftFromMeasure,
  draftProblems,
  expressionReady,
  measureControls,
  type MeasureDraft,
} from "@/components/bi/measures";

export default function MeasureComposer({
  editing,
  figures,
  verdict,
  checking,
  checkError,
  onCheck,
  saving,
  saveError,
  onSubmit,
  onCancel,
}: {
  /** The measure being changed, or null for a new one. */
  editing: BiMeasureRead | null;
  figures: readonly BiCatalogueMeasureRead[];
  verdict: ExpressionVerdict | null;
  checking: boolean;
  checkError: string | null;
  onCheck: (expression: string) => void;
  saving: boolean;
  /** Whatever the save refused with, already worded for a reader. */
  saveError: string | null;
  onSubmit: (draft: MeasureDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<MeasureDraft>(
    editing ? draftFromMeasure(editing) : EMPTY_MEASURE_DRAFT,
  );

  useEffect(() => {
    setDraft(editing ? draftFromMeasure(editing) : EMPTY_MEASURE_DRAFT);
  }, [editing]);

  const keyEditable = editing === null;
  const problems = draftProblems(draft, { keyEditable });
  const ready = expressionReady(draft, verdict);
  const consequence = editing ? measureControls(editing).editConsequence : null;

  function set<K extends keyof MeasureDraft>(
    field: K,
    next: MeasureDraft[K],
  ): void {
    setDraft((current) => ({ ...current, [field]: next }));
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (problems.length > 0 || !ready || saving) return;
        onSubmit(draft);
      }}
    >
      {consequence !== null && (
        <p
          className="flex items-start gap-1.5 rounded-md border border-warning/30 bg-warning-light px-3 py-2 text-caption text-navy"
          role="status"
        >
          <AlertTriangle size={13} aria-hidden className="mt-0.5 shrink-0" />
          <span>{consequence}</span>
        </p>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label
            htmlFor="bi-measure-label"
            className="text-caption font-medium text-navy"
          >
            Name
          </label>
          <input
            id="bi-measure-label"
            value={draft.label}
            maxLength={MEASURE_LABEL_MAX_LENGTH}
            onChange={(event) => set("label", event.target.value)}
            placeholder="Cost of funds against the loan book"
            className="rounded-md border border-border px-3 py-2 text-caption text-navy"
          />
          <p className="text-caption text-slate">
            How the figure will read as a chart heading or a column.
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="bi-measure-key"
            className="text-caption font-medium text-navy"
          >
            Id
          </label>
          <input
            id="bi-measure-key"
            value={draft.measureKey}
            disabled={!keyEditable}
            maxLength={MEASURE_KEY_MAX_LENGTH}
            onChange={(event) =>
              set("measureKey", event.target.value.toLowerCase())
            }
            placeholder="funding_cost_ratio"
            className="rounded-md border border-border px-3 py-2 font-mono text-caption text-navy disabled:opacity-60"
          />
          <p className="text-caption text-slate">
            {keyEditable
              ? "How a chart, a grid and another person's dashboard will name this figure. Lower-case letters, digits, underscores and dots."
              : "An id cannot be changed: charts and dashboards already name this figure by it."}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="bi-measure-description"
          className="text-caption font-medium text-navy"
        >
          What it is for
        </label>
        <textarea
          id="bi-measure-description"
          value={draft.description}
          rows={2}
          maxLength={MEASURE_DESCRIPTION_MAX_LENGTH}
          onChange={(event) => set("description", event.target.value)}
          placeholder="What this figure answers, and anything a reviewer should know about how it is worked out."
          className="rounded-md border border-border px-3 py-2 text-caption text-navy"
        />
      </div>

      <ExpressionEditor
        value={draft.expression}
        onChange={(next) => set("expression", next)}
        figures={figures}
        verdict={verdict}
        checking={checking}
        checkError={checkError}
        onCheck={() => onCheck(draft.expression)}
        disabled={saving}
      />

      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label
            htmlFor="bi-measure-value-type"
            className="text-caption font-medium text-navy"
          >
            What the answer is
          </label>
          <select
            id="bi-measure-value-type"
            value={draft.valueType}
            onChange={(event) =>
              set("valueType", event.target.value as MeasureDraft["valueType"])
            }
            className="rounded-md border border-border px-3 py-2 text-caption text-navy"
          >
            {MEASURE_VALUE_TYPES.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </select>
          <p className="text-caption text-slate">
            This decides how the number is written wherever it is shown.
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label
            htmlFor="bi-measure-direction"
            className="text-caption font-medium text-navy"
          >
            Which way is good
          </label>
          <select
            id="bi-measure-direction"
            value={draft.favourableDirection}
            onChange={(event) =>
              set(
                "favourableDirection",
                event.target.value as MeasureDraft["favourableDirection"],
              )
            }
            className="rounded-md border border-border px-3 py-2 text-caption text-navy"
          >
            {MEASURE_DIRECTIONS.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </select>
          <p className="text-caption text-slate">
            Say it plainly: a surface that colours a movement cannot work this out
            on its own, and guessing it would read as good news for a rising cost.
          </p>
        </div>
      </div>

      {problems.length > 0 && (
        <ul className="rounded-md border border-border bg-surface px-3 py-2">
          {problems.map((problem) => (
            <li key={problem} className="text-caption text-navy">
              {problem}
            </li>
          ))}
        </ul>
      )}

      {saveError !== null && (
        <p
          className="rounded-md border border-critical/30 bg-critical-light px-3 py-2 text-caption text-navy"
          role="alert"
        >
          {saveError}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={problems.length > 0 || !ready || saving}
          className="rounded-md bg-action px-3 py-2 text-caption font-medium text-white disabled:opacity-50"
        >
          {saving
            ? "Saving…"
            : editing
              ? "Save this formula"
              : "Save as my draft"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-2 text-caption text-slate hover:bg-surface"
        >
          Cancel
        </button>
        {!ready && problems.length === 0 && (
          <span className="text-caption text-slate">
            Check the formula before saving — the server decides whether it is one,
            and this page will not guess.
          </span>
        )}
      </div>
    </form>
  );
}
