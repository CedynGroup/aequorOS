"use client";

/**
 * Data Engine overview: cross-source console. Integration status cards with
 * live per-source stats, the canonical-model KPI strip, recent batches across
 * all sources, and the cross-source Activate panel. Everything specific to
 * one integration lives in its tab.
 */

import PageContainer from "@/components/ui/PageContainer";
import PageHeader from "@/components/ui/PageHeader";
import {
  CanonicalSummaryStrip,
  IntegrationCards,
} from "@/components/data-engine/OverviewPanels";
import BatchesTable from "@/components/data-engine/BatchesTable";
import ActivatePanel from "@/components/data-engine/ActivatePanel";
import ConnectionHealthPanel from "@/components/data-engine/ConnectionHealthPanel";
import LiveStatusCard from "@/features/live/components/LiveStatusCard";
import UnreconciledBookBanner from "@/features/live/components/UnreconciledBookBanner";
import { useBankContext } from "@/components/shell/BankContext";
import { useDataPermission } from "@/lib/api/dataAccess";
import DataAccessNotice from "@/components/data-engine/DataAccessNotice";
import ReconciliationPanel from "@/components/data-engine/ReconciliationPanel";

export default function DataEngineOverviewPage() {
  const { bank } = useBankContext();
  const aggregated = useDataPermission(bank?.id, "view", "aggregated");
  const confidential = useDataPermission(bank?.id, "view", "confidential");
  const restricted = useDataPermission(bank?.id, "view", "restricted");
  const run = useDataPermission(bank?.id, "run", "restricted");
  return (
    <>
      <PageHeader eyebrow="Data Engine" title="Data Engine" />
      <PageContainer className="py-6 space-y-8">
        {aggregated && <UnreconciledBookBanner />}
        {aggregated ? (
          <IntegrationCards />
        ) : (
          <DataAccessNotice requirement="DATA · Aggregated · View" />
        )}
        {restricted && <ConnectionHealthPanel />}
        {aggregated && <LiveStatusCard />}
        {aggregated && <CanonicalSummaryStrip />}
        <BatchesTable
          limit={8}
          title="Recent ingestion — all sources"
          emptyDescription="No batches yet. Open the Excel & CSV tab to upload files, or push data through the API."
        />
        {confidential && <ReconciliationPanel />}
        {run && <ActivatePanel />}
      </PageContainer>
    </>
  );
}
