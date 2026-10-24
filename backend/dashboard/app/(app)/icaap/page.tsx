"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import {
  preparationHref,
  frameworkInstrumentStatus,
} from "@/lib/regulatoryInstruments";

/**
 * The ICAAP workspace: the institution's assessment cycles.
 *
 * Visibility is decided in `lib/modules.ts`, not here — the route is hidden
 * (and 404s) without the deployment flag, without CAP/confidential view, and
 * for an SDI tenant, which has no Pillar 2 regime.
 */

import { useRouter } from "next/navigation";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { useBankContext, useModuleScope } from "@/components/shell/BankContext";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { useIcaapFrameworks } from "@/lib/api/icaap";
import { hrefAccess } from "@/lib/modules";
import Link from "next/link";
import FrameworkStatus from "@/components/icaap/FrameworkStatus";
import CycleList from "@/components/icaap/CycleList";
import { regShort } from "@/lib/format";

export default function IcaapPage() {
  return (
    <Suspense>
      <IcaapWorkspace />
    </Suspense>
  );
}

function IcaapWorkspace() {
  const preparing = useSearchParams().get("preparing") === "1";
  const { bank } = useBankContext();
  const router = useRouter();
  const scope = useModuleScope();
  const authorized = hrefAccess("/icaap", scope).state === "enabled";
  const query = useIcaapFrameworks(authorized ? bank?.id : undefined);
  const frameworks = (query.data?.frameworks ?? []).filter(
    (framework) =>
      preparing ||
      frameworkInstrumentStatus(framework.status, framework.effectiveFrom) ===
        "in_force",
  );

  return (
    <>
      <PageHeader
        eyebrow="ICAAP"
        title="Internal Capital Adequacy Assessment"
        subtitle={`The institution's own assessment of the capital it needs, and the evidence behind it, as filed with ${regShort()}.`}
      />
      <QueryBoundary
        contained
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
      >
        <PageContainer className="py-6">
          <Link
            className="text-caption text-action underline"
            href="/submissions/upcoming"
          >
            Preparing for upcoming rules
          </Link>
          {bank && authorized && frameworks.length > 0 ? (
            <>
              <div className="my-3 flex flex-wrap gap-3">
                {frameworks.map((framework) => (
                  <FrameworkStatus
                    key={`${framework.code}|${framework.version}`}
                    bankId={bank.id}
                    framework={framework}
                  />
                ))}
              </div>
              <CycleList
                bankId={bank.id}
                frameworks={frameworks}
                onCreated={(cycleId) =>
                  router.push(
                    preparationHref(`/icaap/${cycleId}/overview`, preparing),
                  )
                }
              />
            </>
          ) : (
            <p className="py-6 text-body text-slate">
              This capability is available from Preparing for upcoming rules.
            </p>
          )}
        </PageContainer>
      </QueryBoundary>
    </>
  );
}
