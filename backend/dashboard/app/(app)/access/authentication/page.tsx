"use client";

import AuthenticationPanel from "@/components/settings/AuthenticationPanel";
import AccessAdminBoundary from "@/components/access/AccessAdminBoundary";

export default function AccessAuthenticationPage() {
  return (
    <AccessAdminBoundary>
      <AuthenticationPanel />
    </AccessAdminBoundary>
  );
}
