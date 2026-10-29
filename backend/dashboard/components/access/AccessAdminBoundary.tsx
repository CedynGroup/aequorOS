"use client";

import { Card, CardBody } from "@/components/ui/Card";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import {
  hasAccountAdministrationAuthority,
  hasGrantAdministrationAuthority,
} from "@/lib/api/accountAdministration";
import { ACCESS_ADMIN_REASON, MEMBERS_OWNER_REASON } from "./AccessTabs";

export default function AccessAdminBoundary({
  children,
  requireOwner = false,
}: {
  children: React.ReactNode;
  requireOwner?: boolean;
}) {
  const { effectiveAuthority, isLoading } = useUserProfile();
  if (isLoading) return null;
  const allowed = requireOwner
    ? hasGrantAdministrationAuthority(effectiveAuthority)
    : hasAccountAdministrationAuthority(effectiveAuthority);
  if (allowed) {
    return <>{children}</>;
  }
  return (
    <Card>
      <CardBody className="p-6">
        <DisabledWithReason
          reason={requireOwner ? MEMBERS_OWNER_REASON : ACCESS_ADMIN_REASON}
        >
          <p className="text-body text-slate">
            {requireOwner
              ? "Only organization owners can manage members and grants."
              : "This section is available to organization owners and administrators."}
          </p>
        </DisabledWithReason>
      </CardBody>
    </Card>
  );
}
