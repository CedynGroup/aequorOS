/**
 * Temenos T24 integration tab: honest status page. The adapter skeleton
 * exists (backend/app/adapters/temenos_t24); implementation is gated on
 * Temenos developer portal access. No fake dashboards.
 */

import Link from 'next/link';
import { ArrowRight, Clock, GitBranch, Server, Workflow } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';

export default function T24Page() {
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
            <span className="inline-flex items-center gap-1 text-caption font-medium text-slate border border-border rounded-full px-2.5 py-0.5">
              <Clock size={12} aria-hidden /> Pending partner access
            </span>
          </span>
        }
        subtitle="Native core-banking integration for T24 sites — implementation is gated on Temenos developer portal access."
      />
      <div className="px-8 py-6 space-y-6 max-w-6xl">
        <div className="card p-5 border-l-4 border-l-warning">
          <h2 className="text-h3 text-navy">Where this stands</h2>
          <p className="mt-2 text-body text-slate leading-relaxed">
            The adapter skeleton is registered in the platform (
            <code className="font-mono text-navy">backend/app/adapters/temenos_t24</code>
            ) and fails honestly if invoked — it does not pretend to ingest. Build-out
            starts when Temenos partner/developer portal access is granted; until then
            there is nothing to configure on this page.
          </p>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card p-5">
            <div className="flex items-center gap-2">
              <Workflow size={16} className="text-slate" aria-hidden />
              <h3 className="text-h3 text-navy">What it will do</h3>
            </div>
            <ul className="mt-3 space-y-2 text-body text-slate leading-relaxed list-disc pl-5">
              <li>
                <span className="text-navy font-medium">TAFJ API extraction</span> for
                on-demand reads of arrangements, customers, and GL balances.
              </li>
              <li>
                <span className="text-navy font-medium">Post-COB batch ingestion</span>{' '}
                aligned to the close-of-business cycle for the daily canonical snapshot.
              </li>
              <li>
                T24 enum translation (e.g. <code className="font-mono">F</code>/
                <code className="font-mono">V</code> rate types) through the same
                per-institution mapping configs used by every other source.
              </li>
            </ul>
          </div>

          <div className="card p-5">
            <div className="flex items-center gap-2">
              <GitBranch size={16} className="text-slate" aria-hidden />
              <h3 className="text-h3 text-navy">Same contract as every adapter</h3>
            </div>
            <p className="mt-3 text-body text-slate leading-relaxed">
              The T24 adapter implements the same adapter contract as Excel/CSV and API
              Push — extract, translate, validate, persist with cell-level lineage — and
              must pass the same conformance suite before it ships. Downstream modules
              will not know or care that the data came from T24.
            </p>
          </div>
        </div>

        <div className="card p-5 border-l-4 border-l-action">
          <div className="flex items-center gap-2">
            <Server size={16} className="text-action" aria-hidden />
            <h3 className="text-h3 text-navy">T24 banks onboard today</h3>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            T24 sites are not blocked: export the close-of-business files and ingest via{' '}
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
            . The canonical model and every calculation module behave identically; the
            native adapter only removes the export step.
          </p>
          <Link
            href="/data-engine/excel-csv"
            className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
          >
            Start with Excel &amp; CSV <ArrowRight size={13} aria-hidden />
          </Link>
        </div>
      </div>
    </>
  );
}
