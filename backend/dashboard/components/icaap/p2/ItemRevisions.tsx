"use client";

/**
 * The history of one Pillar 2 item: every revision, in order, with what changed
 * and what the figure rested on at the time.
 *
 * Revisions are UNALTERABLE on the server, which is what makes this panel worth
 * showing: it is the evidence that an approved amount is the amount that was
 * approved. An approval attaches to a REVISION NUMBER, not to the item, so a
 * reviewer asking "was this figure the one the Board saw?" answers it here.
 *
 * Each revision carries two opaque records the engine wrote — the computation's
 * working and the snapshot of the row — shown as the engine wrote them. This
 * panel does not interpret either: renaming an engine's key in a history panel
 * would make the audit trail disagree with itself.
 */

import { useState } from "react";
import SectionCard from "@/components/ui/SectionCard";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapPillar2ItemRevisions } from "@/lib/api/icaapRiskCapital";
import type { IcaapPillar2Revision } from "@/lib/api/icaapRiskCapital";
import { fmtTimestampValue } from "../format";
import P2Unavailable, { p2UnavailableNotice } from "./availability";
import { DIGEST_CHARS } from "./display";
import { NOT_AVAILABLE, fmtCount, revisionKindLabel } from "./labels";

export default function ItemRevisions({
  bankId,
  cycleId,
  itemId,
  approvedRevisionNo,
}: {
  bankId: string;
  cycleId: string;
  itemId: string | null;
  /** The revision an approval is attached to, when there is one. */
  approvedRevisionNo?: number | null;
}) {
  const query = useIcaapPillar2ItemRevisions(bankId, cycleId, itemId ?? null);
  const history = query.data;

  const unavailable = p2UnavailableNotice(query.error);
  if (unavailable) {
    return <P2Unavailable title="Revision history" message={unavailable} />;
  }

  const revisions = history?.revisions ?? [];
  const ordered = [...revisions].sort(
    (left, right) => right.revisionNo - left.revisionNo,
  );

  return (
    <QueryBoundary
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
      contained
    >
      <SectionCard
        title="Revision history"
        subtitle="Newest first. Revisions cannot be edited or removed."
        noPadding
      >
        {ordered.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="No revisions recorded yet"
              description="A revision is written each time the item is added, edited, computed or retired."
            />
          </div>
        ) : (
          <ul className="divide-y divide-border-light">
            {ordered.map((revision) => (
              <RevisionRow
                key={revision.revisionId}
                revision={revision}
                approved={
                  approvedRevisionNo !== null &&
                  approvedRevisionNo !== undefined &&
                  approvedRevisionNo === revision.revisionNo
                }
              />
            ))}
          </ul>
        )}
      </SectionCard>
    </QueryBoundary>
  );
}

function RevisionRow({
  revision,
  approved,
}: {
  revision: IcaapPillar2Revision;
  approved: boolean;
}) {
  const [open, setOpen] = useState(false);
  const detail = [
    ...(revision.computation ?? []).map((entry) => ({
      ...entry,
      group: "What the engine recorded",
    })),
    ...(revision.snapshot ?? []).map((entry) => ({
      ...entry,
      group: "The item as it stood",
    })),
  ];

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-navy">
          Revision {fmtCount(revision.revisionNo)}
        </span>
        <StatusPill tone="slate">
          {revisionKindLabel(revision.changeKind)}
        </StatusPill>
        {approved && <StatusPill tone="success">Approved revision</StatusPill>}
        <span className="text-caption text-slate">
          {fmtTimestampValue(revision.createdAt, NOT_AVAILABLE)}
        </span>
      </div>

      {revision.note && (
        <p className="mt-1 text-body text-navy/80">{revision.note}</p>
      )}

      <p className="mt-1 text-caption text-slate">
        Round {fmtCount(revision.round)}
        {revision.inputsDigest &&
          ` · inputs ${revision.inputsDigest.slice(0, DIGEST_CHARS)}`}
        {revision.snapshotSha256 &&
          ` · record ${revision.snapshotSha256.slice(0, DIGEST_CHARS)}`}
      </p>

      {detail.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpen((previous) => !previous)}
            className="mt-1 text-caption text-action underline"
          >
            {open ? "Hide what was recorded" : "Show what was recorded"}
          </button>
          {open && (
            <table className="mt-2 w-full text-caption">
              <tbody>
                {detail.map((entry) => (
                  <tr
                    key={`${entry.group}-${entry.label}`}
                    className="border-b border-border-light/60"
                  >
                    <th
                      scope="row"
                      className="py-1 pr-3 text-left font-normal text-slate"
                    >
                      {entry.label}
                    </th>
                    <td className="py-1 text-navy">
                      {entry.value ?? NOT_AVAILABLE}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </li>
  );
}
