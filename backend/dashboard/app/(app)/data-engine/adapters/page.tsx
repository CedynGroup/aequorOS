/**
 * Other adapters tab: the Phase 3 adapter portfolio (Finacle, FlexCube,
 * DB-direct), stated honestly as planned work — no fake connection UI.
 */

import Link from 'next/link';
import { ArrowRight, Database, Server } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';

const PLANNED_ADAPTERS: {
  key: string;
  name: string;
  detail: string;
}[] = [
  {
    key: 'finacle',
    name: 'Infosys Finacle',
    detail:
      'Core-banking adapter for Finacle sites: scheduled extraction of accounts, loans, deposits, and GL balances into the canonical model.',
  },
  {
    key: 'flexcube',
    name: 'Oracle FLEXCUBE',
    detail:
      'Core-banking adapter for FLEXCUBE sites: same extraction scope and cadence, translated through per-institution mapping configs.',
  },
];

export default function AdaptersPage() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Other adapters' },
        ]}
        title="Other adapters"
        subtitle="The Phase 3 adapter portfolio. Database (Direct) has shipped; the core-banking adapters remain planned — no connection UI is shown until an adapter actually ships."
      />
      <div className="px-8 py-6 space-y-6 max-w-6xl">
        <Link
          href="/data-engine/database"
          className="card p-5 block hover:border-action/50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Database size={16} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">Database (Direct)</h3>
            <span className="ml-auto inline-flex items-center gap-1.5 text-caption font-medium text-success border border-success/20 bg-success-light rounded-full px-2.5 py-0.5 uppercase tracking-wider">
              Shipped
            </span>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            Read-only extraction against a bank-hosted reporting replica for cores
            without a workable API — schema mapped per institution during onboarding.
            Connect, test, discover the schema, and sync from the dedicated console.
          </p>
          <span className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-action">
            Open Database (Direct) <ArrowRight size={13} aria-hidden />
          </span>
        </Link>

        <div className="grid gap-4 lg:grid-cols-3">
          {PLANNED_ADAPTERS.map((adapter) => (
            <div key={adapter.key} className="card p-5">
              <div className="flex items-center gap-2">
                <Server size={16} className="text-slate" aria-hidden />
                <h3 className="text-h3 text-navy">{adapter.name}</h3>
                <span className="ml-auto text-caption font-medium text-slate">
                  Planned
                </span>
              </div>
              <p className="mt-2 text-body text-slate leading-relaxed">{adapter.detail}</p>
            </div>
          ))}
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Database size={16} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">One contract, one conformance suite</h3>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            Every adapter — shipped or planned — implements the same contract: extract,
            translate through the institution&apos;s mapping config, validate, and
            persist with cell-level lineage. An adapter passes the same conformance
            suite as Excel/CSV and API Push before it ships, so downstream modules never
            change when a new source comes online.
          </p>
          <p className="mt-3 text-body text-slate">
            Until a native adapter ships, these cores onboard via{' '}
            <Link
              href="/data-engine/excel-csv"
              className="font-medium text-action hover:text-action-hover"
            >
              Excel &amp; CSV
            </Link>{' '}
            or the{' '}
            <Link
              href="/data-engine/api"
              className="font-medium text-action hover:text-action-hover"
            >
              Push API
            </Link>
            .
          </p>
          <Link
            href="/data-engine"
            className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
          >
            Back to the overview <ArrowRight size={13} aria-hidden />
          </Link>
        </div>
      </div>
    </>
  );
}
