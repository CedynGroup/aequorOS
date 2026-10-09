"use client";

/**
 * One view on a saved dashboard, composed from the catalogue.
 *
 * EVERYTHING OFFERED HERE IS SOMETHING THIS READER'S ACCESS ALREADY COVERS. The
 * catalogue arrives filtered member by member through the same decision the query
 * path makes, so a figure this reader has no sentence for is not on the form at
 * all. The server authorizes the canvas again when it is saved, and again per
 * widget every time anybody opens it.
 *
 * A VIEW READS ONE REPORTING DATE — the date whoever opens the dashboard is on.
 * That is stated on the form rather than left to be discovered, and it is the
 * same window Explore asks in. `components/bi/builder.ts::authoredWidget` has the
 * reason it is the only one this surface authors: the read route returns a
 * resolved window, and a relative one cannot be recovered from it without
 * guessing — which would change what a saved view measures.
 *
 * The breakdown offer is the INTERSECTION of what every chosen figure can be
 * grouped by, because the compiler requires that; a union would put a field on
 * screen that refuses the moment it is used.
 */

import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import {
  BI_DIMENSION_CAP,
  measureIsCompatible,
  sliceableDimensions,
  timeBehaviourLabel,
  type ExploreCatalogue,
} from "./exploreQuery";
import { designationLabel, moduleLabel } from "./labels";
import {
  BUILDER_WIDGET_KINDS,
  WIDGET_CAPTION_MAX,
  WIDGET_TITLE_MAX,
  figureWidget,
  isBuilderWidgetKind,
  widgetFigures,
  widgetIdFrom,
  type BuilderWidgetKind,
} from "./builder";
import type { BiPackWidget } from "@aequoros/risk-service-api";

export type WidgetComposerProps = Readonly<{
  catalogue: ExploreCatalogue;
  /** Ids already on the canvas, so a new view's key is unique. */
  takenIds: readonly string[];
  /** The view being changed, or null when a new one is being added. */
  editing: BiPackWidget | null;
  onSave: (widget: BiPackWidget) => void;
  onCancel: () => void;
}>;

function toggle(values: readonly string[], id: string): string[] {
  return values.includes(id)
    ? values.filter((value) => value !== id)
    : [...values, id];
}

export default function WidgetComposer({
  catalogue,
  takenIds,
  editing,
  onSave,
  onCancel,
}: WidgetComposerProps) {
  const existing = editing ? widgetFigures(editing) : null;
  const [title, setTitle] = useState(editing?.title ?? "");
  const [caption, setCaption] = useState(editing?.caption ?? "");
  const [kind, setKind] = useState<BuilderWidgetKind>(
    editing && isBuilderWidgetKind(editing.kind) ? editing.kind : "table",
  );
  const [measures, setMeasures] = useState<readonly string[]>(
    existing?.measures ?? [],
  );
  const [dimensions, setDimensions] = useState<readonly string[]>(
    existing?.dimensions ?? [],
  );

  const measureGroups = useMemo(() => {
    const groups = new Map<string, typeof catalogue.measures>();
    for (const measure of catalogue.measures) {
      groups.set(measure.module, [
        ...(groups.get(measure.module) ?? []),
        measure,
      ]);
    }
    return [...groups.entries()].sort((left, right) =>
      moduleLabel(left[0]).localeCompare(moduleLabel(right[0])),
    );
  }, [catalogue]);

  const availableDimensions = useMemo(
    () => sliceableDimensions(catalogue, measures),
    [catalogue, measures],
  );

  // A field that is no longer offered — because the chosen figures changed — is
  // dropped from the request rather than sent and refused.
  const keptDimensions = dimensions.filter((id) =>
    availableDimensions.some((dimension) => dimension.id === id),
  );

  const named = title.trim();
  const problems: string[] = [];
  if (named.length === 0) problems.push("Give this view a heading.");
  if (named.length > WIDGET_TITLE_MAX) {
    problems.push(
      `Shorten the heading to ${WIDGET_TITLE_MAX} characters or fewer.`,
    );
  }
  if (measures.length === 0)
    problems.push("Choose at least one figure to show.");

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="text-caption font-medium text-navy">Heading</span>
          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={WIDGET_TITLE_MAX}
            placeholder="Deposits by product"
            className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-caption font-medium text-navy">
            Note under the heading
          </span>
          <input
            type="text"
            value={caption}
            onChange={(event) => setCaption(event.target.value)}
            maxLength={WIDGET_CAPTION_MAX}
            placeholder="Optional"
            className="rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-hidden"
          />
        </label>
      </div>

      <fieldset className="flex flex-col gap-1.5">
        <legend className="text-caption font-medium text-navy">
          How it is drawn
        </legend>
        <div className="flex flex-wrap items-center gap-1.5">
          {BUILDER_WIDGET_KINDS.map((option) => (
            <button
              key={option.kind}
              type="button"
              onClick={() => setKind(option.kind)}
              aria-pressed={kind === option.kind}
              className={`rounded-md border px-2.5 py-1 text-caption font-medium ${
                kind === option.kind
                  ? "border-action/30 bg-action-light text-action"
                  : "border-border text-slate hover:bg-surface"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <p className="mb-1 text-caption font-medium text-navy">
            What it measures
          </p>
          <div className="max-h-64 space-y-3 overflow-y-auto rounded-md border border-border p-2">
            {measureGroups.map(([module, entries]) => (
              <div key={module}>
                <p className="mb-1 text-micro font-medium uppercase tracking-wider text-slate">
                  {moduleLabel(module)}
                </p>
                <ul className="space-y-0.5">
                  {entries.map((measure) => {
                    const selectable = measureIsCompatible(
                      catalogue,
                      measures,
                      measure.id,
                    );
                    return (
                      <li key={measure.id}>
                        <label
                          className={`flex items-start gap-2 rounded px-1 py-1 ${
                            selectable ? "hover:bg-surface" : "opacity-60"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={measures.includes(measure.id)}
                            disabled={!selectable}
                            onChange={() =>
                              setMeasures((current) =>
                                toggle(current, measure.id),
                              )
                            }
                            // The figure's own name, so the control announces the
                            // figure rather than the figure plus its description
                            // plus whatever caveat sits under it.
                            aria-label={measure.label}
                            className="mt-0.5"
                          />
                          <span className="min-w-0">
                            <span className="block text-body text-navy">
                              {measure.label}
                            </span>
                            {!selectable && (
                              <span className="block text-caption text-slate">
                                Reported as a{" "}
                                {timeBehaviourLabel(measure.timeBehaviour)},
                                which cannot share a view with what you have
                                already chosen.
                              </span>
                            )}
                            {designationLabel(
                              measure.advisoryDesignation ?? null,
                            ) && (
                              <span className="mt-0.5 inline-block rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro text-slate">
                                {designationLabel(
                                  measure.advisoryDesignation ?? null,
                                )}
                              </span>
                            )}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-1 text-caption font-medium text-navy">
            Broken down by
          </p>
          <div className="max-h-64 overflow-y-auto rounded-md border border-border p-2">
            {measures.length === 0 ? (
              <p className="text-body text-slate">
                Choose a figure first — the fields it can be grouped by depend
                on what is being measured.
              </p>
            ) : availableDimensions.length === 0 ? (
              <p className="text-body text-slate">
                These figures are reported for the institution as a whole and
                cannot be broken down further.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {availableDimensions.map((dimension) => {
                  const chosen = keptDimensions.includes(dimension.id);
                  const atCap =
                    !chosen && keptDimensions.length >= BI_DIMENSION_CAP;
                  return (
                    <li key={dimension.id}>
                      <label
                        className={`flex items-start gap-2 rounded px-1 py-1 ${
                          atCap ? "opacity-60" : "hover:bg-surface"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={chosen}
                          disabled={atCap}
                          onChange={() =>
                            setDimensions((current) =>
                              toggle(current, dimension.id),
                            )
                          }
                          aria-label={dimension.label}
                          className="mt-0.5"
                        />
                        <span className="min-w-0">
                          <span className="block text-body text-navy">
                            {dimension.label}
                          </span>
                          <span className="block text-caption text-slate">
                            {dimension.description}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </div>

      <p className="text-caption leading-relaxed text-slate">
        This view reads the reporting date of whoever opens the dashboard, so it
        stays current without being edited.
      </p>

      {problems.length > 0 && (
        <ul className="space-y-1">
          {problems.map((problem) => (
            <li key={problem} className="text-caption text-warning">
              {problem}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={problems.length > 0}
          onClick={() =>
            onSave(
              figureWidget(
                {
                  id: editing ? editing.id : widgetIdFrom(named, takenIds),
                  title: named,
                  caption: caption.trim(),
                  kind,
                  measures,
                  dimensions: keptDimensions,
                },
                editing ?? undefined,
              ),
            )
          }
          className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white hover:bg-action/90 disabled:opacity-50"
        >
          <Plus size={13} aria-hidden />
          {editing ? "Keep these changes" : "Put it on the canvas"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface"
        >
          <X size={13} aria-hidden />
          Cancel
        </button>
      </div>
    </div>
  );
}
