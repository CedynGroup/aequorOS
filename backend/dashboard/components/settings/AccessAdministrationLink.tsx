"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { hasAccountAdministrationAuthority } from "@/lib/api/accountAdministration";

/**
 * Settings' pointer to the Access area, which owns members, sign-in and
 * integration keys. Lands on integration keys, which uses the same Account
 * administration authority; member grants require Organization Owner authority.
 */
export default function AccessAdministrationLink() {
  const { effectiveAuthority } = useUserProfile();
  if (!hasAccountAdministrationAuthority(effectiveAuthority)) return null;
  return (
    <Link
      href="/access/integration-keys"
      className="inline-flex items-center gap-1.5 text-body font-medium text-action hover:underline"
    >
      Manage integration keys and access <ArrowRight size={15} aria-hidden />
    </Link>
  );
}
