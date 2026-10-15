/** Inspector forms offer hours and minutes; the operator API accepts minutes. */
export type InspectorDurationUnit = "hours" | "minutes";

export function inspectorHours(minutes: number): number {
  return minutes / 60;
}

export function inspectorMinutes(hours: number): number {
  return Math.round(hours * 60);
}

export function inspectorDurationValue(
  minutes: number,
  unit: InspectorDurationUnit,
): number {
  return unit === "hours" ? inspectorHours(minutes) : minutes;
}

export function inspectorDurationMinutes(
  value: number,
  unit: InspectorDurationUnit,
): number {
  return unit === "hours" ? inspectorMinutes(value) : value;
}
