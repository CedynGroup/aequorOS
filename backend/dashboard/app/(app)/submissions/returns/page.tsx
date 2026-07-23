'use client';

/**
 * Regulatory Reporting — Returns workspace. One return family + reporting
 * date at a time: generate/regenerate the immutable package version, preview
 * the snapshot, validate, request approval, export xlsx/csv/pdf artifacts,
 * submit via a channel (ORASS sandbox / email fallback / manual), poll the
 * regulator decision, and read the submission-event trail. Deep-linkable via
 * ?code=&date= (the Calendar and module pages link here).
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  Download,
  FileCheck2,
  FileOutput,
  FlaskConical,
  Loader2,
  Mail,
  PlayCircle,
  RadioTower,
  RefreshCw,
  ScrollText,
  Send,
  ShieldCheck,
  UploadCloud,
} from 'lucide-react';
import type {
  ArtifactKind,
  ChannelCode,
  RegulatoryPackageRead,
  RegulatoryPackageSummaryRead,
  ReturnTemplateRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import {
  useEmailFallbackInstructions,
  useExportRegulatoryPackage,
  useGenerateRegulatoryPackage,
  usePollRegulatorySubmission,
  useRegulatoryPackage,
  useRegulatoryPackages,
  useRequestPackageApproval,
  useReturnTemplates,
  useSessionArtifacts,
  useSubmissionEvents,
  useSubmitRegulatoryPackage,
  useValidateRegulatoryPackage,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, isoDate, shortId } from '@/lib/api/values';
import {
  CHANNEL_LABELS,
  FIDELITY_INFO,
  FidelityPill,
  PackageStatusPill,
  downloadArtifact,
  fmtBytes,
} from '@/components/submissions/shared';
import LifecycleStepper from '@/components/submissions/LifecycleStepper';
import SnapshotPreview from '@/components/submissions/SnapshotPreview';
import ValidationPanel from '@/components/submissions/ValidationPanel';
import EventsFeed from '@/components/submissions/EventsFeed';
import { regShort } from '@/lib/format';

export default function ReturnsWorkspacePage() {
  // useSearchParams requires a Suspense boundary in the app router.
  return (
    <Suspense>
      <ReturnsWorkspace />
    </Suspense>
  );
}

const EXPORT_KINDS: ArtifactKind[] = ['xlsx', 'csv', 'pdf'];
const CHANNEL_OPTIONS: ChannelCode[] = ['orass_sandbox', 'email', 'manual'];

/** The structured ORASS-downtime 409 payload (workflow.submit 409 details). */
type DowntimeFallback = {
  message: string;
};

function downtimeFallback(error: unknown): DowntimeFallback | null {
  if (!isApiError(error) || error.errorCode !== 'channel_downtime') return null;
  return { message: error.message };
}

function ReturnsWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { bank, periods } = useBankContext();
  const bankId = bank?.id;

  const templatesQuery = useReturnTemplates();
  const templates = useMemo(
    () => templatesQuery.data?.templates ?? [],
    [templatesQuery.data]
  );

  const periodDates = useMemo(
    () => periods.map((p) => isoDate(p.periodEnd)),
    [periods]
  );

  const codeParam = searchParams.get('code');
  const dateParam = searchParams.get('date');
  const code =
    codeParam && templates.some((tpl) => tpl.code === codeParam)
      ? codeParam
      : templates[0]?.code;
  const date =
    dateParam && /^\d{4}-\d{2}-\d{2}$/.test(dateParam)
      ? dateParam
      : periodDates[0];

  const setParams = (nextCode: string, nextDate: string | undefined) => {
    const params = new URLSearchParams();
    params.set('code', nextCode);
    if (nextDate) params.set('date', nextDate);
    router.replace(`${pathname}?${params.toString()}`);
  };

  const template = templates.find((tpl) => tpl.code === code);

  const packagesQuery = useRegulatoryPackages(bankId, {
    returnCode: code,
    reportingDate: date,
    includeSuperseded: true,
    limit: 50,
  });
  const versions = useMemo(() => {
    const rows = packagesQuery.data?.packages ?? [];
    return [...rows].sort((a, b) => b.version - a.version);
  }, [packagesQuery.data]);
  const current = versions.find((pkg) => pkg.status !== 'superseded') ?? null;
  const priorVersions = versions.filter((pkg) => pkg.status === 'superseded');

  const packageQuery = useRegulatoryPackage(bankId, current?.id);
  const pkg = packageQuery.data;

  const generate = useGenerateRegulatoryPackage(bankId);

  const ready = Boolean(code && date);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting', href: '/submissions' },
          { label: 'Returns' },
        ]}
        title="Returns workspace"
        subtitle="Generate → validate → approve → export → submit, one immutable package version at a time"
        action={
          <div className="flex items-center gap-2 flex-wrap">
            <label className="flex items-center gap-2 text-caption text-slate">
              Return
              <select
                value={code ?? ''}
                onChange={(e) => setParams(e.target.value, date)}
                className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy max-w-[280px]"
              >
                {templates.map((tpl) => (
                  <option key={tpl.code} value={tpl.code}>
                    {tpl.code} — {tpl.title}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 text-caption text-slate">
              Reporting date
              <select
                value={date ?? ''}
                onChange={(e) => code && setParams(code, e.target.value)}
                className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
              >
                {periodDates.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
                {date && !periodDates.includes(date) && (
                  <option value={date}>{date}</option>
                )}
              </select>
            </label>
          </div>
        }
      />

      <div className="px-8 py-6 space-y-6">
        {template && (
          <FidelityBanner template={template} />
        )}

        <QueryBoundary
          isLoading={templatesQuery.isLoading || packagesQuery.isLoading}
          error={templatesQuery.error ?? packagesQuery.error}
          onRetry={() => {
            void templatesQuery.refetch();
            void packagesQuery.refetch();
          }}
          skeleton={
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SkeletonCard />
              <SkeletonCard />
            </div>
          }
        >
          {!ready ? (
            <EmptyState
              Icon={FileCheck2}
              title="Select a return and reporting date"
              description="Choose a registered return family and one of the bank's reporting periods to open its package workspace."
            />
          ) : !current ? (
            <SectionCard
              title={`${code} · ${date}`}
              subtitle="No package generated for this return and reporting date yet"
            >
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <p className="text-body text-slate leading-relaxed max-w-2xl">
                  Generating mints an immutable, versioned snapshot from the
                  latest succeeded calculation runs — no engine recomputation.
                  Regeneration later supersedes this version; it never mutates
                  it.
                </p>
                <GenerateButton
                  label="Generate package"
                  pending={generate.isPending}
                  onClick={() =>
                    code &&
                    date &&
                    generate.mutate({ returnCode: code, reportingDate: date })
                  }
                />
              </div>
              {generate.error && (
                <div className="mt-4">
                  <ErrorPanel
                    error={generate.error}
                    title="Could not generate the package"
                  />
                </div>
              )}
            </SectionCard>
          ) : (
            <PackageWorkspace
              bankId={bankId!}
              summary={current}
              pkg={pkg}
              pkgLoading={packageQuery.isLoading}
              pkgError={packageQuery.error}
              onRetryPkg={() => packageQuery.refetch()}
              template={template}
              priorVersions={priorVersions}
              onRegenerate={() =>
                code &&
                date &&
                generate.mutate({ returnCode: code, reportingDate: date })
              }
              regeneratePending={generate.isPending}
              regenerateError={generate.error}
            />
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

function FidelityBanner({ template }: { template: ReturnTemplateRead }) {
  const info = FIDELITY_INFO[template.fidelity];
  return (
    <div className="card px-5 py-4 flex items-start gap-3">
      <ScrollText size={16} className="text-action shrink-0 mt-0.5" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-body font-medium text-navy">
            {template.code} — {template.title}
          </p>
          <FidelityPill fidelity={template.fidelity} />
        </div>
        <p className="mt-1 text-caption text-navy/80">{info.blurb}</p>
        <p className="mt-1.5 text-caption text-slate leading-relaxed">
          {template.directiveCitation}
        </p>
      </div>
    </div>
  );
}

function GenerateButton({
  label,
  pending,
  onClick,
}: {
  label: string;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={pending}
      onClick={onClick}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
    >
      {pending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <PlayCircle size={13} aria-hidden />
      )}
      {label}
    </button>
  );
}

function PackageWorkspace({
  bankId,
  summary,
  pkg,
  pkgLoading,
  pkgError,
  onRetryPkg,
  template,
  priorVersions,
  onRegenerate,
  regeneratePending,
  regenerateError,
}: {
  bankId: string;
  summary: RegulatoryPackageSummaryRead;
  pkg: RegulatoryPackageRead | undefined;
  pkgLoading: boolean;
  pkgError: unknown;
  onRetryPkg: () => void;
  template: ReturnTemplateRead | undefined;
  priorVersions: RegulatoryPackageSummaryRead[];
  onRegenerate: () => void;
  regeneratePending: boolean;
  regenerateError: unknown;
}) {
  const validate = useValidateRegulatoryPackage(bankId);
  const requestApproval = useRequestPackageApproval(bankId);
  const exportPackage = useExportRegulatoryPackage(bankId);
  const submit = useSubmitRegulatoryPackage(bankId);
  const poll = usePollRegulatorySubmission(bankId);
  const artifactsQuery = useSessionArtifacts(bankId, summary.id);
  const eventsQuery = useSubmissionEvents(bankId, summary.id);

  const defaultChannel = template?.defaultChannel ?? 'manual';
  const [channel, setChannel] = useState<ChannelCode>(defaultChannel);
  useEffect(() => setChannel(defaultChannel), [defaultChannel, summary.id]);
  const [exportingKind, setExportingKind] = useState<ArtifactKind | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const status = pkg?.status ?? summary.status;
  const report = pkg?.validationReport ?? null;
  const validationBlocked = report ? report.errorCount > 0 || !report.passed : false;
  const canValidate = status === 'generated' || status === 'validated';
  const canRequestApproval = status === 'validated' && report !== null && !validationBlocked;
  const canExport = status !== 'superseded';
  const canSubmit = status === 'approved';

  const events = eventsQuery.data?.events ?? [];
  const latestSubmitted = events.find((event) => event.event === 'submitted');
  const pendingReupload =
    status === 'submitted' &&
    latestSubmitted?.detail?.pending_orass_reupload === true;
  const canPoll =
    status === 'submitted' &&
    latestSubmitted != null &&
    latestSubmitted.channel !== 'manual' &&
    latestSubmitted.externalRef != null;
  const canReupload = pendingReupload;

  const fallback = downtimeFallback(submit.error);
  const instructionsQuery = useEmailFallbackInstructions(
    bankId,
    summary.id,
    Boolean(fallback) || channel === 'email'
  );

  const artifacts = artifactsQuery.data ?? [];

  const runExport = (kind: ArtifactKind) => {
    setExportingKind(kind);
    exportPackage.mutate(
      { packageId: summary.id, kind },
      { onSettled: () => setExportingKind(null) }
    );
  };

  const handleDownload = (artifact: { id: string; objectPath: string }) => {
    setDownloadError(null);
    downloadArtifact(bankId, artifact).catch((error: unknown) =>
      setDownloadError(error instanceof Error ? error.message : 'Download failed.')
    );
  };

  return (
    <div className="space-y-6">
      {/* Current version + lifecycle */}
      <SectionCard
        title={
          <span className="inline-flex items-center gap-2.5">
            {summary.returnCode} · {fmtDateUTC(summary.reportingDate)}
            <span className="font-mono text-caption text-slate tnum">
              v{summary.version}
            </span>
            <PackageStatusPill status={status} />
          </span>
        }
        subtitle={`Generated ${fmtTimestamp(summary.generatedAt)} · immutable snapshot — regeneration supersedes, never mutates`}
        actions={
          <GenerateButton
            label="Regenerate (new version)"
            pending={regeneratePending}
            onClick={onRegenerate}
          />
        }
      >
        <LifecycleStepper status={status} />
        {Boolean(regenerateError) && (
          <div className="mt-4">
            <ErrorPanel error={regenerateError} title="Could not regenerate" />
          </div>
        )}
        {priorVersions.length > 0 && (
          <p className="mt-4 text-caption text-slate tnum">
            Superseded chain:{' '}
            <span className="font-mono text-navy/80">
              v{summary.version} (current)
              {priorVersions.map((prior) => ` ← v${prior.version}`).join('')}
            </span>
          </p>
        )}
      </SectionCard>

      {pendingReupload && (
        <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-4 flex items-start gap-3">
          <UploadCloud size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-body font-medium text-navy">
              Pending ORASS re-upload
            </p>
            <p className="mt-1 text-caption text-navy/80 leading-relaxed">
              This return was submitted via the email fallback during ORASS
              downtime. Per BoG Notice BG/FMD/2026/07 it is deemed complete
              only after re-upload through ORASS once functionality is
              restored.
            </p>
          </div>
          <button
            type="button"
            disabled={submit.isPending}
            onClick={() =>
              submit.mutate({ packageId: summary.id, channel: 'orass_sandbox' })
            }
            className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
          >
            {submit.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <RadioTower size={13} aria-hidden />
            )}
            Re-upload via ORASS
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
        {/* Snapshot + validation (left, wide) */}
        <div className="xl:col-span-2 space-y-6 min-w-0">
          <SectionCard
            title="Snapshot preview"
            subtitle="The immutable generated return content — exactly what the exports render"
          >
            {pkgLoading ? (
              <SkeletonCard />
            ) : pkgError ? (
              <ErrorPanel error={pkgError} onRetry={onRetryPkg} />
            ) : pkg ? (
              <SnapshotPreview snapshot={pkg.snapshot} />
            ) : null}
          </SectionCard>

          <SectionCard
            title="Validation"
            subtitle="Completeness, internal consistency (cross-foots), and prior-period movement checks"
            actions={
              <button
                type="button"
                disabled={!canValidate || validate.isPending}
                onClick={() => validate.mutate(summary.id)}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {validate.isPending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <ShieldCheck size={13} aria-hidden />
                )}
                {status === 'validated' ? 'Re-validate' : 'Validate'}
              </button>
            }
          >
            {validate.error && (
              <div className="mb-3">
                <ErrorPanel error={validate.error} title="Validation call failed" />
              </div>
            )}
            {report ? (
              <ValidationPanel report={report} />
            ) : (
              <p className="text-caption text-slate">
                Not validated yet — run validation to unlock the approval
                request.
              </p>
            )}
          </SectionCard>

          <SectionCard
            title="Submission events"
            subtitle="Chronological channel trail — sandbox interactions are labeled"
            footer={
              latestSubmitted?.detail?.sandbox === true ? (
                <span className="inline-flex items-center gap-1.5">
                  <FlaskConical size={11} aria-hidden />
                  SANDBOX — simulated ORASS; the real portal API is not public
                </span>
              ) : undefined
            }
          >
            <QueryBoundary
              isLoading={eventsQuery.isLoading}
              error={eventsQuery.error}
              onRetry={() => eventsQuery.refetch()}
              skeleton={<SkeletonCard />}
            >
              <EventsFeed events={events} />
            </QueryBoundary>
          </SectionCard>
        </div>

        {/* Actions rail (right) */}
        <div className="space-y-6 min-w-0">
          <SectionCard
            title="Approval"
            subtitle="Maker-checker: a different officer decides on the Approvals tab"
          >
            <button
              type="button"
              disabled={!canRequestApproval || requestApproval.isPending}
              onClick={() => requestApproval.mutate({ packageId: summary.id })}
              className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {requestApproval.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <FileCheck2 size={13} aria-hidden />
              )}
              Request approval
            </button>
            <p className="mt-2 text-caption text-slate leading-relaxed">
              {status === 'pending_approval'
                ? 'Awaiting a checker decision — open the Approvals tab to decide as a second officer.'
                : validationBlocked
                ? 'Blocked: the latest validation report carries ERROR findings. Resolve and re-validate first.'
                : status === 'generated' || report === null
                ? 'Validate the package first; approval can only be requested for a validated package.'
                : status === 'validated'
                ? 'Validation passed — request approval to enter the maker-checker queue.'
                : `Package is '${status}'.`}
            </p>
            {requestApproval.error && (
              <div className="mt-3">
                <ErrorPanel
                  error={requestApproval.error}
                  title="Approval request failed"
                />
              </div>
            )}
          </SectionCard>

          <SectionCard
            title="Export artifacts"
            subtitle={`Renders the snapshot through the declarative ${regShort()} templates`}
          >
            <div className="flex items-center gap-2">
              {EXPORT_KINDS.map((kind) => (
                <button
                  key={kind}
                  type="button"
                  disabled={!canExport || exportPackage.isPending}
                  onClick={() => runExport(kind)}
                  className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60"
                >
                  {exportingKind === kind && exportPackage.isPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <FileOutput size={13} aria-hidden />
                  )}
                  {kind.toUpperCase()}
                </button>
              ))}
            </div>
            {exportPackage.error && (
              <div className="mt-3">
                <ErrorPanel error={exportPackage.error} title="Export failed" />
              </div>
            )}
            {downloadError && (
              <p className="mt-2 text-caption text-critical">{downloadError}</p>
            )}
            {artifacts.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {artifacts.map((artifact) => (
                  <li
                    key={artifact.id}
                    className="flex items-center gap-2 rounded border border-border-light bg-surface px-3 py-2"
                  >
                    <span className="font-mono text-caption font-medium text-navy uppercase">
                      {artifact.kind}
                    </span>
                    <span className="font-mono text-micro text-slate tnum truncate">
                      sha256 {shortId(artifact.checksumSha256, 12)}
                    </span>
                    <CopyButton text={artifact.checksumSha256} label="checksum" />
                    <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                      {fmtBytes(artifact.sizeBytes)}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleDownload(artifact)}
                      aria-label={`Download ${artifact.kind} artifact`}
                      className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate"
                    >
                      <Download size={11} aria-hidden />
                      Download
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 text-caption text-slate">
                No artifacts exported this session. Exports mint checksummed
                files in the outputs tier; submitting via a channel
                auto-exports xlsx when none exists.
              </p>
            )}
          </SectionCard>

          <SectionCard
            title="Submit"
            subtitle="Channel defaults to the registry entry for this return"
          >
            <label className="flex items-center justify-between gap-2 text-caption text-slate">
              Channel
              <select
                value={channel}
                onChange={(e) => setChannel(e.target.value as ChannelCode)}
                className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
              >
                {CHANNEL_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {CHANNEL_LABELS[option]}
                    {option === defaultChannel ? ' · default' : ''}
                  </option>
                ))}
              </select>
            </label>
            {channel === 'orass_sandbox' && (
              <p className="mt-2 inline-flex items-center gap-1.5 px-2 py-1 rounded border border-warning/25 bg-warning-light text-warning text-micro font-medium uppercase tracking-wider">
                <FlaskConical size={11} aria-hidden />
                SANDBOX — simulated ORASS
              </p>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                disabled={!canSubmit || submit.isPending}
                onClick={() => submit.mutate({ packageId: summary.id, channel })}
                className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {submit.isPending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <Send size={13} aria-hidden />
                )}
                Submit
              </button>
              <button
                type="button"
                disabled={!canPoll || poll.isPending}
                onClick={() => poll.mutate(summary.id)}
                className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60"
              >
                {poll.isPending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <RefreshCw size={13} aria-hidden />
                )}
                Poll status
              </button>
            </div>
            {!canSubmit && !canPoll && !canReupload && (
              <p className="mt-2 text-caption text-slate leading-relaxed">
                {status === 'submitted'
                  ? 'Submitted — awaiting the regulator decision.'
                  : status === 'acknowledged'
                  ? 'Acknowledged by the regulator — this obligation is complete.'
                  : status === 'rejected'
                  ? 'Rejected by the regulator — regenerate to mint a superseding version and rework it.'
                  : 'Submission unlocks once the package is approved.'}
              </p>
            )}
            {poll.data && (
              <p className="mt-2 text-caption text-navy/80">
                Last poll:{' '}
                <span className="font-mono">{poll.data.pollStatus}</span>
              </p>
            )}
            {poll.error && (
              <div className="mt-3">
                <ErrorPanel error={poll.error} title="Poll failed" />
              </div>
            )}
            {submit.error && !fallback && (
              <div className="mt-3">
                <ErrorPanel error={submit.error} title="Submission failed" />
              </div>
            )}

            {fallback && (
              <div className="mt-3 rounded border border-warning/30 bg-warning-light/50 px-3.5 py-3 space-y-2.5">
                <p className="inline-flex items-center gap-1.5 text-body font-medium text-navy">
                  <Mail size={13} className="text-warning" aria-hidden />
                  ORASS downtime — email fallback available
                </p>
                <p className="text-caption text-navy/80 leading-relaxed">
                  {fallback.message}
                </p>
                <button
                  type="button"
                  disabled={submit.isPending}
                  onClick={() =>
                    submit.mutate({ packageId: summary.id, channel: 'email' })
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  <Mail size={13} aria-hidden />
                  Use email fallback
                </button>
                {instructionsQuery.data && (
                  <details className="text-caption text-navy/80">
                    <summary className="cursor-pointer font-medium text-navy">
                      Preview send-ready instructions
                    </summary>
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-border-light bg-surface p-3 font-mono text-micro leading-relaxed">
                      {instructionsQuery.data.instructions}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </SectionCard>

          {priorVersions.length > 0 && (
            <SectionCard
              title="Prior versions"
              subtitle="Superseded snapshots remain immutable history"
              noPadding
            >
              <ul>
                {priorVersions.map((prior) => (
                  <li
                    key={prior.id}
                    className="flex items-center gap-3 px-5 py-2.5 border-b border-border-light last:border-b-0"
                  >
                    <span className="font-mono text-caption text-navy tnum">
                      v{prior.version}
                    </span>
                    <StatusPill tone="slate">Superseded</StatusPill>
                    <span className="ml-auto font-mono text-micro text-slate tnum">
                      {fmtTimestamp(prior.generatedAt)}
                    </span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}
        </div>
      </div>
    </div>
  );
}
