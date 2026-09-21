"use client";

/**
 * The cycle's page header: what this cycle IS, in one line, on every tab.
 *
 * Every value is the server's — the reporting date, the deadline, the
 * framework version and the status. Nothing is recomputed here.
 */

import PageHeader from "@/components/ui/PageHeader";
import StatusPill from "@/components/ui/StatusPill";
import type { IcaapCycleRead, IcaapDeadlineStatusRead } from "@/lib/api/icaap";
import DeadlineBadge from "./DeadlineBadge";
import DraftExportMenu from "./DraftExportMenu";
import {
  basisLabel,
  cycleKindLabel,
  cycleStatusLabel,
  fmtDateValue,
} from "./format";

export default function CycleHeader({
  bankId,
  cycle,
  deadline,
  breadcrumbs,
}: {
  bankId: string;
  cycle: IcaapCycleRead;
  deadline?: IcaapDeadlineStatusRead;
  breadcrumbs?: { label: string; href?: string }[];
}) {
  return (
    <PageHeader
      breadcrumbs={breadcrumbs}
      eyebrow="ICAAP"
      title={cycle.title}
      subtitle={
        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>FY {cycle.fiscalYear}</span>
          <span aria-hidden>&middot;</span>
          <span>as at {fmtDateValue(cycle.asOfDate)}</span>
          <span aria-hidden>&middot;</span>
          <span>{basisLabel(cycle.basis)}</span>
          <span aria-hidden>&middot;</span>
          <span>{cycleKindLabel(cycle.cycleKind)}</span>
          <span aria-hidden>&middot;</span>
          <span>
            {cycle.framework.shortTitle} {cycle.framework.version}
          </span>
          <StatusPill
            tone={cycle.editable ? "pending" : "approaching"}
            className="ml-1"
          >
            {cycleStatusLabel(cycle.status)}
          </StatusPill>
          {deadline && <DeadlineBadge deadline={deadline} />}
        </span>
      }
      action={<DraftExportMenu bankId={bankId} cycleId={cycle.id} />}
    />
  );
}
