'use client';

/**
 * Reports Library — the governance console. Three layers:
 *   1. Regulatory pack cards (BSD-2, BSD-3, Board Pack) linking to the
 *      module-owned submission pages and the print-ready board pack.
 *   2. Official Runs registry — every persisted regulatory run, grouped by
 *      day, with provenance (input hash, engine version) and module links.
 */

import Link from 'next/link';
import { Printer } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import PackCards from '@/components/reports/PackCards';
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
        <PackCards bankId={bank?.id} periodId={period?.id} />
        <RunsRegistry bankId={bank?.id} />
      </div>
    </>
  );
}
