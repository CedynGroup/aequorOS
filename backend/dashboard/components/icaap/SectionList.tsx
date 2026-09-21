"use client";

/**
 * The framework's sections, in the regulator's own order.
 *
 * The letter and title are the regulator's printed headings, so they are shown
 * as published rather than reworded. A section whose primary text has not been
 * read yet is flagged: its checklist is incomplete by construction, and a
 * non-rehearsal cycle cannot be frozen while any section is in that state.
 */

import Link from "next/link";
import { ChevronRight, FileWarning } from "lucide-react";
import { regShort } from "@/lib/format";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapCycle } from "@/lib/api/icaap";
import { fmtTimestampValue, sourceStatusNote } from "./format";

export default function SectionList({
  bankId,
  cycleId,
}: {
  bankId: string;
  cycleId: string;
}) {
  const cycleQuery = useIcaapCycle(bankId, cycleId);
  const sections = [...(cycleQuery.data?.sections ?? [])].sort(
    (a, b) => a.order - b.order,
  );

  return (
    <QueryBoundary
      isLoading={cycleQuery.isLoading}
      error={cycleQuery.error}
      onRetry={() => void cycleQuery.refetch()}
      contained
    >
      <div className="card overflow-hidden">
        <ul>
          {sections.map((section) => {
            const pending = sourceStatusNote(section.sourceStatus, regShort());
            const open = section.requirementCounts.open ?? 0;
            return (
              <li key={section.key}>
                <Link
                  href={`/icaap/${cycleId}/sections/${section.key}`}
                  className="flex items-start gap-3 border-b border-border-light px-5 py-3 last:border-0 hover:bg-surface/60"
                >
                  <span className="mt-0.5 w-6 shrink-0 text-caption font-medium uppercase text-slate">
                    ({section.letter})
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-body font-medium text-navy">
                      {section.title}
                    </span>
                    <span className="mt-0.5 block text-caption text-slate">
                      {section.committedVersionNo
                        ? `Version ${section.committedVersionNo}`
                        : "Not committed"}
                      {section.hasUncommittedChanges
                        ? " · uncommitted changes"
                        : ""}
                      {open > 0 ? ` · ${open} requirements open` : ""}
                      {section.workingUpdatedAt
                        ? ` · edited ${fmtTimestampValue(section.workingUpdatedAt)}`
                        : ""}
                    </span>
                    {pending && (
                      <span className="mt-1 inline-flex items-center gap-1 text-caption text-warning">
                        <FileWarning size={11} aria-hidden />
                        {pending}
                      </span>
                    )}
                  </span>
                  <ChevronRight
                    size={14}
                    className="mt-1 shrink-0 text-slate-light"
                    aria-hidden
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </QueryBoundary>
  );
}
