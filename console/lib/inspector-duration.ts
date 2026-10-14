/** Inspector forms display hours; the operator API accepts minutes. */
export function inspectorHours(minutes: number): number {
  return minutes / 60;
}

export function inspectorMinutes(hours: number): number {
  return Math.round(hours * 60);
}
