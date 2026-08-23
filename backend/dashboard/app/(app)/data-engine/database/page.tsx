'use client';

/**
 * Database (Direct) tab of the Data Engine console. The read-only core-database
 * adapter configuration: retain one or more bank-hosted reporting replicas
 * (Oracle, SQL Server, generic JDBC/ODBC), manage credentials, and prepare
 * mappings. A live check requires the matching driver and network path on the
 * deployed service.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Database, Loader2, Plus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import { useBankContext } from '@/components/shell/BankContext';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useDatabaseConnections } from '@/lib/api/database-direct';
import AddConnectionPanel from '@/components/database/AddConnectionPanel';
import ConnectionCard from '@/components/database/ConnectionCard';

export default function DatabaseDirectPage() {
  const { bank } = useBankContext();
  const connections = useDatabaseConnections(bank?.id);
  const [adding, setAdding] = useState(false);

  const rows = connections.data?.connections ?? [];
  const activeCount = rows.filter((row) => row.status === 'ACTIVE').length;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Database (Direct)' },
        ]}
        title={
          <span className="flex items-center gap-3">
            Database (Direct)
            <span className="inline-flex items-center gap-1.5 text-caption font-medium text-warning border border-warning/30 bg-warning-light rounded-full px-2.5 py-0.5 uppercase tracking-wider">
              <Database size={12} aria-hidden /> Deployment-gated
            </span>
          </span>
        }
        subtitle="Configure read-only extraction against a bank-hosted reporting replica. A live connection requires the matching driver, bank credentials, and network path installed on this service; use file upload or the Push API until onboarding is complete."
      />

      <div className="px-8 py-6 max-w-6xl space-y-8">
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-h3 text-navy">Configured connections</h2>
              {rows.length > 0 && (
                <p className="text-caption text-slate mt-0.5">
                  {activeCount} active · {rows.length} total
                </p>
              )}
            </div>
            {!adding && (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
              >
                <Plus size={13} aria-hidden />
                Connect a database
              </button>
            )}
          </div>

          {connections.isError ? (
            <ErrorPanel
              error={connections.error}
              onRetry={() => void connections.refetch()}
              title="Could not load database connections"
            />
          ) : connections.isLoading ? (
            <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Loading database connections…
            </div>
          ) : rows.length === 0 && !adding ? (
            <EmptyState
              Icon={Database}
              title="No database connection configured yet"
              description="Save a read-only reporting replica for onboarding. Live checks and syncs require the matching driver and network path on this deployment."
              action={
                <button
                  type="button"
                  onClick={() => setAdding(true)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  <Plus size={13} aria-hidden />
                  Connect a database
                </button>
              }
            />
          ) : (
            <div className="grid grid-cols-1 gap-4">
              {rows.map((connection) => (
                <ConnectionCard
                  key={connection.id}
                  bankId={bank?.id ?? ''}
                  connection={connection}
                />
              ))}
            </div>
          )}

          {adding && bank && (
            <AddConnectionPanel
              bankId={bank.id}
              existingNames={rows.map((row) => row.displayName)}
              onDone={() => setAdding(false)}
            />
          )}

          <div className="card p-5 border-l-4 border-l-action">
            <h3 className="text-h3 text-navy">Use an available ingestion path</h3>
            <p className="mt-2 text-body text-slate leading-relaxed">
              Cores are never blocked: export the close-of-business files and ingest via{' '}
              <Link
                href="/data-engine/excel-csv"
                className="font-medium text-action hover:text-action-hover"
              >
                Excel &amp; CSV
              </Link>{' '}
              or push from middleware through the{' '}
              <Link
                href="/data-engine/api"
                className="font-medium text-action hover:text-action-hover"
              >
                Push API
              </Link>
              . The canonical model is identical; the direct adapter only removes the
              export step.
            </p>
            <Link
              href="/data-engine/excel-csv"
              className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
            >
              Start with Excel &amp; CSV <ArrowRight size={13} aria-hidden />
            </Link>
          </div>
        </section>
      </div>
    </>
  );
}
