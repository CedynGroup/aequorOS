/**
 * The BI mart backfill request, built from what the operator typed.
 *
 * Pure and dependency-free (the one import is type-only), so plain Node can
 * prove it: `bi-backfill.test.ts` pins that a blank from-date is OMITTED rather
 * than sent as "" (the server would 422 on an empty date, and the default it
 * substitutes — the bank's latest snapshot — is the point of leaving it blank),
 * and that the window is checked here in the backend's own terms before a
 * request that would only be refused is made.
 *
 * The server remains the authority: `app/operator/services/bi_backfill.py`
 * re-checks the window, the snapshot, the enqueue switch and the running-chain
 * rule. This mirror exists so the modal can disable its confirm button with a
 * sentence rather than let the operator discover the rule as a 409.
 */

import type { BiBackfillRequest } from './api';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export type BiBackfillDraft = Readonly<{
  bankId: string;
  /** Newest date to build; blank means "the bank's latest canonical snapshot". */
  fromDate: string;
  /** Oldest date to build, inclusive. Required. */
  untilDate: string;
  note: string;
}>;

/**
 * Why the window cannot be posted, or `null` when it can. Mirrors the backend's
 * rule: a backfill walks NEWEST-FIRST, so `until_date` must be on or before
 * `from_date` whenever one is given. ISO dates compare lexicographically.
 */
export function backfillWindowProblem(
  draft: Pick<BiBackfillDraft, 'fromDate' | 'untilDate'>,
): string | null {
  const until = draft.untilDate.trim();
  const from = draft.fromDate.trim();
  if (!until) return 'Give the oldest date to build (the until date).';
  if (!ISO_DATE.test(until)) return 'The until date must be a calendar date (YYYY-MM-DD).';
  if (from && !ISO_DATE.test(from)) return 'The from date must be a calendar date (YYYY-MM-DD).';
  if (from && until > from) {
    return (
      `The until date ${until} is after the from date ${from}. A backfill walks ` +
      'newest-first, so the until date must be on or before the from date.'
    );
  }
  return null;
}

/** The oldest as-of date among ingested batches — the natural until date. */
export function oldestAsOfDate(batches: readonly { as_of_date: string }[]): string | null {
  let oldest: string | null = null;
  for (const batch of batches) {
    if (!ISO_DATE.test(batch.as_of_date)) continue;
    if (oldest === null || batch.as_of_date < oldest) oldest = batch.as_of_date;
  }
  return oldest;
}

/** The wire body. A blank from-date is omitted so the server's default applies. */
export function biBackfillRequest(draft: BiBackfillDraft): BiBackfillRequest {
  const from = draft.fromDate.trim();
  return {
    bank_id: draft.bankId,
    until_date: draft.untilDate.trim(),
    ...(from ? { from_date: from } : {}),
    note: draft.note.trim(),
  };
}
