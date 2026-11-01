"use client";

/**
 * Operational feed — recent ingestion batches and data activations merged
 * into one compact timeline with status dots, newest first. Reads the Data
 * Engine hooks only; every row links back to the Data Engine console.
 */

import Link from "next/link";
import { ArrowRight, DatabaseZap } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import DataAccessNotice from "@/components/ui/DataAccessNotice";
import { ErrorPanel } from "@/components/ui/QueryBoundary";
import { useDataActivations, useIngestionBatches } from "@/lib/api/ingestion";
import { fmtRelative, labelize } from "@/lib/api/values";

type FeedEvent = {
  key: string;
  at: Date;
  dotClass: string;
  title: string;
  meta: string;
};

const FEED_LIMIT = 8;

function batchDotClass(status: string): string {
  switch (status) {
    case "accepted":
      return "bg-success";
    case "accepted_with_warnings":
      return "bg-warning";
    case "rejected":
    case "failed":
      return "bg-critical";
    default:
      return "bg-slate-light";
  }
}

export default function OperationalFeed({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const batches = useIngestionBatches(bankId);
  const activations = useDataActivations(bankId);

  const events: FeedEvent[] = [
    ...(batches.access === "allowed" && batches.isSuccess
      ? batches.data.batches
      : []
    ).map((b): FeedEvent => {
      const counts = [
        `${b.recordsAccepted} accepted`,
        b.recordsWarning > 0 ? `${b.recordsWarning} warnings` : null,
        b.recordsError > 0 ? `${b.recordsError} errors` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return {
        key: `batch-${b.id}`,
        at: b.completedAt ?? b.startedAt ?? b.createdAt,
        dotClass: batchDotClass(b.status),
        title: `${labelize(b.sourceSystem.toLowerCase())} ingestion · ${labelize(b.status)}`,
        meta: counts,
      };
    }),
    ...(activations.access === "allowed" && activations.isSuccess
      ? activations.data.activations
      : []
    ).map((a, idx): FeedEvent => {
      const parts = [
        a.periodLabel ? `period ${a.periodLabel}` : null,
        typeof a.factsCreated === "number" ? `${a.factsCreated} facts` : null,
        typeof a.modulesSucceeded === "number"
          ? `${a.modulesSucceeded} modules ok`
          : null,
        typeof a.modulesFailed === "number" && a.modulesFailed > 0
          ? `${a.modulesFailed} failed`
          : null,
      ]
        .filter(Boolean)
        .join(" · ");
      const failed = typeof a.modulesFailed === "number" && a.modulesFailed > 0;
      return {
        key: `activation-${a.activatedAt.getTime()}-${idx}`,
        at: a.activatedAt,
        dotClass: failed ? "bg-warning" : "bg-action",
        title: "Data activation",
        meta: parts,
      };
    }),
  ]
    .sort((a, b) => b.at.getTime() - a.at.getTime())
    .slice(0, FEED_LIMIT);

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          <DatabaseZap size={15} className="text-action" aria-hidden />
          Operational feed
        </span>
      }
      subtitle="Recent ingestion batches and data activations"
      actions={
        <Link
          href="/data-engine"
          className="text-caption font-medium text-action hover:text-action-hover inline-flex items-center gap-1 focus:outline-hidden focus-visible:ring-2 focus-visible:ring-focus rounded-sm"
        >
          Data Engine <ArrowRight size={12} aria-hidden />
        </Link>
      }
      noPadding
      footer={
        <span>
          {batches.access === "allowed" &&
            batches.isSuccess &&
            `${batches.data.batches.length} batches on record. `}
          {activations.access === "allowed" &&
            activations.isSuccess &&
            `${activations.data.activations.length} activations on record.`}
        </span>
      }
    >
      {batches.access === "loading" ||
      (batches.access === "allowed" && batches.isPending) ? (
        <p role="status" className="px-5 py-4 text-body text-slate">
          Loading ingestion batches…
        </p>
      ) : batches.access === "denied" ? (
        <DataAccessNotice requirement="DATA · Confidential · View for ingestion batches" />
      ) : batches.isError ? (
        <ErrorPanel
          error={batches.error}
          title="Ingestion batches unavailable"
          onRetry={() => void batches.refetch()}
        />
      ) : batches.data?.batches.length === 0 ? (
        <p className="px-5 py-4 text-body text-slate">
          No ingestion batches on record.
        </p>
      ) : null}
      {activations.access === "loading" ||
      (activations.access === "allowed" && activations.isPending) ? (
        <p role="status" className="px-5 py-4 text-body text-slate">
          Loading data activations…
        </p>
      ) : activations.access === "denied" ? (
        <DataAccessNotice requirement="DATA · Aggregated · View for data activations" />
      ) : activations.isError ? (
        <ErrorPanel
          error={activations.error}
          title="Data activations unavailable"
          onRetry={() => void activations.refetch()}
        />
      ) : activations.data?.activations.length === 0 ? (
        <p className="px-5 py-4 text-body text-slate">
          No data activations on record.
        </p>
      ) : null}
      {events.length > 0 && (
        <ul className="divide-y divide-border-light">
          {events.map((event) => (
            <li key={event.key} className="px-5 py-2.5 flex items-center gap-3">
              <span
                aria-hidden
                className={`w-2 h-2 rounded-full shrink-0 ${event.dotClass}`}
              />
              <div className="min-w-0 flex-1">
                <p className="text-body text-navy truncate">{event.title}</p>
                {event.meta && (
                  <p className="text-caption text-slate truncate">
                    {event.meta}
                  </p>
                )}
              </div>
              <span className="text-caption text-slate whitespace-nowrap shrink-0 font-mono tnum">
                {fmtRelative(event.at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
