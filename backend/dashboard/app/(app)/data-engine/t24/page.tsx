'use client';

/**
 * Temenos T24 tab of the Data Engine console. The native core-banking
 * integration: connect one or more T24 cores (OFS / IRIS / Open API), manage
 * the credential lifecycle, trigger pulls and backfills, review domain
 * coverage, and understand the transport modes. Wired to the live backend
 * through the generated risk-service client — connections, domains, and pull
 * jobs are real.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Database, Loader2, Plus } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
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
  const activeCount = rows.filter((row) => row.status === 'ACTIVE').length;

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
            <span className="inline-flex items-center gap-1.5 text-caption font-medium text-success border border-success/20 bg-success-light rounded-full px-2.5 py-0.5 uppercase tracking-wider">
              <Database size={12} aria-hidden /> Native integration
            </span>
          </span>
        }
        subtitle="Connect your T24 core over OFS, IRIS, or the Transact Open APIs — positions, GL, deals, counterparties and products flow into the canonical model that every module runs on."
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
                <h2 className="text-h3 text-navy">Connected cores</h2>
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
                  Connect a core
                </button>
              )}
            </div>

            {connections.isLoading ? (
              <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" aria-hidden />
                Loading Temenos connections…
              </div>
            ) : rows.length === 0 && !adding ? (
              <div className="card p-8 text-center space-y-2">
                <p className="text-body text-navy font-medium">
                  No Temenos core connected yet
                </p>
                <p className="text-caption text-slate max-w-lg mx-auto">
                  Connect your T24 core over OFS, IRIS, or the Transact Open APIs. The
                  canonical model and every calculation module behave identically to a file
                  upload — the native adapter only removes the export step.
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
                T24 sites are never blocked: export the close-of-business files and ingest via{' '}
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
                . The canonical model is identical; the native adapter only removes the export
                step.
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
