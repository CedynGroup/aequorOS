"use client";

import { Suspense, type ReactNode } from "react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useBankContext, useModuleScope } from "./BankContext";
import ModuleTabs, { type Tab } from "./ModuleTabs";
import { useReturnTemplates } from "@/lib/api/hooks";
import InstrumentStatusBadge from "@/components/submissions/InstrumentStatusBadge";

/** Draft capabilities are reached deliberately from the preparation workspace. */
type Props = {
  tabs: (Tab & { ruleCode?: string })[];
  children: ReactNode;
  contextRule?: string;
};

export default function RegulatoryModuleTabs(props: Props) {
  return (
    <Suspense>
      <ModulePreparationSurface {...props} />
    </Suspense>
  );
}

function ModulePreparationSurface({ tabs, children, contextRule }: Props) {
  const { bank } = useBankContext();
  const query = useReturnTemplates(bank?.id);
  const scope = useModuleScope();
  const ruleFor = (code: string | undefined) =>
    scope.institutionClass === "sdi"
      ? code === "LMT" || code === "LCR-NSFR"
        ? "SDI-LMT-MONTHLY"
        : code === "IRRBB-PILOT"
          ? "SDI-IRRBB-QUARTERLY"
          : code
      : code;
  const pathname = usePathname();
  const preparing = useSearchParams().get("preparing") === "1";
  const visible = tabs.filter(
    (tab) =>
      !tab.ruleCode ||
      query.data?.templates.some(
        (item) =>
          item.code === ruleFor(tab.ruleCode) &&
          item.instrumentStatus === "in_force",
      ) ||
      (preparing && pathname === tab.href),
  );
  const selected = tabs.find((tab) => tab.href === pathname && tab.ruleCode);
  const instrument = query.data?.templates.find(
    (item) => item.code === ruleFor(selected?.ruleCode ?? contextRule),
  );
  return (
    <>
      <ModuleTabs tabs={visible} />
      <div className="px-8 py-2 flex items-center gap-3 text-caption">
        {instrument && (
          <InstrumentStatusBadge
            status={instrument.instrumentStatus}
            effectiveFrom={instrument.effectiveFrom}
            citation={instrument.directiveCitation}
            sourceUrl={instrument.sourceUrl}
            code={instrument.code}
          />
        )}
        <Link className="text-action underline" href="/submissions/upcoming">
          Preparing for upcoming rules
        </Link>
      </div>
      {selected ? (
        <QueryBoundary
          contained
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        >
          {instrument?.instrumentStatus === "in_force" ||
          (preparing && instrument) ? (
            children
          ) : (
            <p className="px-8 py-6 text-body text-slate">
              This capability is available from Preparing for upcoming rules.
            </p>
          )}
        </QueryBoundary>
      ) : (
        children
      )}
    </>
  );
}
