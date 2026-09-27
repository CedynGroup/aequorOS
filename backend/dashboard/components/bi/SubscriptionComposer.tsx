"use client";

/**
 * Choose a question, a schedule and who receives it.
 *
 * The one combination a person gets wrong here is the CADENCE and its schedule.
 * A weekly report needs a weekday, a daily one must not carry one, and a report
 * that goes out when new figures arrive has no clock at all — a database CHECK on
 * the server. It is made unbuildable here: the controls a cadence does not use
 * are not rendered, and `scheduleFor` rebuilds the whole schedule when the
 * cadence changes, so no field survives by being forgotten.
 *
 * The question is built with the SAME module the Explore page builds one with
 * (`exploreQuery.buildExploreQuery`), so every rule the compiler enforces on the
 * shape of a query is enforced before the report is saved, and a combination the
 * server would refuse is explained in words rather than sent.
 *
 * The reporting DATE is deliberately not a control. A date chosen when a report
 * is written would mail the same month for ever; each run supplies its own, which
 * is what the sentence under the schedule says.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, CalendarClock, Mail } from "lucide-react";
import type { BiCatalogueRead, BiQuery } from "@aequoros/risk-service-api";
import {
  BI_DIMENSION_CAP,
  buildExploreQuery,
  EMPTY_SHAPE,
  measureIsCompatible,
  sliceableDimensions,
  timeBehaviourLabel,
  type ExploreCatalogue,
  type ExploreShape,
} from "@/components/bi/exploreQuery";
import {
  CADENCE_OPTIONS,
  CADENCE_SHAPES,
  DAY_OF_MONTH_MAX,
  FORMAT_OPTIONS,
  WEEKDAYS,
  parseRecipients,
  scheduleFor,
  scheduleSentence,
  type ArtifactFormat,
  type Cadence,
  type Schedule,
} from "@/components/bi/notifications";
import RecipientField from "@/components/bi/RecipientField";
import type { BiSubscriptionRead, BiSubscriptionUpsert } from "@/lib/api/bi";

export const SUBSCRIPTION_RECIPIENT_MAX = 50;

function shapeFrom(query: BiQuery | null): ExploreShape {
  if (query === null) return EMPTY_SHAPE;
  return {
    ...EMPTY_SHAPE,
    measures: query.measures ?? [],
    dimensions: query.dimensions ?? [],
  };
}

function toggle(values: readonly string[], id: string): string[] {
  return values.includes(id)
    ? values.filter((value) => value !== id)
    : [...values, id];
}

export default function SubscriptionComposer({
  catalogue,
  asOf,
  timeZone,
  editing,
  saving,
  errorMessage,
  unknownAddresses,
  onSubmit,
  onCancel,
}: {
  catalogue: BiCatalogueRead | undefined;
  /**
   * The reporting date the stored question carries. Each run rebinds it, so this
   * is the date the question is SHAPED for and never the date it reports on.
   */
  asOf: string;
  /** The institution's own zone, from the jurisdiction registry. */
  timeZone: string;
  editing: BiSubscriptionRead | null;
  saving: boolean;
  errorMessage: string | null;
  unknownAddresses: readonly string[];
  onSubmit: (body: BiSubscriptionUpsert) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  const [shape, setShape] = useState<ExploreShape>(() =>
    shapeFrom(editing?.query ?? null),
  );
  const [format, setFormat] = useState<ArtifactFormat>(
    editing?.artifactFormat ?? "csv",
  );
  const [cadence, setCadence] = useState<Cadence>(editing?.cadence ?? "weekly");
  const [schedule, setSchedule] = useState<Schedule>(() =>
    scheduleFor(editing?.cadence ?? "weekly", {
      hour: editing?.hour ?? null,
      minute: editing?.minute ?? null,
      dayOfWeek: editing?.dayOfWeek ?? null,
      dayOfMonth: editing?.dayOfMonth ?? null,
    }),
  );
  const [recipients, setRecipients] = useState(
    (editing?.recipients ?? []).map((person) => person.email).join("\n"),
  );
  const [reason, setReason] = useState("");

  useEffect(() => {
    setName(editing?.name ?? "");
    setShape(shapeFrom(editing?.query ?? null));
    setFormat(editing?.artifactFormat ?? "csv");
    setCadence(editing?.cadence ?? "weekly");
    setSchedule(
      scheduleFor(editing?.cadence ?? "weekly", {
        hour: editing?.hour ?? null,
        minute: editing?.minute ?? null,
        dayOfWeek: editing?.dayOfWeek ?? null,
        dayOfMonth: editing?.dayOfMonth ?? null,
      }),
    );
    setRecipients(
      (editing?.recipients ?? []).map((person) => person.email).join("\n"),
    );
    setReason("");
  }, [editing]);

  const exploreCatalogue: ExploreCatalogue = useMemo(
    () => ({
      measures: catalogue?.measures ?? [],
      dimensions: catalogue?.dimensions ?? [],
    }),
    [catalogue],
  );
  const fields = useMemo(
    () => sliceableDimensions(exploreCatalogue, shape.measures),
    [exploreCatalogue, shape.measures],
  );
  const plan = useMemo(
    () => buildExploreQuery(shape, exploreCatalogue, { asOf }),
    [asOf, exploreCatalogue, shape],
  );
  const blocking = plan.problems.filter((problem) => problem.blocking);
  const advisory = plan.problems.filter((problem) => !problem.blocking);

  const shapeControls = CADENCE_SHAPES[cadence];
  const recipientCount = parseRecipients(recipients).length;
  const complete =
    name.trim() !== "" &&
    reason.trim() !== "" &&
    plan.query !== null &&
    blocking.length === 0 &&
    recipientCount > 0 &&
    recipientCount <= SUBSCRIPTION_RECIPIENT_MAX;

  function chooseCadence(next: Cadence): void {
    setCadence(next);
    // Rebuilt from the new cadence's shape, so the weekday of a weekly report
    // cannot survive a change to daily.
    setSchedule(scheduleFor(next, schedule));
  }

  function submit(): void {
    if (plan.query === null) return;
    onSubmit({
      name: name.trim(),
      query: plan.query,
      artifactFormat: format,
      cadence,
      hour: schedule.hour,
      minute: schedule.minute,
      dayOfWeek: schedule.dayOfWeek,
      dayOfMonth: schedule.dayOfMonth,
      recipientEmails: parseRecipients(recipients),
      isActive: editing?.isActive ?? true,
      reason: reason.trim(),
    });
  }

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (complete && !saving) submit();
      }}
    >
      <Field label="Name" htmlFor="bi-sub-name">
        <input
          id="bi-sub-name"
          value={name}
          maxLength={120}
          onChange={(event) => setName(event.target.value)}
          className={inputClass}
        />
      </Field>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-caption font-medium text-navy">
          Figures to report
        </legend>
        <p className="text-caption text-slate leading-relaxed">
          Only the figures your own access covers are listed. Each
          recipient&apos;s copy is prepared under their access, not yours.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {(catalogue?.measures ?? []).map((measure) => {
            const chosen = shape.measures.includes(measure.id);
            const available = measureIsCompatible(
              exploreCatalogue,
              shape.measures,
              measure.id,
            );
            return (
              <button
                key={measure.id}
                type="button"
                disabled={!available}
                onClick={() =>
                  setShape({
                    ...shape,
                    measures: toggle(shape.measures, measure.id),
                    // Dropping a figure can make a field unsliceable, so the
                    // fields are re-filtered to the ones every remaining figure
                    // still supports.
                    dimensions: shape.dimensions.filter((id) =>
                      sliceableDimensions(
                        exploreCatalogue,
                        toggle(shape.measures, measure.id),
                      ).some((dimension) => dimension.id === id),
                    ),
                  })
                }
                className={`rounded-full border px-2.5 py-1 text-caption ${
                  chosen
                    ? "border-action bg-action-light text-action"
                    : "border-border text-slate hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
                }`}
              >
                {measure.label}
              </button>
            );
          })}
        </div>
        {shape.measures.length > 0 && (
          <p className="text-micro text-slate">
            Measured as the{" "}
            {timeBehaviourLabel(
              (catalogue?.measures ?? []).find(
                (measure) => measure.id === shape.measures[0],
              )?.timeBehaviour ?? "",
            )}
            .
          </p>
        )}
      </fieldset>

      {fields.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-caption font-medium text-navy">
            Broken down by
          </legend>
          <p className="text-caption text-slate">
            Optional, and up to {BI_DIMENSION_CAP} fields.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {fields.map((dimension) => {
              const chosen = shape.dimensions.includes(dimension.id);
              return (
                <button
                  key={dimension.id}
                  type="button"
                  onClick={() =>
                    setShape({
                      ...shape,
                      dimensions: toggle(shape.dimensions, dimension.id),
                    })
                  }
                  className={`rounded-full border px-2.5 py-1 text-caption ${
                    chosen
                      ? "border-action bg-action-light text-action"
                      : "border-border text-slate hover:bg-surface"
                  }`}
                >
                  {dimension.label}
                </button>
              );
            })}
          </div>
        </fieldset>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="How often" htmlFor="bi-sub-cadence">
          <select
            id="bi-sub-cadence"
            value={cadence}
            onChange={(event) => chooseCadence(event.target.value as Cadence)}
            className={inputClass}
          >
            {CADENCE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <p className="text-caption text-slate leading-relaxed">
            {CADENCE_OPTIONS.find((option) => option.value === cadence)?.hint}
          </p>
        </Field>

        <Field label="File format" htmlFor="bi-sub-format">
          <select
            id="bi-sub-format"
            value={format}
            onChange={(event) =>
              setFormat(event.target.value as ArtifactFormat)
            }
            className={inputClass}
          >
            {FORMAT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>

        {/* Each control exists only for a cadence that uses it. */}
        {shapeControls.clock && (
          <Field label={`Time (${timeZone})`} htmlFor="bi-sub-time">
            <input
              id="bi-sub-time"
              type="time"
              value={`${pad(schedule.hour)}:${pad(schedule.minute)}`}
              onChange={(event) => {
                const [hour, minute] = event.target.value.split(":");
                setSchedule({
                  ...schedule,
                  hour: Number(hour),
                  minute: Number(minute),
                });
              }}
              className={inputClass}
            />
            <p className="text-caption text-slate">
              Read where this institution is, not where you are.
            </p>
          </Field>
        )}

        {shapeControls.dayOfWeek && (
          <Field label="Which day" htmlFor="bi-sub-weekday">
            <select
              id="bi-sub-weekday"
              value={schedule.dayOfWeek ?? 1}
              onChange={(event) =>
                setSchedule({
                  ...schedule,
                  dayOfWeek: Number(event.target.value),
                })
              }
              className={inputClass}
            >
              {WEEKDAYS.map((day) => (
                <option key={day.value} value={day.value}>
                  {day.label}
                </option>
              ))}
            </select>
          </Field>
        )}

        {shapeControls.dayOfMonth && (
          <Field label="Day of the month" htmlFor="bi-sub-day">
            <select
              id="bi-sub-day"
              value={schedule.dayOfMonth ?? 1}
              onChange={(event) =>
                setSchedule({
                  ...schedule,
                  dayOfMonth: Number(event.target.value),
                })
              }
              className={inputClass}
            >
              {Array.from(
                { length: DAY_OF_MONTH_MAX },
                (_, index) => index + 1,
              ).map((day) => (
                <option key={day} value={day}>
                  {day}
                </option>
              ))}
            </select>
            <p className="text-caption text-slate">
              Up to the {DAY_OF_MONTH_MAX}th, so the report goes out in every
              month.
            </p>
          </Field>
        )}
      </div>

      <p className="inline-flex items-start gap-2 rounded-md bg-surface px-3 py-2 text-body text-navy">
        <CalendarClock
          size={15}
          className="mt-0.5 shrink-0 text-action"
          aria-hidden
        />
        {scheduleSentence(cadence, schedule, timeZone)} Each send reports the
        institution&apos;s own latest reporting date.
      </p>

      <RecipientField
        value={recipients}
        onChange={setRecipients}
        max={SUBSCRIPTION_RECIPIENT_MAX}
        unknown={unknownAddresses}
        label="Send it to"
        help="Each person receives their own copy, prepared under their own access. Somebody whose access does not cover these figures is sent nothing, and you will see that in the send history."
      />

      <Field label="Why" htmlFor="bi-sub-reason">
        <input
          id="bi-sub-reason"
          value={reason}
          maxLength={500}
          onChange={(event) => setReason(event.target.value)}
          className={inputClass}
        />
        <p className="text-caption text-slate">
          Recorded with your name against this change.
        </p>
      </Field>

      {blocking.map((problem) => (
        <p
          key={problem.id}
          className="inline-flex items-start gap-1.5 text-caption text-critical"
        >
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {problem.message}
        </p>
      ))}
      {advisory.map((problem) => (
        <p
          key={problem.id}
          className="inline-flex items-start gap-1.5 text-caption text-warning"
        >
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {problem.message}
        </p>
      ))}
      {errorMessage && (
        <p className="inline-flex items-start gap-1.5 text-caption text-critical">
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
          {errorMessage}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={!complete || saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-2 text-caption font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Mail size={13} aria-hidden />
          {saving
            ? "Saving…"
            : editing
              ? "Save this report"
              : "Create this report"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function pad(value: number | null): string {
  const number = value ?? 0;
  return number < 10 ? `0${number}` : `${number}`;
}

const inputClass =
  "rounded-md border border-border bg-white px-3 py-2 text-body text-navy focus:border-action focus:outline-none";

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-caption font-medium text-navy">
        {label}
      </label>
      {children}
    </div>
  );
}
