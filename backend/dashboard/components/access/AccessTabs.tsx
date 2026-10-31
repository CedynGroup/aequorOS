"use client";

import { usePathname } from "next/navigation";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  hasAccountAdministrationAuthority,
  hasGrantAdministrationAuthority,
} from "@/lib/api/accountAdministration";

const tabs = [
  { href: "/access/members", label: "Members", authority: "owner" },
  {
    href: "/access/authentication",
    label: "Authentication",
    authority: "admin",
  },
  {
    href: "/access/integration-keys",
    label: "Integration keys",
    authority: "admin",
  },
  { href: "/access/my-access", label: "My access", authority: "self" },
] as const;

export const ACCESS_ADMIN_REASON =
  "Requires Account · restricted · administer. Ask your organization owner or admin to grant it.";

export const MEMBERS_OWNER_REASON =
  "Requires Organization Owner authority. Delegated administrators can use Authentication and Integration keys.";

export default function AccessTabs() {
  const pathname = usePathname();
  const { effectiveAuthority } = useUserProfile();
  const canAdminister = hasAccountAdministrationAuthority(effectiveAuthority);
  const canManageGrants = hasGrantAdministrationAuthority(effectiveAuthority);
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
            reason={
              tab.authority === "owner" && !canManageGrants
                ? MEMBERS_OWNER_REASON
                : tab.authority === "admin" && !canAdminister
                  ? ACCESS_ADMIN_REASON
                  : undefined
            }
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
