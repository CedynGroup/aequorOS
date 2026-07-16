'use client';

/**
 * Regulatory Reporting — History. Every package version (superseded included)
 * with family/status/date-range filters; selecting a row expands the full
 * record: approvals trail, submission events, session-exported artifacts,
 * and the superseded chain for its (return, reporting date).
 */

import { useMemo, useState } from 'react';
import { Archive, Download } from 'lucide-react';
import type {
  PackageStatus,
  RegulatoryPackageSummaryRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard, SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryPackage,
  useRegulatoryPackages,
  useSessionArtifacts,
  useSubmissionEvents,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, isoDate, labelize, shortId } from '@/lib/api/values';
import {
  FAMILY_LABELS,
  PACKAGE_STATUS_LABELS,
  PackageStatusPill,
  downloadArtifact,
  fmtBytes,
  officerName,
} from '@/components/submissions/shared';
import EventsFeed from '@/components/submissions/EventsFeed';

const ALL = 'all';
const STATUS_OPTIONS: PackageStatus[] = [
  'generated',
  'validated',
  'pending_approval',
  'approved',
  'submitted',
  'acknowledged',
  'rejected',
  'superseded',
];

export default function HistoryPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const [family, setFamily] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Fetch wide and filter client-side: the list endpoint filters by exact
  // return_code/reporting_date only, and demo volumes are small.
  const query = useRegulatoryPackages(bankId, {
    includeSuperseded: true,
    limit: 100,
  });
  const all = useMemo(() => query.data?.packages ?? [], [query.data]);

  const rows = useMemo(
    () =>
      all.filter((pkg) => {
        if (family !== ALL && pkg.returnFamily !== family) return false;
        if (status !== ALL && pkg.status !== status) return false;
        const date = isoDate(pkg.reportingDate);
        if (from && date < from) return false;
        if (to && date > to) return false;
        return true;
      }),
    [all, family, status, from, to]
  );

  const selected = rows.find((pkg) => pkg.id === selectedId) ?? null;

  const columns: Column<RegulatoryPackageSummaryRead>[] = [
    {
      key: 'return',
      header: 'Return',
      render: (pkg) => (
        <span className="font-mono text-caption font-medium text-navy">
          {pkg.returnCode}
        </span>
      ),
    },
    {
      key: 'family',
      header: 'Family',
      render: (pkg) => (
        <span className="text-caption text-slate">
          {FAMILY_LABELS[pkg.returnFamily] ?? pkg.returnFamily}
        </span>
      ),
    },
    {
      key: 'reportingDate',
      header: 'Reporting date',
      render: (pkg) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtDateUTC(pkg.reportingDate)}
        </span>
      ),
    },
    {
      key: 'version',
      header: 'Version',
      numeric: true,
      render: (pkg) => `v${pkg.version}`,
    },
    {
      key: 'status',
      header: 'Status',
      render: (pkg) => <PackageStatusPill status={pkg.status} />,
    },
    {
      key: 'validation',
      header: 'Validation',
      render: (pkg) =>
        pkg.validationPassed == null ? (
          <span className="text-caption text-slate">Not run</span>
        ) : pkg.validationPassed ? (
          <StatusPill tone="success">Passed</StatusPill>
        ) : (
          <StatusPill tone="critical">Failed</StatusPill>
        ),
    },
    {
      key: 'generatedAt',
      header: 'Generated',
      render: (pkg) => (
        <span className="font-mono text-micro text-slate tnum">
          {fmtTimestamp(pkg.generatedAt)}
        </span>
      ),
    },
  ];

  const families = Array.from(new Set(all.map((pkg) => pkg.returnFamily)));

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'History' },
        ]}
        title="History"
        subtitle="Every package version — immutable snapshots, approvals, channel events, and artifacts"
        action={
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={family}
              onChange={(e) => setFamily(e.target.value)}
              aria-label="Filter by family"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
            >
              <option value={ALL}>All families</option>
              {families.map((value) => (
                <option key={value} value={value}>
                  {FAMILY_LABELS[value] ?? value}
                </option>
              ))}
            </select>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Filter by status"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
            >
              <option value={ALL}>All statuses</option>
              {STATUS_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {PACKAGE_STATUS_LABELS[value]}
                </option>
              ))}
            </select>
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              aria-label="Reporting date from"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy tnum"
            />
            <span className="text-caption text-slate">to</span>
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-label="Reporting date to"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy tnum"
            />
          </div>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
          skeleton={
            <div className="card">
              <SkeletonTable rows={6} />
            </div>
          }
        >
          <SectionCard
            title="Packages"
            subtitle={`${rows.length} of ${all.length} versions — click a row for the full record`}
            noPadding
          >
            {rows.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={Archive}
                  title="No packages match"
                  description="Generate a return from the Returns workspace, or widen the filters."
                />
              </div>
            ) : (
              <DataTable
                columns={columns}
                rows={rows}
                density="compact"
                onRowClick={(pkg) =>
                  setSelectedId((prev) => (prev === pkg.id ? null : pkg.id))
                }
                rowClassName={(pkg) =>
                  pkg.id === selected?.id ? 'bg-action-light/40' : ''
                }
              />
            )}
          </SectionCard>

          {selected && (
            <PackageRecord bankId={bankId!} summary={selected} siblings={all} />
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

function PackageRecord({
  bankId,
  summary,
  siblings,
}: {
  bankId: string;
  summary: RegulatoryPackageSummaryRead;
  siblings: RegulatoryPackageSummaryRead[];
}) {
  const detail = useRegulatoryPackage(bankId, summary.id);
  const events = useSubmissionEvents(bankId, summary.id);
  const artifacts = useSessionArtifacts(bankId, summary.id);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const chain = siblings
    .filter(
      (pkg) =>
        pkg.returnCode === summary.returnCode &&
        isoDate(pkg.reportingDate) === isoDate(summary.reportingDate)
    )
    .sort((a, b) => b.version - a.version);

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          <span className="font-mono">{summary.returnCode}</span>
          {fmtDateUTC(summary.reportingDate)}
          <span className="font-mono text-caption text-slate tnum">
            v{summary.version}
          </span>
          <PackageStatusPill status={summary.status} />
        </span>
      }
      subtitle={`Package ${shortId(summary.id, 8)} · generated by ${officerName(summary.generatedBy)} · ${fmtTimestamp(summary.generatedAt)}`}
    >
      <div className="space-y-5">
        {/* Superseded chain */}
        <div>
          <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
            Version chain
          </p>
          <p className="font-mono text-caption text-navy/85 tnum">
            {chain
              .map(
                (pkg) =>
                  `v${pkg.version}${pkg.status === 'superseded' ? '' : ` (${PACKAGE_STATUS_LABELS[pkg.status].toLowerCase()})`}`
              )
              .join(' ← ')}
          </p>
        </div>

        {detail.isLoading ? (
          <SkeletonCard />
        ) : detail.error ? (
          <ErrorPanel error={detail.error} onRetry={() => detail.refetch()} />
        ) : detail.data ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {/* Approvals trail */}
            <div>
              <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
                Approvals trail
              </p>
              {detail.data.approvals.length === 0 ? (
                <p className="text-caption text-slate">No approval actions yet.</p>
              ) : (
                <ul className="space-y-1.5">
                  {detail.data.approvals.map((approval) => (
                    <li
                      key={approval.id}
                      className="flex items-baseline gap-2 text-caption"
                    >
                      <span className="font-medium text-navy w-20 shrink-0">
                        {labelize(approval.action)}
                      </span>
                      <span className="text-navy/85">
                        {officerName(approval.actorUserId)}
                      </span>
                      {approval.reason && (
                        <span className="text-slate truncate">
                          — {approval.reason}
                        </span>
                      )}
                      <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                        {fmtTimestamp(approval.occurredAt)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <p className="text-micro font-medium text-slate uppercase tracking-wider mt-4 mb-1.5">
                Source runs
              </p>
              <ul className="space-y-1">
                {detail.data.sourceRuns.map((run) => (
                  <li
                    key={run.runId}
                    className="flex items-center gap-2 font-mono text-micro text-slate tnum"
                  >
                    <span className="text-navy/85">{run.module}</span>
                    <span>{run.engineVersion}</span>
                    <span className="truncate">{shortId(run.inputHash, 12)}</span>
                    <CopyButton text={run.inputHash} label="input hash" />
                  </li>
                ))}
              </ul>
            </div>

            {/* Events + artifacts */}
            <div>
              <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
                Submission events
              </p>
              {events.isLoading ? (
                <SkeletonCard />
              ) : events.error ? (
                <ErrorPanel error={events.error} onRetry={() => events.refetch()} />
              ) : (
                <EventsFeed events={events.data?.events ?? []} />
              )}

              <p className="text-micro font-medium text-slate uppercase tracking-wider mt-4 mb-1.5">
                Artifacts
              </p>
              {downloadError && (
                <p className="mb-1.5 text-caption text-critical">{downloadError}</p>
              )}
              {(artifacts.data ?? []).length === 0 ? (
                <p className="text-caption text-slate leading-relaxed">
                  No artifacts exported this session — exports minted from the
                  Returns workspace appear here with checksums and downloads.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {(artifacts.data ?? []).map((artifact) => (
                    <li
                      key={artifact.id}
                      className="flex items-center gap-2 text-caption"
                    >
                      <span className="font-mono font-medium text-navy uppercase">
                        {artifact.kind}
                      </span>
                      <span className="font-mono text-micro text-slate tnum">
                        sha256 {shortId(artifact.checksumSha256, 12)}
                      </span>
                      <span className="font-mono text-micro text-slate tnum">
                        {fmtBytes(artifact.sizeBytes)}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          setDownloadError(null);
                          downloadArtifact(bankId, artifact).catch(
                            (error: unknown) =>
                              setDownloadError(
                                error instanceof Error
                                  ? error.message
                                  : 'Download failed.'
                              )
                          );
                        }}
                        className="ml-auto inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-micro font-medium text-slate hover:text-navy hover:border-slate"
                      >
                        <Download size={11} aria-hidden />
                        Download
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}
