'use client';

/**
 * Route guard for institution-type module scoping (docs/sdi.md §3.1/§6.3).
 *
 * Hiding a module from the nav is not enough — a hidden route stays reachable by
 * URL. This guard 404s any path the active tenant's institution class is not
 * entitled to, so a savings-&-loans tenant that types `/fx` or `/basel/rwa`
 * gets not-found, not a bank-only screen. Unscoped tenants (banks) and the
 * pre-load window (module set not yet resolved) pass through unchanged.
 *
 * Two hubs are the exception: `/` is where every sign-in lands and `/settings`
 * is what people type for "my settings", so a user whose grants do not reach
 * them is sent to the first surface they are authorized to see instead of a
 * 404 (docs/rbac.md §8.3). On 2026-09-16 an Org Owner holding Account
 * administration alone signed in to production and met "This page could not
 * be found" — Settings was theirs, but nothing routed them there — and an
 * analyst could no longer open the page that shows their own signer ID.
 */

import { usePathname, notFound, redirect } from 'next/navigation';
import { useModuleScope } from './BankContext';
import { hubRedirectFor, isPathVisible } from '@/lib/modules';

export default function ModuleGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  if (!isPathVisible(pathname, moduleScope)) {
    const destination = hubRedirectFor(pathname, moduleScope);
    if (destination) redirect(destination);
    notFound();
  }
  return <>{children}</>;
}
