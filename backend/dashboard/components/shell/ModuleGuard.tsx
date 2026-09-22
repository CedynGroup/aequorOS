"use client";

/**
 * Route guard for institution-type module scoping (docs/sdi.md §3.1/§6.3).
 *
 * Hiding a module from the nav is not enough — a hidden route stays reachable by
 * URL. This guard 404s any path the active tenant's institution class is not
 * entitled to, so a savings-&-loans tenant that types `/fx` or `/basel/rwa`
 * gets not-found, not a bank-only screen. Unscoped tenants (banks) and the
 * pre-load window (module set not yet resolved) pass through unchanged.
 *
 * Hub redirects and the baseline-only public-route allow-list are owned by
 * docs/rbac.md §8.2 and lib/modules.ts. Unknown, structurally excluded, and
 * hidden object-specific paths still resolve as not-found.
 */

import { usePathname, notFound, redirect } from "next/navigation";
import { useModuleScope } from "./BankContext";
import { forecastingWorkspaceAccess, hubRedirectFor, isPathVisible } from "@/lib/modules";

import ModuleTabs from "./ModuleTabs";
import { forecastingTabs } from "@/components/forecasting/tabs";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";

export default function ModuleGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  const forecastingAccess = forecastingWorkspaceAccess(pathname, moduleScope);
  if (forecastingAccess && !moduleScope.isResolved) return null;
  if (forecastingAccess?.state === "disabled") {
    return (
      <>
        <ModuleTabs tabs={forecastingTabs} />
        <section className="px-8 py-6 space-y-4" aria-label="Forecasting workspace">
          <h1 className="text-heading font-semibold">Forecasting</h1>
          <DisabledWithReason reason={forecastingAccess.reason}>
            <button type="button" disabled className="btn-primary px-4 py-2">
              View Forecasting
            </button>
          </DisabledWithReason>
          <p className="text-body text-slate">{forecastingAccess.reason}</p>
        </section>
      </>
    );
  }
  if (!isPathVisible(pathname, moduleScope)) {
    const destination = hubRedirectFor(pathname, moduleScope);
    if (destination) redirect(destination);
    notFound();
  }
  return <>{children}</>;
}
