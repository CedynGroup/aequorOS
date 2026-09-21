'use client';

/**
 * Compare two versions of ONE return — v1 against v2 of LCR-NSFR, say.
 *
 * The Compare page had two modes, both over module RUN metrics: version
 * (two runs of one period) and period (one module across two periods). Those
 * compare `lcr_pct`-style keys, which are stable across versions AND periods
 * and carry a favourable direction — a deliberate choice, because package
 * cells are keyed on per-return regulator row codes that align across neither.
 *
 * But it left no way to ask the question an officer actually asks after a
 * send-back: *what changed between the version I filed and the one I am about
 * to*. That comparison existed — `version_chain.compare_versions`, server-side,
 * over the two immutable snapshots — and was reachable only from inside a
 * return's own workspace. Somebody looking for it on the Compare page found
 * module categories and no returns.
 *
 * So this is the same server-computed diff, given its own way in. It renders
 * through `ComparisonPanel`, the renderer the workspace already uses: a second
 * renderer would be a second opinion, and the diff an examiner reads has to be
 * the one the platform computed, shown one way.
 */

import { useEffect, useMemo, useState } from 'react';
import { GitCompareArrows } from 'lucide-react';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import { SkeletonCard } from '@/components/ui/Skeleton';
import {
  useComparePackageVersions,
  useRegulatoryPackages,
  useReturnTemplates,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate } from '@/lib/api/values';
import { ComparisonPanel } from './PriorVersionsCard';

export default function ReturnVersionCompare({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const templatesQuery = useReturnTemplates();
  const templates = useMemo(
    () => templatesQuery.data?.templates ?? [],
    [templatesQuery.data],
  );

  const [code, setCode] = useState<string>('');
  useEffect(() => {
    if (!code && templates.length > 0) setCode(templates[0].code);
  }, [code, templates]);

  // Every version of this return, newest first. `includeSuperseded` is the
  // point: the versions worth comparing are precisely the ones a later version
  // replaced.
  const packagesQuery = useRegulatoryPackages(bankId, {
    returnCode: code || undefined,
    includeSuperseded: true,
    limit: 100,
  });
  const packages = useMemo(
    () =>
      [...(packagesQuery.data?.packages ?? [])].sort((a, b) => {
        const date = isoDate(b.reportingDate).localeCompare(isoDate(a.reportingDate));
        return date !== 0 ? date : b.version - a.version;
      }),
    [packagesQuery.data],
  );

  const dates = useMemo(
    () => [...new Set(packages.map((pkg) => isoDate(pkg.reportingDate)))],
    [packages],
  );
  const [date, setDate] = useState<string>('');
  useEffect(() => {
    if (dates.length > 0 && !dates.includes(date)) setDate(dates[0]);
  }, [date, dates]);

  const versions = useMemo(
    () => packages.filter((pkg) => isoDate(pkg.reportingDate) === date),
    [packages, date],
  );

  const [baseId, setBaseId] = useState<string>('');
  const [targetId, setTargetId] = useState<string>('');
  useEffect(() => {
    // Default to the most useful pair: the newest version against the one it
    // replaced. That is the question a send-back leaves behind.
    if (versions.length >= 2) {
      setTargetId(versions[0].id);
      setBaseId(versions[1].id);
    } else {
      setTargetId(versions[0]?.id ?? '');
      setBaseId('');
    }
  }, [versions]);

  const comparison = useComparePackageVersions(
    bankId,
    baseId || null,
    targetId || null,
    Boolean(baseId && targetId && baseId !== targetId),
  );

  const targetVersion =
    versions.find((pkg) => pkg.id === targetId)?.version ?? null;

  return (
    <div className="space-y-6">
      <SectionCard
        title="What to compare"
        subtitle="Two versions of the same return and reporting date"
      >
        <QueryBoundary
          contained
          isLoading={templatesQuery.isLoading || packagesQuery.isLoading}
          error={templatesQuery.error ?? packagesQuery.error}
          onRetry={() => {
            void templatesQuery.refetch();
            void packagesQuery.refetch();
          }}
          skeleton={<SkeletonCard />}
        >
          <div className="flex flex-wrap items-end gap-4">
            <Selector label="Return" value={code} onChange={setCode}>
              {templates.map((template) => (
                <option key={template.code} value={template.code}>
                  {template.code} — {template.title}
                </option>
              ))}
            </Selector>

            <Selector
              label="Reporting date"
              value={date}
              onChange={setDate}
              disabled={dates.length === 0}
            >
              {dates.map((value) => (
                <option key={value} value={value}>
                  {fmtDateUTC(new Date(value))}
                </option>
              ))}
            </Selector>

            <Selector
              label="Baseline version"
              value={baseId}
              onChange={setBaseId}
              disabled={versions.length < 2}
            >
              {versions.map((pkg) => (
                <option key={pkg.id} value={pkg.id}>
                  v{pkg.version}
                </option>
              ))}
            </Selector>

            <Selector
              label="Compared version"
              value={targetId}
              onChange={setTargetId}
              disabled={versions.length < 2}
            >
              {versions.map((pkg) => (
                <option key={pkg.id} value={pkg.id}>
                  v{pkg.version}
                </option>
              ))}
            </Selector>
          </div>
        </QueryBoundary>
      </SectionCard>

      {versions.length < 2 ? (
        <SectionCard title="Line-by-line delta">
          <EmptyState
            Icon={GitCompareArrows}
            title="Only one version of this return"
            description="A comparison needs two. A second version appears when a return is regenerated — after a send-back, or a correction."
          />
        </SectionCard>
      ) : baseId === targetId ? (
        <SectionCard title="Line-by-line delta">
          <EmptyState
            Icon={GitCompareArrows}
            title="Pick two different versions"
            description="The baseline and the compared version are the same."
          />
        </SectionCard>
      ) : (
        <SectionCard
          title="Line-by-line delta"
          subtitle="Computed server-side from the two immutable snapshots — never derived here."
        >
          <QueryBoundary
            contained
            isLoading={comparison.isLoading}
            error={comparison.error}
            onRetry={() => void comparison.refetch()}
            skeleton={<SkeletonCard />}
          >
            {comparison.error ? (
              <ErrorPanel
                error={comparison.error}
                title="Could not compare these versions"
              />
            ) : comparison.data && targetVersion !== null ? (
              <ComparisonPanel
                comparison={comparison.data}
                currentVersion={targetVersion}
              />
            ) : null}
          </QueryBoundary>
        </SectionCard>
      )}
    </div>
  );
}

function Selector({
  label,
  value,
  onChange,
  disabled = false,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy disabled:opacity-60"
      >
        {children}
      </select>
    </label>
  );
}
