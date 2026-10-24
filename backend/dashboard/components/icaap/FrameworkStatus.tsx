"use client";

import {
  useIcaapFramework,
  type IcaapFrameworkSummaryRead,
} from "@/lib/api/icaap";
import { frameworkInstrumentStatus } from "@/lib/regulatoryInstruments";
import InstrumentStatusBadge from "@/components/submissions/InstrumentStatusBadge";
import QueryBoundary from "@/components/ui/QueryBoundary";

export default function FrameworkStatus({
  bankId,
  framework,
}: {
  bankId: string;
  framework: Pick<IcaapFrameworkSummaryRead, "code" | "version">;
}) {
  const query = useIcaapFramework(bankId, framework.code, framework.version);
  const evidence = query.data;
  const source = evidence?.documents[0] as Record<string, unknown> | undefined;
  return (
    <QueryBoundary
      contained
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => void query.refetch()}
    >
      {evidence && (
        <InstrumentStatusBadge
          status={frameworkInstrumentStatus(
            evidence.status,
            evidence.effectiveFrom,
          )}
          effectiveFrom={evidence.effectiveFrom}
          citation={evidence.title}
          sourceUrl={typeof source?.url === "string" ? source.url : null}
          regulator={evidence.regulator}
        />
      )}
    </QueryBoundary>
  );
}
