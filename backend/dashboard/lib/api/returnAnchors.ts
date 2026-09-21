/**
 * The Returns workspace reporting-date picker, as pure functions.
 *
 * The dates come from `/return-anchors` — the REGULATOR's own reporting dates
 * for the selected return, never the bank's ingested reporting periods. The
 * backend offers one bounded window around today: a trailing `lookback_months`
 * of dates the bank already owes, and a forward `horizon_months` of dates
 * coming up (`services/regulatory_reporting/anchors.py`).
 *
 * The trailing half is the point. An overdue return for an elapsed period is
 * exactly the one still owed to the regulator, so those dates are offered with
 * their real state rather than filtered out — including the ones with no
 * computed position behind them, because the filing deadline ran regardless.
 * The picker therefore has to SAY which is which; that is what the label below
 * is for.
 */

import { isoDate } from './values';

/** One selectable reporting date, reduced to what the picker renders. */
export type ReportingDateOption = {
  /** ISO `YYYY-MM-DD`, the value the workspace deep-links on. */
  readonly date: string;
  /** Whether a computed position exists for this date. */
  readonly hasComputedPosition: boolean;
  /** Whether its filing deadline has passed without a completed submission. */
  readonly isOverdue: boolean;
};

/** The `/return-anchors` fields the picker reads. Structural on purpose. */
type AnchorLike = {
  readonly reportingDate: Date;
  readonly dataStatus: string;
  readonly rag: string;
};

export function toReportingDateOptions(
  anchors: readonly AnchorLike[]
): ReportingDateOption[] {
  return anchors.map((anchor) => ({
    date: isoDate(anchor.reportingDate),
    hasComputedPosition: anchor.dataStatus === 'computed',
    isOverdue: anchor.rag === 'overdue',
  }));
}

/**
 * What one option reads as in the dropdown.
 *
 * Two facts, in plain words, and only when they apply: whether the date is
 * past its deadline, and whether there are figures for it yet. A date with
 * neither note is an ordinary upcoming period end and needs no explanation.
 */
export function reportingDateOptionLabel(option: ReportingDateOption): string {
  const notes: string[] = [];
  if (option.isOverdue) notes.push('past due');
  if (!option.hasComputedPosition) notes.push('no figures yet');
  return notes.length > 0 ? `${option.date} — ${notes.join(', ')}` : option.date;
}

/**
 * The date the picker opens on: the most recent ELAPSED reporting date.
 *
 * That is the return actually due now — not the newest date offered (a future
 * period end nobody files yet) and not the newest date WITH figures (which
 * would quietly skip past an overdue period the bank has not ingested a book
 * for). Falls back to the newest date when every date offered is still ahead.
 */
export function defaultReportingDate(
  options: readonly ReportingDateOption[],
  asOf: string | undefined
): string | undefined {
  if (options.length === 0) return undefined;
  const dates = options.map((option) => option.date).sort();
  const newest = dates[dates.length - 1];
  if (!asOf) return newest;
  const elapsed = dates.filter((date) => date <= asOf);
  return elapsed.length > 0 ? elapsed[elapsed.length - 1] : newest;
}
