'use client';

/**
 * Database (Direct) tab of the Data Engine console. The read-only core-database
 * adapter: connect one or more bank-hosted reporting replicas (Oracle, SQL
 * Server, generic JDBC/ODBC), manage the credential lifecycle, test
 * reachability, discover the source schema for mapping, and run on-demand syncs
 * that mint immutable ingestion batches. Wired to the live backend through the
 * generated risk-service DatabaseDirectApi.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Database, Loader2, Plus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
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
            <span className="inline-flex items-center gap-1.5 text-caption font-medium text-success border border-success/20 bg-success-light rounded-full px-2.5 py-0.5 uppercase tracking-wider">
              <Database size={12} aria-hidden /> Native adapter
            </span>
          </span>
        }
        subtitle="Read-only extraction against a bank-hosted reporting replica for cores without a workable API. Positions, GL, deals, and reference data flow into the canonical model every module runs on."
      />

      <div className="px-8 py-6 max-w-6xl space-y-8">
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-h3 text-navy">Connections</h2>
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
            <div className="card p-8 text-center space-y-2">
              <p className="text-body text-navy font-medium">
                No database connected yet
              </p>
              <p className="text-caption text-slate max-w-lg mx-auto">
                Connect a read-only reporting replica. Test reachability, discover the
                source schema for mapping, then sync — the canonical model and every
                module behave identically to a file upload.
              </p>
            </div>
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
            <h3 className="text-h3 text-navy">Not ready for a live connection?</h3>
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
