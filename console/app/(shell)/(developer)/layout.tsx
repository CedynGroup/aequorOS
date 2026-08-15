import type { ReactNode } from 'react';

/**
 * Developer route group (Tenants / Onboard / Operations). Navigation now lives
 * in the AppShell rail (the "Tenants" group), so this layout is a pass-through
 * — the old horizontal top-tab bar was removed to avoid a second nav row.
 */
export default function DeveloperLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
