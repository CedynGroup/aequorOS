"use client";

/**
 * One dashboard, read.
 *
 * The canvas answers each widget separately, authorized for whoever opened the
 * page, so two readers of the same dashboard see two different sets of tiles —
 * one may see a chart where the other sees "Access restricted", and neither
 * learns anything about the other's view.
 *
 * A dashboard id that resolves to nothing is not found. That is the honest
 * answer while no dashboard source is published: see
 * `components/bi/dashboards.ts`.
 */

import { use, useState } from "react";
import { notFound } from "next/navigation";
import type { BiFilter, BiQuery } from "@aequoros/risk-service-api";
import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import { useBankContext } from "@/components/shell/BankContext";
import DashboardCanvas from "@/components/bi/DashboardCanvas";
import ExplainDrawer from "@/components/bi/ExplainDrawer";
import ExportMenu, { printExportOption } from "@/components/bi/ExportMenu";
import FilterBar from "@/components/bi/FilterBar";
import TrustBadge from "@/components/bi/TrustBadge";
import {
  CERTIFICATION_LABELS,
  findDashboard,
} from "@/components/bi/dashboards";
import { useBiCatalogue, useBiTrust } from "@/lib/api/bi";
import { isoDay } from "@/lib/api/biKeys";

export default function DashboardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { bank, period } = useBankContext();

  const defaultDate = isoDay(period?.periodEnd) ?? isoDay(new Date()) ?? "";
  const [chosenDate, setChosenDate] = useState<string | null>(null);
  const asOf = chosenDate ?? defaultDate;
  const [filters, setFilters] = useState<BiFilter[]>([]);
  const [explaining, setExplaining] = useState<{
    measure: string;
    query: BiQuery;
  } | null>(null);

  const catalogue = useBiCatalogue(bank?.id);
  const trust = useBiTrust(bank?.id, asOf);

  const dashboard = findDashboard(id);
  if (!dashboard) notFound();

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Dashboards", href: "/dashboards" }]}
        title={dashboard.title}
        subtitle={dashboard.description}
        asOf={asOf}
        action={
          <div className="flex items-center gap-2">
            <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
              {CERTIFICATION_LABELS[dashboard.certification]}
            </span>
            <TrustBadge
              status={trust.data?.status}
              failingChecks={(trust.data?.checks ?? [])
                .filter((check) => check.status !== "green")
                .map((check) => check.checkId)}
            />
            <ExportMenu options={[printExportOption()]} />
          </div>
        }
      />

      <PageContainer className="space-y-6 py-6">
        <FilterBar
          catalogue={catalogue.data}
          asOf={asOf}
          onAsOfChange={setChosenDate}
          filters={filters}
          onFiltersChange={setFilters}
        />

        <DashboardCanvas
          bankId={bank?.id}
          dashboard={dashboard}
          selection={{ asOf }}
          filters={filters}
          onExplain={(measure, query) => setExplaining({ measure, query })}
        />
      </PageContainer>

      <ExplainDrawer
        bankId={bank?.id}
        measure={explaining?.measure ?? null}
        query={explaining?.query ?? null}
        onClose={() => setExplaining(null)}
      />
    </>
  );
}
