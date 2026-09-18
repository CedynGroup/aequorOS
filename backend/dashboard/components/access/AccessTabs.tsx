"use client";

import { usePathname } from "next/navigation";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { hasAccountAdministrationAuthority } from "@/lib/api/accountAdministration";

const tabs = [
  { href: "/access/members", label: "Members", admin: true },
  { href: "/access/authentication", label: "Authentication", admin: true },
  {
    href: "/access/integration-keys",
    label: "Integration keys",
    admin: true,
  },
  { href: "/access/my-access", label: "My access", admin: false },
] as const;

export const ACCESS_ADMIN_REASON =
  "Requires Account · restricted · administer. Ask your organization owner or admin to grant it.";

export default function AccessTabs() {
  const pathname = usePathname();
  const { effectiveAuthority } = useUserProfile();
  const canAdminister = hasAccountAdministrationAuthority(effectiveAuthority);
  return (
    <nav
      aria-label="Access control sections"
      className="-mb-px flex gap-1 overflow-x-auto border-b border-border-light"
    >
      {tabs.map((tab) => {
        const active = pathname === tab.href;
        return (
          <PermissionLink
            key={tab.href}
            href={tab.href}
            reason={tab.admin && !canAdminister ? ACCESS_ADMIN_REASON : undefined}
            ariaLabel={tab.label}
            wrapperClassName="shrink-0"
            className={`inline-flex px-4 py-2.5 text-body font-medium border-b-2 whitespace-nowrap transition-colors ${
              active
                ? "border-action text-navy"
                : "border-transparent text-slate hover:border-border hover:text-navy"
            }`}
            disabledClassName="opacity-45 hover:border-transparent hover:text-slate"
          >
            {tab.label}
          </PermissionLink>
        );
      })}
    </nav>
  );
}
