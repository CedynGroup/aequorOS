'use client';

/**
 * Temenos T24 tab of the Data Engine console. The native core-banking
 * configuration: store the details for one or more T24 cores (OFS / IRIS /
 * Open API), manage credentials and mappings, review domain coverage, and
 * understand transport modes. Live T24 transport is not installed in this
 * deployment, so pull jobs are safely blocked.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Database, Loader2, Plus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import { useBankContext } from '@/components/shell/BankContext';
import { useTemenosConnections } from '@/lib/api/hooks';
import AddConnectionPanel from '@/components/t24/AddConnectionPanel';
import ConnectionCard from '@/components/t24/ConnectionCard';
import DomainCoverage from '@/components/t24/DomainCoverage';
import TransportModes from '@/components/t24/TransportModes';

type Section = 'connections' | 'domains' | 'modes';

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'connections', label: 'Connections' },
  { key: 'domains', label: 'Domain coverage' },
  { key: 'modes', label: 'Transport modes' },
];

export default function T24Page() {
  const { bank } = useBankContext();
  const connections = useTemenosConnections(bank?.id);
  const [section, setSection] = useState<Section>('connections');
  const [adding, setAdding] = useState(false);

  const rows = connections.data?.connections ?? [];
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Temenos T24' },
        ]}
        title={
          <span className="flex items-center gap-3">
            Temenos T24
            <span className="inline-flex items-center gap-1.5 text-caption font-medium text-warning border border-warning/30 bg-warning-light rounded-full px-2.5 py-0.5 uppercase tracking-wider">
              <Database size={12} aria-hidden /> Configuration only
            </span>
          </span>
        }
        subtitle="Save T24 endpoint, credentials, and domain mappings for onboarding. Live OFS, IRIS, and Transact Open API requests are unavailable in this deployment; use file upload or the Push API for ingestion."
      />

      {/* Secondary sub-navigation */}
      <div className="bg-surface-raised border-b border-border-light px-8">
        <nav className="-mb-px flex gap-1" aria-label="Temenos sections">
          {SECTIONS.map((item) => {
            const active = section === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setSection(item.key)}
                className={`px-4 py-2.5 text-body font-medium border-b-2 whitespace-nowrap transition-colors ${
                  active
                    ? 'border-action text-navy'
                    : 'border-transparent text-slate hover:text-navy hover:border-border'
                }`}
              >
                {item.label}
                {item.key === 'connections' && rows.length > 0 && (
                  <span className="ml-1.5 text-caption font-mono text-slate">
                    {rows.length}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="px-8 py-6 max-w-6xl space-y-8">
        {section === 'connections' && (
          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-h3 text-navy">Configured cores</h2>
                {rows.length > 0 && (
                  <p className="text-caption text-slate mt-0.5">
                    {rows.length} saved configuration{rows.length === 1 ? '' : 's'}
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
                  Configure a core
                </button>
              )}
            </div>

            {connections.isLoading ? (
              <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" aria-hidden />
                Loading Temenos connections…
              </div>
            ) : rows.length === 0 && !adding ? (
              <EmptyState
                Icon={Database}
                title="No Temenos core configured yet"
                description="Save T24 connection details and mappings for onboarding. Live transport is not available in this deployment, so ingest through file upload or the Push API."
                action={
                  <button
                    type="button"
                    onClick={() => setAdding(true)}
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                  >
                    <Plus size={13} aria-hidden />
                    Configure a core
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
                Export the close-of-business files and ingest via{' '}
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
                . Both paths write the same canonical model while live T24 transport remains
                unavailable.
              </p>
              <Link
                href="/data-engine/excel-csv"
                className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
              >
                Start with Excel &amp; CSV <ArrowRight size={13} aria-hidden />
              </Link>
            </div>
          </section>
        )}

        {section === 'domains' && (
          <section className="space-y-4">
            <div>
              <h2 className="text-h3 text-navy">Domain coverage</h2>
              <p className="text-caption text-slate mt-0.5">
                What each transport mode pulls from the core, mapped to the canonical model.
              </p>
            </div>
            <DomainCoverage bankId={bank?.id} />
          </section>
        )}

        {section === 'modes' && (
          <section className="space-y-4">
            <div>
              <h2 className="text-h3 text-navy">Transport modes</h2>
              <p className="text-caption text-slate mt-0.5">
                Three channels into T24, one canonical model.
              </p>
            </div>
            <TransportModes />
          </section>
        )}
      </div>
    </>
  );
}
