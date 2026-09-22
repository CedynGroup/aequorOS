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
import AccessDeniedPage from "@/components/access/AccessDeniedPage";
import {
  accessDeniedForPath,
  hubRedirectFor,
  isPathVisible,
} from "@/lib/modules";

export default function ModuleGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  if (!isPathVisible(pathname, moduleScope)) {
    const destination = hubRedirectFor(pathname, moduleScope);
    if (destination) redirect(destination);
    const denied = accessDeniedForPath(pathname, moduleScope);
    if (denied) return <AccessDeniedPage denied={denied} route={pathname} />;
    notFound();
  }
  return <>{children}</>;
}
