'use client';

/**
 * Bank + reporting-period selection context for the app shell.
 *
 * Loads the tenant's banks (selecting the first) and its reporting periods
 * (selecting the latest — the API lists them period-end descending). When no
 * banks are provisioned it renders a full-screen empty state routing to the
 * Data Engine — a bank is created by its first ingestion, never seeded.
 */

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Landmark } from 'lucide-react';
import type {
  BankRead,
  BankReportingPeriodRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import { useBanks, useReportingPeriods } from '@/lib/api/hooks';
import { setActiveJurisdiction } from '@/lib/format';
import Logo from './Logo';

type BankContextValue = {
  bank: BankRead | null;
  period: BankReportingPeriodRead | null;
  periods: BankReportingPeriodRead[];
  setPeriodId: (periodId: string) => void;
  isLoading: boolean;
  isEmpty: boolean;
};

const BankContext = createContext<BankContextValue | null>(null);

export function useBankContext(): BankContextValue {
  const value = useContext(BankContext);
  if (!value) {
    throw new Error('useBankContext must be used within <BankProvider>.');
  }
  return value;
}

export default function BankProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const banksQuery = useBanks();
  const bank = banksQuery.data?.banks[0] ?? null;

  // Bind the resolved jurisdiction (registry row on the bank payload) into the
  // formatter module BEFORE children render, so every fmtCurrency/regShort call
  // in the same commit reflects this bank's country. Render-phase assignment is
  // deliberate and idempotent — an effect would leave the first paint on the
  // GH defaults for a non-GH bank.
  useMemo(() => {
    if (bank) {
      setActiveJurisdiction(
        bank.jurisdiction
          ? {
              currencyCode: bank.jurisdiction.currencyCode,
              locale: bank.jurisdiction.locale,
              regulatorShort: bank.jurisdiction.regulatorShort,
              centralBankName: bank.jurisdiction.centralBankName,
              countryName: bank.jurisdiction.countryName,
              submissionPortal: bank.jurisdiction.submissionPortal ?? null,
            }
          : // No registry row for this code: neutral labels + raw bank fields
            // (never the GH defaults — that would mislabel the regulator).
            {
              currencyCode: bank.currency,
              locale: 'en-US',
              regulatorShort: 'Regulator',
              centralBankName: 'Central bank',
              countryName: bank.jurisdictionCode,
              submissionPortal: null,
            }
      );
    }
  }, [bank]);

  const periodsQuery = useReportingPeriods(bank?.id);
  const periods = useMemo(
    () =>
      [...(periodsQuery.data?.periods ?? [])].sort(
        (a, b) => b.periodEnd.getTime() - a.periodEnd.getTime()
      ),
    [periodsQuery.data]
  );

  const [selectedPeriodId, setSelectedPeriodId] = useState<string | null>(null);
  const period =
    periods.find((p) => p.id === selectedPeriodId) ?? periods[0] ?? null;

  const isLoading =
    banksQuery.isLoading || (Boolean(bank) && periodsQuery.isLoading);
  const isEmpty = !banksQuery.isLoading && !banksQuery.error && !bank;

  const value = useMemo<BankContextValue>(
    () => ({
      bank,
      period,
      periods,
      setPeriodId: setSelectedPeriodId,
      isLoading,
      isEmpty,
    }),
    [bank, period, periods, isLoading, isEmpty]
  );

  if (banksQuery.error) {
    return (
      <FullScreenPanel
        title="Risk service unreachable"
        description={
          isApiError(banksQuery.error)
            ? banksQuery.error.message
            : 'Could not load banks from the risk service.'
        }
        action={
          <button
            type="button"
            onClick={() => banksQuery.refetch()}
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
          >
            Retry
          </button>
        }
      />
    );
  }

  if (isEmpty) {
    return <NoBanksPanel />;
  }

  // Bank exists but holds no reporting periods (fresh install or a full
  // reset): module dashboards have nothing to render, so steer the user to
  // the Data Engine. The Data Engine and Settings routes stay reachable —
  // uploading is exactly how a period comes into existence.
  const zeroPeriods = !isLoading && Boolean(bank) && periods.length === 0;
  const allowWithoutPeriods =
    pathname.startsWith('/data-engine') || pathname.startsWith('/settings');
  if (zeroPeriods && !allowWithoutPeriods) {
    return <NoPeriodsPanel />;
  }

  return <BankContext.Provider value={value}>{children}</BankContext.Provider>;
}

function NoPeriodsPanel() {
  return (
    <FullScreenPanel
      title="No data yet"
      description="This bank has no reporting periods. Upload your data in the Data Engine and activate it — the dashboards will populate for the uploaded as-of period."
      action={
        <Link
          href="/data-engine"
          className="inline-flex items-center gap-2 px-4 py-2 text-caption font-medium btn-primary"
        >
          Open the Data Engine
        </Link>
      }
    />
  );
}

function NoBanksPanel() {
  // No seeding path in the product: every data point flows through the Data
  // Engine (uploads, core-banking adapters, API push). A bank is provisioned by
  // its first ingestion.
  return (
    <FullScreenPanel
      title="No banks provisioned"
      description="This organization has no banks yet. Connect a data source in the Data Engine — the bank is created by its first ingestion (Excel/CSV upload, core-banking connection, or API push)."
      action={
        <Link
          href="/data-engine"
          className="inline-flex items-center gap-2 px-4 py-2 text-caption font-medium btn-primary"
        >
          Open the Data Engine
        </Link>
      }
    />
  );
}

function FullScreenPanel({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-alt px-6">
      <div className="card p-10 max-w-md w-full flex flex-col items-center text-center gap-4">
        <Logo variant="light" />
        <div className="w-12 h-12 rounded-full bg-surface text-slate inline-flex items-center justify-center">
          <Landmark size={20} aria-hidden />
        </div>
        <p className="text-h2 text-navy">{title}</p>
        <p className="text-body text-slate leading-relaxed">{description}</p>
        {action}
      </div>
    </div>
  );
}
