"use client";

/**
 * "This part of the ICAAP is not available yet" — a first-class state, not a
 * failure.
 *
 * The risk & capital tabs ship ahead of their API. On a deployment where the
 * P2 routes do not exist, every one of these reads answers 404 (or 405, or 501
 * from a proxy that knows the path but not the method). A red "Could not load
 * data" panel is the wrong answer to that: nothing is broken, the feature is
 * simply not switched on for this tenant yet, and a preparer who sees an error
 * will raise a ticket about a system that is working as intended.
 *
 * So every P2 panel asks this module FIRST. When the answer is a sentence, the
 * panel renders the sentence and never touches the payload — which is also why
 * a missing route can no longer reach the rendering code at all.
 *
 * Genuine failures (403, 409, 500, a network drop) are NOT handled here. They
 * fall through to `QueryBoundary`, which shows the server's own message and a
 * retry, because those are errors and must look like errors.
 */

import type { ReactNode } from "react";

/**
 * Statuses that mean "the server does not serve this here", as opposed to "the
 * server refused you" or "the server broke".
 *
 * 404 is the one that matters in practice — it is what FastAPI answers for a
 * path with no route, and also what the ICAAP routes answer for a tenant the
 * feature is switched off for. Both are the same thing to a reader: there is
 * nothing to show yet.
 */
const UNAVAILABLE_STATUSES = new Set([404, 405, 501]);

/**
 * The sentence to render instead of this panel, or null to render the panel.
 *
 * Deliberately total: it accepts `unknown`, because it is called with whatever
 * TanStack Query put in `error`, and a guard that can itself throw is not a
 * guard.
 */
export function p2UnavailableNotice(error: unknown): string | null {
  if (!error) return null;

  const candidate = error as {
    name?: unknown;
    status?: unknown;
    reason?: unknown;
  };

  // The backend's graceful "no computed data yet" envelope, surfaced by
  // `apiCall` as a typed empty state. Its own reason is the better sentence.
  if (candidate.name === "ModuleUnavailableError") {
    return typeof candidate.reason === "string" && candidate.reason.trim() !== ""
      ? candidate.reason
      : NOT_AVAILABLE_YET;
  }

  if (
    typeof candidate.status === "number" &&
    UNAVAILABLE_STATUSES.has(candidate.status)
  ) {
    return NOT_AVAILABLE_YET;
  }

  return null;
}

export const NOT_AVAILABLE_YET =
  "This part of the ICAAP is not available for this institution yet. " +
  "Nothing has gone wrong — the risk and capital data has not been enabled here.";

/**
 * The panel itself. Plain elements on purpose: it is the screen a reader sees
 * when the rest of the module could not load, so it must not depend on
 * anything that could be the reason it could not load.
 */
export default function P2Unavailable({
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
