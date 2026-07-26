'use client';

/**
 * Regulatory Reporting — Awaiting my signature.
 *
 * Named routing (`attestation.routing`) exists so that a finished return lands
 * on a particular colleague's desk rather than being left for whoever notices.
 * This is that desk. Every row is a nomination someone made deliberately, and
 * only a named recipient can fill the slot — so an empty queue means nothing is
 * owed by this person, not that nothing is outstanding anywhere.
 *
 * "Open and sign" deep-links into the return workspace with `?sign=<role>`,
 * which opens the signing workspace on the same document the preparer placed the
 * fields on. Reviewing and signing are one act: an approver who has to read the
 * figures on one screen and commit on another is reading and committing to two
 * different things.
 */

import Link from 'next/link';
import { PenLine, Signature } from 'lucide-react';
import type { AwaitingSignatureRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import DataTable, { type Column } from '@/components/ui/DataTable';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { useReturnsAwaitingMySignature } from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, isoDate } from '@/lib/api/values';
import { FAMILY_LABELS } from '@/components/submissions/shared';
import { AttestationStatePill, roleNoun } from '@/components/attestation/shared';

export default function AwaitingSignaturePage() {
  const { bank } = useBankContext();
  const queue = useReturnsAwaitingMySignature();
  const items = queue.data?.items ?? [];

  const columns: Column<AwaitingSignatureRead>[] = [
    {
      key: 'return',
      header: 'Return',
      render: (row) => (
        <span className="font-mono text-caption font-medium text-navy">{row.returnCode}</span>
      ),
    },
    {
      key: 'reportingDate',
      header: 'Reporting date',
      render: (row) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtDateUTC(row.reportingDate)}
        </span>
      ),
    },
    {
      key: 'version',
      header: 'Version',
      render: (row) => (
        <span className="font-mono text-caption text-slate tnum">v{row.packageVersion}</span>
      ),
    },
    {
      key: 'role',
      header: 'Your slot',
      render: (row) => (
        <span className="text-caption text-navy">{roleNoun(row.signingRole)}</span>
      ),
    },
    {
      key: 'state',
      header: 'Attestation',
      render: (row) => <AttestationStatePill state={row.attestationState} />,
    },
    {
      key: 'requestedAt',
      header: 'Requested',
      render: (row) => (
        <span className="font-mono text-caption text-slate tnum">
          {fmtTimestamp(row.requestedAt)}
        </span>
      ),
    },
    {
      key: 'open',
      header: '',
      render: (row) => (
        <Link
          href={`/submissions/returns?code=${encodeURIComponent(row.returnCode)}&date=${isoDate(
            row.reportingDate
          )}&sign=${row.signingRole}`}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium btn-primary"
        >
          <PenLine size={13} aria-hidden />
          Open and sign
        </Link>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'Signatures' },
        ]}
        title="Awaiting my signature"
        subtitle="Returns a colleague sent to you by name — open one to read the figures and sign in the same act"
      />

      <div className="px-8 py-6 space-y-6">
        <SectionCard
          title="My signature queue"
          subtitle={`${items.length} outstanding · only the named recipient can fill a routed slot`}
          noPadding
        >
          <QueryBoundary
            isLoading={queue.isLoading}
            error={queue.error}
            onRetry={() => queue.refetch()}
            skeleton={<SkeletonTable rows={4} />}
          >
            {items.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={Signature}
                  title="Nothing is waiting on you"
                  description="Returns appear here the moment a colleague certifies one and names you as the next signer. An empty queue means nothing is owed by you — not that nothing is outstanding."
                />
              </div>
            ) : (
              <DataTable columns={columns} rows={items} />
            )}
          </QueryBoundary>
        </SectionCard>

        {items.some((row) => row.bankId !== bank?.id) && (
          <p className="text-caption text-slate leading-relaxed">
            Some requests belong to an institution other than the one selected in
            the header. Switch institution before opening those — the returns
            workspace reads the active one.
          </p>
        )}
      </div>
    </>
  );
}
