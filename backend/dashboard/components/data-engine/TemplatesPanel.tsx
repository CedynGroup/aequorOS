'use client';

/**
 * Downloadable ingestion templates. Two formats (canonical spec / source
 * columns); each file carries the correct headers + an example row. Surfaces
 * which mapping each format needs and offers one-click activation.
 */

import { useState } from 'react';
import { Download, FileDown } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import {
  STARTER_TEMPLATES,
  useActivateTemplate,
  useMappingConfigs,
} from '@/lib/api/ingestion';
import {
  REQUIRED_MAPPING,
  TEMPLATES,
  templateCsv,
  type TemplateFormat,
} from '@/lib/templates';
import { downloadTextFile } from '@/lib/download';

const FORMATS: { key: TemplateFormat; label: string; blurb: string }[] = [
  {
    key: 'canonical',
    label: 'Canonical spec',
    blurb:
      'Provider-neutral headers matching our published API fields. Map your core system to this spec once — self-documenting and portable across institutions.',
  },
  {
    key: 'source',
    label: 'Source columns (demo)',
    blurb:
      'Headers matching the Sample Bank demo files exactly — ingest immediately with zero setup and round-trip with the demo dataset.',
  },
];

export default function TemplatesPanel() {
  const { bank } = useBankContext();
  const [format, setFormat] = useState<TemplateFormat>('canonical');
  const configsQuery = useMappingConfigs(bank?.id);
  const activate = useActivateTemplate(bank?.id);

  const requiredName = REQUIRED_MAPPING[format];
  const activeConfig = (configsQuery.data?.configs ?? []).find(
    (config) => config.status === 'active' && config.sourceSystem === 'EXCEL_CSV',
  );
  const mappingReady = activeConfig?.name === requiredName;
  const starter = STARTER_TEMPLATES.find((template) => template.name === requiredName);
  const activeFormat = FORMATS.find((entry) => entry.key === format)!;

  return (
    <section className="space-y-4">
      <div className="card p-5">
        <h3 className="text-h3 text-navy">Download a starting template</h3>
        <p className="mt-1 text-body text-slate leading-relaxed">
          Fill a template with your data and upload it in the Upload tab. Each file
          carries the correct columns, an example row, and the exact filename the
          engine expects.
        </p>

        <div className="mt-4 inline-flex rounded-md border border-border p-0.5">
          {FORMATS.map((entry) => (
            <button
              key={entry.key}
              type="button"
              onClick={() => setFormat(entry.key)}
              className={`px-3 py-1.5 rounded text-caption font-medium transition-colors ${
                format === entry.key ? 'bg-nav text-white' : 'text-slate hover:text-navy'
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-caption text-slate leading-relaxed">{activeFormat.blurb}</p>

        <div
          className={`mt-3 rounded border p-3 text-caption ${
            mappingReady
              ? 'border-success/30 bg-success-light/40'
              : 'border-warning/30 bg-warning-light/40'
          }`}
        >
          {mappingReady ? (
            <span className="text-success">
              Active mapping <span className="font-medium">{requiredName}</span> — these
              files ingest as-is.
            </span>
          ) : (
            <span className="inline-flex flex-wrap items-center gap-2 text-navy/80">
              These files ingest against the{' '}
              <span className="font-medium">{requiredName}</span> mapping.
              {starter && (
                <button
                  type="button"
                  disabled={!bank || activate.isPending}
                  onClick={() => activate.mutate(starter)}
                  className="inline-flex items-center px-2 py-1 rounded bg-action text-white font-medium hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {activate.isPending ? 'Activating…' : 'Activate it'}
                </button>
              )}
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {TEMPLATES[format].map((template) => (
          <div key={template.filename} className="card p-4 flex flex-col">
            <div className="flex items-center gap-2">
              <FileDown size={16} className="text-action shrink-0" aria-hidden />
              <p className="text-body font-medium text-navy">{template.label}</p>
              <span className="ml-auto text-caption font-mono text-slate">
                {template.filename}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {template.columns.map((column) => (
                <span
                  key={column}
                  className="px-1.5 py-0.5 rounded bg-surface text-micro font-mono text-slate"
                >
                  {column}
                </span>
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                downloadTextFile(
                  template.filename,
                  templateCsv(template),
                  'text/csv;charset=utf-8',
                )
              }
              className="mt-3 self-start inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-caption font-medium bg-action text-white hover:bg-action-hover"
            >
              <Download size={13} aria-hidden /> Download CSV
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
