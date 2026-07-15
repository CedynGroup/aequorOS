import type { CapitalLineRead } from "@aequoros/risk-service-api";
import { useQuery } from "@tanstack/react-query";
import { Loader2, PlayCircle } from "lucide-react";

import {
  Alert,
  Button,
  Panel,
  PanelHeader,
  Skeleton,
} from "../../components/ui";
import { apiErrorDetail, riskApi } from "../../lib/api";
import { truncateId } from "../../lib/utils";
import { ErrorPanel } from "../../shared/route-ui";
import type { AlmTabProps } from "./alm-console";
import { DenseTable, NumCell, Td, Th } from "./shared/dense-table";
import {
  formatMoneyCompact,
  formatMoneyFull,
  formatPct,
} from "./shared/format";
import { useRunBaseline } from "./shared/regulatory";

export function RwaTab({ tenant, bank, period }: AlmTabProps) {
  const breakdown = useQuery({
    queryKey: ["alm-rwa-breakdown", tenant, bank.id, period.id],
    queryFn: () => riskApi.getRwaBreakdown(tenant, bank.id, period.id),
    retry: false,
  });
  const runBaseline = useRunBaseline(tenant, bank.id, period.id, "capital");

  if (breakdown.isLoading) {
    return (
      <div aria-label="Loading RWA breakdown" className="space-y-3">
        <Skeleton className="h-32" />
        <Skeleton className="h-52" />
      </div>
    );
  }
  if (breakdown.error) {
    return (
      <NoBaselineRunBoundary
        error={breakdown.error}
        title="RWA breakdown requires a baseline capital run"
        runBaseline={runBaseline}
      />
    );
  }
  if (!breakdown.data) return null;

  const data = breakdown.data;

  return (
    <div className="space-y-3">
      <Panel>
        <PanelHeader
          title="Credit risk RWA"
          meta={`Standardized approach · run ${truncateId(data.runId)}`}
        />
        <div className="p-3">
          <RwaLineTable
            ariaLabel="Credit RWA line items"
            lines={data.creditLines}
            currency={bank.currency}
            totalLabel="Total credit RWA"
            totalValue={data.creditRwaGhs}
          />
        </div>
      </Panel>
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel>
          <PanelHeader
            title="Market risk RWA"
            meta="Net open position · 8% charge × 12.5"
          />
          <div className="p-3">
            <RwaLineTable
              ariaLabel="Market RWA line items"
              lines={data.marketLines}
              currency={bank.currency}
              totalLabel="Total market RWA"
              totalValue={data.marketRwaGhs}
            />
          </div>
        </Panel>
        <Panel>
          <PanelHeader
            title="Operational risk RWA"
            meta="Basic indicator approach over three gross income years"
          />
          <div className="p-3">
            <RwaLineTable
              ariaLabel="Operational RWA line items"
              lines={data.operationalLines}
              currency={bank.currency}
              totalLabel="Total operational RWA"
              totalValue={data.operationalRwaGhs}
            />
          </div>
        </Panel>
      </div>
      <Panel>
        <PanelHeader title="RWA totals" meta="Ties to the capital dashboard total" />
        <div className="p-3">
          <DenseTable ariaLabel="RWA totals">
            <tbody>
              <TotalsRow
                label="Credit RWA"
                value={data.creditRwaGhs}
                currency={bank.currency}
              />
              <TotalsRow
                label="Market RWA"
                value={data.marketRwaGhs}
                currency={bank.currency}
              />
              <TotalsRow
                label="Operational RWA"
                value={data.operationalRwaGhs}
                currency={bank.currency}
              />
              <TotalsRow
                label="Total RWA"
                value={data.totalRwaGhs}
                currency={bank.currency}
                emphasis
              />
            </tbody>
          </DenseTable>
        </div>
      </Panel>
    </div>
  );
}

export function NoBaselineRunBoundary({
  error,
  title,
  runBaseline,
}: {
  error: unknown;
  title: string;
  runBaseline: { isPending: boolean; mutate: () => void };
}) {
  const detail = apiErrorDetail(error);
  if (detail?.statusCode !== 409 || detail.code !== "no_baseline_run") {
    return <ErrorPanel error={error} />;
  }
  return (
    <Panel>
      <PanelHeader title={title} />
      <div className="space-y-3 p-4">
        <Alert title="No baseline run for this reporting period">
          {detail.message}
        </Alert>
        <Button
          size="sm"
          disabled={runBaseline.isPending}
          onClick={() => runBaseline.mutate()}
        >
          {runBaseline.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <PlayCircle className="size-3.5" />
          )}
          Run baseline
        </Button>
      </div>
    </Panel>
  );
}

export function RwaLineTable({
  ariaLabel,
  lines,
  currency,
  totalLabel,
  totalValue,
}: {
  ariaLabel: string;
  lines: CapitalLineRead[];
  currency: string;
  totalLabel: string;
  totalValue: string;
}) {
  return (
    <DenseTable ariaLabel={ariaLabel}>
      <thead>
        <tr>
          <Th>Line</Th>
          <Th>Exposure line</Th>
          <Th align="right">Exposure</Th>
          <Th align="right">Weight %</Th>
          <Th align="right">RWA</Th>
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <tr key={line.lineCode}>
            <Td mono tone="muted">
              {line.lineCode}
            </Td>
            <Td>{line.description}</Td>
            <NumCell
              value={
                line.exposureAmount === null
                  ? "—"
                  : formatMoneyCompact(line.exposureAmount, currency)
              }
              title={
                line.exposureAmount === null
                  ? undefined
                  : formatMoneyFull(line.exposureAmount, currency)
              }
            />
            <NumCell
              value={line.ratePct === null ? "—" : formatPct(line.ratePct)}
            />
            <NumCell
              value={formatMoneyCompact(line.weightedAmount, currency)}
              title={formatMoneyFull(line.weightedAmount, currency)}
            />
          </tr>
        ))}
        {!lines.length ? (
          <tr>
            <Td colSpan={5} tone="muted">
              No line items in this section.
            </Td>
          </tr>
        ) : null}
        <tr>
          <Td colSpan={4} emphasis>
            {totalLabel}
          </Td>
          <NumCell
            value={formatMoneyCompact(totalValue, currency)}
            title={formatMoneyFull(totalValue, currency)}
            emphasis
          />
        </tr>
      </tbody>
    </DenseTable>
  );
}

function TotalsRow({
  label,
  value,
  currency,
  emphasis = false,
}: {
  label: string;
  value: string;
  currency: string;
  emphasis?: boolean;
}) {
  return (
    <tr>
      <Td emphasis={emphasis}>{label}</Td>
      <NumCell
        value={formatMoneyCompact(value, currency)}
        title={formatMoneyFull(value, currency)}
        emphasis={emphasis}
      />
    </tr>
  );
}
