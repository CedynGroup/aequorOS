"use client";

/**
 * Regulatory Reporting → Settings chrome: the settings menu and the page
 * heading every settings page shares. "How this bank files" is one surface:
 * the filing profile, the submission channels, signing and deadlines.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { regShort } from "@/lib/format";

type MenuItem = { href: string; label: string; indent?: boolean };

const BASE = "/submissions/settings";

function menu(): MenuItem[] {
  return [
    { href: BASE, label: "Overview" },
    { href: `${BASE}/filing-profile`, label: "Filing profile" },
    {
      href: `${BASE}/reporting-gateway`,
      label: "AequorOS Reporting Gateway",
      indent: true,
    },
    { href: `${BASE}/orass`, label: "ORASS", indent: true },
    { href: `${BASE}/email`, label: "Email fallback", indent: true },
    { href: `${BASE}/manual`, label: "Manual record", indent: true },
    { href: `${BASE}/signing`, label: "Signing policies" },
    { href: `${BASE}/deadlines`, label: "Deadlines" },
  ];
}

export function SettingsNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Settings" className="lg:sticky lg:top-4">
      <ul className="flex flex-col gap-0.5">
        {menu().map((item, index) => {
          const current = pathname === item.href;
          const startsChannels = index === 2;
          return (
            <li key={item.href}>
              {startsChannels && (
                <p className="px-3 pt-3 pb-1 text-micro font-medium uppercase tracking-wider text-slate">
                  Submission channels
                </p>
              )}
              <Link
                href={item.href}
                aria-current={current ? "page" : undefined}
                className={`flex min-h-10 items-center rounded-sm px-3 text-body transition-colors ${
                  item.indent ? "pl-6" : ""
                } ${
                  current
                    ? "bg-surface-raised font-semibold text-navy"
                    : "text-slate hover:bg-surface-raised/60 hover:text-navy"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function SettingsHeading({
  title,
  section,
  subtitle,
  status,
  action,
}: {
  title: ReactNode;
  /** Breadcrumb section under Settings, e.g. "Submission channels". */
  section?: string;
  subtitle?: ReactNode;
  status?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 space-y-1.5">
        <p className="text-caption text-slate">
          <Link href={BASE} className="hover:text-navy underline-offset-2 hover:underline">
            Settings
          </Link>
          {section ? ` / ${section}` : null}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-h1 text-navy">{title}</h1>
          {status}
        </div>
        {subtitle && (
          <p className="max-w-3xl text-body text-slate leading-relaxed">{subtitle}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </header>
  );
}
