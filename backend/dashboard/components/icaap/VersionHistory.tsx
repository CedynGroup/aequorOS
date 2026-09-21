"use client";

/**
 * The section's committed versions.
 *
 * Versions are immutable: there is no edit and no delete here, because there
 * is none in the API. Each row is what someone committed, when, and the digest
 * of exactly that text — the evidence a later reviewer compares against.
 */

import { History } from "lucide-react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapSectionVersions } from "@/lib/api/icaap";
import { fmtTimestampValue, shortHash } from "./format";

export default function VersionHistory({
  bankId,
  cycleId,
  sectionKey,
}: {
  bankId: string;
  cycleId: string;
  sectionKey: string;
}) {
  const versionsQuery = useIcaapSectionVersions(bankId, cycleId, sectionKey);
  return (
    <section className="card overflow-hidden">
      <div className="border-b border-border-light px-4 py-3">
        <h3 className="inline-flex items-center gap-2 text-h3 text-navy">
          <History size={14} aria-hidden />
          Version history
        </h3>
      </div>
      <QueryBoundary
        isLoading={versionsQuery.isLoading}
        error={versionsQuery.error}
        onRetry={() => void versionsQuery.refetch()}
        contained
        skeleton={<div className="h-24 animate-pulse bg-surface" />}
      >
        {(versionsQuery.data?.versions.length ?? 0) === 0 ? (
          <p className="px-4 py-3 text-body text-slate">
            Nothing committed yet. Committing takes a snapshot of the section as
            it stands, so a reviewer reads a fixed text rather than a moving one.
          </p>
        ) : (
          <ul className="max-h-64 overflow-y-auto">
            {versionsQuery.data?.versions.map((version) => (
              <li
                key={version.id}
                className="border-b border-border-light px-4 py-2.5 last:border-0"
              >
                <p className="text-body text-navy">
                  Version {version.versionNo}
                  {version.commitNote ? ` — ${version.commitNote}` : ""}
                </p>
                <p className="mt-0.5 text-caption text-slate">
                  {version.committedBy ?? "Unknown author"} &middot;{" "}
                  {fmtTimestampValue(version.createdAt)} &middot;{" "}
                  <span className="font-mono">{shortHash(version.docSha256)}</span>
                </p>
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
    </section>
  );
}
