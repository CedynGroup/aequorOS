"use client";

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
} from "react";
import { Landmark } from "lucide-react";
import { usePathname } from "next/navigation";
import type {
  BankRead,
  BankReadInstitutionTypeDetail,
  BankReportingPeriodRead,
} from "@aequoros/risk-service-api";
import { isApiError } from "@/lib/api/client";
import { loginUrlWithReason } from "@/lib/loginUrl";
import { useBanks, useReportingPeriods } from "@/lib/api/hooks";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { setActiveJurisdiction } from "@/lib/format";
import {
  effectiveInstitutionModules,
  effectiveOrganizationModules,
  hasEffectiveCapability,
  hasStructuralCapability,
  isPersonalSettingsPath,
  moduleSetFrom,
  type ModuleScope,
} from "@/lib/modules";
import Logo from "./Logo";

/**
 * The active tenant's institution-type discriminator (docs/sdi.md §1), resolved
 * from the bank payload. `code` is the typed licence class always carried on the
 * bank; `institutionClass` is the coarse regime axis ('bank' | 'sdi') future SDI
 * phases will gate modules on. Phase A only surfaces it — nothing is gated yet.
 */
export type InstitutionTypeInfo = {
  code: string;
  institutionClass: string | null;
  detail: BankReadInstitutionTypeDetail | null;
};

type BankContextValue = {
  bank: BankRead | null;
  institutionType: InstitutionTypeInfo | null;
  /**
   * The active tenant's module scope (docs/sdi.md §3, §6.3) — the set of
   * top-level modules its institution class is entitled to, plus its class.
   * `modules: null` means unscoped (a universal bank, or not yet loaded) so
   * every module stays visible. The nav surfaces + route guard consult this.
   */
  moduleScope: ModuleScope;
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
    throw new Error("useBankContext must be used within <BankProvider>.");
  }
  return value;
}

/**
 * The active institution type, for later-phase module scoping. Returns null
 * until a bank is loaded. Deliberately does NOT gate anything in Phase A.
 */
export function useInstitutionType(): InstitutionTypeInfo | null {
  return useBankContext().institutionType;
}

/**
 * The active tenant's module scope, for the nav surfaces + route guard. Until the
 * bank payload settles (`isResolved` false) the nav + data hooks restrict to
 * CORE_MODULES rather than show/fetch everything — so no out-of-scope module (FX,
 * FTP, …) flashes or fires a request during the load (docs/sdi.md §3.2/§6.3).
 */
export function useModuleScope(): ModuleScope {
  return useBankContext().moduleScope;
}

export default function BankProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isPersonalSelfService = isPersonalSettingsPath(pathname);
  const profileQuery = useUserProfile();
  const banksQuery = useBanks(!isPersonalSelfService);
  const bank = banksQuery.data?.banks[0] ?? null;
  const authority = profileQuery.effectiveAuthority;
  const institutionCapabilities = useMemo(
    () =>
      authority?.institutionCapabilities.find(
        (entry) => entry.institutionId === bank?.id,
      )?.capabilities ?? [],
    [authority, bank?.id],
  );
  const organizationCapabilities = useMemo(
    () => authority?.organizationCapabilities ?? [],
    [authority],
  );

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
              locale: "en-US",
              regulatorShort: "Regulator",
              centralBankName: "Central bank",
              countryName: bank.jurisdictionCode,
              submissionPortal: null,
            },
      );
    }
  }, [bank]);

  const periodsQuery = useReportingPeriods(bank?.id);
  const periods = useMemo(
    () =>
      [...(periodsQuery.data?.periods ?? [])].sort(
        (a, b) => b.periodEnd.getTime() - a.periodEnd.getTime(),
      ),
    [periodsQuery.data],
  );

  // The active tenant's institution-type discriminator, resolved from the bank
  // payload alongside jurisdiction. Surfaced for later-phase module scoping;
  // Phase A gates nothing.
  const institutionType = useMemo<InstitutionTypeInfo | null>(
    () =>
      bank
        ? {
            code: bank.institutionType,
            institutionClass:
              bank.institutionTypeDetail?.institutionClass ?? null,
            detail: bank.institutionTypeDetail ?? null,
          }
        : null,
    [bank],
  );

  // The tenant's scoped module set (docs/sdi.md §3): built from the API's
  // default_modules. `isResolved` is false until the bank payload settles, so the
  // nav + data hooks restrict to CORE_MODULES rather than flash every module and
  // fire out-of-scope requests during the load (the every-module-on-refresh race).
  const moduleScope = useMemo<ModuleScope>(
    () => ({
      modules: effectiveInstitutionModules(
        bank?.institutionTypeDetail?.defaultModules,
        institutionCapabilities,
      ),
      entitledModules: moduleSetFrom(
        bank?.institutionTypeDetail?.defaultModules,
      ),
      organizationModules: effectiveOrganizationModules(
        organizationCapabilities,
      ),
      hasInstitutionAuthority: institutionCapabilities.length > 0,
      institutionClass: bank?.institutionTypeDetail?.institutionClass ?? null,
      liquidityAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "liq",
        "aggregated",
        "view",
      ),
      liquidityConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "liq",
        "confidential",
        "view",
      ),
      riskConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "risk",
        "confidential",
        "view",
      ),
      capitalAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "aggregated",
        "view",
      ),
      capitalConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "view",
      ),
      capitalRestrictedView: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "restricted",
        "view",
      ),
      capitalRun: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "run",
      ),
      capitalCreate: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "create",
      ),
      capitalEdit: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "edit",
      ),
      capitalExport: hasEffectiveCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "export",
      ),
      // The checker half of the ICAAP Pillar 2 maker-checker. Institution-scoped
      // like the rest of CAP; the service still refuses self-approval.
      //
      // Read STRUCTURALLY, because `approve` is projected with
      // `requires_contextual_authorization` — the evaluator cannot answer
      // "may this officer approve THIS revision" without the revision. Asking
      // `hasEffectiveCapability` therefore answered false for every holder of a
      // correct binding, and the approve-and-sign control was offered to nobody
      // at all. It is an offer, not authority: the service re-decides, and
      // additionally refuses an approver who authored the revision.
      capitalApprove: hasStructuralCapability(
        institutionCapabilities,
        "cap",
        "confidential",
        "approve",
      ),
      // ORGANIZATION-scoped on purpose: internal audit's authority is over the
      // organization, not granted institution by institution. It gates only
      // whether the "Record independent review" control is offered — the
      // service decides independence from who worked on the cycle.
      auditCreate: hasEffectiveCapability(
        organizationCapabilities,
        "audit",
        "confidential",
        "create",
      ),
      irrbbAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "irrbb",
        "aggregated",
        "view",
      ),
      irrbbConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "irrbb",
        "confidential",
        "view",
      ),
      irrbbRun: hasEffectiveCapability(
        institutionCapabilities,
        "irrbb",
        "confidential",
        "run",
      ),
      fxAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "fx",
        "aggregated",
        "view",
      ),
      fxConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "fx",
        "confidential",
        "view",
      ),
      fxRun: hasEffectiveCapability(
        institutionCapabilities,
        "fx",
        "confidential",
        "run",
      ),
      ftpAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "ftp",
        "aggregated",
        "view",
      ),
      ftpConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "ftp",
        "confidential",
        "view",
      ),
      ftpRun: hasEffectiveCapability(
        institutionCapabilities,
        "ftp",
        "confidential",
        "run",
      ),
      forecastingAggregatedView: hasEffectiveCapability(
        institutionCapabilities,
        "fcst",
        "aggregated",
        "view",
      ),
      forecastingConfidentialView: hasEffectiveCapability(
        institutionCapabilities,
        "fcst",
        "confidential",
        "view",
      ),
      forecastingRun: hasEffectiveCapability(
        institutionCapabilities,
        "fcst",
        "confidential",
        "run",
      ),
      isResolved: !banksQuery.isLoading && !profileQuery.isLoading,
    }),
    [
      bank,
      banksQuery.isLoading,
      institutionCapabilities,
      organizationCapabilities,
      profileQuery.isLoading,
    ],
  );

  const [selectedPeriodId, setSelectedPeriodId] = useState<string | null>(null);
  const period =
    periods.find((p) => p.id === selectedPeriodId) ?? periods[0] ?? null;

  const isLoading =
    banksQuery.isLoading ||
    profileQuery.isLoading ||
    (Boolean(bank) && periodsQuery.isLoading);
  const isEmpty =
    !banksQuery.isLoading &&
    !profileQuery.isLoading &&
    !banksQuery.error &&
    !profileQuery.error &&
    !bank;

  const value = useMemo<BankContextValue>(
    () => ({
      bank,
      institutionType,
      moduleScope,
      period,
      periods,
      setPeriodId: setSelectedPeriodId,
      isLoading,
      isEmpty,
    }),
    [bank, institutionType, moduleScope, period, periods, isLoading, isEmpty],
  );

  if (banksQuery.error || profileQuery.error) {
    const authorityError = banksQuery.error ?? profileQuery.error;
    const apiError = isApiError(authorityError) ? authorityError : null;
    // A 401 here is not an outage. Every grant, revocation or status change
    // bumps the holder's authorization version in the same transaction, which
    // is what makes new authority take effect at once and stops an old token
    // keeping the old view — so a routine grant lands the grantee here. The
    // service answered, promptly and correctly; the session is simply spent.
    //
    // Saying "unreachable" and offering "Retry" was wrong twice over: it named
    // an outage that had not happened, and the only control on the screen was
    // one that can never succeed, because no number of retries re-mints a
    // token. `access_changed` already existed as a sign-in reason and the sign
    // -in form already renders it; nothing had ever routed anyone to it.
    const staleSession = apiError?.status === 401;
    return (
      <FullScreenPanel
        title={
          staleSession ? "Your access has changed" : "Risk service unreachable"
        }
        description={
          staleSession
            ? (apiError?.message ??
              "Your permissions changed, so this session is out of date. Sign in again to pick them up.")
            : apiError
              ? (apiError.message ??
                "Effective authority is temporarily unavailable.")
              : "Could not resolve effective authority from the risk service."
        }
        action={
          staleSession ? (
            <a
              href={loginUrlWithReason("access_changed")}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              Sign in again
            </a>
          ) : (
            <button
              type="button"
              onClick={() => {
                void banksQuery.refetch();
                void profileQuery.refetch();
              }}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              Retry
            </button>
          )
        }
      />
    );
  }

  if (isEmpty) {
    return (
      <BankContext.Provider value={value}>{children}</BankContext.Provider>
    );
  }

  return <BankContext.Provider value={value}>{children}</BankContext.Provider>;
}

export function NoAuthorizedInstitutionsPanel() {
  return (
    <div className="px-8 py-8">
      <div className="card mx-auto max-w-2xl p-8 text-center">
        <div className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-full bg-surface text-slate">
          <Landmark size={20} aria-hidden />
        </div>
        <p className="mt-4 text-h2 text-navy">No authorized institutions yet</p>
        <p className="mt-2 text-body leading-relaxed text-slate">
          Ask your organization owner to grant access to an institution.
        </p>
      </div>
    </div>
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
