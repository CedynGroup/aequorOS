import type { CapitalLineRead } from "@aequoros/risk-service-api";
import { useQuery } from "@tanstack/react-query";
import { Fragment } from "react";

import { Panel, PanelHeader, Skeleton } from "../../components/ui";
import { riskApi } from "../../lib/api";
import { truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { NoBaselineRunBoundary } from "./rwa-tab";
import { DenseTable, NumCell, Td, Th } from "./shared/dense-table";
import {
  formatMoneyCompact,
  formatMoneyFull,
  formatPct,
  formatPp,
} from "./shared/format";
import { useRunBaseline } from "./shared/regulatory";

export function CapitalStructureTab({ tenant, bank, period }: AlmTabProps) {
  const structure = useQuery({
    queryKey: ["alm-capital-structure", tenant, bank.id, period.id],
    queryFn: () => riskApi.getCapitalStructure(tenant, bank.id, period.id),
    retry: false,
  });
  const dashboard = useQuery({
    queryKey: ["alm-capital-dashboard", tenant, bank.id, period.id],
    queryFn: () => riskApi.getCapitalDashboard(tenant, bank.id, period.id),
  });
  const runBaseline = useRunBaseline(tenant, bank.id, period.id, "capital");

  if (structure.isLoading) {
    return (
      <div aria-label="Loading capital structure" className="space-y-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (structure.error) {
    return (
      <NoBaselineRunBoundary
        error={structure.error}
        title="Capital structure requires a baseline capital run"
        runBaseline={runBaseline}
      />
    );
  }
  if (!structure.data) return null;

  const data = structure.data;
  const metrics = dashboard.data?.metrics;
  const gpCapNote = dashboard.data?.validations.find(
    (validation) => validation.ruleCode === "tier2_gp_cap_applied",
  );
  const at1Pp =
    metrics === undefined
      ? null
      : Number(metrics.tier1RatioPct) - Number(metrics.cet1RatioPct);
  const tier2Pp =
    metrics === undefined
      ? null
      : Number(metrics.carPct) - Number(metrics.tier1RatioPct);

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Capital structure"
          meta={`Qualifying capital by tier · run ${truncateId(data.runId)}`}
        />
        <div className="p-3">
          <DenseTable ariaLabel="Capital structure by tier">
            <thead>
              <tr>
                <Th>Line</Th>
                <Th>Component</Th>
                <Th align="right">Amount</Th>
                <Th align="right">% of RWA</Th>
              </tr>
            </thead>
            <tbody>
              <ComponentRows lines={data.cet1Components} currency={bank.currency} />
              <ComponentRows
                lines={data.cet1Deductions}
                currency={bank.currency}
                tone="danger"
              />
              <SubtotalRow
                label="CET1 capital"
                value={data.cet1CapitalGhs}
                currency={bank.currency}
                ratio={metrics ? formatPct(metrics.cet1RatioPct) : "—"}
              />
              <ComponentRows lines={data.at1Components} currency={bank.currency} />
              <SubtotalRow
                label="Additional Tier 1 capital"
                value={data.at1CapitalGhs}
                currency={bank.currency}
                ratio={
                  at1Pp !== null && Number.isFinite(at1Pp)
                    ? formatPp(at1Pp).replace("+", "")
                    : "—"
                }
              />
              <SubtotalRow
                label="Tier 1 capital"
                value={data.tier1CapitalGhs}
                currency={bank.currency}
                ratio={metrics ? formatPct(metrics.tier1RatioPct) : "—"}
              />
              <ComponentRows lines={data.tier2Components} currency={bank.currency} />
              <SubtotalRow
                label="Tier 2 capital"
                value={data.tier2CapitalGhs}
                currency={bank.currency}
                ratio={
                  tier2Pp !== null && Number.isFinite(tier2Pp)
                    ? formatPp(tier2Pp).replace("+", "")
                    : "—"
                }
              />
              <SubtotalRow
                label="Total qualifying capital"
                value={data.totalCapitalGhs}
                currency={bank.currency}
                ratio={metrics ? formatPct(metrics.carPct) : "—"}
                emphasis
              />
            </tbody>
          </DenseTable>
          <p className="mt-2 text-[10px] text-[rgb(var(--muted-foreground))]">
            CET1, Tier 1, and total ratios come from the capital dashboard; AT1
            and Tier 2 columns show the pp contribution between those API
            ratios.
          </p>
        </div>
      </Panel>
      {gpCapNote ? (
        <Panel>
          <PanelHeader title="Tier 2 general provisions cap" />
          <div className="px-3 py-2 text-xs text-[rgb(var(--muted-foreground))]">
            {gpCapNote.message}
          </div>
        </Panel>
      ) : null}
      {dashboard.error ? <ErrorPanel error={dashboard.error} /> : null}
    </div>
  );
}

function ComponentRows({
  lines,
  currency,
  tone = "default",
}: {
  lines: CapitalLineRead[];
  currency: string;
  tone?: "default" | "danger";
}) {
  return (
    <Fragment>
      {lines.map((line) => (
        <tr key={line.lineCode}>
          <Td mono tone="muted">
            {line.lineCode}
          </Td>
          <Td tone={tone === "danger" ? "danger" : "default"}>
            {line.description}
          </Td>
          <NumCell
            value={formatMoneyCompact(line.weightedAmount, currency)}
            title={formatMoneyFull(line.weightedAmount, currency)}
            tone={tone === "danger" ? "danger" : "default"}
          />
          <Td />
        </tr>
      ))}
    </Fragment>
  );
}

function SubtotalRow({
  label,
  value,
  currency,
  ratio,
  emphasis = false,
}: {
  label: string;
  value: string;
  currency: string;
  ratio: string;
  emphasis?: boolean;
}) {
  return (
    <tr className="bg-[rgb(var(--surface-2))]">
      <Td />
      <Td emphasis>{label}</Td>
      <NumCell
        value={formatMoneyCompact(value, currency)}
        title={formatMoneyFull(value, currency)}
        emphasis
      />
      <NumCell value={ratio} emphasis={emphasis} />
    </tr>
  );
}
