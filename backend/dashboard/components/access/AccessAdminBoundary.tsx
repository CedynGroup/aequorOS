"use client";

import { Card, CardBody } from "@/components/ui/Card";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { hasAccountAdministrationAuthority } from "@/lib/api/accountAdministration";
import { ACCESS_ADMIN_REASON } from "./AccessTabs";

export default function AccessAdminBoundary({
  children,
}: {
  children: React.ReactNode;
}) {
  const { effectiveAuthority, isLoading } = useUserProfile();
  if (isLoading) return null;
  if (hasAccountAdministrationAuthority(effectiveAuthority)) {
    return <>{children}</>;
  }
  return (
    <Card>
      <CardBody className="p-6">
        <DisabledWithReason reason={ACCESS_ADMIN_REASON}>
          <p className="text-body text-slate">
            This section is available to organization owners and administrators.
          </p>
        </DisabledWithReason>
      </CardBody>
    </Card>
  );
}
