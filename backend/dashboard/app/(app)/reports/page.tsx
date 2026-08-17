'use client';

/**
 * Reports Library — the governance console. Three layers:
 *   1. Regulatory pack cards (CAR-RWA, LCR-NSFR, Board Pack) linking to the
 *      module-owned submission pages and the print-ready board pack.
 *   2. Official Runs registry — every persisted regulatory run, grouped by
 *      day, with provenance (input hash, engine version) and module links.
 */

import Link from 'next/link';
import { ChevronRight, FlaskConical, Printer } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import PackCards from '@/components/reports/PackCards';
import FreshnessStrip from '@/components/reports/FreshnessStrip';
import RunsRegistry from '@/components/reports/RunsRegistry';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';

export default function ReportsPage() {
  const { bank, period } = useBankContext();

  return (
    <>
      <PageHeader
        title="Reports Library"
        subtitle="Governance console · Immutable runs, regulatory packs, board reporting"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <Link
            href="/reports/board-pack"
            className="btn-primary inline-flex items-center gap-2 px-4 py-2 text-caption font-medium"
          >
            <Printer size={14} aria-hidden />
            Board pack
          </Link>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {bank && period && <FreshnessStrip bankId={bank.id} period={period} />}
        <PackCards bankId={bank?.id} periodId={period?.id} />

        {/* Cross-module saved-analyses index (ALCO prep) */}
        <Link
          href="/reports/analyses"
          className="card px-5 py-4 flex items-center justify-between gap-4 hover:border-action/40 transition-colors group"
        >
          <span className="flex items-center gap-3 min-w-0">
            <FlaskConical size={16} className="text-action shrink-0" aria-hidden />
            <span className="min-w-0">
              <span className="block text-body font-medium text-navy">
                Saved Analyses
              </span>
              <span className="block text-caption text-slate">
                Every saved scenario analysis across the five treasury
                workbenches, in one index for ALCO prep.
              </span>
            </span>
          </span>
          <ChevronRight
            size={16}
            className="text-slate group-hover:text-action shrink-0"
            aria-hidden
          />
        </Link>

        <RunsRegistry bankId={bank?.id} />
      </div>
    </>
  );
}
