'use client';

/**
 * Data source connections: which source systems this institution can ingest
 * from, and which mapping configuration is active. Excel/CSV is live; core
 * banking adapters surface their honest pipeline status.
 */

import { CheckCircle2, Clock, FileSpreadsheet, Server } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { isApiError } from '@/lib/api/client';
import {
  STARTER_TEMPLATES,
  useActivateTemplate,
  useMappingConfigs,
} from '@/lib/api/ingestion';

export default function SourcesPanel() {
  const { bank } = useBankContext();
  const configsQuery = useMappingConfigs(bank?.id);
  const activate = useActivateTemplate(bank?.id);

  const configs = configsQuery.data?.configs ?? [];
  const activeConfig = configs.find((config) => config.status === 'active');

  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-h2 text-navy">Data sources</h2>
        <p className="text-caption text-slate">
          Adapters translate each source into the canonical model — downstream
          modules never see source-system formats.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card p-5 border-l-4 border-l-success">
          <div className="flex items-center gap-2">
            <FileSpreadsheet size={18} className="text-success" aria-hidden />
            <h3 className="text-h3 text-navy">Excel / CSV</h3>
            <span className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-success">
              <CheckCircle2 size={13} aria-hidden /> Connected
            </span>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            First-class onboarding path: workbook and CSV drops with
            mapping-driven translation, cell-level lineage, and validation
            gating.
          </p>
          <div className="mt-3 pt-3 border-t border-border-light">
            <p className="text-micro uppercase tracking-wider text-slate">
              Active mapping
            </p>
            {activeConfig ? (
              <p className="mt-1 text-body text-navy">
                {activeConfig.name}{' '}
                <span className="font-mono text-caption text-slate">
                  v{activeConfig.version}
                </span>
              </p>
            ) : (
              <p className="mt-1 text-body text-warning">
                None — activate a starter mapping below before ingesting.
              </p>
            )}
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">Temenos T24</h3>
            <span className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-slate">
              <Clock size={13} aria-hidden /> Pending partner access
            </span>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            Native TAFJ API and post-COB batch integration. Adapter skeleton is
            in place; implementation is gated on Temenos developer portal
            access. T24 banks onboard via Excel/CSV today.
          </p>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-slate" aria-hidden />
            <h3 className="text-h3 text-navy">Finacle · FlexCube · DB-direct</h3>
            <span className="ml-auto text-caption font-medium text-slate">
              Planned
            </span>
          </div>
          <p className="mt-2 text-body text-slate leading-relaxed">
            Phase 3 adapter portfolio. Every adapter implements the same
            contract and passes the same conformance suite before it ships.
          </p>
        </div>
      </div>

      <div className="card p-5">
        <h3 className="text-h3 text-navy">Starter mappings for the Sample Bank dataset</h3>
        <p className="mt-1 text-body text-slate">
          Mappings are the onboarding deliverable: they translate each source
          column to a canonical field. One mapping is active per source system
          at a time; activating another creates a new version.
        </p>
        {configsQuery.isError && (
          <div className="mt-3">
            <ErrorPanel
              error={configsQuery.error}
              onRetry={() => void configsQuery.refetch()}
              title="Could not load mapping configurations"
            />
          </div>
        )}
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {STARTER_TEMPLATES.map((template) => {
            const isActive = activeConfig?.name === template.name;
            return (
              <div
                key={template.key}
                className={`rounded border p-4 ${
                  isActive ? 'border-success bg-success-light/40' : 'border-border'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-body font-medium text-navy">{template.name}</p>
                  {isActive && (
                    <span className="text-caption font-medium text-success">Active</span>
                  )}
                </div>
                <p className="mt-1 text-caption text-slate leading-relaxed">
                  {template.description}
                </p>
                <ul className="mt-2 space-y-0.5">
                  {template.ingests.map((file) => (
                    <li key={file} className="text-caption font-mono text-slate">
                      {file}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  disabled={isActive || activate.isPending || !bank}
                  onClick={() => activate.mutate(template)}
                  className="mt-3 inline-flex items-center px-3 py-1.5 rounded text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {isActive ? 'Currently active' : 'Activate mapping'}
                </button>
              </div>
            );
          })}
        </div>
        {activate.isError && (
          <p className="mt-3 text-caption text-critical">
            {isApiError(activate.error)
              ? activate.error.message
              : 'Could not activate the mapping.'}
          </p>
        )}
      </div>
    </section>
  );
}
