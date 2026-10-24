"use client";

import { Suspense, type ReactNode } from "react";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useBankContext, useModuleScope } from "./BankContext";
import ModuleTabs, { type Tab } from "./ModuleTabs";
import { useReturnTemplates } from "@/lib/api/hooks";
import { hrefAccess } from "@/lib/modules";
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
  const pathname = usePathname();
  const preparing = useSearchParams().get("preparing") === "1";
  const ruleFor = (code: string | undefined) =>
    scope.institutionClass === "sdi" && code === "LMT"
      ? "SDI-LMT-MONTHLY"
      : code;
  const moduleRule =
    scope.institutionClass === "sdi" && contextRule === "IRRBB-PILOT"
      ? "SDI-IRRBB-QUARTERLY"
      : contextRule;
  const instrumentFor = (code: string | undefined) =>
    query.data?.templates.find((item) => item.code === ruleFor(code));
  const available = (code: string | undefined) => {
    if (!code) return true;
    const instrument = instrumentFor(code);
    return Boolean(
      instrument &&
      (instrument.instrumentStatus === "in_force" ||
        instrument.instrumentStatus === "unpublished" ||
        preparing),
    );
  };
  const visible = tabs
    .filter((tab) => available(tab.ruleCode ?? moduleRule))
    .map((tab) => ({
      ...tab,
      href: preparing ? `${tab.href}?preparing=1` : tab.href,
    }));
  const selected = tabs.find((tab) => tab.href === pathname);
  const selectedRule = selected?.ruleCode ?? moduleRule;
  const instrument = instrumentFor(selectedRule);
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
      {selectedRule ? (
        <QueryBoundary
          contained
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        >
          {available(selectedRule) &&
          hrefAccess(pathname, scope).state === "enabled" ? (
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
