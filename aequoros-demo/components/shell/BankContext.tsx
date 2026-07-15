'use client';

/**
 * Bank + reporting-period selection context for the app shell.
 *
 * Loads the tenant's banks (selecting the first) and its reporting periods
 * (selecting the latest — the API lists them period-end descending). When no
 * banks are provisioned it renders a full-screen empty state with a
 * one-click demo seed.
 */

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { Landmark, Loader2 } from 'lucide-react';
import type {
  BankRead,
  BankReportingPeriodRead,
} from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import { useBanks, useReportingPeriods, useSeedDemoBank } from '@/lib/api/hooks';
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
  const banksQuery = useBanks();
  const bank = banksQuery.data?.banks[0] ?? null;

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
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700"
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

  return <BankContext.Provider value={value}>{children}</BankContext.Provider>;
}

function NoBanksPanel() {
  const seed = useSeedDemoBank();
  return (
    <FullScreenPanel
      title="No banks provisioned"
      description="This organization has no banks in the risk service yet. Seed the synthetic Sample Bank Ltd dataset to explore the platform."
      action={
        <div className="flex flex-col items-center gap-3">
          <button
            type="button"
            disabled={seed.isPending}
            onClick={() => seed.mutate()}
            className="inline-flex items-center gap-2 px-4 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
          >
            {seed.isPending && (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            )}
            Seed Sample Bank Ltd (demo)
          </button>
          {seed.error && (
            <p className="text-caption text-critical">
              {isApiError(seed.error)
                ? seed.error.message
                : 'Seeding failed — check the risk service.'}
            </p>
          )}
        </div>
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
