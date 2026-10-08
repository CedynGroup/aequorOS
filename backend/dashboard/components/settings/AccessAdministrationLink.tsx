"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { hasAccountAdministrationAuthority } from "@/lib/api/accountAdministration";

/**
 * Settings' pointer to the Access area, which owns members, sign-in and
 * integration keys. Shown only to those who can administer them; everyone else
 * reaches their own access from the sidebar.
 */
export default function AccessAdministrationLink() {
  const { effectiveAuthority } = useUserProfile();
  if (!hasAccountAdministrationAuthority(effectiveAuthority)) return null;
  return (
    <Link
      href="/access/members"
      className="inline-flex items-center gap-1.5 text-body font-medium text-action hover:underline"
    >
      Manage members and access <ArrowRight size={15} aria-hidden />
    </Link>
  );
}
