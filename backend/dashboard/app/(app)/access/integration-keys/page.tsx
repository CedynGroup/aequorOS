"use client";

import { IntegrationKeysPanel } from "@/components/data-engine/ApiPushGuide";
import { Card, CardBody } from "@/components/ui/Card";
import AccessAdminBoundary from "@/components/access/AccessAdminBoundary";

export default function AccessIntegrationKeysPage() {
  return (
    <AccessAdminBoundary>
      <Card>
        <CardBody className="p-5">
          <IntegrationKeysPanel />
        </CardBody>
      </Card>
    </AccessAdminBoundary>
  );
}
