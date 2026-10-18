"use client";

import { useBankContext } from "@/components/shell/BankContext";
import SectionCard from "@/components/ui/SectionCard";
import QueryBoundary from "@/components/ui/QueryBoundary";
import { PermissionLink } from "@/components/ui/DisabledWithReason";
import { useRegulatoryRuns, useScenarioCatalogue } from "@/lib/api/hooks";
import { hasEffectiveCapability, hrefAccess } from "@/lib/modules";
import { fmtDateUTC, labelize } from "@/lib/api/values";
import type {
  EffectiveCapabilityRead,
  WorkbenchModule,
} from "@aequoros/risk-service-api";
import FtpWhatIf from "@/components/ftp/FtpWhatIf";

function MetricValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") {
    return (
      <pre className="whitespace-pre-wrap break-words">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return String(value);
}

export const MODULE_NAMES: Record<WorkbenchModule, string> = {
  irr: "IRRBB",
  liquidity: "Liquidity",
  capital: "Capital",
  fx: "FX",
  ftp: "FTP",
};
export const AUTHORITY_MODULE: Record<
  WorkbenchModule,
  EffectiveCapabilityRead["module"]
> = { irr: "irrbb", liquidity: "liq", capital: "cap", fx: "fx", ftp: "ftp" };

/** Module definitions and stored scenario results; opening the page never runs an engine. */
export default function ScenarioWorkbench({
  module,
}: {
  module: WorkbenchModule;
}) {
  const { bank, period, institutionCapabilities, moduleScope } =
    useBankContext();
  const name = MODULE_NAMES[module];
  const confidential = hasEffectiveCapability(
    institutionCapabilities,
    AUTHORITY_MODULE[module],
    "confidential",
    "view",
  );
  const catalogue = useScenarioCatalogue(
    confidential ? bank?.id : undefined,
    module,
    period?.id,
  );
  const runs = useRegulatoryRuns(period ? bank?.id : undefined, {
    module: module === "irr" ? "irr" : module,
    reportingPeriodId: period?.id,
    limit: 100,
    allPages: true,
  });
  const enterprise = hrefAccess("/irr/enterprise-stress", moduleScope);
  // The API registry is newest first. Retain each scenario's latest stored result.
  const seen = new Set<string>();
  const latest = (runs.data?.runs ?? []).filter((run) => {
    if (seen.has(run.scenarioCode)) return false;
    seen.add(run.scenarioCode);
    return true;
  });
  return (
    <div className="space-y-6">
      <p className="text-body text-slate">
        {name} scenarios for{" "}
        {period
          ? `${period.label} · ${fmtDateUTC(period.periodEnd)}`
          : "the selected as-of period"}
        . Stored regulatory scenarios and exploratory what-if analyses are
        separate from current live results.
      </p>
      {enterprise.state !== "hidden" && (
        <PermissionLink
          href="/irr/enterprise-stress"
          className="text-action underline"
          reason={
            enterprise.state === "disabled" ? enterprise.reason : undefined
          }
        >
          Open enterprise stress workbench
        </PermissionLink>
      )}
      <SectionCard
        title={`${name} scenario definitions`}
        subtitle="System shocks come from the effective-dated parameter register. Custom scenarios belong to this module."
      >
        {!confidential ? (
          <p>
            Requires {name} · Confidential · View to read scenario definitions.
          </p>
        ) : (
          <QueryBoundary
            isLoading={catalogue.isLoading}
            error={catalogue.error}
            onRetry={() => catalogue.refetch()}
          >
            <ul className="divide-y divide-border-light">
              {catalogue.data?.scenarios
                .filter((entry) => !entry.isArchived)
                .map((entry) => (
                  <li key={`${entry.kind}:${entry.code}`} className="py-3">
                    <p className="font-medium">
                      {entry.name} ·{" "}
                      {entry.kind === "system" ? "Regulatory" : "Custom"} ·{" "}
                      {entry.configured ? "Configured" : "Parameters missing"}
                    </p>
                    <p className="text-caption text-slate">
                      {entry.description}
                    </p>
                    <dl className="text-caption">
                      {Object.entries(entry.shocks).map(([key, value]) => (
                        <div key={key}>
                          <dt className="inline">{labelize(key)}: </dt>
                          <dd className="inline">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  </li>
                ))}
            </ul>
          </QueryBoundary>
        )}
      </SectionCard>
      <SectionCard
        title={`${name} stored scenario results`}
        subtitle="Latest stored result per scenario in the selected period. Reading this registry creates no runs."
      >
        <QueryBoundary
          isLoading={runs.isLoading}
          error={runs.error}
          onRetry={() => runs.refetch()}
        >
          {latest.length === 0 ? (
            <p>No stored {name} scenario runs for this period.</p>
          ) : (
            <ul className="divide-y divide-border-light">
              {latest.map((run) => (
                <li key={run.id} className="py-3">
                  <p className="font-medium">
                    {labelize(run.scenarioCode)} · {run.status}
                  </p>
                  <p className="text-caption text-slate">
                    {run.periodLabel} · {fmtDateUTC(run.createdAt)} ·{" "}
                    {run.engineVersion}
                  </p>
                  {run.error && <p role="alert">{run.error.message}</p>}
                  <dl className="grid gap-2 sm:grid-cols-3 text-caption">
                    {Object.entries(run.metrics).map(
                      ([key, value]: [string, unknown]) => (
                        <div key={key} className="min-w-0">
                          <dt>{labelize(key.replace(/_ghs$/, ""))}</dt>
                          <dd className="font-mono">
                            <MetricValue value={value} />
                          </dd>
                        </div>
                      ),
                    )}
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </QueryBoundary>
      </SectionCard>
      {module === "ftp" && confidential && (
        <FtpWhatIf
          key={`${bank?.id}:${period?.id}`}
          catalogue={catalogue.data}
        />
      )}
    </div>
  );
}
