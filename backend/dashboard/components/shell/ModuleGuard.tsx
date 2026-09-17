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
 * Hub URLs and public product-module paths redirect when an active member has
 * no institution authority. Unknown paths and hidden object-specific routes
 * still resolve as not-found.
 */

import { usePathname, notFound, redirect } from "next/navigation";
import { useModuleScope } from "./BankContext";
import { hubRedirectFor, isPathVisible } from "@/lib/modules";

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
    notFound();
  }
  return <>{children}</>;
}
