"use client";

/**
 * Whether a BI figure reconciles to what the platform already files.
 *
 * FOUR states, and the fourth is the point. Green, amber and red are verdicts a
 * reconciliation check reached. GREY IS "NOT ASSESSED" — no check has run for
 * this institution and date, or the run could not reach one. It is not a pass,
 * it must never be drawn like a pass, and it is what every figure carries until
 * the checks have actually run. A badge that reads green over a check that did
 * not run is the one failure mode this component exists to prevent.
 *
 * Nothing here computes a status. The verdict arrives on the payload
 * (`BiTrustBadge` / `BiTrustRead`), and an unrecognised value degrades to grey
 * rather than to the most flattering neighbour.
 *
 * Two components. `TrustBadge` draws a verdict it is GIVEN — a statement's, a
 * check's, a widget's. `ReconciliationTrustBadge` fetches the institution's
 * verdict for one reporting date and is what a module chart mounts in its
 * `ChartFrame` `trust` slot (`docs/bi.md` §Insights layer).
 */

import {
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
} from "lucide-react";
import { useBiAvailability, useBiTrust } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";
/**
 * The four states, as the generated client names them. Declared here rather
 * than imported because the OpenAPI generator emits one enum per schema
 * (`BiTrustBadgeStatusEnum`, `BiTrustReadStatusEnum`, …) for what is one
 * vocabulary on the wire; every one of them is assignable to this.
 */
export type BiTrustStatus = "green" | "amber" | "red" | "grey";

type Presentation = {
  label: string;
  description: string;
  Icon: typeof ShieldCheck;
  className: string;
};

const PRESENTATION: Record<BiTrustStatus, Presentation> = {
  green: {
    label: "Reconciled",
    description:
      "These figures match the position the platform computed for this date.",
    Icon: ShieldCheck,
    className: "bg-success-light text-success border-success/20",
  },
  amber: {
    label: "Differences found",
    description:
      "These figures differ from the computed position by more than the tolerance for at least one check.",
    Icon: ShieldAlert,
    className: "bg-warning-light text-warning border-warning/20",
  },
  red: {
    label: "Does not reconcile",
    description:
      "At least one check failed. Treat these figures as indicative until the difference is resolved.",
    Icon: ShieldX,
    className: "bg-critical-light text-critical border-critical/20",
  },
  grey: {
    label: "Not assessed",
    description:
      "No reconciliation check has been completed for this date, so these figures have not been compared with the computed position.",
    Icon: ShieldQuestion,
    className: "bg-surface text-slate border-border",
  },
};

/** Never widen an unknown value into a pass. */
export function trustPresentation(status: string | null | undefined) {
  if (status === "green" || status === "amber" || status === "red") {
    return PRESENTATION[status];
  }
  return PRESENTATION.grey;
}

/**
 * WHAT EACH RECONCILIATION CHECK MEANS, in the platform's own words.
 *
 * A badge names the checks that did not pass on hover. `failingChecks` arrives
 * as check IDS (`BiTrustBadge.failing_checks`), and an id is a wire token:
 * "Checks outstanding: R11, R12" told a treasurer nothing (audit A360-6). The
 * sentences below are the server's own `CHECK_LABELS`
 * (`app/features/read_bi.py`) — the same words `GET …/bi/trust` puts on each
 * check's `label`, so the hover on a chart and the list on the Insights hub
 * cannot describe one check two ways. What each check MEASURES is documented
 * on its function in `app/services/bi/reconciliation.py`; R11 and R12 are the
 * Phase 5 additions, and `landingSurfaces.test.ts` pins this map against the
 * server's so a list of ten cannot go stale here silently.
 *
 * An id this client cannot name is COUNTED, never printed: a raw token is the
 * defect, and inventing a sentence for a check nobody described is worse.
 */
export const RECONCILIATION_CHECK_LABELS: Readonly<Record<string, string>> = {
  R1: "Non-performing loans agree with the credit engine",
  R2: "Loan balances agree with the balance sheet",
  R3: "Deposit balances agree with the balance sheet",
  R4: "Income-statement lines agree with the regulatory return",
  R5: "Every position in the book reached the analytics tables",
  R6: "Every balance is stated in the reporting currency",
  R7: "Every balance is attributed to a known branch",
  R8: "The analytics tables are as current as the live figures",
  R9: "The balance sheet balances",
  R10: "Arrears ageing is complete",
  R11: "The branch breakdown adds up to the ledger",
  R12: "Stated arrears cover the whole loan book",
};

/**
 * The hover sentence for the checks that did not pass, or `""` when none did.
 * Production copy only — see `RECONCILIATION_CHECK_LABELS`.
 */
export function outstandingChecksSentence(
  failingChecks: readonly string[],
): string {
  if (failingChecks.length === 0) return "";
  const named: string[] = [];
  let unnamed = 0;
  for (const id of failingChecks) {
    const label = RECONCILIATION_CHECK_LABELS[id];
    if (label) named.push(label);
    else unnamed += 1;
  }
  const parts: string[] = [];
  if (named.length > 0) parts.push(named.join("; "));
  if (unnamed > 0) {
    parts.push(
      unnamed === 1
        ? "one further check this client cannot name"
        : `${unnamed} further checks this client cannot name`,
    );
  }
  return ` Checks not passed: ${parts.join("; ")}.`;
}

export default function TrustBadge({
  status,
  failingChecks = [],
  size = "default",
  className = "",
}: {
  status: BiTrustStatus | string | null | undefined;
  /** Check ids that did not pass, for the hover detail. Never figures. */
  failingChecks?: readonly string[];
  size?: "default" | "compact";
  className?: string;
}) {
  const presentation = trustPresentation(status);
  const { Icon } = presentation;
  const failing = outstandingChecksSentence(failingChecks);

  return (
    <span
      title={`${presentation.description}${failing}`}
      className={`inline-flex items-center gap-1.5 rounded border font-medium uppercase tracking-wider ${
        size === "compact"
          ? "px-1.5 py-0.5 text-micro"
          : "px-2 py-0.5 text-caption"
      } ${presentation.className} ${className}`}
    >
      <Icon size={size === "compact" ? 11 : 12} aria-hidden />
      {presentation.label}
    </span>
  );
}

/**
 * THE INSTITUTION'S RECONCILIATION VERDICT FOR ONE REPORTING DATE, on a module
 * chart. `GET …/bi/trust` returns every check for (institution, date) with a
 * roll-up status; this draws that status and names the checks that did not
 * pass. It computes nothing.
 *
 * It renders NOTHING — not grey, not green — in every state where there is no
 * verdict to draw, and the four are deliberately indistinguishable to the
 * reader, because in each of them the truth is the same: no verdict is
 * available to this person on this page.
 *
 *  * BI is off or not yet known to be on (`biEnabled !== true`): the surface is
 *    not there. `undefined` is treated as off so nothing flashes.
 *  * No institution or no reporting date yet: nothing to ask about.
 *  * The request has not answered: a placeholder verdict would be a verdict.
 *  * The route refused (403), answered 404 (the flag is off, or a sibling
 *    tenant), or failed: a refusal cannot be carried in a badge's width
 *    without either naming what was hidden or drawing "Not assessed" over a
 *    check that WAS assessed and withheld — so the slot stays empty. The strip
 *    above the chart is the surface that renders a refusal as a refusal.
 *
 * A grey verdict from the server IS drawn: "Not assessed" is the platform's own
 * statement that no check has run for the date, and hiding it would read as
 * "nothing to say", which is a different claim.
 */
export function ReconciliationTrustBadge({
  bankId,
  asOf,
  size = "compact",
}: {
  bankId: string | undefined;
  /** The page's reporting date, ISO day or `Date`. */
  asOf: Date | string | null | undefined;
  size?: "default" | "compact";
}) {
  const availability = useBiAvailability();
  const enabled = availability.biEnabled === true;
  const day = isoDay(asOf);
  const trust = useBiTrust(bankId, day, enabled);

  if (!enabled || !bankId || day === null) return null;
  if (trust.isPending || trust.error || !trust.data) return null;

  const outstanding = trust.data.checks
    .filter((check) => check.status !== "green")
    .map((check) => check.checkId);

  return (
    <TrustBadge
      status={trust.data.status}
      failingChecks={outstanding}
      size={size}
    />
  );
}
