import type { ReactNode } from 'react';

/**
 * Admin route group (Operators / Audit Log / Tenant Inspector). Navigation
 * lives in the AppShell rail ("Admin" group); this layout is a pass-through.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
