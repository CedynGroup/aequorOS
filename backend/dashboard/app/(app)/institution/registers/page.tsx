'use client';

/**
 * Governance → Board Registers: the editor surface over the Board's four
 * risk-configuration registers — liquidity thresholds (LMTD ¶11), EWI
 * trigger levels (LRMD ¶28), CRM collateral haircuts (Basel ¶151) and IFRS 9
 * ECL assumptions. The calculation pages read these registers; this is where
 * an approver records the Board's adopted levels with approval evidence.
 * Every PUT is approver-gated server-side and audited with a required
 * reason; the Edit actions mirror that gate client-side.
 */

import PageHeader from '@/components/ui/PageHeader';
import { useBankContext } from '@/components/shell/BankContext';
import { fmtDateUTC } from '@/lib/api/values';
import ThresholdRegisterCard from '@/components/institution/registers/ThresholdRegisterCard';
import EwiRegisterCard from '@/components/institution/registers/EwiRegisterCard';
import CreditThresholdCard from '@/components/institution/registers/CreditThresholdCard';
import CrmHaircutCard from '@/components/institution/registers/CrmHaircutCard';
import EclAssumptionCard from '@/components/institution/registers/EclAssumptionCard';

export default function BoardRegistersPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/institution' },
          { label: 'Board Registers' },
        ]}
        title="Board Registers"
        subtitle="The Board's adopted risk configuration — liquidity thresholds, EWI trigger levels, CRM haircuts and ECL assumptions, each generation recorded with approval evidence"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <div className="px-8 py-6 space-y-6">
        {!bankId ? (
          <p className="text-body text-slate">
            No institution yet — a bank is created by its first ingestion
            through the Data Engine.
          </p>
        ) : (
          <>
            <ThresholdRegisterCard bankId={bankId} />
            <EwiRegisterCard bankId={bankId} periodId={periodId} />
            <CreditThresholdCard bankId={bankId} />
            <CrmHaircutCard bankId={bankId} />
            <EclAssumptionCard bankId={bankId} />
          </>
        )}
      </div>
    </>
  );
}
