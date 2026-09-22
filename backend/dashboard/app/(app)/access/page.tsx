"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { accessIndexDestination } from "@/lib/api/accountAdministration";

/**
 * Lands administrators on the Members table and everyone else on My access;
 * the decision needs the projected authority, so it runs client-side.
 */
export default function AccessPage() {
  const router = useRouter();
  const { effectiveAuthority, isLoading } = useUserProfile();
  useEffect(() => {
    if (isLoading) return;
    router.replace(accessIndexDestination(effectiveAuthority));
  }, [effectiveAuthority, isLoading, router]);
  return null;
}
