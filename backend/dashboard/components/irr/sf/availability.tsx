"use client";

/**
 * "There is nothing to show here yet" — a first-class state on the IRRBB
 * Standardised Framework screen, not a failure.
 *
 * TWO DIFFERENT NOTHINGS, and the screen must not confuse them:
 *
 *   1. **The framework is not available here.** The route is absent (405, 501
 *      from a proxy that knows the path but not the method), or the platform
 *      answered its graceful "no computed data yet" envelope. Nothing is
 *      broken; the framework is simply not switched on for this institution.
 *   2. **No result exists for this reporting date.** The read answers 404
 *      whenever the newest run for the date has not succeeded — which is also
 *      what it answers when the caller holds no IRRBB binding, because that
 *      route hides rather than announces. Either way the honest sentence is
 *      the same, and a preparer with run authority is offered the framework.
 *
 * WHY THE SERVER'S 404 SENTENCE IS NEVER PRINTED. One of the two sentences it
 * can carry is the deny-hide ("Bank not found."), which would be both
 * confusing and a small enumeration hint. So this module chooses its own copy
 * from the STATUS, and the payload is never read.
 *
 * Genuine failures (403, 409, 500, a network drop) are NOT handled here. They
 * fall through to `QueryBoundary`, which shows the server's own message and a
 * retry, because those are errors and must look like errors.
 */

import type { ReactNode } from "react";

/**
 * Statuses that mean "the server does not serve this here", as opposed to
 * "the server has no result for this date", "the server refused you" or "the
 * server broke".
 */
const NOT_SERVED_HERE = new Set([405, 501]);

/** The status that means "no result for this reporting date". */
const NO_RESULT = 404;

export type SfNotice = {
  kind: "not-enabled" | "no-result";
  title: string;
  message: string;
};

export const SF_NOT_ENABLED_TITLE = "The standardised framework is not available here";
export const SF_NOT_ENABLED =
  "Nothing has gone wrong — the IRRBB standardised framework has not been " +
  "enabled for this institution yet.";

export const SF_NO_RESULT_TITLE = "No standardised framework result for this reporting date";
export const SF_NO_RESULT =
  "Nothing has gone wrong. The framework has not produced a result for this " +
  "reporting date, so there is nothing to read yet.";

/**
 * The notice to render instead of the framework, or null to render it.
 *
 * Deliberately total: it accepts `unknown`, because it is called with whatever
 * TanStack Query put in `error`, and a guard that can itself throw is not a
 * guard.
 */
export function sfNotice(error: unknown): SfNotice | null {
  if (!error) return null;

  const candidate = error as {
    name?: unknown;
    status?: unknown;
    reason?: unknown;
  };

  // The backend's graceful "no computed data yet" envelope, surfaced by
  // `apiCall` as a typed empty state. Its own reason is a designed sentence,
  // not a denial, so it is the better one.
  if (candidate.name === "ModuleUnavailableError") {
    return {
      kind: "not-enabled",
      title: SF_NOT_ENABLED_TITLE,
      message:
        typeof candidate.reason === "string" && candidate.reason.trim() !== ""
          ? candidate.reason
          : SF_NOT_ENABLED,
    };
  }

  if (typeof candidate.status !== "number") return null;

  if (NOT_SERVED_HERE.has(candidate.status)) {
    return {
      kind: "not-enabled",
      title: SF_NOT_ENABLED_TITLE,
      message: SF_NOT_ENABLED,
    };
  }

  if (candidate.status === NO_RESULT) {
    return {
      kind: "no-result",
      title: SF_NO_RESULT_TITLE,
      message: SF_NO_RESULT,
    };
  }

  return null;
}

/**
 * The panel itself. Plain elements on purpose: it is the screen a reader sees
 * when the rest of the module could not load, so it must not depend on
 * anything that could be the reason it could not load.
 */
export default function SfUnavailable({
  title,
  message,
  children,
}: {
  title: string;
  message: string;
  children?: ReactNode;
}) {
  return (
    <div className="card border-l-4 border-l-border bg-surface/60 p-5">
      <p className="text-body font-medium text-navy">{title}</p>
      <p className="mt-1 text-body leading-relaxed text-navy/80">{message}</p>
      {children}
    </div>
  );
}
