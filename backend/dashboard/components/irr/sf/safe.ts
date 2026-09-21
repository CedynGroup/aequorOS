/**
 * Belt to the normaliser's braces.
 *
 * Every framework read goes through `lib/api/irrbbSfNormalize.ts`, and the
 * hooks make that normaliser a REQUIRED argument, so in the running app a
 * panel's `view` always satisfies its declared type. These helpers exist for
 * the case that rule cannot cover: a panel mounted from somewhere that skipped
 * the adapter.
 *
 * The ICAAP filing suite found five components reading `data.stages.length`
 * straight off an unnormalised body, each of which threw rather than degrading.
 * A framework panel that throws takes the whole screen down, so the lists are
 * read through here instead.
 *
 * NOTE WHAT IS **NOT** HERE. There is no numeric fallback and there never may
 * be: an absent list is genuinely empty, but an absent economic-value loss is
 * NOT zero, and the moment this file grows a `?? 0` it stops being a guard and
 * becomes the defect it was written against. Figures are read by `figures.ts`,
 * which prints absences as absences.
 */

/** A list, or an empty one. Never a crash, never an invented row. */
export function rows<T>(value: readonly T[] | null | undefined): readonly T[] {
  return Array.isArray(value) ? value : [];
}
