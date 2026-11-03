/**
 * How a BI refusal is READ, before anything is rendered for it.
 *
 * Pure on purpose — no transport, no React, no `@/` value import — so that
 * `disclosure.test.ts` can execute it under plain Node. `lib/api/bi.ts` wraps
 * these for `ApiError`; the components consume the wrappers.
 *
 * TWO KINDS OF 403, AND ONLY ONE OF THEM IS PARAPHRASED.
 *
 * A GRANT DENIAL is the evaluator refusing a member of the question. Its body
 * names the refused members and their labels, because an operator needs them to
 * write the grant — and a reader must not be shown them, because "you may not
 * see Largest single-name share" tells them the institution tracks one. So a
 * grant denial is rendered by `RestrictedWidget`, which restates the decision
 * ("an organization owner can grant it") and repeats no detail. The three codes
 * below are the three routes that make that decision: the query surfaces, the
 * insights strip, and the export job — which re-runs the same sentence.
 *
 * EVERY OTHER 403 is a different decision with its own production sentence and
 * nothing sensitive in it: an impersonated staff session that may not read BI
 * (`bi_human_principal_required`), a read-only staff session refused a mutation
 * (`deps.py`, a plain-string detail with no code), a data scope this view cannot
 * narrow to (`bi_data_scope_unsupported`), a dashboard only its owner may delete
 * (`bi_content_owner_only`). These used to be rendered as "Access restricted —
 * an organization owner can grant it" too, because the client keyed on the
 * status alone: a different decision from the server's, and an instruction the
 * reader could not act on. They are rendered as the server's own sentence, by
 * `RefusedWidget`, and this module is where the two are told apart.
 */

import type { BiPackWidgetView } from "./types";

/** The 403 codes whose body may name refused members. Paraphrased on screen. */
export const BI_GRANT_DENIAL_CODES: readonly string[] = [
  "bi_authorization_denied",
  "bi_insights_authorization_denied",
  "bi_export_denied",
];

/** The shape of a normalised API failure this module reads. Structural on purpose. */
export type RefusalShape = Readonly<{
  status?: number | null;
  errorCode?: string | null;
  message?: string | null;
}>;

/** A 403 that is the evaluator's grant decision, and must be paraphrased. */
export function isGrantDenial(failure: RefusalShape): boolean {
  if (failure.status !== 403) return false;
  const code = failure.errorCode;
  return typeof code === "string" && BI_GRANT_DENIAL_CODES.includes(code);
}

/**
 * The server's own sentence for a 403 that is NOT a grant denial, or `null`.
 *
 * `null` means "not a refusal this client renders verbatim": a grant denial
 * (paraphrased elsewhere), a non-403, or a 403 that arrived without a sentence
 * — that last one falls to the ordinary failure panel rather than to a sentence
 * composed here, because a refusal the platform did not word must not read as
 * though it had.
 */
export function refusalSentence(failure: RefusalShape): string | null {
  if (failure.status !== 403) return null;
  if (isGrantDenial(failure)) return null;
  const message = failure.message;
  if (typeof message !== "string" || message.trim() === "") return null;
  return message;
}

/**
 * How many widgets of a canvas were refused.
 *
 * The server's count when it sent one; otherwise the refusals actually in the
 * payload. `?? 0` was the previous answer for an absent count, and it hid the
 * refusal notice over a canvas of locked tiles — a reader looking at four
 * "Access restricted" cards under a page that said nothing about it.
 */
export function restrictedWidgetCount(
  reported: number | null | undefined,
  widgets: readonly Pick<BiPackWidgetView, "state">[],
): number {
  const counted = widgets.filter((widget) => widget.state === "restricted").length;
  if (typeof reported === "number" && Number.isFinite(reported)) {
    return Math.max(reported, counted);
  }
  return counted;
}
