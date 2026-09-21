"use client";

/**
 * What still stands between this cycle and a freeze.
 *
 * Every item, its severity and its wording come from the readiness engine
 * (`GET .../readiness`). The dashboard groups them and links to where the work
 * is; it does not decide what blocks, and it does not summarise "ready" from
 * anything other than the server's own `readyForFreeze`.
 */

import Link from "next/link";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import SectionCard from "@/components/ui/SectionCard";
import { regShort } from "@/lib/format";
import {
  useIcaapCycle,
  useIcaapReadiness,
  type IcaapReadinessItemRead,
  type IcaapSeverity,
} from "@/lib/api/icaap";
import DeadlineBadge from "./DeadlineBadge";
import {
  ExposureDraftBanner,
  FrameworkDigestNotice,
  RehearsalBanner,
} from "./Notices";

const SEVERITY_ORDER: IcaapSeverity[] = [
  "blocking",
  "warning",
  "info",
];

const SEVERITY_COPY: Record<
  IcaapSeverity,
  { title: string; description: string; icon: typeof AlertOctagon; tone: string }
> = {
  blocking: {
    title: "Must be resolved",
    description: "The cycle cannot be frozen while any of these is open.",
    icon: AlertOctagon,
    tone: "text-critical",
  },
  warning: {
    title: "Worth attention",
    description: "None of these stops a freeze, but each one is a judgement a reviewer will ask about.",
    icon: AlertTriangle,
    tone: "text-warning",
  },
  info: {
    title: "For information",
    description: "Context about this cycle.",
    icon: Info,
    tone: "text-slate",
  },
};

/** Where an item's `ref` points, when the dashboard can resolve it to a page. */
function itemHref(
  cycleId: string,
  item: IcaapReadinessItemRead,
): string | null {
  if (!item.ref) return null;
  if (item.scope === "section" || item.scope === "requirement") {
    return `/icaap/${cycleId}/sections/${item.ref.split(":")[0]}`;
  }
  if (item.scope === "attachment") return `/icaap/${cycleId}/attachments`;
  if (item.scope === "block") return `/icaap/${cycleId}/sections`;
  return null;
}

export default function OverviewReadiness({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const cycleQuery = useIcaapCycle(bankId, cycleId);
  const readinessQuery = useIcaapReadiness(bankId, cycleId);
  const cycle = cycleQuery.data;
  const readiness = readinessQuery.data;

  return (
    <QueryBoundary
      isLoading={cycleQuery.isLoading || readinessQuery.isLoading}
      error={cycleQuery.error ?? readinessQuery.error}
      onRetry={() => {
        void cycleQuery.refetch();
        void readinessQuery.refetch();
      }}
      contained
    >
      {cycle && readiness && (
        <div className="space-y-4">
          {cycle.cycleKind === "rehearsal" && <RehearsalBanner />}
          <ExposureDraftBanner framework={cycle.framework} />
          {!cycle.frameworkDigestMatches && <FrameworkDigestNotice />}

          <SectionCard
            title={
              readiness.readyForFreeze
                ? "Ready to freeze"
                : "Not ready to freeze"
            }
            subtitle={
              readiness.readyForFreeze
                ? "Every blocking item is resolved."
                : "The blocking items below are what remain."
            }
            actions={<DeadlineBadge deadline={readiness.deadline} />}
          >
            <div className="flex items-start gap-3">
              {readiness.readyForFreeze ? (
                <CheckCircle2
                  size={18}
                  className="mt-0.5 shrink-0 text-success"
                  aria-hidden
                />
              ) : (
                <AlertOctagon
                  size={18}
                  className="mt-0.5 shrink-0 text-critical"
                  aria-hidden
                />
              )}
              <div className="min-w-0 text-body text-navy/80">
                <p>
                  {readiness.items.filter((i) => i.severity === "blocking").length}{" "}
                  blocking,{" "}
                  {readiness.items.filter((i) => i.severity === "warning").length}{" "}
                  to review.
                </p>
                {readiness.pendingPrimaryTextSections.length > 0 && (
                  <p className="mt-1">
                    {readiness.pendingPrimaryTextSections.length} section
                    {readiness.pendingPrimaryTextSections.length === 1
                      ? " has"
                      : "s have"}{" "}
                    an incomplete checklist &mdash; pending {regShort()} text.
                  </p>
                )}
              </div>
            </div>
          </SectionCard>

          {SEVERITY_ORDER.map((severity) => {
            const items = readiness.items.filter(
              (item) => item.severity === severity,
            );
            if (items.length === 0) return null;
            const meta = SEVERITY_COPY[severity];
            const Icon = meta.icon;
            return (
              <SectionCard
                key={severity}
                title={meta.title}
                subtitle={meta.description}
                noPadding
              >
                <ul>
                  {items.map((item, index) => {
                    const href = itemHref(cycleId, item);
                    return (
                      <li
                        key={`${item.code}-${item.ref ?? index}`}
                        className="flex items-start gap-3 border-b border-border-light px-5 py-3 last:border-0"
                      >
                        <Icon
                          size={15}
                          className={`mt-0.5 shrink-0 ${meta.tone}`}
                          aria-hidden
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-body text-navy">{item.message}</p>
                        </div>
                        {href && (
                          <Link
                            href={href}
                            className="shrink-0 text-caption font-medium text-action hover:underline"
                          >
                            Open
                          </Link>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </SectionCard>
            );
          })}
        </div>
      )}
    </QueryBoundary>
  );
}
