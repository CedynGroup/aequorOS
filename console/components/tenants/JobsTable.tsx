'use client';

import { useRouter } from 'next/navigation';
import type { OperatorJob } from '@/lib/api';
import { fmtTs, relTime, DASH } from '@/lib/format';
import { DataTable, MonoId, StatusChip, type Column } from '@/components/ui';

/** Cross-tenant queue rows, including the runtime that claimed each job. */
export function JobsTable({
  jobs,
  orgNameFor,
}: {
  jobs: OperatorJob[];
  orgNameFor: (organizationId: string) => string | undefined;
}) {
  const router = useRouter();
  const columns: Column<OperatorJob>[] = [
    {
      key: 'org',
      header: 'Organization',
      sortable: true,
      sortAccessor: (job) => orgNameFor(job.organization_id) ?? job.organization_id,
      render: (job) => (
        <div className="min-w-0">
          <div className="truncate text-body text-navy">
            {orgNameFor(job.organization_id) ?? 'Unknown organization'}
          </div>
          <MonoId id={job.organization_id} />
        </div>
      ),
    },
    {
      key: 'job',
      header: 'Job',
      sortable: true,
      sortAccessor: (job) => job.job_type,
      render: (job) => (
        <div>
          <div className="font-mono text-caption text-navy">{job.job_type}</div>
          <div className="text-caption text-slate">{job.bank_id ?? 'Organization scope'}</div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (job) => job.status,
      render: (job) => <StatusChip value={job.status} />,
    },
    {
      key: 'claimant',
      header: 'Claimed by',
      sortable: true,
      sortAccessor: (job) => job.claimed_by ?? '',
      render: (job) => (
        <span className="font-mono text-caption text-slate">{job.claimed_by ?? DASH}</span>
      ),
    },
    {
      key: 'attempts',
      header: 'Attempts',
      sortable: true,
      sortAccessor: (job) => job.attempts,
      render: (job) => <span className="text-body text-navy">{job.attempts}/{job.max_attempts}</span>,
    },
    {
      key: 'time',
      header: 'Latest event',
      sortable: true,
      sortAccessor: (job) => job.completed_at ?? job.started_at ?? job.queued_at,
      render: (job) => {
        const timestamp = job.completed_at ?? job.started_at ?? job.queued_at;
        return <span title={fmtTs(timestamp)} className="text-body text-navy/90">{relTime(timestamp)}</span>;
      },
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={jobs}
      density="compact"
      initialSort={{ key: 'time', dir: 'desc' }}
      getFilterText={(job) =>
        [
          job.organization_id,
          orgNameFor(job.organization_id) ?? '',
          job.bank_id ?? '',
          job.job_type,
          job.status,
          job.claimed_by ?? '',
          job.error ?? '',
        ].join(' ')
      }
      filterPlaceholder="Search jobs, workers, or organizations…"
      pageSize={25}
      emptyMessage="No queued or historical jobs match the current view."
      onRowClick={(job) => router.push(`/tenants/${job.organization_id}`)}
    />
  );
}