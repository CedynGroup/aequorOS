import type {
  Bsd2PreviewRead,
  Bsd2RowRead,
  Bsd2SummaryRowRead,
  Bsd2WeightedRowRead,
  Bsd3PreviewRead,
} from "@aequoros/risk-service-api";
import { useQuery } from "@tanstack/react-query";
import { FileCheck2 } from "lucide-react";
import { useState } from "react";

import { Badge, Skeleton } from "../../components/ui";
import { apiErrorDetail, riskApi } from "../../lib/api";
import { cn } from "../../lib/utils";
import type { AlmTabProps } from "./alm-console";
import { NoBaselineRunBoundary } from "./rwa-tab";
import { DenseTable, NumCell, Td, Th } from "./shared/dense-table";
import { formatMoneyFull, formatPct } from "./shared/format";
import { useRunBaseline } from "./shared/regulatory";
import { PreviewFrame, PreviewSection } from "./shared/preview-frame";
import { ValidationList, type ValidationRow } from "./shared/validation-list";

type SubmissionForm = "bsd3" | "bsd2";

const formCopy: Record<
  SubmissionForm,
  { code: string; title: string; regulator: string }
> = {
  bsd3: { code: "BSD-3", title: "Liquidity Return", regulator: "Bank of Ghana" },
  bsd2: {
    code: "BSD-2",
    title: "Capital Adequacy Return",
    regulator: "Bank of Ghana",
  },
};

export function SubmissionsTab({ tenant, bank, period }: AlmTabProps) {
  const [selected, setSelected] = useState<SubmissionForm>("bsd3");
  const bsd3 = useQuery({
    queryKey: ["alm-bsd3", tenant, bank.id, period.id],
    queryFn: () => riskApi.getBsd3Preview(tenant, bank.id, period.id),
    retry: false,
  });
  const bsd2 = useQuery({
    queryKey: ["alm-bsd2", tenant, bank.id, period.id],
    queryFn: () => riskApi.getBsd2Preview(tenant, bank.id, period.id),
    retry: false,
  });
  const runLiquidityBaseline = useRunBaseline(
    tenant,
    bank.id,
    period.id,
    "liquidity",
  );
  const runCapitalBaseline = useRunBaseline(
    tenant,
    bank.id,
    period.id,
    "capital",
  );

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <SubmissionCard
          form="bsd3"
          periodLabel={period.label}
          selected={selected === "bsd3"}
          onSelect={() => setSelected("bsd3")}
          isLoading={bsd3.isLoading}
          error={bsd3.error}
          validations={bsd3.data?.validations}
          header={bsd3.data?.header}
        />
        <SubmissionCard
          form="bsd2"
          periodLabel={period.label}
          selected={selected === "bsd2"}
          onSelect={() => setSelected("bsd2")}
          isLoading={bsd2.isLoading}
          error={bsd2.error}
          validations={bsd2.data?.validations}
          header={bsd2.data?.header}
        />
      </div>
      {selected === "bsd3" ? (
        bsd3.isLoading ? (
          <Skeleton aria-label="Loading BSD-3 preview" className="h-96" />
        ) : bsd3.error ? (
          <NoBaselineRunBoundary
            error={bsd3.error}
            title="Run baseline calculations first"
            runBaseline={runLiquidityBaseline}
          />
        ) : bsd3.data ? (
          <Bsd3Document preview={bsd3.data} />
        ) : null
      ) : bsd2.isLoading ? (
        <Skeleton aria-label="Loading BSD-2 preview" className="h-96" />
      ) : bsd2.error ? (
        <NoBaselineRunBoundary
          error={bsd2.error}
          title="Run baseline calculations first"
          runBaseline={runCapitalBaseline}
        />
      ) : bsd2.data ? (
        <Bsd2Document preview={bsd2.data} />
      ) : null}
    </div>
  );
}

function SubmissionCard({
  form,
  periodLabel,
  selected,
  onSelect,
  isLoading,
  error,
  validations,
  header,
}: {
  form: SubmissionForm;
  periodLabel: string;
  selected: boolean;
  onSelect: () => void;
  isLoading: boolean;
  error: unknown;
  validations?: ValidationRow[];
  header?: { formCode: string; formTitle: string; regulator: string };
}) {
  const copy = formCopy[form];
  const failed = validations?.filter((validation) => !validation.passed) ?? [];
  const detail = apiErrorDetail(error);
  return (
    <button
      onClick={onSelect}
      className={cn(
        "rounded-md border border-[rgb(var(--border))] bg-[rgb(var(--surface))] p-3 text-left hover:bg-[rgb(var(--muted))]",
        selected && "border-[rgb(var(--primary))]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-2">
          <FileCheck2 className="size-4 text-[rgb(var(--muted-foreground))]" />
          <span className="font-mono text-sm font-semibold">
            {header?.formCode ?? copy.code}
          </span>
        </span>
        {isLoading ? (
          <Badge tone="neutral">Loading</Badge>
        ) : error ? (
          <Badge tone={detail?.code === "no_baseline_run" ? "warning" : "danger"}>
            {detail?.code === "no_baseline_run"
              ? "Baseline run required"
              : "Unavailable"}
          </Badge>
        ) : failed.length ? (
          <Badge tone="danger">{failed.length} failed rules</Badge>
        ) : (
          <Badge tone="success">All rules pass</Badge>
        )}
      </div>
      <div className="mt-1 text-sm font-medium">
        {header?.formTitle ?? copy.title}
      </div>
      <div className="mt-0.5 text-xs text-[rgb(var(--muted-foreground))]">
        {header?.regulator ?? copy.regulator} · {periodLabel}
      </div>
    </button>
  );
}

function Bsd3Document({ preview }: { preview: Bsd3PreviewRead }) {
  const header = preview.header;
  const summaryByCode = new Map(
    preview.summaryRows.map((row) => [row.rowCode, row]),
  );
  return (
    <PreviewFrame header={header}>
      <PreviewSection title="High quality liquid assets">
        <DenseTable ariaLabel="BSD-3 HQLA rows">
          <thead>
            <tr>
              <Th>Row</Th>
              <Th>Description</Th>
              <Th align="right">Amount ({header.currency})</Th>
            </tr>
          </thead>
          <tbody>
            {preview.hqlaRows.map((row) => (
              <tr key={row.rowCode}>
                <RowCode code={row.rowCode} />
                <Td>{row.description}</Td>
                <NumCell value={formatMoneyFull(row.amount, header.currency)} />
              </tr>
            ))}
            <SummaryTr row={summaryByCode.get("3.0")} currency={header.currency} />
          </tbody>
        </DenseTable>
      </PreviewSection>
      <PreviewSection title="Cash outflows (30 days)">
        <WeightedRowTable
          ariaLabel="BSD-3 outflow rows"
          rows={preview.outflowRows}
          currency={header.currency}
          rateHeader="Runoff %"
          summaryRows={[summaryByCode.get("5.0")]}
        />
      </PreviewSection>
      <PreviewSection title="Cash inflows (30 days)">
        <WeightedRowTable
          ariaLabel="BSD-3 inflow rows"
          rows={preview.inflowRows}
          currency={header.currency}
          rateHeader="Inflow %"
          summaryRows={[summaryByCode.get("7.0"), summaryByCode.get("8.0")]}
        />
      </PreviewSection>
      <PreviewSection title="Liquidity coverage ratio">
        <DenseTable ariaLabel="BSD-3 LCR row">
          <tbody>
            <SummaryTr row={summaryByCode.get("9.0")} currency={header.currency} />
          </tbody>
        </DenseTable>
      </PreviewSection>
      <PreviewSection title="Net stable funding ratio">
        <WeightedRowTable
          ariaLabel="BSD-3 ASF rows"
          rows={preview.nsfr.asfRows}
          currency={header.currency}
          rateHeader="Weight %"
          summaryRows={[preview.nsfr.asfTotal]}
        />
        <div className="mt-3">
          <WeightedRowTable
            ariaLabel="BSD-3 RSF rows"
            rows={preview.nsfr.rsfRows}
            currency={header.currency}
            rateHeader="Weight %"
            summaryRows={[preview.nsfr.rsfTotal, preview.nsfr.nsfrRatio]}
          />
        </div>
      </PreviewSection>
      <PreviewSection title="Validations">
        <ValidationList validations={preview.validations} />
      </PreviewSection>
    </PreviewFrame>
  );
}

function Bsd2Document({ preview }: { preview: Bsd2PreviewRead }) {
  const header = preview.header;
  return (
    <PreviewFrame header={header}>
      <PreviewSection title="Capital structure">
        <DenseTable ariaLabel="BSD-2 capital structure rows">
          <thead>
            <tr>
              <Th>Row</Th>
              <Th>Description</Th>
              <Th align="right">Amount ({header.currency})</Th>
            </tr>
          </thead>
          <tbody>
            <PlainRows rows={preview.cet1Rows} currency={header.currency} />
            <PlainRows
              rows={preview.deductionRows}
              currency={header.currency}
              tone="danger"
            />
            <SummaryTr row={preview.cet1Total} currency={header.currency} />
            <PlainRows rows={preview.at1Rows} currency={header.currency} />
            <SummaryTr row={preview.tier1Total} currency={header.currency} />
            <PlainRows rows={preview.tier2Rows} currency={header.currency} />
            <SummaryTr row={preview.totalCapital} currency={header.currency} />
          </tbody>
        </DenseTable>
      </PreviewSection>
      <PreviewSection title="Risk weighted assets">
        <WeightedRowTable
          ariaLabel="BSD-2 credit RWA rows"
          rows={preview.creditRwaRows}
          currency={header.currency}
          rateHeader="Weight %"
        />
        <div className="mt-3">
          <WeightedRowTable
            ariaLabel="BSD-2 market RWA rows"
            rows={preview.marketRwaRows}
            currency={header.currency}
            rateHeader="Charge %"
          />
        </div>
        <div className="mt-3">
          <WeightedRowTable
            ariaLabel="BSD-2 operational RWA rows"
            rows={preview.operationalRwaRows}
            currency={header.currency}
            rateHeader="Factor %"
            summaryRows={[preview.totalRwa]}
          />
        </div>
      </PreviewSection>
      <PreviewSection title="Capital ratios">
        <DenseTable ariaLabel="BSD-2 capital ratios">
          <thead>
            <tr>
              <Th>Row</Th>
              <Th>Ratio</Th>
              <Th align="right">Value</Th>
              <Th align="right">Minimum</Th>
              <Th align="right">Result</Th>
            </tr>
          </thead>
          <tbody>
            {preview.ratioRows.map((row) => (
              <tr key={row.rowCode}>
                <RowCode code={row.rowCode} />
                <Td>{row.description}</Td>
                <NumCell value={formatPct(row.valuePct)} emphasis />
                <NumCell value={formatPct(row.minimumPct)} />
                <Td align="right">
                  <Badge tone={row.passed ? "success" : "danger"}>
                    {row.passed ? "Pass" : "Fail"}
                  </Badge>
                </Td>
              </tr>
            ))}
          </tbody>
        </DenseTable>
      </PreviewSection>
      <PreviewSection title="Validations">
        <ValidationList validations={preview.validations} />
      </PreviewSection>
    </PreviewFrame>
  );
}

function RowCode({ code }: { code: string }) {
  return (
    <Td mono tone="muted">
      {code}
    </Td>
  );
}

function PlainRows({
  rows,
  currency,
  tone = "default",
}: {
  rows: Bsd2RowRead[];
  currency: string;
  tone?: "default" | "danger";
}) {
  return (
    <>
      {rows.map((row) => (
        <tr key={row.rowCode}>
          <RowCode code={row.rowCode} />
          <Td tone={tone === "danger" ? "danger" : "default"}>
            {row.description}
          </Td>
          <NumCell
            value={formatMoneyFull(row.amount, currency)}
            tone={tone === "danger" ? "danger" : "default"}
          />
        </tr>
      ))}
    </>
  );
}

function SummaryTr({
  row,
  currency,
  colSpanBefore = 1,
}: {
  row?: Bsd2SummaryRowRead;
  currency: string;
  colSpanBefore?: number;
}) {
  if (!row) return null;
  return (
    <tr className="bg-[rgb(var(--surface-2))]">
      <RowCode code={row.rowCode} />
      <Td colSpan={colSpanBefore} emphasis>
        {row.description}
      </Td>
      <NumCell
        value={
          row.unit === "pct"
            ? formatPct(row.value, 2)
            : formatMoneyFull(row.value, currency)
        }
        emphasis
      />
    </tr>
  );
}

function WeightedRowTable({
  ariaLabel,
  rows,
  currency,
  rateHeader,
  summaryRows = [],
}: {
  ariaLabel: string;
  rows: Bsd2WeightedRowRead[];
  currency: string;
  rateHeader: string;
  summaryRows?: Array<Bsd2SummaryRowRead | undefined>;
}) {
  return (
    <DenseTable ariaLabel={ariaLabel}>
      <thead>
        <tr>
          <Th>Row</Th>
          <Th>Description</Th>
          <Th align="right">Balance ({currency})</Th>
          <Th align="right">{rateHeader}</Th>
          <Th align="right">Weighted ({currency})</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.rowCode}>
            <RowCode code={row.rowCode} />
            <Td>{row.description}</Td>
            <NumCell value={formatMoneyFull(row.balance, currency)} />
            <NumCell value={formatPct(row.ratePct)} />
            <NumCell value={formatMoneyFull(row.weightedAmount, currency)} />
          </tr>
        ))}
        {summaryRows.map((row) =>
          row ? (
            <SummaryTr
              key={row.rowCode}
              row={row}
              currency={currency}
              colSpanBefore={3}
            />
          ) : null,
        )}
      </tbody>
    </DenseTable>
  );
}
