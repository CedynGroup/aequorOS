import type { ReactNode } from "react";

import { Alert } from "../../../components/ui";
import { formatDate, formatDateTime } from "./format";

export type PreviewHeader = {
  regulator: string;
  bankName: string;
  licenseType: string;
  formCode: string;
  formTitle: string;
  reportingPeriodLabel: string;
  periodEnd: Date;
  currency: string;
  generatedAt: Date;
  previewNote: string;
};

export function PreviewFrame({
  header,
  children,
}: {
  header: PreviewHeader;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border-2 border-[rgb(var(--border))] bg-[rgb(var(--surface))]">
      <div className="border-b-2 border-[rgb(var(--border))] px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[rgb(var(--muted-foreground))]">
          {header.regulator}
        </div>
        <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-sm font-semibold">
            {header.formCode}
          </span>
          <span className="text-sm font-semibold">{header.formTitle}</span>
        </div>
        <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-0.5 text-[11px] md:grid-cols-4">
          <PreviewHeaderItem label="Reporting institution" value={header.bankName} />
          <PreviewHeaderItem label="License type" value={header.licenseType} />
          <PreviewHeaderItem
            label="Reporting period"
            value={`${header.reportingPeriodLabel} · ${formatDate(header.periodEnd)}`}
          />
          <PreviewHeaderItem label="Currency" value={header.currency} />
        </dl>
      </div>
      <div className="border-b border-[rgb(var(--border))] px-4 py-2">
        <Alert title="Preview only" tone="warning">
          {header.previewNote}
        </Alert>
      </div>
      <div className="space-y-4 px-4 py-3">{children}</div>
      <div className="border-t border-[rgb(var(--border))] px-4 py-2 text-[11px] text-[rgb(var(--muted-foreground))]">
        Generated {formatDateTime(header.generatedAt)}
      </div>
    </div>
  );
}

function PreviewHeaderItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-[0.04em] text-[rgb(var(--muted-foreground))]">
        {label}
      </dt>
      <dd className="m-0 truncate font-medium" title={value}>
        {value}
      </dd>
    </div>
  );
}

export function PreviewSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-[rgb(var(--muted-foreground))]">
        {title}
      </h3>
      {children}
    </section>
  );
}
