'use client';

/**
 * Regulatory Reporting — the Returns workspace.
 *
 * ## The screen shows the officer's own work, not the union of everyone's
 *
 * This page used to render Generate, Validate, Approve and Submit-to-the-
 * regulator in one column for everybody, each with a disabled state and a
 * blocked reason, and asked a preparer to infer from four greyed-out controls
 * that none of it was theirs (docs/filing_workflow_redesign.md §4b). The rule
 * now has two halves, and both are decided in `lib/submissions/returnsSurface`:
 *
 *  - **Absent** when the surface is not this role's. The regulator's channel,
 *    the downtime bundle, the submission trail and the resubmission workflow
 *    belong to the Validator. They are not greyed out for anyone else — this
 *    module does not contain their vocabulary at all, and a test asserts that.
 *  - **Disabled, with the reason on screen** when the control IS this officer's
 *    and it is not yet their turn.
 *
 * The role comes from the `/auth/me` projection for the ACTIVE institution and
 * the package's own position, never from a scalar session role. Before the
 * projection resolves, nothing actionable is rendered: a flash of the wrong
 * role's controls is the same defect in miniature.
 *
 * ## The return is the subject, so the return gets the page
 *
 * A command bar carries the identity, the artifacts an officer takes away, and
 * the single act available to them. Under it, one line says where the return is
 * and who holds it. Then the figures, full width, with their headline totals
 * spelled out and their line codes pinned while the currency columns scroll.
 * Everything else — certification detail, the channel, the trail, the earlier
 * versions — collapses to a row that states its own status, so it is opened
 * deliberately rather than competing with the numbers.
 *
 * Attestation (docs/attestation_esignature.md) remains a parallel dimension on
 * the package: the preparer certifies and freezes, an approver certifies the
 * identical frozen figures, and filing is gated on a complete attestation. The
 * certification card owns the ceremony; the command bar lifts its ACT to the top
 * so the officer meets one decision, in one place.
 */

import PageContainer from '@/components/ui/PageContainer';
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  BadgeCheck,
  CornerUpLeft,
  FileCheck2,
  Loader2,
  MessageSquareWarning,
  PenLine,
  PlayCircle,
  RefreshCw,
  ScrollText,
  Send,
  ShieldCheck,
  UploadCloud,
} from 'lucide-react';
import type {
  ArtifactKind,
  AttestationStatusRead,
  ChannelCode,
  PackageStatus,
  RegulatoryArtifactVersionRead,
  RegulatoryPackageRead,
  RegulatoryPackageSummaryRead,
  ReturnAnchorRead,
  ReturnTemplateRead,
  SigningRole,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import CopyButton from '@/components/ui/CopyButton';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import { useUserProfile } from '@/components/profile/ProfileProvider';
import { useImpersonation } from '@/components/impersonation/useImpersonation';
import {
  useDecideResubmission,
  useEmailFallbackInstructions,
  useExportRegulatoryPackage,
  useGenerateRegulatoryPackage,
  useOrganizationUsers,
  usePackageArtifactVersions,
  usePackageArtifacts,
  usePackageAttestation,
  usePollRegulatorySubmission,
  useComparePackageVersions,
  useRegulatoryPackage,
  useRegulatoryPackages,
  useRequestPackageApproval,
  useRequestResubmission,
  useResubmissionRequests,
  useReturnAnchors,
  useReturnTemplates,
  useSubmissionEvents,
  useSubmitRegulatoryPackage,
  useValidateRegulatoryPackage,
} from '@/lib/api/hooks';
import { hasAccountDirectoryAuthority } from '@/lib/api/accountAdministration';
import { fmtDateUTC, fmtTimestamp, isoDate, shortId } from '@/lib/api/values';
import {
  defaultReportingDate,
  reportingDateOptionLabel,
  toReportingDateOptions,
} from '@/lib/api/returnAnchors';
import {
  FIDELITY_INFO,
  FidelityPill,
  PackageStatusPill,
  RehearsalNotice,
  RehearsalPill,
  downloadArtifact,
  downloadArtifactVersion,
  downloadEmailFallbackEml,
  fmtBytes,
} from '@/components/submissions/shared';
import FilingChainPanel from '@/components/submissions/FilingChain';
import {
  ArtifactGroup,
  PrimaryActionButton,
  ReturnCommandBar,
} from '@/components/submissions/ReturnCommandBar';
import DisclosureRow from '@/components/submissions/DisclosureRow';
import PriorVersionsCard from '@/components/submissions/PriorVersionsCard';
import SnapshotPreview, {
  snapshotLineKey,
} from '@/components/submissions/SnapshotPreview';
import ChecksPanel, {
  checkCountSummary,
} from '@/components/submissions/ChecksPanel';
import EventsFeed from '@/components/submissions/EventsFeed';
import TransmissionCard, {
  REUPLOAD_CHANNEL,
  TransmissionNotice,
  filingRefusalMessage,
  transmissionSummary,
} from '@/components/submissions/TransmissionCard';
import ResubmissionCard from '@/components/submissions/ResubmissionCard';
import AttestationPanel from '@/components/attestation/AttestationPanel';
import {
  AttestationStatePill,
  SubmissionClearancePill,
  outstandingSummary,
  roleNoun,
} from '@/components/attestation/shared';
import { certificationSummary } from '@/lib/submissions/certificationSummary';
import { centralBankName, regShort } from '@/lib/format';
import {
  filingAuthorityFor,
  type FilingAuthority,
} from '@/lib/submissions/filingAuthority';
import {
  heldStages,
  noActionExplanation,
  primaryFilingAction,
  stageForStatus,
  surfaceSections,
  type SurfaceInput,
} from '@/lib/submissions/returnsSurface';

export default function ReturnsWorkspacePage() {
  // useSearchParams requires a Suspense boundary in the app router.
  return (
    <Suspense>
      <ReturnsWorkspace />
    </Suspense>
  );
}

/** Export order: the submission document first, then the workbooks, then CSV. */
const EXPORT_KINDS: ArtifactKind[] = ['pdf', 'xlsx', 'csv'];
const EXPORT_KINDS_WITH_FORMULAS: ArtifactKind[] = [
  'pdf',
  'xlsx',
  'xlsx_working',
  'csv',
];

/** The official regulator form family, where the live workbook is also filed. */
const BOG_FORM_FAMILY = 'bsd';

const BASIS_LABELS: Record<string, string> = {
  solo: 'Bank only',
  consolidated: 'Group',
};

function ReturnsWorkspace() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { bank, moduleScope } = useBankContext();
  const { effectiveAuthority, isLoading: profileLoading } = useUserProfile();
  const inspection = useImpersonation();
  const bankId = bank?.id;
  const isSdi = moduleScope.institutionClass === 'sdi';

  // Who this officer is on this institution's returns. Everything the screen
  // offers hangs off this, and until it resolves it offers nothing.
  const authority = filingAuthorityFor(effectiveAuthority, bankId, {
    resolved: moduleScope.isResolved && !profileLoading,
    readOnly: inspection.impersonating,
  });

  const templatesQuery = useReturnTemplates();
  const templates = useMemo(
    () =>
      (templatesQuery.data?.templates ?? []).filter((template) =>
        isSdi ? template.family === 'sdi' : template.family !== 'sdi'
      ),
    [isSdi, templatesQuery.data]
  );

  const codeParam = searchParams.get('code');
  const dateParam = searchParams.get('date');
  const code =
    codeParam && templates.some((tpl) => tpl.code === codeParam)
      ? codeParam
      : templates[0]?.code;

  // The reporting dates come from the RETURN — the regulator's cadence — not
  // from the bank's ingested reporting periods. Selecting from the latter made
  // the filing calendar a function of data arrival. The window runs BOTH ways,
  // so a bank a quarter behind is still offered the dates it already owes. The
  // one exception is an event-driven pack (the LRT corporate family): the
  // regulator sets no reporting date for it, so the backend offers the bank's
  // computed position dates instead and labels them as such.
  const anchorsQuery = useReturnAnchors(bankId, code);
  const anchors = useMemo<ReturnAnchorRead[]>(
    () => anchorsQuery.data?.anchors ?? [],
    [anchorsQuery.data]
  );
  const dateOptions = useMemo(() => toReportingDateOptions(anchors), [anchors]);
  const snapshotDated =
    anchorsQuery.data?.reportingDateSource === 'computed_snapshot';
  const anchorDates = useMemo(
    () => dateOptions.map((option) => option.date),
    [dateOptions]
  );
  const defaultDate = useMemo(
    () =>
      defaultReportingDate(
        dateOptions,
        anchorsQuery.data?.asOf ? isoDate(anchorsQuery.data.asOf) : undefined
      ),
    [dateOptions, anchorsQuery.data]
  );
  const date =
    dateParam && /^\d{4}-\d{2}-\d{2}$/.test(dateParam) ? dateParam : defaultDate;
  const selectedAnchor = anchors.find(
    (anchor) => isoDate(anchor.reportingDate) === date
  );
  const awaitingData = selectedAnchor?.dataStatus === 'awaiting_data';

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
  // The checks run WITH generation (docs/filing_workflow_redesign.md §4b.2):
  // machine validation is not a human act, so nobody presses a button to make
  // it happen. The service still exposes them as a separate call, so the client
  // makes the pair one gesture — and "Re-run checks" stays available beside
  // Regenerate for the case the button is actually for, which is source figures
  // that moved under a version that already exists.
  const runChecks = useValidateRegulatoryPackage(bankId);
  const generateAndCheck = (returnCode: string, reportingDate: string) =>
    generate.mutate(
      { returnCode, reportingDate },
      {
        onSuccess: (created) => {
          if (authority.mayRunChecks) runChecks.mutate(created.id);
        },
      }
    );

  // An ICAAP return is NOT generated from this workspace. It is minted by
  // freezing an ICAAP cycle, in one transaction with the cycle's seal, so the
  // filing and the assessment it came from cannot disagree. Offering the button
  // and letting the server refuse it would teach a preparer that the platform is
  // broken, rather than where the act lives.
  const isIcaapReturn =
    templates.find((entry) => entry.code === code)?.family === 'icaap';

  const ready = Boolean(code && date);

  return (
    <>
      <PageHeader
        eyebrow="Regulatory Reporting"
        title="Returns workspace"
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
                disabled={anchorsQuery.isLoading || anchorDates.length === 0}
                className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy disabled:opacity-60"
              >
                {dateOptions.map((option) => (
                  <option key={option.date} value={option.date}>
                    {reportingDateOptionLabel(option)}
                  </option>
                ))}
                {date && !anchorDates.includes(date) && (
                  <option value={date}>{date}</option>
                )}
              </select>
            </label>
            {snapshotDated && (
              <p
                data-testid="reporting-date-source"
                className="basis-full text-caption text-slate"
              >
                Event-driven pack — the regulator sets no reporting date for
                it. The dates offered are your computed position dates; the
                pack reports your institution as of the one you choose.
              </p>
            )}
          </div>
        }
      />

      <PageContainer className="py-6 space-y-4">
        {isSdi && (
          <SectionCard
            title="Specialised deposit-taking returns"
            subtitle="This workspace is scoped to the return family configured for this institution."
          >
            <p className="text-body text-slate leading-relaxed">
              Only applicable templates are shown. Where the regulator has not
              issued a licensed return template, the workspace stays
              intentionally unavailable rather than inventing a form.
            </p>
          </SectionCard>
        )}
        {template && <FidelityBanner template={template} />}

        <QueryBoundary
          contained
          isLoading={
            templatesQuery.isLoading ||
            anchorsQuery.isLoading ||
            packagesQuery.isLoading
          }
          error={
            templatesQuery.error ?? anchorsQuery.error ?? packagesQuery.error
          }
          onRetry={() => {
            void templatesQuery.refetch();
            void anchorsQuery.refetch();
            void packagesQuery.refetch();
          }}
          skeleton={
            <div className="space-y-4">
              <SkeletonCard />
              <SkeletonCard />
            </div>
          }
        >
          {anchorsQuery.data?.ineligibleReason ? (
            <EmptyState
              Icon={FileCheck2}
              title="This return does not apply to your institution"
              description={anchorsQuery.data.ineligibleReason}
            />
          ) : !ready ? (
            <EmptyState
              Icon={FileCheck2}
              title="Select a return and reporting date"
              description="Choose a registered return, then the reporting date it covers, to open its workspace."
            />
          ) : awaitingData && !current ? (
            <SectionCard
              title={`${code} · ${date}`}
              subtitle="No position has been computed as of this reporting date"
            >
              <p className="text-body text-slate leading-relaxed max-w-2xl">
                {code} reports your position on {date}, so it is built from your
                book as of that date. Nothing has been computed for it yet.
                {selectedAnchor?.nearestComputedBefore
                  ? ` Your most recent computed position is ${selectedAnchor.nearestComputedBefore} — an earlier book is not this date's position, so it is not used in its place.`
                  : ''}
              </p>
              {/* The remedy belongs to whoever can carry it out. This card used
                  to tell EVERY role to ingest a book and generate the return —
                  including an approver, who holds neither authority, and then
                  linked them to a data surface that is not theirs. Naming work
                  the reader cannot do reads as the product being broken. An
                  approver is told the true and useful thing instead: there is
                  nothing here for them yet. */}
              {authority.mayPrepare ? (
                <>
                  <p className="mt-3 text-body text-slate leading-relaxed max-w-2xl">
                    Upload or push the book as of {date} through the Data
                    Engine, then generate the return.
                  </p>
                  <div className="mt-4">
                    <Link
                      href="/data-engine"
                      data-testid="awaiting-data-ingest"
                      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                    >
                      <UploadCloud size={13} aria-hidden />
                      Go to Data Engine
                    </Link>
                  </div>
                </>
              ) : (
                <p
                  className="mt-3 text-body text-slate leading-relaxed max-w-2xl"
                  data-testid="awaiting-data-no-preparation"
                >
                  Nothing has been prepared for this reporting date, so there is
                  nothing here for you to review. It reaches you once a preparer
                  has generated it and sent it on.
                </p>
              )}
            </SectionCard>
          ) : !current ? (
            <FirstVersionCard
              code={code!}
              date={date!}
              isIcaapReturn={isIcaapReturn}
              authority={authority}
              onGenerate={() => code && date && generateAndCheck(code, date)}
              pending={generate.isPending || runChecks.isPending}
              error={generate.error}
            />
          ) : (
            <PackageWorkspace
              bankId={bankId!}
              authority={authority}
              summary={current}
              pkg={pkg}
              pkgLoading={packageQuery.isLoading}
              pkgError={packageQuery.error}
              onRetryPkg={() => packageQuery.refetch()}
              template={template}
              priorVersions={priorVersions}
              onRegenerate={() => code && date && generateAndCheck(code, date)}
              regeneratePending={generate.isPending || runChecks.isPending}
              regenerateError={generate.error ?? runChecks.error}
              chainRefreshing={
                generate.isPending ||
                runChecks.isPending ||
                packagesQuery.isFetching
              }
            />
          )}
        </QueryBoundary>
      </PageContainer>
    </>
  );
}

/**
 * Nothing exists for this return and date yet.
 *
 * Generating is the preparer's act. An officer who does not hold it is told who
 * does, rather than shown a button the server would refuse.
 */
function FirstVersionCard({
  code,
  date,
  isIcaapReturn,
  authority,
  onGenerate,
  pending,
  error,
}: {
  code: string;
  date: string;
  isIcaapReturn: boolean;
  authority: FilingAuthority;
  onGenerate: () => void;
  pending: boolean;
  error: unknown;
}) {
  return (
    <SectionCard
      title={`${code} · ${date}`}
      subtitle={
        isIcaapReturn
          ? 'This return is produced by sealing an ICAAP cycle'
          : 'No version of this return exists for this reporting date yet'
      }
    >
      <div className="flex items-start justify-between gap-4 flex-wrap">
        {isIcaapReturn ? (
          <>
            <p className="text-body text-slate leading-relaxed max-w-2xl">
              The ICAAP report is not generated here. It is produced when an
              ICAAP cycle is sealed at the end of its review chain, so that the
              filing and the assessment behind it are the same document.
            </p>
            <Link
              href="/icaap"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
            >
              Open ICAAP
            </Link>
          </>
        ) : (
          <>
            <p className="text-body text-slate leading-relaxed max-w-2xl">
              Generating mints an immutable, versioned snapshot from the figures
              already computed for this reporting date — no engine is re-run.
              Regenerating later supersedes that version; it never changes it.
            </p>
            {!authority.isResolved ? (
              <SkeletonCard />
            ) : authority.mayPrepare ? (
              <button
                type="button"
                disabled={pending}
                onClick={onGenerate}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
              >
                {pending ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden />
                ) : (
                  <PlayCircle size={13} aria-hidden />
                )}
                Generate the return
              </button>
            ) : (
              <p className="text-caption text-slate leading-relaxed max-w-sm">
                Generating this return is the preparer&apos;s act. It will appear
                here once they have built it.
              </p>
            )}
          </>
        )}
      </div>
      {error ? (
        <div className="mt-4">
          <ErrorPanel error={error} title="Could not generate the return" />
        </div>
      ) : null}
    </SectionCard>
  );
}

/**
 * Compact identity line for the selected return — the directive citation and
 * fidelity blurb collapse behind a disclosure rather than taking a full card.
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

function PackageWorkspace({
  bankId,
  authority,
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
  authority: FilingAuthority;
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
  /** A regeneration is in flight, so version-bound acts hold. */
  chainRefreshing: boolean;
}) {
  const { effectiveAuthority } = useUserProfile();
  const validate = useValidateRegulatoryPackage(bankId);
  const requestApproval = useRequestPackageApproval(bankId);
  const exportPackage = useExportRegulatoryPackage(bankId);
  const submit = useSubmitRegulatoryPackage(bankId);
  const poll = usePollRegulatorySubmission(bankId);
  const requestResubmission = useRequestResubmission(bankId);
  const decideResubmission = useDecideResubmission(bankId);
  const artifactsQuery = usePackageArtifacts(bankId, summary.id);
  // The append-only chain, where the SIGNED revisions live: the artifact list is
  // upserted per kind and therefore always names the unsigned export.
  const versionsQuery = usePackageArtifactVersions(bankId, summary.id);
  // Read for EVERY role: the chain names the regulator's own decisions, and a
  // preparer reworking a returned filing has to see them. Only the officer who
  // holds transmission is shown the channel trail itself.
  const eventsQuery = useSubmissionEvents(bankId, summary.id);
  const resubmissionsQuery = useResubmissionRequests(bankId, summary.id);
  const attestationQuery = usePackageAttestation(bankId, summary.id);
  // Names for the decision trail. Most preparers cannot read the organization
  // roster, so this is asked for only when the projection says it is readable —
  // an unresolved actor reads as "not named", never as a raw identifier.
  const usersQuery = useOrganizationUsers(
    hasAccountDirectoryAuthority(effectiveAuthority)
  );
  const resolveOfficer = useCallback(
    (actorUserId: string) => {
      const user = usersQuery.data?.users.find(
        (entry) => entry.id === actorUserId
      );
      if (!user) return null;
      return {
        name: user.displayName ?? user.email,
        title: user.jobTitle ?? null,
      };
    },
    [usersQuery.data]
  );

  const defaultChannel = template?.defaultChannel ?? 'manual';
  const [channel, setChannel] = useState<ChannelCode>(defaultChannel);
  useEffect(() => setChannel(defaultChannel), [defaultChannel, summary.id]);
  const [takingKind, setTakingKind] = useState<ArtifactKind | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [emlError, setEmlError] = useState<string | null>(null);
  // The ceremony is opened from the command bar and rendered by the
  // certification card, so the surface owns which role is signing.
  const [ceremonyRole, setCeremonyRole] = useState<SigningRole | null>(null);
  const [certificationOpen, setCertificationOpen] = useState(false);

  // The signature queue deep-links a colleague straight into the ceremony
  // (`?sign=approver`), and the SSO step-up comes back the same way. The
  // certification row has to be OPEN before that happens: the panel that reads
  // the deep link lives inside it, and a collapsed row would never mount it —
  // the request would land on a screen that quietly did nothing. Read from
  // `location` rather than `useSearchParams` so this stays in step with the
  // panel, which consumes and strips the parameters itself.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('sign') ?? params.get('certify')) setCertificationOpen(true);
  }, []);

  const status = pkg?.status ?? summary.status;
  const report = pkg?.validationReport ?? null;
  const checkErrors = report?.errorCount ?? 0;
  const checksClean = report !== null && report.passed && checkErrors === 0;
  const canRunChecks =
    authority.mayRunChecks && (status === 'generated' || status === 'validated');

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

  // The portal's structured refusal, read through the filing surface's own
  // helper: this module deliberately cannot spell that vocabulary.
  const filingRefusal = filingRefusalMessage(submit.error);
  const instructionsQuery = useEmailFallbackInstructions(
    bankId,
    summary.id,
    authority.mayTransmit && (Boolean(filingRefusal) || channel === 'email')
  );

  const attestation = attestationQuery.data ?? null;
  // The service computes clearance from the policy in force and the signatures
  // on record. Re-deriving it here is how a screen ends up offering a filing on
  // an unsigned return; an unread status blocks too.
  const clearedToSubmit = attestation?.canSubmit === true;
  const signingRequired = attestation?.policy.requireSignature ?? true;

  const artifacts = useMemo(
    () => artifactsQuery.data?.artifacts ?? [],
    [artifactsQuery.data]
  );
  const availableKinds = useMemo(
    () => new Set(artifacts.map((artifact) => artifact.kind as string)),
    [artifacts]
  );
  // At most one, and only once an officer has certified: the revision the last
  // signature pinned. It is what a filing sends, so it is what Download hands over.
  const filedVersion =
    versionsQuery.data?.versions.find((version) => version.isFiled) ?? null;
  const resubmissions = resubmissionsQuery.data?.requests ?? [];
  const submissionRevision =
    pkg?.submissionRevision ?? summary.submissionRevision;
  const regulatorComments = pkg?.regulatorComments ?? summary.regulatorComments;

  const supportsWorkingCopy = template?.supportsWorkingCopy ?? false;
  const exportKinds: ArtifactKind[] = supportsWorkingCopy
    ? EXPORT_KINDS_WITH_FORMULAS
    : EXPORT_KINDS;
  // Only the regulator's own form goes to them with its formulas live. Saying
  // "filed" over a calculation sheet would be the same overclaim the workbook
  // labels were corrected for.
  const formulaCopyIsFiled =
    supportsWorkingCopy && summary.returnFamily === BOG_FORM_FAMILY;

  const regulatorShortName = regShort();
  const regulatorFullName = centralBankName();

  const surfaceInput: SurfaceInput = {
    authority,
    status,
    hasPackage: true,
    checksClean,
    checkErrors,
    clearedToSubmit,
    outstandingSummary: attestation
      ? outstandingSummary(attestation.outstanding)
      : null,
    signingRequired,
    isRehearsal: summary.isRehearsal === true,
    pendingReupload,
    canPoll,
    regulatorName: regulatorShortName,
  };
  const action = primaryFilingAction(surfaceInput);
  const sections = surfaceSections(surfaceInput);
  const showSection = (section: (typeof sections)[number]) =>
    sections.includes(section);
  const atStage = stageForStatus(status);
  // The return has come BACK. This is the preparer's instruction, so it is a
  // banner rather than a line inside a collapsed history: §4b.5's "what has
  // been sent back to them, with the comment that sent it".
  const sentBack = useMemo(() => {
    if (atStage !== 'prepare') return null;
    const returned = (pkg?.approvals ?? [])
      .filter((approval) => approval.action === 'rejected')
      .sort((a, b) => a.occurredAt.getTime() - b.occurredAt.getTime())
      .at(-1);
    return returned ?? null;
  }, [atStage, pkg?.approvals]);
  const heldByViewer =
    atStage !== 'closed' &&
    atStage !== 'regulator' &&
    heldStages(authority).includes(atStage);

  // An approver re-reading a return after they sent it back should see what
  // moved, not re-read the whole book. The diff is the SERVER's — the platform
  // has to be able to stand behind the claim that a figure changed.
  const previousVersion = priorVersions[0] ?? null;
  const comparison = useComparePackageVersions(
    bankId,
    summary.id,
    previousVersion?.id,
    authority.mayApprove && previousVersion != null
  );
  const changedLineKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const section of comparison.data?.sections ?? []) {
      for (const line of section.lines) {
        if (line.change === 'changed' || line.change === 'added') {
          keys.add(snapshotLineKey(section.code, line.code));
        }
      }
    }
    return keys;
  }, [comparison.data]);

  const takeArtifact = (kind: ArtifactKind) => {
    setDownloadError(null);
    const fail = (error: unknown) =>
      setDownloadError(
        error instanceof Error ? error.message : 'The download did not finish.'
      );
    if (kind === 'pdf' && filedVersion) {
      downloadArtifactVersion(bankId, filedVersion).catch(fail);
      return;
    }
    const existing = artifacts.find((artifact) => artifact.kind === kind);
    if (existing) {
      downloadArtifact(bankId, existing).catch(fail);
      return;
    }
    setTakingKind(kind);
    exportPackage.mutate(
      { packageId: summary.id, kind },
      {
        onSuccess: (artifact) => {
          downloadArtifact(bankId, artifact).catch(fail);
        },
        onSettled: () => setTakingKind(null),
      }
    );
  };

  const handleEmlDownload = () => {
    setEmlError(null);
    downloadEmailFallbackEml(bankId, summary.id).catch((error: unknown) =>
      setEmlError(
        error instanceof Error ? error.message : 'The download did not finish.'
      )
    );
  };

  const runAction = () => {
    if (!action) return;
    switch (action.kind) {
      case 'generate':
      case 'regenerate':
        onRegenerate();
        return;
      case 'certify':
        if (signingRequired) {
          setCertificationOpen(true);
          setCeremonyRole('preparer');
        } else {
          requestApproval.mutate({ packageId: summary.id });
        }
        return;
      case 'review':
        setCertificationOpen(true);
        setCeremonyRole('approver');
        return;
      case 'transmit':
        submit.mutate({ packageId: summary.id, channel });
        return;
      case 'reupload':
        submit.mutate({ packageId: summary.id, channel: REUPLOAD_CHANNEL });
        return;
      case 'poll':
        poll.mutate(summary.id);
        return;
      default:
        return;
    }
  };

  const actionPending =
    regeneratePending ||
    submit.isPending ||
    poll.isPending ||
    requestApproval.isPending ||
    chainRefreshing;

  const actionIcon: Record<string, ReactNode> = {
    generate: <PlayCircle size={14} aria-hidden />,
    regenerate: <RefreshCw size={14} aria-hidden />,
    certify: <PenLine size={14} aria-hidden />,
    review: <BadgeCheck size={14} aria-hidden />,
    transmit: <Send size={14} aria-hidden />,
    reupload: <Send size={14} aria-hidden />,
    poll: <RefreshCw size={14} aria-hidden />,
  };

  const artifactSentence = formulaCopyIsFiled
    ? 'Four documents go with it: the signed submission document, the protected workbook, the workbook with live formulas, and the comma-separated copy. The formula workbook recalculates when a cell is edited and is never the signed record.'
    : supportsWorkingCopy
      ? 'The signed submission document, the protected workbook and the comma-separated copy go with it. The formula workbook is for internal review and is never filed.'
      : 'The signed submission document, the workbook and the comma-separated copy go with it.';

  return (
    <div className="space-y-4">
      <ReturnCommandBarBlock
        summary={summary}
        template={template}
        status={status}
        attestation={attestation}
        submissionRevision={submissionRevision}
        exportKinds={exportKinds}
        availableKinds={availableKinds}
        takingKind={takingKind}
        canExport={authority.mayExport}
        onTake={takeArtifact}
        filedVersion={filedVersion}
        action={action}
        actionPending={actionPending}
        actionIcon={action ? actionIcon[action.kind] : null}
        onAction={runAction}
        notice={
          authority.mayTransmit &&
          action !== null &&
          (action.kind === 'transmit' || action.kind === 'reupload') ? (
            <TransmissionNotice
              regulatorName={regulatorFullName}
              artifactSentence={artifactSentence}
              isRehearsal={summary.isRehearsal === true}
            />
          ) : null
        }
        explanation={action ? null : noActionExplanation(surfaceInput)}
        secondary={
          authority.mayPrepare || authority.mayRunChecks ? (
            <PreparationControls
              canRegenerate={authority.mayPrepare}
              onRegenerate={onRegenerate}
              regeneratePending={regeneratePending}
              canRunChecks={canRunChecks && !chainRefreshing}
              mayRunChecks={authority.mayRunChecks}
              onRunChecks={() => validate.mutate(summary.id)}
              runChecksPending={validate.isPending}
            />
          ) : null
        }
      />

      {summary.isRehearsal && <RehearsalNotice />}
      {regenerateError ? (
        <ErrorPanel error={regenerateError} title="Could not regenerate" />
      ) : null}
      {requestApproval.error ? (
        <ErrorPanel
          error={requestApproval.error}
          title="Could not send this for approval"
        />
      ) : null}
      {downloadError && (
        <p className="text-caption text-critical">{downloadError}</p>
      )}
      {regulatorComments && (
        <SupervisorCommentsPanel status={status} comments={regulatorComments} />
      )}
      {sentBack && (
        <SentBackNotice
          comment={sentBack.reason}
          at={sentBack.occurredAt}
          officer={resolveOfficer(sentBack.actorUserId)}
        />
      )}

      <FilingChainPanel
        status={status}
        approvals={pkg?.approvals ?? []}
        attestation={attestation}
        events={events}
        checksClean={checksClean}
        resolveOfficer={resolveOfficer}
        heldByViewer={heldByViewer}
        showRegulatorStage={authority.mayTransmit}
        defaultOpen={
          status === 'rejected' ||
          sentBack !== null ||
          (heldByViewer && atStage === 'transmit')
        }
      />

      {showSection('checks') && (
        <DisclosureRow
          title="Checks"
          testId="checks-row"
          tone={checkErrors > 0 ? 'attention' : 'default'}
          defaultOpen={checkErrors > 0}
          summary={
            validate.error
              ? 'The checks could not be run — see inside.'
              : report
                ? checkErrors > 0
                  ? `${checkErrors} to clear before these figures can be certified · ${checkCountSummary(report)}`
                  : `Every check passed · ${checkCountSummary(report)}`
                : 'Not run against this version yet.'
          }
        >
          {validate.error ? (
            <div className="mb-3">
              <ErrorPanel
                error={validate.error}
                title="The checks could not be run"
              />
            </div>
          ) : null}
          {report ? (
            <ChecksPanel report={report} />
          ) : (
            <p className="text-caption text-slate leading-relaxed">
              The checks have not run against this version yet. They run with
              generation; re-run them from the top of this screen if the source
              figures have moved since.
            </p>
          )}
        </DisclosureRow>
      )}

      {/* The return itself. Full width, because the figures are the content and
          an officer is about to attest to them. */}
      <SectionCard
        title="The return"
        subtitle="The immutable figures this version carries — exactly what the artifacts render"
      >
        <div>
          {pkgLoading ? (
            <SkeletonCard />
          ) : pkgError ? (
            <ErrorPanel error={pkgError} onRetry={onRetryPkg} />
          ) : pkg ? (
            <SnapshotPreview
              snapshot={pkg.snapshot}
              changedLineKeys={changedLineKeys}
              changedNote={
                previousVersion
                  ? `Marked figures changed since version ${previousVersion.version}, the one this replaced.`
                  : undefined
              }
            />
          ) : null}
        </div>
      </SectionCard>

      {showSection('certification') && (
        <DisclosureRow
          title="Certification"
          testId="certification-row"
          flush
          open={certificationOpen || ceremonyRole !== null}
          onOpenChange={setCertificationOpen}
          summary={
            attestation
              ? certificationSummary({
                  canSubmit: attestation.canSubmit,
                  signatureCount: attestation.signatures.length,
                  signedRoles: attestation.signatures.map((signature) =>
                    roleNoun(signature.signingRole).toLowerCase(),
                  ),
                  outstandingLabel: outstandingSummary(attestation.outstanding),
                })
              : 'Reading who has signed…'
          }
        >
          <AttestationPanel
            bankId={bankId}
            packageId={summary.id}
            returnLabel={`${summary.returnCode} · ${fmtDateUTC(summary.reportingDate)} v${summary.version}`}
            packageStatus={status}
            validationClean={checksClean}
            returnFamily={summary.returnFamily}
            certifyRole={ceremonyRole}
            onCertifyRoleChange={setCeremonyRole}
            showInlineCertifyActions={false}
          />
        </DisclosureRow>
      )}

      {showSection('transmission') && (
        <DisclosureRow
          title="Filing"
          testId="transmission-row"
          defaultOpen={atStage === 'transmit' || pendingReupload}
          summary={transmissionSummary({
            pendingReupload,
            refusal: filingRefusal,
            regulatorName: regulatorShortName,
          })}
        >
          <TransmissionCard
            channel={channel}
            defaultChannel={defaultChannel}
            onChannelChange={setChannel}
            latestSubmittedChannel={latestSubmitted?.channel ?? null}
            pendingReupload={pendingReupload}
            refusal={filingRefusal}
            instructions={instructionsQuery.data?.instructions ?? null}
            onUseEmailFallback={() =>
              submit.mutate({ packageId: summary.id, channel: 'email' })
            }
            onDownloadEml={handleEmlDownload}
            emlError={emlError}
            submitError={submit.error}
            pollStatus={poll.data?.pollStatus ?? null}
            pollError={poll.error}
            fallbackPending={submit.isPending}
          />
        </DisclosureRow>
      )}

      {showSection('events') && (
        <DisclosureRow
          title="Trail"
          testId="events-row"
          summary={
            events.length === 0
              ? 'Nothing has gone to the regulator yet.'
              : `${events.length} ${events.length === 1 ? 'entry' : 'entries'}, most recent first.`
          }
        >
          <QueryBoundary
            contained
            isLoading={eventsQuery.isLoading}
            error={eventsQuery.error}
            onRetry={() => eventsQuery.refetch()}
            skeleton={<SkeletonCard />}
          >
            <EventsFeed events={events} />
          </QueryBoundary>
        </DisclosureRow>
      )}

      {showSection('resubmission') && (
        <DisclosureRow
          title="Corrections"
          testId="resubmission-row"
          summary={
            resubmissions.length === 0
              ? 'No correction has been asked for.'
              : `${resubmissions.length} ${resubmissions.length === 1 ? 'request' : 'requests'} on record.`
          }
        >
          <ResubmissionCard
            status={status}
            requests={resubmissions}
            requestsError={resubmissionsQuery.error}
            latestSubmittedChannel={latestSubmitted?.channel ?? null}
            canRequest={status === 'submitted' || status === 'acknowledged'}
            onRequest={(reason) =>
              requestResubmission.mutate({ packageId: summary.id, reason })
            }
            requestPending={requestResubmission.isPending}
            requestError={requestResubmission.error}
            onDecide={(requestId, decision, note) =>
              decideResubmission.mutate({
                packageId: summary.id,
                requestId,
                decision,
                note,
              })
            }
            decidePending={decideResubmission.isPending}
            decideError={decideResubmission.error}
            regulatorName={regulatorFullName}
          />
        </DisclosureRow>
      )}

      {/* Only when there IS history. A row that opens onto nothing has cost the
          officer a click to learn what its own summary already said. */}
      {showSection('versions') && priorVersions.length > 0 && (
        <DisclosureRow
          title="Earlier versions"
          testId="versions-row"
          flush
          summary={`${priorVersions.length} superseded ${
            priorVersions.length === 1 ? 'version' : 'versions'
          }, kept unchanged as history.`}
        >
          <PriorVersionsCard bankId={bankId} packageId={summary.id} />
        </DisclosureRow>
      )}
    </div>
  );
}

/**
 * Regenerating and re-running the checks — the preparer's two secondary acts.
 *
 * "Re-run checks" is exactly that, and it sits beside Generate because that is
 * where it belongs: the checks run WITH generation, and re-running them is what
 * you do when the source figures have moved. It is not a lifecycle step and it
 * is not a person's decision, which is why there is no longer a "Validate"
 * button anywhere (docs/filing_workflow_redesign.md §4b.2).
 */
function PreparationControls({
  canRegenerate,
  onRegenerate,
  regeneratePending,
  canRunChecks,
  mayRunChecks,
  onRunChecks,
  runChecksPending,
}: {
  canRegenerate: boolean;
  onRegenerate: () => void;
  regeneratePending: boolean;
  canRunChecks: boolean;
  mayRunChecks: boolean;
  onRunChecks: () => void;
  runChecksPending: boolean;
}) {
  return (
    <div className="mt-2.5 flex items-center gap-2 flex-wrap">
      {canRegenerate && (
        <button
          type="button"
          disabled={regeneratePending}
          onClick={onRegenerate}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-caption font-medium text-navy hover:bg-surface disabled:opacity-60"
        >
          {regeneratePending ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : (
            <RefreshCw size={12} aria-hidden />
          )}
          Regenerate
        </button>
      )}
      {mayRunChecks && (
        <button
          type="button"
          disabled={!canRunChecks || runChecksPending}
          onClick={onRunChecks}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-caption font-medium text-navy hover:bg-surface disabled:opacity-60"
        >
          {runChecksPending ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : (
            <ShieldCheck size={12} aria-hidden />
          )}
          Re-run checks
        </button>
      )}
    </div>
  );
}

/** The command bar, assembled. Kept here so the page owns what goes in it. */
function ReturnCommandBarBlock({
  summary,
  template,
  status,
  attestation,
  submissionRevision,
  exportKinds,
  availableKinds,
  takingKind,
  canExport,
  onTake,
  filedVersion,
  action,
  actionPending,
  actionIcon,
  onAction,
  notice,
  explanation,
  secondary,
}: {
  summary: RegulatoryPackageSummaryRead;
  template: ReturnTemplateRead | undefined;
  status: PackageStatus;
  attestation: AttestationStatusRead | null;
  submissionRevision: string | null;
  exportKinds: ArtifactKind[];
  availableKinds: ReadonlySet<string>;
  takingKind: ArtifactKind | null;
  canExport: boolean;
  onTake: (kind: ArtifactKind) => void;
  filedVersion: RegulatoryArtifactVersionRead | null;
  action: ReturnType<typeof primaryFilingAction>;
  actionPending: boolean;
  actionIcon: ReactNode;
  onAction: () => void;
  notice: ReactNode;
  explanation: string | null;
  secondary: ReactNode;
}) {
  return (
    <>
      <ReturnCommandBar
        identity={
          <>
            <span className="font-mono">{summary.returnCode}</span>
            {template ? (
              <span className="ml-2 font-normal text-navy/80">
                {template.title}
              </span>
            ) : null}
          </>
        }
        meta={
          <>
            {fmtDateUTC(summary.reportingDate)} · Version {summary.version} ·{' '}
            {BASIS_LABELS[summary.basis] ?? summary.basis} · generated{' '}
            {fmtTimestamp(summary.generatedAt)}
          </>
        }
        pills={
          <>
            <PackageStatusPill status={status} />
            {summary.isRehearsal && <RehearsalPill />}
            {attestation && (
              <AttestationStatePill state={attestation.attestationState} />
            )}
            {attestation && <SubmissionClearancePill status={attestation} />}
            {submissionRevision && (
              <span
                title="Revision — a correction carries the next one"
                className="rounded border border-border px-1.5 py-0.5 font-mono text-caption text-slate tnum"
              >
                Rev {submissionRevision}
              </span>
            )}
          </>
        }
        artifacts={
          <>
            <ArtifactGroup
              kinds={exportKinds}
              available={availableKinds}
              busyKind={takingKind}
              canExport={canExport}
              onTake={onTake}
              signedLabelFor={(kind) =>
                kind === 'pdf' && filedVersion ? 'Signed PDF' : null
              }
              unavailableReason="This document has not been produced yet, and producing it is the preparer's act."
            />
            {filedVersion && <SignedReturnLine version={filedVersion} />}
          </>
        }
        notice={notice}
        action={
          action ? (
            <>
              <PrimaryActionButton
                action={action}
                pending={actionPending}
                onClick={onAction}
                icon={actionIcon}
                testId="primary-filing-action"
              />
              {secondary}
            </>
          ) : (
            <>
              <p
                data-testid="no-action-explanation"
                className="text-caption text-slate leading-relaxed"
              >
                {explanation}
              </p>
              {secondary}
            </>
          )
        }
      />
    </>
  );
}

/** Who signed the document the filing sends, named rather than inferred. */
function SignedReturnLine({
  version,
}: {
  version: RegulatoryArtifactVersionRead;
}) {
  const signer = version.signedBy;
  return (
    <p className="mt-1.5 flex items-center gap-1.5 text-micro text-slate">
      <ShieldCheck size={11} className="text-success shrink-0" aria-hidden />
      {signer
        ? `Signed by ${signer.signerDisplayName ?? 'an officer'}${
            signer.officerTitle ? ` — ${signer.officerTitle}` : ''
          }, ${fmtTimestamp(signer.signedAt)}`
        : 'Signed'}
      <span className="font-mono tnum">
        · {fmtBytes(version.sizeBytes)} · sha256{' '}
        {shortId(version.checksumSha256, 8)}
      </span>
      <CopyButton text={version.checksumSha256} label="checksum" />
    </p>
  );
}

/**
 * The approver sent it back, and this is what they said.
 *
 * On the record it is one decision among several; to the preparer it is the
 * only thing on this screen that tells them what to do next, so it is stated
 * before the figures rather than folded into a history nobody opens.
 */
function SentBackNotice({
  comment,
  at,
  officer,
}: {
  comment: string | null;
  at: Date;
  officer: { name: string; title: string | null } | null;
}) {
  return (
    <div
      data-testid="sent-back-notice"
      className="flex items-start gap-2.5 rounded border border-warning/25 bg-warning-light/50 px-3.5 py-2.5"
    >
      <CornerUpLeft size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
      <div className="min-w-0 text-body">
        <p className="font-medium text-navy">
          Sent back for corrections by{' '}
          {officer
            ? `${officer.name}${officer.title ? ` — ${officer.title}` : ''}`
            : 'the approver'}
          , {fmtTimestamp(at)}
        </p>
        <p className="mt-0.5 text-caption text-navy/85 leading-relaxed whitespace-pre-wrap">
          {comment ?? 'No comment was recorded with the decision.'}
        </p>
        <p className="mt-1 text-caption text-slate leading-relaxed">
          The certification that froze these figures has been withdrawn. Correct
          the source figures, regenerate, and send it again.
        </p>
      </div>
    </div>
  );
}

/**
 * The regulator's comments on the return. Critical framing on a refusal, which
 * is final; amber on a return for correction.
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
        <p className="font-medium text-navy">
          What the supervisor said about this return
        </p>
        <p className="mt-0.5 text-caption text-navy/80 leading-relaxed whitespace-pre-wrap">
          {comments}
        </p>
        {critical && (
          <p className="mt-1 text-caption font-medium text-critical">
            Refused — the decision on this return is final.
          </p>
        )}
      </div>
    </div>
  );
}
