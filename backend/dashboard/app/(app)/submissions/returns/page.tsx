'use client';

/**
 * Regulatory Reporting — Returns workspace. One return family + reporting
 * date at a time: generate/regenerate the immutable package version, preview
 * the snapshot, validate, request approval, export xlsx/csv/pdf artifacts,
 * submit via a channel (ORASS API / sandbox / email fallback / manual), poll
 * the regulator decision, read the submission-event trail, and run the
 * ORASS-parity resubmission workflow (request → grant/deny → regenerate the
 * corrected revision). Deep-linkable via ?code=&date= (the Calendar and
 * module pages link here).
 *
 * Attestation (docs/attestation_esignature.md) is a parallel, additive
 * dimension on the package rather than a replacement for the lifecycle above:
 * the preparer certifies and freezes, an approver certifies the identical
 * frozen figures, and submission is gated on a complete attestation. The
 * Attestation card owns those affordances.
 *
 * The workspace is STAGE-FOCUSED: the lifecycle stepper sits in its own
 * full-width card under the selectors, and the panel(s) an operator acts on at
 * the current stage (stageFor(status)) take the wide primary column. Every
 * other card stays mounted and reachable in the rail — nothing is ever
 * unmounted by the stage, because the e2e journeys (and operators) reach for
 * out-of-stage controls: the Submit card's disabled state and blocked reason
 * are asserted while the return is merely validated, the PDF export is pulled
 * on a freshly generated package, and prior versions are compared mid-chain.
 */

import { Fragment, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  CheckCircle2,
  Download,
  FileCheck2,
  FileOutput,
  FlaskConical,
  Loader2,
  Mail,
  MessageSquareWarning,
  PlayCircle,
  RadioTower,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Send,
  ShieldCheck,
  UploadCloud,
  XCircle,
} from 'lucide-react';
import type {
  ArtifactKind,
  ChannelCode,
  PackageStatus,
  RegulatoryArtifactVersionRead,
  RegulatoryPackageRead,
  RegulatoryPackageSummaryRead,
  ResubmissionRequestRead,
  ReturnTemplateRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import CopyButton from '@/components/ui/CopyButton';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import {
  useDecideResubmission,
  useEmailFallbackInstructions,
  useExportRegulatoryPackage,
  useGenerateRegulatoryPackage,
  usePackageArtifactVersions,
  usePackageArtifacts,
  usePackageAttestation,
  usePollRegulatorySubmission,
  useRegulatoryPackage,
  useRegulatoryPackages,
  useRequestPackageApproval,
  useRequestResubmission,
  useResubmissionRequests,
  useReturnTemplates,
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
  ResubmissionStatusPill,
  downloadArtifact,
  downloadArtifactVersion,
  downloadEmailFallbackEml,
  fmtBytes,
} from '@/components/submissions/shared';
import LifecycleStepper, {
  stageFor,
  type LifecycleStage,
} from '@/components/submissions/LifecycleStepper';
import PriorVersionsCard from '@/components/submissions/PriorVersionsCard';
import SnapshotPreview from '@/components/submissions/SnapshotPreview';
import ValidationPanel from '@/components/submissions/ValidationPanel';
import EventsFeed from '@/components/submissions/EventsFeed';
import AttestationPanel from '@/components/attestation/AttestationPanel';
import {
  AttestationStatePill,
  outstandingSummary,
} from '@/components/attestation/shared';
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

// Friendly artifact-kind labels. `xlsx_working` is the ALM/Finance working copy
// carrying formulas — a review aid, never a filing artifact. BSD forms preserve
// BoG's workbook formulas; SDI packets carry an explicit AequorOS working
// calculation sheet derived from the sealed snapshot.
const KIND_LABELS: Record<string, string> = {
  xlsx: 'XLSX',
  csv: 'CSV',
  pdf: 'PDF',
  xlsx_working: 'XLSX with formulas',
};
const CHANNEL_OPTIONS: ChannelCode[] = [
  'orass_api',
  'orass_sandbox',
  'email',
  'manual',
];

/** One line under the stepper naming what this stage is for. Wording is kept
 * deliberately distinct from the state-specific copy inside the cards — the
 * e2e journeys assert several of those phrases as SINGLE elements. */
const STAGE_HINTS: Record<LifecycleStage, string> = {
  generated: 'Review the snapshot, then run validation to unlock certification.',
  validated:
    'Validation passed — certify as preparer to freeze the figures and route the return for approval.',
  pending_approval:
    'Figures frozen — the named approver reviews, then approves and signs, or sends the return back with a note.',
  approved: 'Fully approved — export artifacts and submit through a channel.',
  submitted: 'With the regulator — poll the channel for a decision.',
  acknowledged: 'Complete — the regulator acknowledged this filing.',
  rejected:
    'The regulator returned this filing — read the comments, then rework it on a superseding version.',
  superseded: 'Superseded — a newer version of this return and reporting date exists.',
};

type PanelId =
  | 'snapshot'
  | 'validation'
  | 'approval'
  | 'export'
  | 'submit'
  | 'events'
  | 'resubmission'
  | 'prior_versions';

/** The panel(s) an operator acts on at each stage — promoted to the wide
 * primary column. Everything else stays mounted in the rail. */
const PRIMARY_PANELS: Record<LifecycleStage, PanelId[]> = {
  generated: ['snapshot', 'validation'],
  validated: ['validation', 'approval'],
  pending_approval: ['approval'],
  approved: ['export', 'submit'],
  submitted: ['events', 'submit'],
  acknowledged: ['submit', 'export'],
  rejected: ['resubmission', 'events'],
  superseded: ['prior_versions'],
};

/** Rail ordering: action cards first, then the history group. */
const RAIL_ACTIONS: PanelId[] = ['approval', 'export', 'submit', 'resubmission'];
const RAIL_HISTORY: PanelId[] = ['snapshot', 'validation', 'events', 'prior_versions'];

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
  const { bank, periods, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const isSdi = moduleScope.institutionClass === 'sdi';

  const templatesQuery = useReturnTemplates();
  const templates = useMemo(
    () =>
      (templatesQuery.data?.templates ?? []).filter((template) =>
        isSdi ? template.family === 'sdi' : template.family !== 'sdi'
      ),
    [isSdi, templatesQuery.data]
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
        subtitle={isSdi ? 'SDI return family · generate → validate → approve → export → submit, one immutable package version at a time' : 'Generate → validate → approve → export → submit, one immutable package version at a time'}
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
        {isSdi && (
          <SectionCard
            title="SDI regulatory returns"
            subtitle="This workspace is scoped to the return family configured for this specialised deposit-taking institution."
          >
            <p className="text-body text-slate leading-relaxed">
              Only applicable templates are shown. Where the regulator has not issued a licensed return template, the workspace remains intentionally unavailable rather than inventing a form.
            </p>
          </SectionCard>
        )}
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
              chainRefreshing={generate.isPending || packagesQuery.isFetching}
            />
          )}
        </QueryBoundary>
      </div>
    </>
  );
}

/**
 * Compact identity line for the selected return — the full directive citation
 * and fidelity blurb collapse behind a disclosure rather than occupying a
 * full-width card above the workspace. The `{code} — {title}` paragraph stays
 * always-visible: it is what confirms the selector landed on the right return
 * (and what the LRT deep-link journey asserts).
 */
function FidelityBanner({ template }: { template: ReturnTemplateRead }) {
  const info = FIDELITY_INFO[template.fidelity];
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2.5 flex-wrap">
        <ScrollText size={13} className="text-action shrink-0" aria-hidden />
        <p className="text-caption font-medium text-navy min-w-0">
          {template.code} — {template.title}
        </p>
        <FidelityPill fidelity={template.fidelity} />
      </div>
      <details className="mt-1 pl-6">
        <summary className="cursor-pointer text-caption font-medium text-action hover:text-action-hover">
          Directive basis
        </summary>
        <p className="mt-1 text-caption text-navy/80 max-w-3xl">{info.blurb}</p>
        <p className="mt-1 text-caption text-slate leading-relaxed max-w-3xl">
          {template.directiveCitation}
        </p>
      </details>
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
  chainRefreshing,
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
  /** A regeneration (or package-list refresh) is in flight, so the version on
   * screen may be about to be superseded — version-bound actions hold. */
  chainRefreshing: boolean;
}) {
  const validate = useValidateRegulatoryPackage(bankId);
  const requestApproval = useRequestPackageApproval(bankId);
  const exportPackage = useExportRegulatoryPackage(bankId);
  const submit = useSubmitRegulatoryPackage(bankId);
  const poll = usePollRegulatorySubmission(bankId);
  const artifactsQuery = usePackageArtifacts(bankId, summary.id);
  // The append-only chain, which is where the SIGNED revisions live: the
  // artifact list above is upserted per kind and therefore always names the
  // unsigned export.
  const versionsQuery = usePackageArtifactVersions(bankId, summary.id);
  const eventsQuery = useSubmissionEvents(bankId, summary.id);
  const resubmissionsQuery = useResubmissionRequests(bankId, summary.id);
  // Shared with <AttestationPanel /> through the query cache — read here so the
  // header pill and the Submit card can state the attestation gate honestly.
  const attestationQuery = usePackageAttestation(bankId, summary.id);

  const defaultChannel = template?.defaultChannel ?? 'manual';
  const [channel, setChannel] = useState<ChannelCode>(defaultChannel);
  useEffect(() => setChannel(defaultChannel), [defaultChannel, summary.id]);
  const [exportingKind, setExportingKind] = useState<ArtifactKind | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [emlError, setEmlError] = useState<string | null>(null);

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

  const attestation = attestationQuery.data ?? null;
  // Driven by `canSubmit` and by nothing looser. The service computes it from the
  // policy in force and the signatures on record; re-deriving it here from the
  // policy flag or the attestation state is how a screen ends up offering Submit
  // on an unsigned return. An unread status blocks too — a Submit button enabled
  // because a query has not answered yet is the same defect with better luck.
  const attestationBlocks = !attestation?.canSubmit;
  // Until the policy is known, assume signatures are required: the platform
  // default requires them, so the safe reading is the strict one.
  const signingRequired = attestation?.policy.requireSignature ?? true;
  const attestationBlockReason = !attestationBlocks
    ? null
    : attestation == null
      ? 'Blocked: the attestation status could not be read, so the signatures cannot be confirmed.'
      : `Blocked: this return is not fully certified. Outstanding — ${outstandingSummary(
          attestation.outstanding
        )}. See the Attestation card.`;

  const artifacts = artifactsQuery.data?.artifacts ?? [];
  // At most one, and only once an officer has certified: the revision the last
  // signature pinned. It is what submission files, so it is what Download has
  // to hand over.
  const filedVersion =
    versionsQuery.data?.versions.find((version) => version.isFiled) ?? null;
  const resubmissions = resubmissionsQuery.data?.requests ?? [];
  const submissionRevision =
    pkg?.submissionRevision ?? summary.submissionRevision;
  const regulatorComments = pkg?.regulatorComments ?? summary.regulatorComments;

  // The API declares working-copy support so the UI never guesses from a return
  // family. BSD forms preserve official workbook formulas; SDI packets expose a
  // reviewed calculation sheet derived from the sealed snapshot.
  const supportsWorkingCopy = template?.supportsWorkingCopy ?? false;
  const exportKinds: ArtifactKind[] = supportsWorkingCopy
    ? [...EXPORT_KINDS, 'xlsx_working']
    : EXPORT_KINDS;

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

  const handleVersionDownload = (version: { id: string; objectPath: string }) => {
    setDownloadError(null);
    downloadArtifactVersion(bankId, version).catch((error: unknown) =>
      setDownloadError(error instanceof Error ? error.message : 'Download failed.')
    );
  };

  const handleEmlDownload = () => {
    setEmlError(null);
    downloadEmailFallbackEml(bankId, summary.id).catch((error: unknown) =>
      setEmlError(error instanceof Error ? error.message : 'Download failed.')
    );
  };

  const stage = stageFor(status);
  // Pre-approval the certification card holds the full-width slot above the
  // columns; from approved onwards it retreats to the top of the rail — its
  // clearance pill answers the submission gate and must stay readable at every
  // stage. `generated` is deliberately in the full-width set even though
  // certifying is not yet possible there: regeneration flips a validated
  // package back to generated in place, and moving the panel between slots on
  // that transition remounts the certify button mid-click — the signing
  // workspace e2e journey clicks it exactly across that boundary. Position
  // stability across generated ↔ validated is part of the contract.
  const attestationPrimary =
    stage === 'generated' ||
    stage === 'validated' ||
    stage === 'pending_approval';

  /* Current version — identity, immutability, the supersession chain. */
  const revisionCard = (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2.5">
          {summary.returnCode} · {fmtDateUTC(summary.reportingDate)}
          <span className="font-mono text-caption text-slate tnum">
            v{summary.version}
          </span>
          <PackageStatusPill status={status} />
          {attestation && (
            <AttestationStatePill state={attestation.attestationState} />
          )}
          {submissionRevision && (
            <span
              title="Submission revision — resubmissions carry +0.1"
              className="font-mono text-caption text-slate tnum rounded border border-border px-1.5 py-0.5"
            >
              Rev {submissionRevision}
            </span>
          )}
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
      {Boolean(regenerateError) && (
        <div className="mb-3">
          <ErrorPanel error={regenerateError} title="Could not regenerate" />
        </div>
      )}
      {priorVersions.length > 0 ? (
        <p className="text-caption text-slate tnum">
          Superseded chain:{' '}
          <span className="font-mono text-navy/80">
            v{summary.version} (current)
            {priorVersions.map((prior) => ` ← v${prior.version}`).join('')}
          </span>
        </p>
      ) : (
        <p className="text-caption text-slate leading-relaxed">
          v{summary.version} is the only version on this chain — regenerating
          mints a new immutable version and supersedes this one; history is
          never mutated.
        </p>
      )}
    </SectionCard>
  );

  const supervisorBanner = regulatorComments && (
    <SupervisorCommentsPanel status={status} comments={regulatorComments} />
  );

  const reuploadBanner = pendingReupload && (
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
      {/* Also a submission, so also gated: the re-upload sends the filed
          document to the regulator, and every path that does reads the same
          clearance rather than trusting the earlier submission. */}
      <button
        type="button"
        disabled={submit.isPending || attestationBlocks}
        title={attestationBlockReason ?? undefined}
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
  );

  /* Certification — the signing ceremony is the gate everything downstream
     depends on. Full-width at the stages where certifying is the act in
     progress; top of the rail everywhere else. */
  const attestationCard = (
    <AttestationPanel
      bankId={bankId}
      packageId={summary.id}
      returnLabel={`${summary.returnCode} · ${fmtDateUTC(summary.reportingDate)} v${summary.version}`}
      packageStatus={status}
      validationClean={report !== null && !validationBlocked}
    />
  );

  const snapshotCard = (
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
  );

  const validationCard = (
    <SectionCard
      title="Validation"
      subtitle="Completeness, internal consistency (cross-foots), and prior-period movement checks"
      actions={
        /* Two contracts on this one control. Its accessible name starts with
           "Validate" in BOTH states ("Validate" / "Validate again"), because a
           regeneration can swap the on-screen version from validated to
           generated between a lookup and a click — a name that stops matching
           mid-swap silently skips validation and strands the new version
           unvalidated. And it LOCKS while the chain is refreshing for the same
           reason: a validate aimed at a version about to be superseded must
           wait and land on its successor. */
        <button
          type="button"
          disabled={!canValidate || validate.isPending || chainRefreshing}
          onClick={() => validate.mutate(summary.id)}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
        >
          {validate.isPending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <ShieldCheck size={13} aria-hidden />
          )}
          {status === 'validated' ? 'Validate again' : 'Validate'}
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
  );

  const eventsCard = (
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
  );

  const approvalCard = (
    <SectionCard
      title="Approval"
      subtitle={
        signingRequired
          ? 'Maker-checker: certifying as preparer is the request; a different officer approves and signs'
          : 'Maker-checker: a different officer decides on the Approvals tab'
      }
    >
      {/* Where signatures are required, the preparer's certification IS the
          request for approval — it freezes the figures and routes the return
          to the officer they name. Offering a separate Request approval
          button here would move the package out of 'validated', which is the
          only status a preparer certification is accepted from, leaving a
          return that neither officer can sign. */}
      {signingRequired ? (
        <p className="text-caption text-navy/85 leading-relaxed">
          {status === 'pending_approval'
            ? 'Sent for approval by the preparer’s certification — the named approver reviews, then approves and signs in one act, or sends it back with a note.'
            : status === 'validated'
            ? 'Validation passed. Certify as preparer in the Attestation card above: that freezes the figures and sends the return to the approver you name.'
            : validationBlocked
            ? 'Blocked: the latest validation report carries ERROR findings. Resolve and re-validate first.'
            : status === 'generated' || report === null
            ? 'Validate the package first; certification is only accepted for a validated package.'
            : `Package is '${status}'.`}
        </p>
      ) : (
        <>
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
        </>
      )}
      {requestApproval.error && (
        <div className="mt-3">
          <ErrorPanel
            error={requestApproval.error}
            title="Approval request failed"
          />
        </div>
      )}
    </SectionCard>
  );

  const exportCard = (
    <SectionCard
      title="Export artifacts"
      subtitle={`Renders the snapshot through the declarative ${regShort()} templates`}
    >
      <div className="flex flex-wrap items-center gap-2">
        {exportKinds.map((kind) => (
          <button
            key={kind}
            type="button"
            disabled={!canExport || exportPackage.isPending}
            onClick={() => runExport(kind)}
            className="flex-1 min-w-[5rem] inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60"
          >
            {exportingKind === kind && exportPackage.isPending ? (
              <Loader2 size={13} className="animate-spin" aria-hidden />
            ) : (
              <FileOutput size={13} aria-hidden />
            )}
            {KIND_LABELS[kind] ?? kind.toUpperCase()}
          </button>
        ))}
      </div>
      {supportsWorkingCopy && (
        <p className="mt-2 text-caption text-slate">
          <span className="font-medium text-navy">XLSX with formulas</span> is the
          official layout with the template&apos;s live formulas — for internal
          ALM/Finance review, never filed or signed.
        </p>
      )}
      {exportPackage.error && (
        <div className="mt-3">
          <ErrorPanel error={exportPackage.error} title="Export failed" />
        </div>
      )}
      {downloadError && (
        <p className="mt-2 text-caption text-critical">{downloadError}</p>
      )}
      {filedVersion && (
        <SignedReturnRow
          version={filedVersion}
          onDownload={() => handleVersionDownload(filedVersion)}
        />
      )}
      {artifacts.length > 0 ? (
        <>
          {filedVersion && (
            <p className="mt-4 text-micro font-medium uppercase tracking-wider text-slate">
              Pre-signature engine output
            </p>
          )}
          <ul className="mt-2 space-y-2">
            {artifacts.map((artifact) => (
              <li
                key={artifact.id}
                className="flex items-center gap-2 rounded border border-border-light bg-surface px-3 py-2"
              >
                <span className="font-mono text-caption font-medium text-navy uppercase">
                  {KIND_LABELS[artifact.kind] ?? artifact.kind}
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
          {filedVersion && (
            <p className="mt-2 text-caption text-slate leading-relaxed">
              Retained for provenance — what the engine rendered before
              anyone signed. It is never filed.
            </p>
          )}
        </>
      ) : (
        <p className="mt-3 text-caption text-slate">
          No artifacts exported yet. Exports mint checksummed files in
          the outputs tier; submitting via a channel auto-exports xlsx
          when none exists.
        </p>
      )}
    </SectionCard>
  );

  const submitCard = (
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
          data-testid="submit-package"
          disabled={!canSubmit || attestationBlocks || submit.isPending}
          title={attestationBlockReason ?? undefined}
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
      {/* Named, not merely refused: an operator staring at a greyed-out
          Submit needs to know which signature is missing, and the disabled
          title alone is invisible to a touch device. */}
      {attestationBlockReason && (
        <p
          data-testid="submit-blocked-reason"
          className="mt-2 text-caption text-navy/85 leading-relaxed"
        >
          {attestationBlockReason} No return reaches a channel without every
          signature the policy in force requires.
        </p>
      )}
      {!canSubmit && !canPoll && !canReupload && (
        <p className="mt-2 text-caption text-slate leading-relaxed">
          {status === 'submitted'
            ? 'Submitted — awaiting the regulator decision.'
            : status === 'acknowledged'
            ? 'Acknowledged by the regulator — this obligation is complete.'
            : status === 'rejected'
            ? 'Rejected by the regulator — regenerate to mint a superseding version and rework it.'
            : status === 'declined'
            ? 'Declined by the regulator — the decision is final; see the supervisor comments above.'
            : 'Submission unlocks once the package is approved.'}
        </p>
      )}
      {latestSubmitted?.channel === 'email' && (
        <div className="mt-3 space-y-1.5">
          <button
            type="button"
            onClick={handleEmlDownload}
            className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
          >
            <Download size={13} aria-hidden />
            Download .eml
          </button>
          <p className="text-micro text-slate leading-relaxed">
            Send-ready downtime email bundle (subject, instructions, and
            attachments) — open in your mail client and send.
          </p>
        </div>
      )}
      {emlError && (
        <p className="mt-2 text-caption text-critical">{emlError}</p>
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
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              disabled={submit.isPending || attestationBlocks}
              title={attestationBlockReason ?? undefined}
              onClick={() =>
                submit.mutate({ packageId: summary.id, channel: 'email' })
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              <Mail size={13} aria-hidden />
              Use email fallback
            </button>
            <button
              type="button"
              onClick={handleEmlDownload}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
            >
              <Download size={13} aria-hidden />
              Download .eml
            </button>
          </div>
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
  );

  const panels: Record<PanelId, ReactNode> = {
    snapshot: snapshotCard,
    validation: validationCard,
    approval: approvalCard,
    export: exportCard,
    submit: submitCard,
    events: eventsCard,
    resubmission: (
      <ResubmissionCard
        bankId={bankId}
        packageId={summary.id}
        status={status}
        requests={resubmissions}
        requestsError={resubmissionsQuery.error}
        latestSubmittedChannel={latestSubmitted?.channel ?? null}
      />
    ),
    prior_versions: <PriorVersionsCard bankId={bankId} packageId={summary.id} />,
  };

  const primaryIds = PRIMARY_PANELS[stage];
  const railActionIds = RAIL_ACTIONS.filter((id) => !primaryIds.includes(id));
  const railHistoryIds = RAIL_HISTORY.filter((id) => !primaryIds.includes(id));

  return (
    <div className="space-y-6">
      {/* Lifecycle — its own full-width card directly under the selectors;
          the stage it reports decides which panels take the primary column. */}
      <SectionCard title="Lifecycle" subtitle={STAGE_HINTS[stage]}>
        <LifecycleStepper status={status} />
      </SectionCard>

      {revisionCard}

      {supervisorBanner}

      {reuploadBanner}

      {attestationPrimary && attestationCard}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-start">
        {/* The current stage's primary panel(s) — wide column */}
        <div className="xl:col-span-2 space-y-6 min-w-0">
          {primaryIds.map((id) => (
            <Fragment key={id}>{panels[id]}</Fragment>
          ))}
        </div>

        {/* Everything else stays mounted and reachable: action cards first,
            then the history group. The e2e journeys drive out-of-stage
            controls (a disabled Submit while merely validated, a PDF export
            on a freshly generated package, prior-version diffs mid-chain), so
            nothing here may be collapsed away. */}
        <div className="space-y-6 min-w-0">
          {!attestationPrimary && attestationCard}
          {railActionIds.map((id) => (
            <Fragment key={id}>{panels[id]}</Fragment>
          ))}
          <div className="space-y-4">
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              History &amp; artifacts
            </p>
            {railHistoryIds.map((id) => (
              <Fragment key={id}>{panels[id]}</Fragment>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The document, once officers have certified it.
 *
 * Given top billing rather than being one row among the exports, because it is
 * the only one that is true: this revision carries the signatures, this revision
 * is what the channel files, and the exports beneath it are the render that
 * preceded it. Naming the signers here is the point — an operator downloading a
 * return before a deadline needs to see whose signature they are sending, not
 * infer it from a filename.
 */
function SignedReturnRow({
  version,
  onDownload,
}: {
  version: RegulatoryArtifactVersionRead;
  onDownload: () => void;
}) {
  const signer = version.signedBy;
  return (
    <div className="mt-3 rounded border border-success/30 bg-success-light/40 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <ShieldCheck size={13} className="text-success shrink-0" aria-hidden />
        <span className="text-caption font-medium text-navy">
          Signed return — filed to {regShort()}
        </span>
        <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
          {fmtBytes(version.sizeBytes)}
        </span>
      </div>
      {signer && (
        <p className="mt-1 text-caption text-slate leading-relaxed">
          Last signed by {signer.signerDisplayName ?? signer.signerId}
          {signer.officerTitle ? ` — ${signer.officerTitle}` : ''} as{' '}
          {signer.signingRole}, {fmtTimestamp(signer.signedAt)}.
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <span className="font-mono text-micro text-slate tnum truncate">
          sha256 {shortId(version.checksumSha256, 12)}
        </span>
        <CopyButton text={version.checksumSha256} label="checksum" />
        <button
          type="button"
          onClick={onDownload}
          aria-label="Download signed return"
          className="ml-auto inline-flex items-center gap-1.5 rounded px-2.5 py-1.5 text-micro font-medium btn-primary"
        >
          <Download size={11} aria-hidden />
          Download
        </button>
      </div>
    </div>
  );
}

/**
 * Regulator ("supervisor") comments carried on the package — ORASS "View
 * Comments" parity. Critical framing on a declined package (final decision),
 * amber otherwise (rejected / resubmission feedback).
 */
function SupervisorCommentsPanel({
  status,
  comments,
}: {
  status: PackageStatus;
  comments: string;
}) {
  const critical = status === 'declined';
  return (
    <div
      className={`flex items-start gap-2.5 rounded border px-3.5 py-2.5 ${
        critical
          ? 'border-critical/25 bg-critical-light/50'
          : 'border-warning/25 bg-warning-light/50'
      }`}
    >
      <MessageSquareWarning
        size={15}
        className={`${critical ? 'text-critical' : 'text-warning'} shrink-0 mt-0.5`}
        aria-hidden
      />
      <div className="min-w-0 text-body">
        <p className="font-medium text-navy">Supervisor comments</p>
        <p className="mt-0.5 text-caption text-navy/80 leading-relaxed whitespace-pre-wrap">
          {comments}
        </p>
        {critical && (
          <p className="mt-1 text-caption font-medium text-critical">
            Declined — the regulator&apos;s decision on this return is final.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * ORASS-parity resubmission workflow for a submitted/acknowledged package:
 * file a request (reason required); ORASS-channel requests are decided by
 * the portal, while email/manual submissions record the regulator's offline
 * grant/deny here. A granted request unlocks the corrected revision (+0.1)
 * via regeneration.
 */
function ResubmissionCard({
  bankId,
  packageId,
  status,
  requests,
  requestsError,
  latestSubmittedChannel,
}: {
  bankId: string;
  packageId: string;
  status: PackageStatus;
  requests: ResubmissionRequestRead[];
  requestsError: unknown;
  latestSubmittedChannel: ChannelCode | null;
}) {
  const request = useRequestResubmission(bankId);
  const decide = useDecideResubmission(bankId);
  const [formOpen, setFormOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  const canRequest = status === 'submitted' || status === 'acknowledged';
  if (!canRequest && requests.length === 0) return null;

  const manualDecide =
    latestSubmittedChannel === 'email' || latestSubmittedChannel === 'manual';
  const hasOpenRequest = requests.some((entry) => entry.status === 'requested');
  const grantedPending = requests.some(
    (entry) => entry.status === 'granted' && entry.consumedByPackageId == null
  );

  const runDecision = (requestId: string, decision: 'granted' | 'denied') =>
    decide.mutate(
      { packageId, requestId, decision, note: note.trim() || undefined },
      { onSuccess: () => setNote('') }
    );

  return (
    <SectionCard
      title="Resubmission"
      subtitle="Corrections to an already-submitted return require the regulator's go-ahead"
    >
      {canRequest && !hasOpenRequest && (
        <>
          {!formOpen ? (
            <button
              type="button"
              onClick={() => setFormOpen(true)}
              className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface"
            >
              <RotateCcw size={13} aria-hidden />
              Request resubmission
            </button>
          ) : (
            <div className="space-y-2">
              <label
                className="block text-caption font-medium text-navy"
                htmlFor="resubmission-reason"
              >
                Reason <span className="font-normal text-slate">(required)</span>
              </label>
              <textarea
                id="resubmission-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder="e.g. Corrected HQLA misclassification found after submission."
                className="w-full rounded border border-border bg-surface-raised px-2.5 py-2 text-body text-navy placeholder:text-slate-light"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={request.isPending || reason.trim().length === 0}
                  onClick={() =>
                    request.mutate(
                      { packageId, reason: reason.trim() },
                      {
                        onSuccess: () => {
                          setFormOpen(false);
                          setReason('');
                        },
                      }
                    )
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
                >
                  {request.isPending ? (
                    <Loader2 size={13} className="animate-spin" aria-hidden />
                  ) : (
                    <RotateCcw size={13} aria-hidden />
                  )}
                  File request
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setFormOpen(false);
                    setReason('');
                  }}
                  className="inline-flex items-center px-3 py-2 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {request.error && (
        <div className="mt-3">
          <ErrorPanel error={request.error} title="Resubmission request failed" />
        </div>
      )}
      {Boolean(requestsError) && (
        <div className="mt-3">
          <ErrorPanel
            error={requestsError}
            title="Could not load resubmission requests"
          />
        </div>
      )}

      {grantedPending && (
        <p className="mt-3 rounded border border-success/25 bg-success-light/50 px-3 py-2 text-caption text-navy/85 leading-relaxed">
          Resubmission granted — regenerate to mint the corrected version; the
          next submission carries revision +0.1.
        </p>
      )}

      {requests.length > 0 && (
        <ul className="mt-3 space-y-2">
          {requests.map((entry) => (
            <li
              key={entry.id}
              className="rounded border border-border-light bg-surface px-3 py-2 space-y-1.5"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <ResubmissionStatusPill status={entry.status} />
                <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
                  {fmtTimestamp(entry.occurredAt)}
                </span>
              </div>
              <p className="text-caption text-navy/80 leading-relaxed">
                {entry.reason}
              </p>
              {entry.decidedAt && (
                <p className="font-mono text-micro text-slate tnum">
                  decided {fmtTimestamp(new Date(entry.decidedAt))}
                </p>
              )}
              {entry.status === 'requested' && manualDecide && (
                <div className="pt-1 space-y-1.5">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Decision note (optional)"
                    aria-label="Decision note"
                    className="w-full rounded border border-border bg-surface-raised px-2.5 py-1.5 text-caption text-navy placeholder:text-slate-light"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={decide.isPending}
                      onClick={() => runDecision(entry.id, 'granted')}
                      className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-caption font-medium text-success border border-success/30 bg-success-light/40 rounded-md hover:bg-success-light disabled:opacity-60"
                    >
                      <CheckCircle2 size={13} aria-hidden />
                      Grant
                    </button>
                    <button
                      type="button"
                      disabled={decide.isPending}
                      onClick={() => runDecision(entry.id, 'denied')}
                      className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-caption font-medium text-critical border border-critical/30 bg-critical-light/40 rounded-md hover:bg-critical-light disabled:opacity-60"
                    >
                      <XCircle size={13} aria-hidden />
                      Deny
                    </button>
                  </div>
                  <p className="text-micro text-slate leading-relaxed">
                    Email/manual submissions: record the regulator&apos;s
                    offline decision here. ORASS-channel requests are decided
                    by the portal poll.
                  </p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {decide.error && (
        <div className="mt-3">
          <ErrorPanel error={decide.error} title="Decision failed" />
        </div>
      )}
    </SectionCard>
  );
}
