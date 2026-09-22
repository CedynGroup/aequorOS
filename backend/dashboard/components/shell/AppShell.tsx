"use client";

import { useState, useEffect } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import Header from "./Header";
import HistoricalPeriodRibbon from "./HistoricalPeriodRibbon";
import ModuleGuard from "./ModuleGuard";
import { NoAuthorizedInstitutionsPanel, useBankContext } from "./BankContext";
import {
  isAccessPath,
  isBaselineOnlyScope,
  isPersonalSettingsPath,
  moduleForPath,
} from "@/lib/modules";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();
  const { isEmpty, moduleScope } = useBankContext();

  // Close mobile menu on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Lock body scroll when mobile menu open
  useEffect(() => {
    if (mobileOpen) {
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = "";
      };
    }
  }, [mobileOpen]);

  return (
    <div className="min-h-screen">
      <div className="flex min-h-screen">
        {/* Sidebar — always visible on lg+, drawer on smaller */}
        <div className="hidden lg:block">
          <Sidebar />
        </div>

        {/* Mobile drawer */}
        {mobileOpen && (
          <div className="lg:hidden fixed inset-0 z-40 flex">
            <button
              type="button"
              aria-label="Close menu"
              onClick={() => setMobileOpen(false)}
              className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            />
            <div className="relative">
              <Sidebar />
            </div>
          </div>
        )}

        <div className="flex-1 min-w-0 flex flex-col">
          <Header onMobileMenu={() => setMobileOpen(true)} />
          <HistoricalPeriodRibbon />
          <main className="flex-1">
            <ModuleGuard>
              {isEmpty &&
              isBaselineOnlyScope(moduleScope) &&
              moduleForPath(pathname) !== null &&
              !isAccessPath(pathname) &&
              !isPersonalSettingsPath(pathname) ? (
                <NoAuthorizedInstitutionsPanel />
              ) : (
                children
              )}
            </ModuleGuard>
          </main>
        </div>
      </div>
    </div>
  );
}
