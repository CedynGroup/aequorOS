"use client";

/**
 * The institution's ICAAP cycles.
 *
 * One row per cycle, with the deadline the server computed and the counts the
 * readiness engine produced. Nothing here is derived locally.
 */

import { useState } from "react";
import Link from "next/link";
import { ClipboardCheck, FlaskConical, Plus } from "lucide-react";
import { regShort } from "@/lib/format";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import { useIcaapCycles, useIcaapFrameworks } from "@/lib/api/icaap";
import { useModuleScope } from "@/components/shell/BankContext";
import CreateCycleDialog from "./CreateCycleDialog";
import { PrimaryButton } from "./Dialog";
import {
  basisLabel,
  cycleKindLabel,
  cycleStatusLabel,
  fmtDateValue,
} from "./format";

function statusTone(status: string) {
  switch (status) {
    case "submitted":
    case "acknowledged":
    case "board_approved":
      return "compliant" as const;
    case "returned":
      return "breach" as const;
    case "frozen":
    case "in_review":
      return "approaching" as const;
    default:
      return "pending" as const;
  }
}

export default function CycleList({
  bankId,
  onCreated,
}: {
  bankId: string;
  onCreated: (cycleId: string) => void;
}) {
  const scope = useModuleScope();
  const cyclesQuery = useIcaapCycles(bankId);
  const frameworksQuery = useIcaapFrameworks(bankId);
  const [creating, setCreating] = useState(false);
  const canCreate = scope.capitalCreate === true;

  return (
    <QueryBoundary
      isLoading={cyclesQuery.isLoading}
      error={cyclesQuery.error}
      onRetry={() => void cyclesQuery.refetch()}
      contained
    >
      <div className="space-y-4">
        <div className="flex items-center justify-end">
          <PrimaryButton
            onClick={() => setCreating(true)}
            disabled={!canCreate || frameworksQuery.isLoading}
          >
            <Plus size={14} aria-hidden />
            New cycle
          </PrimaryButton>
        </div>
        {!canCreate && (
          <p className="text-caption text-slate">
            Creating a cycle requires Basel Capital &middot; Confidential &middot;
            Create. Ask your organization owner or admin to grant it.
          </p>
        )}

        {(cyclesQuery.data?.cycles.length ?? 0) === 0 ? (
          <EmptyState
            Icon={ClipboardCheck}
            title="No ICAAP cycle yet"
            description={`Start a rehearsal to walk the whole assessment end to end without it counting as a filing, or start the annual cycle ${regShort()} expects for the financial year.`}
          />
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-body">
              <thead>
                <tr className="border-b border-border-light text-left text-caption uppercase tracking-wider text-slate">
                  <th className="px-4 py-2.5 font-medium">Cycle</th>
                  <th className="px-4 py-2.5 font-medium">Year</th>
                  <th className="px-4 py-2.5 font-medium">Kind</th>
                  <th className="px-4 py-2.5 font-medium">Basis</th>
                  <th className="px-4 py-2.5 font-medium">Reporting date</th>
                  <th className="px-4 py-2.5 font-medium">Due</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {cyclesQuery.data?.cycles.map((cycle) => (
                  <tr
                    key={cycle.id}
                    className="border-b border-border-light last:border-0 hover:bg-surface/60"
                  >
                    <td className="px-4 py-2.5">
                      <Link
                        href={`/icaap/${cycle.id}/overview`}
                        className="font-medium text-action hover:underline"
                      >
                        {cycle.title}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 tnum">{cycle.fiscalYear}</td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center gap-1.5">
                        {cycle.cycleKind === "rehearsal" && (
                          <FlaskConical
                            size={12}
                            className="text-slate"
                            aria-hidden
                          />
                        )}
                        {cycleKindLabel(cycle.cycleKind)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">{basisLabel(cycle.basis)}</td>
                    <td className="px-4 py-2.5 tnum">
                      {fmtDateValue(cycle.asOfDate)}
                    </td>
                    <td className="px-4 py-2.5 tnum">
                      {cycle.dueDate
                        ? fmtDateValue(cycle.dueDate)
                        : "No fixed date"}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusPill tone={statusTone(cycle.status)}>
                        {cycleStatusLabel(cycle.status)}
                      </StatusPill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {creating && (
        <CreateCycleDialog
          bankId={bankId}
          frameworks={frameworksQuery.data?.frameworks ?? []}
          onClose={() => setCreating(false)}
          onCreated={(cycleId) => {
            setCreating(false);
            onCreated(cycleId);
          }}
        />
      )}
    </QueryBoundary>
  );
}
