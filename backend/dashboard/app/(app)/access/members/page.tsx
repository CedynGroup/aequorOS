"use client";

import MembersPanel from "@/components/settings/MembersPanel";
import AccessAdminBoundary from "@/components/access/AccessAdminBoundary";

export default function AccessMembersPage() {
  return (
    <AccessAdminBoundary>
      <MembersPanel />
    </AccessAdminBoundary>
  );
}
