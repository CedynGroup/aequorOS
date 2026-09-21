"use client";

/**
 * The standing notices of the ICAAP workspace.
 *
 * Each one exists because a reader could otherwise mistake what they are
 * looking at for something it is not: a draft framework for settled law, a
 * rehearsal for a filing, an empty panel for a clean bill of health.
 */

import type { ReactNode } from "react";
import { AlertTriangle, FlaskConical, Info, Lock, Database } from "lucide-react";
import { regShort } from "@/lib/format";
import { REHEARSAL_NOTICE } from "./format";
import type { IcaapFrameworkSummaryRead } from "@/lib/api/icaap";

function Notice({
  tone,
  icon,
  title,
  children,
}: {
  tone: "info" | "warn" | "crit";
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  const border =
    tone === "crit"
      ? "border-l-critical bg-critical-light/40"
      : tone === "warn"
        ? "border-l-warning bg-warning-light/40"
        : "border-l-action bg-action-light/30";
  return (
    <div className={`card border-l-4 ${border} flex items-start gap-3 p-4`}>
      <span className="mt-0.5 shrink-0 text-slate" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-body font-medium text-navy">{title}</p>
        {children && (
          <div className="mt-1 text-body leading-relaxed text-navy/80">
            {children}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The GH framework is an exposure draft: the regulator may still change the
 * text this checklist is built from. Saying so is the difference between a
 * working paper and a claim about the law.
 */
export function ExposureDraftBanner({
  framework,
}: {
  framework: IcaapFrameworkSummaryRead;
}) {
  if (framework.status !== "exposure_draft") return null;
  return (
    <Notice
      tone="warn"
      icon={<AlertTriangle size={16} />}
      title="This framework is an exposure draft"
    >
      <p>
        {regShort()} may change the text before it is final. {framework.title} (
        {framework.version}) is used here as published; requirements and
        headings can still move.
      </p>
    </Notice>
  );
}

/** D-029. A rehearsal runs the full lifecycle, so it is labelled everywhere. */
export function RehearsalBanner() {
  return (
    <Notice
      tone="info"
      icon={<FlaskConical size={16} />}
      title="Rehearsal cycle"
    >
      <p>{REHEARSAL_NOTICE}</p>
    </Notice>
  );
}

/**
 * A panel the signed-in user is not entitled to see or act on. It states what
 * is missing and who grants it — never an empty space that reads as "nothing
 * to report".
 */
export function RestrictedWidget({
  title = "You do not have access to this",
  requirement,
}: {
  title?: string;
  requirement: string;
}) {
  return (
    <Notice tone="info" icon={<Lock size={16} />} title={title}>
      <p>
        Requires {requirement}. Ask your organization owner or admin to grant
        it.
      </p>
    </Notice>
  );
}

/**
 * The module has nothing computed for this date yet. A valid empty state —
 * deliberately NOT an error, and deliberately not a zero.
 */
export function NeedsDataWidget({
  title = "Waiting for data",
  reason,
  children,
}: {
  title?: string;
  reason: string;
  children?: ReactNode;
}) {
  return (
    <Notice tone="info" icon={<Database size={16} />} title={title}>
      <p>{reason}</p>
      {children}
    </Notice>
  );
}

/** A framework digest that no longer matches the cycle's stored copy. */
export function FrameworkDigestNotice() {
  return (
    <Notice
      tone="crit"
      icon={<Info size={16} />}
      title="The framework has changed since this cycle was created"
    >
      <p>
        The checklist this cycle was built from no longer matches the framework
        on file. Move the cycle to the current framework version before it is
        frozen, so the requirements and the evidence agree.
      </p>
    </Notice>
  );
}
