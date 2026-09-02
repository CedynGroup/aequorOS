import type { Metadata } from 'next';
import Link from 'next/link';
import Kicker from '@/components/Kicker';
import PageHeader from '@/components/PageHeader';
import ModuleShowcase from '@/components/ModuleShowcase';
import ScreenTabs from '@/components/ScreenTabs';

export const metadata: Metadata = {
  title: 'Product — AequorOS',
  description:
    'Ingestion, seven calculation engines, and regulatory reporting on one auditable spine. Browse the working product interface — no login required.',
};

export default function ProductPage() {
  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 lg:px-16">
      <PageHeader
        kicker="The platform"
        title="From first file to signed return."
        lede="Ingestion, seven calculation engines, and regulatory reporting on one auditable spine. Start with the workflow that hurts most; adopt the rest when your team is ready."
      />

      {/* Compact pipeline strip */}
      <div className="mb-16 md:mb-20 bg-navy-deep rounded-md px-6 md:px-8 py-5 flex flex-wrap items-center gap-3.5">
        <span className="text-sm font-semibold text-white">Core data</span>
        <StripArrow />
        <span className="text-sm font-semibold text-accent">Data Engine</span>
        <StripArrow />
        <span className="text-sm font-semibold text-white">Seven engines</span>
        <StripArrow />
        <span className="text-sm font-semibold text-white">Signed returns</span>
        <span className="ml-auto hidden md:inline text-[13px] text-white/60">
          Every figure traceable to its source load
        </span>
      </div>

      {/* Module showcase: click an engine in the index, its row opens above */}
      <ModuleShowcase />

      {/* Data Engine */}
      <section id="data-engine" className="pb-20 md:pb-24 scroll-mt-24">
        <div className="border-t border-hairline pt-14 md:pt-16">
          <div className="flex flex-col gap-3.5 mb-8 max-w-2xl">
            <Kicker>The data plane</Kicker>
            <h2 className="font-serif font-medium text-3xl md:text-[38px] leading-[1.12] tracking-tight">
              Connect the source you have. Normalize everything.
            </h2>
          </div>
          <ScreenTabs
            items={[
              {
                tab: 'Ingestion & health',
                eyebrow: 'DATA ENGINE · INGESTION',
                title: 'Every load lands in an auditable canonical model.',
                body: 'File upload, secure API push, or a read-only extract from the core your bank already runs. Where a source is unusual, the mapping is configured for that bank — never hard-coded into the product. Downstream calculations trigger automatically on every accepted load.',
                screenId: 'data-engine',
              },
              {
                tab: 'Behavioral models',
                eyebrow: 'DATA ENGINE · BEHAVIORAL',
                title: 'Assumptions your institution can stand behind.',
                body: 'Per-institution behavioral models for non-maturity deposits and prepayment, reviewed and versioned before any engine consumes them. Core/volatile splits come from evidence, not folklore.',
                screenId: 'behavioral',
              },
              {
                tab: 'Positions & lineage',
                eyebrow: 'DATA ENGINE · LINEAGE',
                title: 'Every figure answers for itself.',
                body: 'The canonical position book behind every module calculation. Each number traces back to its source input, batch, and timestamp; corrections supersede prior records, and nothing is silently overwritten.',
                screenId: 'positions-lineage',
              },
            ]}
          />
        </div>
      </section>

      {/* Governance */}
      <section id="governance" className="pb-20 md:pb-24 scroll-mt-24">
        <div className="border-t border-hairline pt-14 md:pt-16">
          <div className="flex flex-col gap-3.5 mb-8 max-w-2xl">
            <Kicker>Governance</Kicker>
            <h2 className="font-serif font-medium text-3xl md:text-[38px] leading-[1.12] tracking-tight">
              Returns you can defend. Lineage you can show.
            </h2>
          </div>
          <ScreenTabs
            items={[
              {
                tab: 'Regulatory reporting',
                eyebrow: 'GOVERNANCE · REGULATORY REPORTING',
                title: 'Sealed runs, export-ready returns.',
                body: "Bank of Ghana returns generated from immutable calculation runs in the regulator's own workbook layouts, exported to Excel, CSV, and PDF for officer review and signature.",
                screenId: 'submissions',
              },
              {
                tab: 'Reports',
                eyebrow: 'GOVERNANCE · REPORTS',
                title: 'One library for every official number.',
                body: 'Run freshness per module, return packages with their provenance, saved analyses, and a print-ready executive board pack — composed from live figures, formatted for the boardroom.',
                screenId: 'reports',
              },
              {
                tab: 'Institution profile',
                eyebrow: 'GOVERNANCE · INSTITUTION PROFILE',
                title: 'The registers behind the numbers.',
                body: "Your institution's parties, products, outlets, and Board-governed parameter registers, each change carrying approval evidence and an audit trail.",
                screenId: 'institution',
              },
            ]}
          />
        </div>
      </section>

      {/* Closing row */}
      <div className="border-t border-hairline pt-12 pb-24 flex flex-col md:flex-row md:items-center md:justify-between gap-8">
        <h2 className="font-serif font-medium text-[26px] md:text-[30px] leading-[1.15] tracking-tight">
          Ready to run it on your bank&apos;s questions?
        </h2>
        <Link
          href="/contact"
          className="inline-flex h-12 items-center rounded bg-navy-deep px-6 text-[15px] font-semibold text-white hover:bg-navy transition-colors shrink-0"
        >
          Request a demo
        </Link>
      </div>
    </div>
  );
}

function StripArrow() {
  return (
    <svg width="22" height="12" viewBox="0 0 22 12" aria-hidden>
      <path
        d="M0 6 H16 M12 1 L18 6 L12 11"
        stroke="rgba(255,255,255,0.4)"
        strokeWidth="2"
        fill="none"
      />
    </svg>
  );
}
