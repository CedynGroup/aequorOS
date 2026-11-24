"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type NavItem = { key: string; label: string; href: string; icon: string; badge?: number | null; badgeLabel?: string };
export type NavGroup = { label: string; items: NavItem[] };

function activeKey(pathname: string): string {
  if (pathname === "/") return "overview";
  const first = pathname.split("/")[1] ?? "";
  return { submissions: "inbox", queries: "queries", calendar: "calendar", institutions: "institutions", returns: "returns", onboarding: "onboarding", audit: "audit", access: "access" }[first] ?? "";
}

export function NavLinks({ groups }: { groups: NavGroup[] }) {
  const current = activeKey(usePathname());
  return (
    <>
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-0.5">
          <span id={`nav-${group.label}`} className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-nav-subtle">
            {group.label}
          </span>
          <ul aria-labelledby={`nav-${group.label}`} className="m-0 flex list-none flex-col gap-0.5 p-0">
            {group.items.map((item) => {
              const on = item.key === current;
              return (
                <li key={item.key}>
                  <Link
                    href={item.href}
                    aria-current={on ? "page" : undefined}
                    className={
                      "flex min-h-11 items-center gap-3 rounded-lg px-2.5 text-[14px] no-underline " +
                      (on ? "bg-nav-active font-semibold text-white hover:text-white" : "font-medium text-nav-text hover:bg-nav-card hover:text-white")
                    }
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d={item.icon} />
                    </svg>
                    <span className="flex-auto">{item.label}</span>
                    {item.badge ? (
                      <span className="rounded-full bg-nav-badge px-2 py-px text-[12px] font-semibold text-white">
                        {item.badge}
                        {item.badgeLabel && <span className="sr-only"> {item.badgeLabel}</span>}
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </>
  );
}
