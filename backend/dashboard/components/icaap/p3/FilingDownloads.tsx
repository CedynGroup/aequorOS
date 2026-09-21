"use client";

/**
 * The files a sealed report carries.
 *
 * THREE KINDS, AND THEY ARE NOT INTERCHANGEABLE:
 *
 *  - the SEALED report, as produced when the cycle was frozen;
 *  - the SIGNED revisions — one per officer signature, each covering the exact
 *    bytes that officer signed. The one marked as filed is the document the
 *    regulator receives, and it is the only one that should ever be sent;
 *  - the WORKING COPY, which exists for drafting and internal review and is
 *    deliberately excluded from the filing set. The card says so, because a
 *    Word file that opens and edits looks more like "the report" than a signed
 *    PDF does.
 *
 * Nothing here decides which file is which: `isFiled` is the server's, and an
 * absent flag never reads as filed.
 */

import { useState } from "react";
import { Download, FileText } from "lucide-react";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import StatusPill from "@/components/ui/StatusPill";
import EmptyState from "@/components/ui/EmptyState";
import P2Unavailable, {
  p2UnavailableNotice,
} from "@/components/icaap/p2/availability";
import {
  downloadArtifact,
  downloadArtifactVersion,
  fmtBytes,
} from "@/components/submissions/shared";
import { fmtLocale } from "@/lib/format";
import { ICON_SM } from "@/components/icaap/p2/display";
import { useExportRegulatoryPackage } from "@/lib/api/hooks";
import {
  useIcaapPackageArtifactVersions,
  useIcaapPackageArtifacts,
} from "@/lib/api/icaapFiling";
import { DOWNLOAD_WORKING_COPY_NOTE } from "./labels";

const KIND_LABELS: Record<string, string> = {
  pdf: "The sealed report",
  docx_working: "Working copy (Word)",
  xlsx_working: "Working copy (spreadsheet)",
  xlsx: "Spreadsheet",
  xlsx_official: "Official layout",
  csv: "Data extract",
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? "Report file";
}

function moment(value: string | null): string {
  if (value === null) return "";
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString(fmtLocale())
    : "";
}

export default function FilingDownloads({
  bankId,
  packageId,
  canExport,
}: {
  bankId: string;
  packageId: string;
  canExport: boolean;
}) {
  const artifacts = useIcaapPackageArtifacts(bankId, packageId);
  const versions = useIcaapPackageArtifactVersions(bankId, packageId);
  const exportPackage = useExportRegulatoryPackage(bankId);
  const [failure, setFailure] = useState<string | null>(null);

  const unavailable =
    p2UnavailableNotice(artifacts.error) ?? p2UnavailableNotice(versions.error);
  if (unavailable) {
    return <P2Unavailable title="Report files" message={unavailable} />;
  }

  // `Array.isArray` rather than `?? []`: the resilience suite also renders
  // this against an UNNORMALISED body, where `data` is an object rather than
  // the list the contract declares.
  const files = Array.isArray(artifacts.data) ? artifacts.data : [];
  const revisions = Array.isArray(versions.data) ? versions.data : [];
  const signed = revisions.filter((version) => version.signedByName != null);

  return (
    <QueryBoundary
      isLoading={artifacts.isLoading}
      error={artifacts.error}
      onRetry={() => void artifacts.refetch()}
      contained
    >
      <SectionCard
        title="Report files"
        subtitle="The sealed report, each signed revision of it, and the working copy."
      >
        <div className="space-y-4">
          {failure && (
            <p className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80">
              {failure}
            </p>
          )}

          {files.length === 0 ? (
            <EmptyState
              title="No files yet"
              description="The sealed report is produced when the assessment is frozen."
            />
          ) : (
            <ul className="space-y-2">
              {files.map((artifact) => (
                <li
                  key={artifact.id}
                  className="flex flex-wrap items-start justify-between gap-2 rounded border border-border-light p-3"
                >
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium text-navy">
                      <FileText size={ICON_SM} aria-hidden />
                      {kindLabel(artifact.kind)}
                    </p>
                    <p className="text-caption text-slate">
                      {artifact.sizeBytes !== null
                        ? `${fmtBytes(artifact.sizeBytes)} · `
                        : ""}
                      {moment(artifact.createdAt)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface hover:text-navy"
                    onClick={() => {
                      setFailure(null);
                      void downloadArtifact(bankId, artifact).catch(
                        (error: Error) => setFailure(error.message),
                      );
                    }}
                  >
                    <Download size={ICON_SM} aria-hidden />
                    Download
                  </button>
                </li>
              ))}
            </ul>
          )}

          {signed.length > 0 && (
            <div>
              <p className="text-caption font-medium text-navy">
                Signed revisions
              </p>
              <p className="text-caption text-slate">
                Each one covers the exact document that officer signed. The
                revision marked as the filed document is the one the regulator
                receives.
              </p>
              <ul className="mt-2 space-y-2">
                {signed.map((version) => (
                  <li
                    key={version.id}
                    className="flex flex-wrap items-start justify-between gap-2 rounded border border-border-light p-3"
                  >
                    <div className="min-w-0">
                      <p className="font-medium text-navy">
                        {version.signedByName}
                        {version.signedByTitle
                          ? `, ${version.signedByTitle}`
                          : ""}
                      </p>
                      <p className="text-caption text-slate">
                        {moment(version.signedAt ?? version.createdAt)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {version.isFiled && (
                        <StatusPill tone="success">
                          The filed document
                        </StatusPill>
                      )}
                      <button
                        type="button"
                        className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface hover:text-navy"
                        onClick={() => {
                          setFailure(null);
                          void downloadArtifactVersion(bankId, version).catch(
                            (error: Error) => setFailure(error.message),
                          );
                        }}
                      >
                        <Download size={ICON_SM} aria-hidden />
                        Download
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {canExport && (
            <div>
              <button
                type="button"
                disabled={exportPackage.isPending}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-caption font-medium text-slate hover:bg-surface hover:text-navy disabled:cursor-not-allowed disabled:opacity-50"
                onClick={() => {
                  setFailure(null);
                  exportPackage.mutate(
                    { packageId, kind: "docx_working" },
                    {
                      onError: (error: Error) => setFailure(error.message),
                    },
                  );
                }}
              >
                <FileText size={ICON_SM} aria-hidden />
                {exportPackage.isPending
                  ? "Preparing…"
                  : "Produce a Word working copy"}
              </button>
              <p className="mt-1 text-caption text-slate">
                {DOWNLOAD_WORKING_COPY_NOTE}
              </p>
            </div>
          )}
        </div>
      </SectionCard>
    </QueryBoundary>
  );
}
