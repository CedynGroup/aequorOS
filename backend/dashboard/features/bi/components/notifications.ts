/**
 * The rules that make a threshold alert and a scheduled report unbuildable wrong.
 *
 * Two combinations are what a person will get wrong here, and both are refused by
 * the server AND by the database:
 *
 * * **a cadence and its schedule.** A weekly report needs a weekday; a daily one
 *   must not carry one; one that fires when new figures arrive carries no clock
 *   at all. So the controls do not offer the fields a cadence does not use, and
 *   changing the cadence CLEARS them — `scheduleFor` is the whole of that rule,
 *   and it returns a complete schedule rather than patching one, so no field can
 *   survive a cadence change by being forgotten.
 * * **a threshold basis and its number.** An alert is judged either against a
 *   number the bank states or against the limit already governed for the figure.
 *   The governed option only exists for a figure that declares a governed source
 *   (`thresholdsSource` in the catalogue), so it is not offered for one that does
 *   not, and choosing it clears the number.
 *
 * Everything here is pure and React-free so `notifications.test.ts` can prove the
 * properties under node — including against the backend's own CHECK constraints,
 * which it reads rather than copies.
 */

/** Mirrors `models.bi_notifications.SUBSCRIPTION_CADENCES`. */
export type Cadence = "daily" | "weekly" | "monthly" | "on_new_data";
/** Mirrors `models.bi_notifications.SUBSCRIPTION_FORMATS`. */
export type ArtifactFormat = "csv" | "xlsx" | "pdf";
/** Mirrors `models.bi_notifications.ALERT_DIRECTIONS`. */
export type AlertDirection = "above" | "below";
/** Mirrors `models.bi_notifications.ALERT_THRESHOLD_BASES`. */
export type ThresholdBasis = "stated" | "governed_limit";
/** Mirrors `models.bi_notifications.DELIVERY_STATUSES`. */
export type DeliveryStatus =
  "pending" | "sent" | "denied" | "no_data" | "failed";

/**
 * The clock and calendar fields a schedule can carry. All four are nullable
 * because which ones apply is decided by the cadence, and a field that does not
 * apply must be ABSENT rather than zero: `hour: 0` is midnight, not "unset".
 */
export type Schedule = Readonly<{
  hour: number | null;
  minute: number | null;
  dayOfWeek: number | null;
  dayOfMonth: number | null;
}>;

/** Which controls a cadence needs. Anything not named here is cleared. */
export type ScheduleShape = Readonly<{
  clock: boolean;
  dayOfWeek: boolean;
  dayOfMonth: boolean;
}>;

/**
 * `day_of_month` stops at 28 so a monthly report fires in every month. A 31st
 * would silently skip most of them, which is why the server refuses it rather
 * than rolling back to the last day — the pinned value is the CHECK constraint's.
 */
export const DAY_OF_MONTH_MAX = 28;

/** The default send time when a reader first picks a scheduled cadence. */
export const DEFAULT_HOUR = 7;
export const DEFAULT_MINUTE = 30;
/** Monday, in ISO-8601 weekday numbering. */
export const DEFAULT_DAY_OF_WEEK = 1;
export const DEFAULT_DAY_OF_MONTH = 1;

export const CADENCE_SHAPES: Readonly<Record<Cadence, ScheduleShape>> = {
  daily: { clock: true, dayOfWeek: false, dayOfMonth: false },
  weekly: { clock: true, dayOfWeek: true, dayOfMonth: false },
  monthly: { clock: true, dayOfWeek: false, dayOfMonth: true },
  on_new_data: { clock: false, dayOfWeek: false, dayOfMonth: false },
};

export const CADENCE_OPTIONS: readonly {
  value: Cadence;
  label: string;
  hint: string;
}[] = [
  {
    value: "daily",
    label: "Every day",
    hint: "Sent once a day at the time you choose.",
  },
  {
    value: "weekly",
    label: "Every week",
    hint: "Sent on one weekday, at the time you choose.",
  },
  {
    value: "monthly",
    label: "Every month",
    hint: "Sent on one day of the month, at the time you choose.",
  },
  {
    value: "on_new_data",
    label: "When new figures arrive",
    hint: "Sent each time this institution's figures are rebuilt for a reporting date. No fixed time.",
  },
];

/** Monday first, as ISO-8601 numbers them — which is what the server stores. */
export const WEEKDAYS: readonly { value: number; label: string }[] = [
  { value: 1, label: "Monday" },
  { value: 2, label: "Tuesday" },
  { value: 3, label: "Wednesday" },
  { value: 4, label: "Thursday" },
  { value: 5, label: "Friday" },
  { value: 6, label: "Saturday" },
  { value: 7, label: "Sunday" },
];

export const FORMAT_OPTIONS: readonly {
  value: ArtifactFormat;
  label: string;
}[] = [
  { value: "csv", label: "Comma-separated values" },
  { value: "xlsx", label: "Excel workbook" },
  { value: "pdf", label: "PDF" },
];

export const DIRECTION_OPTIONS: readonly {
  value: AlertDirection;
  label: string;
}[] = [
  { value: "above", label: "rises above" },
  { value: "below", label: "falls below" },
];

/**
 * A complete schedule for one cadence, keeping what still applies.
 *
 * Returns the WHOLE schedule rather than a patch: that is what makes an invalid
 * combination unbuildable instead of merely rejected. Switching a weekly report
 * to daily cannot leave its weekday behind, because the daily shape does not
 * name one and every field is rebuilt from the shape.
 */
export function scheduleFor(cadence: Cadence, current?: Schedule): Schedule {
  const shape = CADENCE_SHAPES[cadence];
  return {
    hour: shape.clock ? (current?.hour ?? DEFAULT_HOUR) : null,
    minute: shape.clock ? (current?.minute ?? DEFAULT_MINUTE) : null,
    dayOfWeek: shape.dayOfWeek
      ? (current?.dayOfWeek ?? DEFAULT_DAY_OF_WEEK)
      : null,
    dayOfMonth: shape.dayOfMonth
      ? (current?.dayOfMonth ?? DEFAULT_DAY_OF_MONTH)
      : null,
  };
}

/** Whether a schedule carries exactly the fields its cadence needs. */
export function scheduleIsComplete(
  cadence: Cadence,
  schedule: Schedule,
): boolean {
  const shape = CADENCE_SHAPES[cadence];
  const present = (value: number | null): boolean => value !== null;
  return (
    present(schedule.hour) === shape.clock &&
    present(schedule.minute) === shape.clock &&
    present(schedule.dayOfWeek) === shape.dayOfWeek &&
    present(schedule.dayOfMonth) === shape.dayOfMonth
  );
}

function twoDigits(value: number): string {
  return value < 10 ? `0${value}` : `${value}`;
}

function ordinal(day: number): string {
  const tens = day % 100;
  if (tens >= 11 && tens <= 13) return `${day}th`;
  const ones = day % 10;
  if (ones === 1) return `${day}st`;
  if (ones === 2) return `${day}nd`;
  if (ones === 3) return `${day}rd`;
  return `${day}th`;
}

/**
 * When this report goes out, in words, naming the zone it is read in.
 *
 * The zone is always stated because the clock fields are read where the
 * institution is, not where the reader is: "07:30" with no zone is the sentence
 * that makes somebody expect a pack at half past seven their own time.
 */
export function scheduleSentence(
  cadence: Cadence,
  schedule: Schedule,
  timeZone: string,
): string {
  if (cadence === "on_new_data") {
    return "Sent whenever this institution's figures are rebuilt for a reporting date.";
  }
  if (schedule.hour === null || schedule.minute === null) {
    return "Choose a time to send this report.";
  }
  const at = `${twoDigits(schedule.hour)}:${twoDigits(schedule.minute)} ${timeZone}`;
  if (cadence === "daily") return `Sent every day at ${at}.`;
  if (cadence === "weekly") {
    const day = WEEKDAYS.find((entry) => entry.value === schedule.dayOfWeek);
    return day
      ? `Sent every ${day.label} at ${at}.`
      : "Choose a weekday to send this report.";
  }
  return schedule.dayOfMonth === null
    ? "Choose a day of the month to send this report."
    : `Sent on the ${ordinal(schedule.dayOfMonth)} of every month at ${at}.`;
}

/**
 * What a figure may be judged against.
 *
 * `governed_limit` exists only for a figure the catalogue says a limit is
 * governed for. Offering it otherwise would let a reader build an alert that can
 * only ever record "there was no line to judge it against" — which the server
 * refuses, but refusing a control the interface should not have offered is a
 * worse experience than not offering it.
 */
export function thresholdBasisOptions(
  measure: { thresholdsSource?: string | null } | null | undefined,
): readonly { value: ThresholdBasis; label: string; hint: string }[] {
  const options: { value: ThresholdBasis; label: string; hint: string }[] = [
    {
      value: "stated",
      label: "A number I set",
      hint: "The alert is judged against this number until you change it.",
    },
  ];
  if (measure?.thresholdsSource) {
    options.push({
      value: "governed_limit",
      label: "The limit already governed for this figure",
      hint: "The line comes from the institution's register on each reporting date, so it moves when the limit does.",
    });
  }
  return options;
}

/** Whether the governed option is available for this figure at all. */
export function governedLimitAvailable(
  measure: { thresholdsSource?: string | null } | null | undefined,
): boolean {
  return Boolean(measure?.thresholdsSource);
}

/**
 * A basis and the number that may accompany it, as one value.
 *
 * Same reasoning as `scheduleFor`: the pair is rebuilt rather than patched, so a
 * governed basis cannot keep a number that a previous stated basis left behind —
 * the combination the database's CHECK refuses.
 */
export function thresholdFor(
  basis: ThresholdBasis,
  stated: string,
): { basis: ThresholdBasis; threshold: string | null } {
  return basis === "stated"
    ? { basis, threshold: stated }
    : { basis, threshold: null };
}

/** What this alert will watch for, in words, before it is saved. */
export function alertSentence(
  measureLabel: string | null,
  direction: AlertDirection,
  basis: ThresholdBasis,
  stated: string,
): string {
  const figure = measureLabel ?? "the figure you choose";
  const moves = direction === "above" ? "rises above" : "falls below";
  if (basis === "governed_limit") {
    return `Tell me when ${figure} ${moves} the limit governed for it.`;
  }
  const trimmed = stated.trim();
  return trimmed
    ? `Tell me when ${figure} ${moves} ${trimmed}.`
    : `Tell me when ${figure} ${moves} a number you set.`;
}

/**
 * How a delivery's outcome should READ, which is not the same as whether it
 * succeeded.
 *
 * `denied` is deliberately `neutral`: a recipient whose access does not cover the
 * figures was correctly sent nothing, and painting that red would make an owner
 * chase a defect that is the platform working. `failed` is the only `warning` —
 * the relay did not accept the message, and somebody has to look at it.
 */
export type DeliveryTone = "positive" | "neutral" | "warning" | "pending";

export function deliveryTone(status: DeliveryStatus): DeliveryTone {
  if (status === "sent") return "positive";
  if (status === "failed") return "warning";
  if (status === "pending") return "pending";
  return "neutral";
}

/**
 * Addresses a reader typed, one per line or comma-separated.
 *
 * Trimmed, lower-cased and de-duplicated, which is exactly what the server does
 * before it resolves them — so the count shown against the cap here is the count
 * the server will apply it to. Nothing is validated as an address beyond
 * requiring an `@`: whether an address names a colleague is the server's
 * question, and it answers it by refusing the ones it cannot resolve.
 */
export function parseRecipients(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of text.split(/[\s,;]+/)) {
    const address = part.trim().toLowerCase();
    if (!address || !address.includes("@") || seen.has(address)) continue;
    seen.add(address);
    out.push(address);
  }
  return out;
}

/** Addresses the reader typed that cannot be an address at all. */
export function malformedRecipients(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[\s,;]+/)) {
    const candidate = part.trim();
    if (candidate && !candidate.includes("@")) out.push(candidate);
  }
  return out;
}

/**
 * The unit a threshold must be typed in, as a phrase rather than a code.
 *
 * Jurisdiction-neutral on purpose: an amount is "the reporting currency", which
 * the institution's own jurisdiction resolves — never a currency literal. A code
 * the catalogue adds later degrades to itself rather than being hidden, so a new
 * unit is visible the day it exists.
 */
const VALUE_TYPE_UNITS: Readonly<Record<string, string>> = {
  amount: "the reporting currency",
  pct: "per cent",
  fraction: "a proportion of one",
  index: "index points",
  duration_years: "years",
  count: "whole numbers",
};

export function valueTypeLabel(code: string | null | undefined): string {
  if (!code) return "the figure's own unit";
  return VALUE_TYPE_UNITS[code] ?? code.replace(/_/g, " ");
}
