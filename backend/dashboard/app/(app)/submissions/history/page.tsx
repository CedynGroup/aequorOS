'use client';

/**
 * Regulatory Reporting — History. Every package version (superseded included)
 * with server-side family/status/date-range filters and offset pagination;
 * selecting a row expands the full record: the ORASS View-Audit-Log-parity
 * audit table (version chain + approvals + channel events, chronological),
 * approvals trail, submission events, persisted artifacts, resubmission
 * requests, regulator comments, and the superseded chain for its
 * (return, reporting date).
 */

import { useMemo, useState, type ReactNode } from 'react';
import { Archive, ChevronLeft, ChevronRight, Download, Mail } from 'lucide-react';
import type {
  PackageApprovalRead,
  PackageStatusFilter,
  RegulatoryPackageSummaryRead,
  SubmissionEventRead,
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
  useOfficerNames,
  usePackageArtifacts,
  usePackageAttestation,
  useRegulatoryPackage,
  useRegulatoryPackages,
  useResubmissionRequests,
  useSubmissionEvents,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, isoDate, labelize, shortId } from '@/lib/api/values';
import {
  CHANNEL_LABELS,
  FAMILY_LABELS,
  PACKAGE_STATUS_LABELS,
  PackageStatusPill,
  ResubmissionStatusPill,
  downloadArtifact,
  downloadEmailFallbackEml,
  fmtBytes,
} from '@/components/submissions/shared';
import EventsFeed from '@/components/submissions/EventsFeed';
import { AttestationSummary } from '@/components/attestation/shared';

const ALL = 'all';
const PAGE_SIZE = 25;
const STATUS_OPTIONS: PackageStatusFilter[] = [
  'generated',
  'validated',
  'pending_approval',
  'approved',
  'submitted',
  'acknowledged',
  'rejected',
  'declined',
  'superseded',
];

export default function HistoryPage() {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const [family, setFamily] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Every filter change restarts pagination from the first page.
  const applyFilter = (apply: () => void) => {
    apply();
    setOffset(0);
    setSelectedId(null);
  };

  // Server-side filtering + pagination — the list endpoint filters by
  // family/status/date range and counts the filtered set.
  const query = useRegulatoryPackages(bankId, {
    returnFamily: family !== ALL ? family : undefined,
    status: status !== ALL ? (status as PackageStatusFilter) : undefined,
    reportingDateFrom: from || undefined,
    reportingDateTo: to || undefined,
    includeSuperseded: true,
    limit: PAGE_SIZE,
    offset,
  });
  const rows = useMemo(() => query.data?.packages ?? [], [query.data]);
  const total = query.data?.total ?? 0;
  const hasMore = query.data?.hasMore ?? false;

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

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = offset + rows.length;

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
              onChange={(e) => applyFilter(() => setFamily(e.target.value))}
              aria-label="Filter by family"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
            >
              <option value={ALL}>All families</option>
              {Object.entries(FAMILY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              value={status}
              onChange={(e) => applyFilter(() => setStatus(e.target.value))}
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
              onChange={(e) => applyFilter(() => setFrom(e.target.value))}
              aria-label="Reporting date from"
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy tnum"
            />
            <span className="text-caption text-slate">to</span>
            <input
              type="date"
              value={to}
              onChange={(e) => applyFilter(() => setTo(e.target.value))}
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
            subtitle={`Showing ${rangeStart}–${rangeEnd} of ${total} versions — click a row for the full record`}
            noPadding
            footer={
              <span className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={offset === 0 || query.isFetching}
                  onClick={() => {
                    setOffset((prev) => Math.max(prev - PAGE_SIZE, 0));
                    setSelectedId(null);
                  }}
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate disabled:opacity-50 disabled:hover:text-slate disabled:hover:border-border"
                >
                  <ChevronLeft size={11} aria-hidden />
                  Prev
                </button>
                <button
                  type="button"
                  disabled={!hasMore || query.isFetching}
                  onClick={() => {
                    setOffset((prev) => prev + PAGE_SIZE);
                    setSelectedId(null);
                  }}
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate disabled:opacity-50 disabled:hover:text-slate disabled:hover:border-border"
                >
                  Next
                  <ChevronRight size={11} aria-hidden />
                </button>
                <span className="font-mono text-micro tnum">
                  page {Math.floor(offset / PAGE_SIZE) + 1}
                </span>
              </span>
            }
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

          {selected && <PackageRecord bankId={bankId!} summary={selected} />}
        </QueryBoundary>
      </div>
    </>
  );
}

function PackageRecord({
  bankId,
  summary,
}: {
  bankId: string;
  summary: RegulatoryPackageSummaryRead;
}) {
  const detail = useRegulatoryPackage(bankId, summary.id);
  const events = useSubmissionEvents(bankId, summary.id);
  const artifacts = usePackageArtifacts(bankId, summary.id);
  const resubmissions = useResubmissionRequests(bankId, summary.id);
  // Fetched for the SELECTED package only: neither package payload carries the
  // attestation state, so a table column would be one request per row.
  const attestation = usePackageAttestation(bankId, summary.id);
  const officerName = useOfficerNames();
  const [downloadError, setDownloadError] = useState<string | null>(null);

  // The full version chain for this (return, reporting date) — fetched
  // server-filtered so it stays complete regardless of the page window.
  const chainQuery = useRegulatoryPackages(bankId, {
    returnCode: summary.returnCode,
    reportingDate: isoDate(summary.reportingDate),
    includeSuperseded: true,
    limit: 50,
  });
  const chain = useMemo(() => {
    const rows = chainQuery.data?.packages ?? [];
    return [...rows].sort((a, b) => b.version - a.version);
  }, [chainQuery.data]);

  // Latest submitted channel drives the .eml affordance (email fallback only).
  const latestSubmitted = (events.data?.events ?? []).find(
    (event) => event.event === 'submitted'
  );
  const emailChannel = latestSubmitted?.channel === 'email';

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
            {chain.length === 0
              ? `v${summary.version}`
              : chain
                  .map(
                    (pkg) =>
                      `v${pkg.version}${pkg.submissionRevision ? ` rev ${pkg.submissionRevision}` : ''}${pkg.status === 'superseded' ? '' : ` (${PACKAGE_STATUS_LABELS[pkg.status].toLowerCase()})`}`
                  )
                  .join(' ← ')}
          </p>
        </div>

        {(detail.data?.regulatorComments ?? summary.regulatorComments) && (
          <div>
            <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
              Supervisor comments
            </p>
            <p className="text-caption text-navy/80 leading-relaxed whitespace-pre-wrap rounded border border-border-light bg-surface px-3 py-2">
              {detail.data?.regulatorComments ?? summary.regulatorComments}
            </p>
          </div>
        )}

        {/* Attestation — the signature record for this exact version, including
            voids, whose signatures are retained as history rather than deleted. */}
        <div>
          <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
            Attestation
          </p>
          {attestation.isLoading ? (
            <SkeletonCard />
          ) : attestation.error ? (
            <ErrorPanel
              error={attestation.error}
              onRetry={() => attestation.refetch()}
              title="Could not load the attestation record"
            />
          ) : attestation.data ? (
            <AttestationSummary status={attestation.data} />
          ) : null}
        </div>

        <AuditLog
          summary={summary}
          chain={chain}
          approvals={detail.data?.approvals ?? []}
          events={events.data?.events ?? []}
          officerName={officerName}
        />

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
              {(artifacts.data?.artifacts ?? []).length === 0 ? (
                <p className="text-caption text-slate leading-relaxed">
                  No artifacts exported yet — exports minted from the Returns
                  workspace appear here with checksums and downloads.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {(artifacts.data?.artifacts ?? []).map((artifact) => (
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

              {emailChannel && (
                <button
                  type="button"
                  onClick={() => {
                    setDownloadError(null);
                    downloadEmailFallbackEml(bankId, summary.id).catch(
                      (error: unknown) =>
                        setDownloadError(
                          error instanceof Error
                            ? error.message
                            : 'Download failed.'
                        )
                    );
                  }}
                  className="mt-2 inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate"
                >
                  <Mail size={11} aria-hidden />
                  Download .eml
                </button>
              )}

              <p className="text-micro font-medium text-slate uppercase tracking-wider mt-4 mb-1.5">
                Resubmission requests
              </p>
              {(resubmissions.data?.requests ?? []).length === 0 ? (
                <p className="text-caption text-slate">
                  No resubmission requests filed for this version.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {(resubmissions.data?.requests ?? []).map((request) => (
                    <li
                      key={request.id}
                      className="flex items-baseline gap-2 text-caption"
                    >
                      <ResubmissionStatusPill status={request.status} />
                      <span className="text-slate truncate">
                        — {request.reason}
                      </span>
                      <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                        {request.decidedAt
                          ? `decided ${fmtTimestamp(new Date(request.decidedAt))}`
                          : fmtTimestamp(request.occurredAt)}
                      </span>
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

// ---------------------------------------------------------------------------
// Audit log — ORASS View-Audit-Log parity: one row per version in the chain,
// with the selected package's approvals and channel events interleaved
// chronologically.
// ---------------------------------------------------------------------------

type AuditRow = {
  id: string;
  action: string;
  actor: string;
  detail: ReactNode;
  at: Date;
};

function AuditLog({
  summary,
  chain,
  approvals,
  events,
  officerName,
}: {
  summary: RegulatoryPackageSummaryRead;
  chain: RegulatoryPackageSummaryRead[];
  approvals: PackageApprovalRead[];
  events: SubmissionEventRead[];
  officerName: (userId: string) => string;
}) {
  const rows = useMemo(() => {
    const versionRows: AuditRow[] = (chain.length > 0 ? chain : [summary]).map(
      (pkg) => ({
        id: `version-${pkg.id}`,
        action: `Generated v${pkg.version}${
          pkg.submissionRevision ? ` · rev ${pkg.submissionRevision}` : ''
        }`,
        actor: officerName(pkg.generatedBy),
        detail: <PackageStatusPill status={pkg.status} />,
        at: pkg.generatedAt,
      })
    );
    const approvalRows: AuditRow[] = approvals.map((approval) => ({
      id: `approval-${approval.id}`,
      action: labelize(approval.action),
      actor: officerName(approval.actorUserId),
      detail: approval.reason ? (
        <span className="text-caption text-slate">{approval.reason}</span>
      ) : null,
      at: approval.occurredAt,
    }));
    const eventRows: AuditRow[] = events.map((event) => ({
      id: `event-${event.id}`,
      action: labelize(event.event),
      actor: CHANNEL_LABELS[event.channel] ?? event.channel,
      detail: event.externalRef ? (
        <span className="font-mono text-micro text-slate tnum">
          ref {shortId(event.externalRef, 24)}
        </span>
      ) : null,
      at: event.occurredAt,
    }));
    return [...versionRows, ...approvalRows, ...eventRows].sort(
      (a, b) => a.at.getTime() - b.at.getTime()
    );
  }, [summary, chain, approvals, events, officerName]);

  const columns: Column<AuditRow>[] = [
    {
      key: 'action',
      header: 'Action',
      render: (row) => (
        <span className="text-caption font-medium text-navy">{row.action}</span>
      ),
    },
    {
      key: 'actor',
      header: 'Actor / channel',
      render: (row) => (
        <span className="text-caption text-navy/85">{row.actor}</span>
      ),
    },
    {
      key: 'detail',
      header: 'Detail',
      render: (row) => row.detail ?? <span className="text-caption text-slate">—</span>,
    },
    {
      key: 'at',
      header: 'When',
      align: 'right',
      render: (row) => (
        <span className="font-mono text-micro text-slate tnum whitespace-nowrap">
          {fmtTimestamp(row.at)}
        </span>
      ),
    },
  ];

  return (
    <div>
      <p className="text-micro font-medium text-slate uppercase tracking-wider mb-1.5">
        Audit log
      </p>
      <div className="rounded border border-border-light overflow-hidden">
        <DataTable columns={columns} rows={rows} density="compact" />
      </div>
    </div>
  );
}
