"use client";

/**
 * Market Data tab of the Data Engine console (spec §9.3 "Market Data
 * Sources"). Source cards for every connection (vendor or manual), the
 * add-source stepper (vendor pick → credentials → scopes → test → schedule →
 * activate), and the manual-upload block: template downloads plus the
 * file+as-of upload that lands canonical market data and auto-recomputes
 * dependent modules. Vendor concepts stay behind the adapters; this page
 * speaks scopes, freshness, and quota only.
 *
 * Markets authority is read from the effective-authority projection: the
 * connection list needs MARKETS/restricted view (it carries credential
 * metadata), quota and templates need MARKETS/published view, and the manual
 * upload needs MARKETS/published create. A missing grant leaves the control
 * visible but disabled with the sentence the user needs to ask for.
 */

import PageContainer from "@/components/ui/PageContainer";
import { useState } from "react";
import {
  LineChart,
  Plus,
  UploadCloud,
  FileSpreadsheet,
  Loader2,
} from "lucide-react";
import type { MarketDataUploadRead } from "@aequoros/risk-service-api";
import PageHeader from "@/components/ui/PageHeader";
import EmptyState from "@/components/ui/EmptyState";
import { useBankContext } from "@/components/shell/BankContext";
import PermissionAction from "@/components/markets/PermissionAction";
import { isApiError } from "@/lib/api/client";
import {
  MARKETS_PUBLISHED_CREATE_REASON,
  MARKETS_PUBLISHED_VIEW_REASON,
  MARKETS_RESTRICTED_VIEW_REASON,
} from "@/lib/modules";
import {
  useMarketDataConnections,
  useMarketDataQuota,
  useUploadMarketData,
} from "@/lib/api/hooks";
import SourceCard from "@/components/market-data/SourceCards";
import AddSourcePanel from "@/components/market-data/AddSourcePanel";
import {
  TEMPLATE_KINDS,
  downloadTemplate,
} from "@/components/market-data/shared";

export default function MarketDataPage() {
  const { bank, moduleScope } = useBankContext();
  const canViewConnections = moduleScope.marketsRestrictedView === true;
  const connections = useMarketDataConnections(
    canViewConnections ? bank?.id : undefined,
  );
  const quota = useMarketDataQuota(
    moduleScope.marketsPublishedView ? bank?.id : undefined,
  );
  const [adding, setAdding] = useState(false);

  const rows = connections.data?.connections ?? [];
  const quotaByVendor = new Map(
    (quota.data?.vendors ?? []).map((entry) => [entry.vendor, entry]),
  );

  return (
    <>
      <PageHeader eyebrow="Data Engine" title="Market Data" />
      <PageContainer className="py-6 space-y-8">
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-h3 text-navy">Configured sources</h2>
            {!adding && (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
              >
                <Plus size={13} aria-hidden />
                Connect a source
              </button>
            )}
          </div>
          {!canViewConnections ? (
            <p
              className="card p-6 text-body text-slate"
              data-testid="market-data-connections-restricted"
            >
              {MARKETS_RESTRICTED_VIEW_REASON}
            </p>
          ) : connections.isLoading ? (
            <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Loading market data sources…
            </div>
          ) : rows.length === 0 && !adding ? (
            <EmptyState
              Icon={LineChart}
              title="No market data sources configured yet"
              description="Configure Bloomberg or LSEG for onboarding, or use the available manual upload path below. Calculations consume the same canonical scopes either way."
              action={
                <button
                  type="button"
                  onClick={() => setAdding(true)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  <Plus size={13} aria-hidden />
                  Connect a source
                </button>
              }
            />
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {rows.map((connection) => (
                <SourceCard
                  key={connection.id}
                  bankId={bank?.id ?? ""}
                  connection={connection}
                  quota={quotaByVendor.get(connection.vendor)}
                />
              ))}
            </div>
          )}
          {adding && bank && (
            <AddSourcePanel
              bankId={bank.id}
              existingVendors={rows.map((row) => row.vendor)}
              onDone={() => setAdding(false)}
            />
          )}
        </section>

        <ManualUploadSection
          bankId={bank?.id}
          templateReason={
            moduleScope.marketsPublishedView
              ? undefined
              : MARKETS_PUBLISHED_VIEW_REASON
          }
          uploadReason={
            moduleScope.marketsUpload
              ? undefined
              : MARKETS_PUBLISHED_CREATE_REASON
          }
        />
      </PageContainer>
    </>
  );
}

function ManualUploadSection({
  bankId,
  templateReason,
  uploadReason,
}: {
  bankId: string | undefined;
  /** The grant missing for template downloads, or undefined when authorized. */
  templateReason?: string;
  /** The grant missing for the upload itself, or undefined when authorized. */
  uploadReason?: string;
}) {
  const upload = useUploadMarketData(bankId);
  const [file, setFile] = useState<File | null>(null);
  const [asOfDate, setAsOfDate] = useState("");
  const result: MarketDataUploadRead | undefined = upload.data;
  const canUpload = uploadReason === undefined;

  return (
    <section className="space-y-4">
      <h2 className="text-h3 text-navy">Manual upload</h2>
      <div className="card p-6 space-y-5">
        <div>
          <p className="text-body text-navy font-medium">
            1. Download a template
          </p>
          <p className="text-caption text-slate mb-3">
            One template per scope category. Rates are entered as percentages
            (e.g. 15.80); AequorOS normalizes on ingest.
          </p>
          <div className="flex flex-wrap gap-2">
            {TEMPLATE_KINDS.map(({ kind, label }) => (
              <PermissionAction
                key={kind}
                reason={templateReason}
                disabled={!bankId}
                onClick={() => {
                  if (bankId) void downloadTemplate(kind, bankId);
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption text-navy border border-border rounded-md hover:bg-surface"
              >
                <FileSpreadsheet size={13} aria-hidden />
                {label}
              </PermissionAction>
            ))}
          </div>
        </div>
        <div className="border-t border-border-light pt-5">
          <p className="text-body text-navy font-medium">
            2. Upload the filled file
          </p>
          <p className="text-caption text-slate mb-3">
            Multi-sheet workbooks are supported (one scope category per sheet).
            Accepted data lands in the canonical model and dependent modules
            recompute automatically.
          </p>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (canUpload && file && asOfDate)
                upload.mutate({ file, asOfDate });
            }}
          >
            <label className="block">
              <span className="block text-caption text-slate mb-1">
                File (.xlsx / .csv)
              </span>
              <input
                type="file"
                accept=".xlsx,.csv"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="block text-caption text-navy file:mr-3 file:px-3 file:py-1.5 file:border file:border-border file:rounded-md file:bg-surface-raised file:text-caption file:font-medium file:text-navy hover:file:bg-surface"
              />
            </label>
            <label className="block">
              <span className="block text-caption text-slate mb-1">
                As-of date
              </span>
              <input
                type="date"
                value={asOfDate}
                onChange={(event) => setAsOfDate(event.target.value)}
                className="px-3 py-1.5 text-caption text-navy bg-surface-raised border border-border rounded-md"
              />
            </label>
            <PermissionAction
              reason={uploadReason}
              disabled={!file || !asOfDate || upload.isPending}
              onClick={() => {
                if (canUpload && file && asOfDate)
                  upload.mutate({ file, asOfDate });
              }}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              {upload.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <UploadCloud size={13} aria-hidden />
              )}
              Upload
            </PermissionAction>
          </form>
          {upload.error && (
            <p className="mt-3 text-caption text-critical">
              {isApiError(upload.error)
                ? upload.error.message
                : "Upload failed — check the file against its template."}
            </p>
          )}
          {result && (
            <div className="mt-4 rounded-md border border-border-light bg-surface px-4 py-3 space-y-1">
              <p className="text-caption text-navy font-medium">
                Batch {result.status.replaceAll("_", " ")} —{" "}
                {result.canonicalRecordsProduced} canonical records across{" "}
                {result.scopes.length} scope
                {result.scopes.length === 1 ? "" : "s"}
              </p>
              <p className="text-caption text-slate">
                {result.scopes.join(", ")}
              </p>
              {result.warnings.map((warning) => (
                <p key={warning} className="text-caption text-warning">
                  {warning}
                </p>
              ))}
              {result.errors.map((error) => (
                <p key={error} className="text-caption text-critical">
                  {error}
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
