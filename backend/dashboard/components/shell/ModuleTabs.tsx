"use client";

import { usePathname } from "next/navigation";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import { hrefAccess } from "@/lib/modules";
import { useModuleScope } from "./BankContext";

export type Tab = { href: string; label: string };

export default function ModuleTabs({ tabs }: { tabs: Tab[] }) {
  const pathname = usePathname();
  const moduleScope = useModuleScope();
  // Scope the sub-tabs to the institution type (docs/sdi.md §3.2/§6.3): an SDI
  // tenant drops Basel-only liquidity tabs (Buffer/NSFR) and the full Basel
  // stack, keeping only its in-scope sections. Banks keep every tab.
  const visibleTabs = tabs
    .map((tab) => ({ ...tab, access: hrefAccess(tab.href, moduleScope) }))
    .filter((tab) => tab.access.state !== "hidden");
  return (
    <div className="bg-surface-raised border-b border-border-light px-8">
      <nav
        className="-mb-px flex gap-1 overflow-x-auto"
        aria-label="Module sections"
      >
        {visibleTabs.map((t) => {
          const active =
            t.href === pathname ||
            (t.href !== "/" && pathname.startsWith(t.href + "/")) ||
            (t.href.split("/").length > 2 && pathname === t.href);
          // Refined active match: exact match or first path-segment-after-href is empty
          const isActive = pathname === t.href;
          const reason =
            t.access.state === "disabled" ? t.access.reason : undefined;
          return (
            <PermissionLink
              key={t.href}
              href={t.href}
              reason={reason}
              className={`px-4 py-3 text-body font-medium border-b-2 whitespace-nowrap transition-colors ${
                isActive
                  ? "border-action text-navy"
                  : "border-transparent text-slate hover:text-navy hover:border-border"
              }`}
              disabledClassName="border-transparent text-slate-light hover:text-slate-light hover:border-transparent"
            >
              {t.label}
            </PermissionLink>
          );
        })}
      </nav>
    </div>
  );
}
