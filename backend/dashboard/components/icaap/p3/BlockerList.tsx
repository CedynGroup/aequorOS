"use client";

/**
 * The reasons a report cannot yet be sealed or filed, as a list of things to
 * do.
 *
 * ONE RULE: the server's own `message` is rendered verbatim and is never
 * replaced. The freeze preflight and the submission gate both compute their
 * refusals from the same authority the act itself uses, so a sentence written
 * here instead would be a second opinion about whether a bank may file — which
 * is exactly the disagreement `services/icaap/filing.py` exists to prevent.
 *
 * `blockerTitle` adds a SHORT HEADING over that sentence where the code is one
 * we recognise, so a list of eleven refusals reads as a list of eleven tasks.
 * A code with no heading shows the sentence alone; nothing is invented.
 */

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import StatusPill from "@/components/ui/StatusPill";
import { ICON_SM } from "@/components/icaap/p2/display";
import type { IcaapPreflightItem } from "@/lib/api/icaapFiling";
import { blockerTitle, severityCopy } from "./labels";

const SCOPE_COPY: Record<string, string> = {
  cycle: "This assessment",
  section: "A section",
  requirement: "A checklist item",
  block: "A set of figures",
  attachment: "A document",
};

export default function BlockerList({
  items,
  emptyTitle,
  emptyDescription,
}: {
  items: readonly IcaapPreflightItem[];
  emptyTitle: string;
  emptyDescription: string;
}) {
  const rows = items ?? [];
  if (rows.length === 0) {
    return (
      <div className="flex items-start gap-2 rounded border border-success/25 bg-success-light/50 px-3 py-2.5">
        <CheckCircle2
          size={ICON_SM}
          className="mt-0.5 shrink-0 text-success"
          aria-hidden
        />
        <div>
          <p className="text-body font-medium text-navy">{emptyTitle}</p>
          <p className="text-caption text-navy/80">{emptyDescription}</p>
        </div>
      </div>
    );
  }

  return (
    <ul className="space-y-2" data-testid="blocker-list">
      {rows.map((item, index) => {
        const tone = severityCopy(item.severity);
        const title = blockerTitle(item.code);
        return (
          <li
            key={`${item.code}-${item.ref ?? ""}-${index}`}
            className="rounded border border-border-light p-3"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="flex items-start gap-2 text-body font-medium text-navy">
                {item.severity === "info" ? (
                  <Info size={ICON_SM} className="mt-0.5 shrink-0" aria-hidden />
                ) : (
                  <AlertTriangle
                    size={ICON_SM}
                    className="mt-0.5 shrink-0"
                    aria-hidden
                  />
                )}
                {title ?? SCOPE_COPY[item.scope] ?? SCOPE_COPY.cycle}
              </p>
              <StatusPill tone={tone.tone}>{tone.label}</StatusPill>
            </div>
            <p className="mt-1 text-body text-navy/80">{item.message}</p>
            {item.ref && (
              <p className="mt-1 text-caption text-slate">
                {SCOPE_COPY[item.scope] ?? SCOPE_COPY.cycle}: {item.ref}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
