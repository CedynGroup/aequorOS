'use client';

/**
 * Route guard for institution-type module scoping (docs/sdi.md §3.1/§6.3).
 *
 * Hiding a module from the nav is not enough — a hidden route stays reachable by
 * URL. This guard 404s any path the active tenant's institution class is not
 * entitled to, so a savings-&-loans tenant that types `/fx` or `/basel/rwa`
 * gets not-found, not a bank-only screen. Unscoped tenants (banks) and the
 * pre-load window (module set not yet resolved) pass through unchanged.
 */

import { usePathname, notFound } from 'next/navigation';
import { useModuleScope } from './BankContext';
import { isPathVisible } from '@/lib/modules';

export default function ModuleGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  if (!isPathVisible(pathname, moduleScope)) {
    notFound();
  }
  return <>{children}</>;
}
