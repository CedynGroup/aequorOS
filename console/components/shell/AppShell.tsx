'use client';

import type { ReactNode } from 'react';
import Sidebar from './Sidebar';
import Header from './Header';
import ImpersonationBanner from './ImpersonationBanner';
import { ToastProvider } from '@/components/ui';
import { InspectorProvider } from '@/lib/inspector';

/**
 * The console application frame: grouped collapsible rail + top bar, with the
 * global ToastProvider and Inspector session context wrapping everything and
 * the un-dismissable ImpersonationBanner sitting directly under the header.
 *
 * The console is dark-only and desktop-first (staff tooling); there is no
 * theme provider and no mobile drawer.
 */
export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <InspectorProvider>
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Header />
            <ImpersonationBanner />
            <main className="min-w-0 flex-1 px-6 py-6">{children}</main>
          </div>
        </div>
      </InspectorProvider>
    </ToastProvider>
  );
}
