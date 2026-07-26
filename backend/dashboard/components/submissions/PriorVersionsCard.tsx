'use client';

/**
 * Prior versions — the superseded chain, made answerable.
 *
 * The card used to list a SUPERSEDED pill and a timestamp per row, which
 * answers none of the three questions actually asked about a prior filing:
 * who certified it, which file went to the regulator, and what changed since.
 * Each row now expands onto those three, in that order.
 *
 * Two rules the rendering exists to keep:
 *
 * - **No dead affordance.** A version that was never exported holds no file,
 *   and the row says so rather than offering a download that cannot resolve.
 *   The comparison stays available on that same row: the snapshot is immutable
 *   and always present, so the figures are answerable even when no file is.
 * - **A withdrawn signature never looks current.** A void preserves its
 *   signatures instead of deleting them, so a version can read `unsigned` and
 *   still hold two — struck through, muted, and labelled with the cycle they
 *   were withdrawn from.
 *
 * The diff itself is computed server-side (`comparePackageVersions`) and only
 * rendered here; nothing in this file decides what changed.
 */

import { useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Download,
  FileX2,
  GitCompareArrows,
  Loader2,
  ShieldCheck,
} from 'lucide-react';
import type {
  PackageComparisonRead,
  PackageVersionChainEntryRead,
  PackageVersionSignatureRead,
  RegulatoryArtifactVersionRead,
  SnapshotSectionDiffRead,
} from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import {
  useComparePackageVersions,
  usePackageVersionChain,
} from '@/lib/api/hooks';
import { fmtTimestamp, shortId } from '@/lib/api/values';
import {
  AttestationStatePill,
  fmtSignatureTimestamp,
  roleAttribution,
} from '@/components/attestation/shared';
import {
  downloadArtifact,
  downloadArtifactVersion,
  fmtBytes,
} from '@/components/submissions/shared';
import { fmtSnapshotCell } from '@/components/submissions/SnapshotPreview';
import type { AttestationState } from '@aequoros/risk-service-api';

const CHANGE_TONES: Record<string, StatusTone> = {
  added: 'action',
  removed: 'critical',
  changed: 'amber',
};

const CHANGE_LABELS: Record<string, string> = {
  added: 'Added',
  removed: 'Removed',
  changed: 'Changed',
};

/** A movement carries its sign explicitly — the parenthesised-negative
 * convention reads as a balance, not as a direction of travel. */
function fmtDelta(delta: string | null): string {
  if (delta === null) return '—';
  const parsed = Number(delta);
  if (!Number.isFinite(parsed)) return delta;
  const magnitude = Math.abs(parsed).toLocaleString('en-GB', {
    maximumFractionDigits: 2,
  });
  if (parsed === 0) return '0';
  return `${parsed > 0 ? '+' : '−'}${magnitude}`;
}

function SignatureLine({ signature }: { signature: PackageVersionSignatureRead }) {
  const name = signature.signerDisplayName ?? signature.signerId;
  return (
    <li
      className={`flex items-start gap-2 ${
        signature.withdrawn ? 'text-slate' : 'text-navy/85'
      }`}
    >
      <ShieldCheck
        size={12}
        aria-hidden
        className={`mt-0.5 shrink-0 ${
          signature.withdrawn ? 'text-slate' : 'text-success'
        }`}
      />
      <span className="min-w-0 text-caption leading-relaxed">
        <span className={signature.withdrawn ? 'line-through' : ''}>
          {roleAttribution(signature.signingRole)} {name}
          {signature.officerTitle ? ` — ${signature.officerTitle}` : ''},{' '}
          {fmtSignatureTimestamp(signature.signedAt)}
        </span>{' '}
        <span className="font-mono text-micro text-slate">
          {signature.signerId}
        </span>
        {signature.withdrawn && (
          <span className="ml-1.5 inline-flex items-center rounded border border-border px-1.5 py-0.5 text-micro font-medium uppercase tracking-wider text-slate">
            Withdrawn · cycle {signature.attestationCycle}
          </span>
        )}
      </span>
    </li>
  );
}

function VersionFileRow({
  label,
  detail,
  checksum,
  sizeBytes,
  onDownload,
  ariaLabel,
}: {
  label: string;
  detail?: string;
  checksum: string;
  sizeBytes: number;
  onDownload: () => void;
  ariaLabel: string;
}) {
  return (
    <li className="flex items-center gap-2 rounded border border-border-light bg-surface px-3 py-2">
      <span className="font-mono text-caption font-medium text-navy uppercase whitespace-nowrap">
        {label}
      </span>
      {detail && (
        <span className="text-caption text-slate truncate">{detail}</span>
      )}
      <span className="font-mono text-micro text-slate tnum truncate">
        sha256 {shortId(checksum, 12)}
      </span>
      <CopyButton text={checksum} label="checksum" />
      <span className="ml-auto font-mono text-micro text-slate tnum whitespace-nowrap">
        {fmtBytes(sizeBytes)}
      </span>
      <button
        type="button"
        onClick={onDownload}
        aria-label={ariaLabel}
        className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-micro font-medium text-slate hover:text-navy hover:border-slate"
      >
        <Download size={11} aria-hidden />
        Download
      </button>
    </li>
  );
}

function signedRevisionLabel(version: RegulatoryArtifactVersionRead): string {
  const signer = version.signedBy;
  if (!signer) return 'Archived render';
  const name = signer.signerDisplayName ?? signer.signerId;
  return `Signed by ${name} as ${signer.signingRole}`;
}

function ComparisonTable({
  section,
}: {
  section: SnapshotSectionDiffRead;
}) {
  return (
    <div className="rounded border border-border-light overflow-hidden">
      <div className="flex items-center gap-2 flex-wrap px-3 py-2 bg-surface border-b border-border-light">
        <span className="text-caption font-medium text-navy">
          {section.title || section.code}
        </span>
        <StatusPill tone={CHANGE_TONES[section.change] ?? 'slate'}>
          {CHANGE_LABELS[section.change] ?? section.change}
        </StatusPill>
        {section.unchangedLineCount > 0 && (
          <span className="ml-auto text-micro text-slate">
            {section.unchangedLineCount} line
            {section.unchangedLineCount === 1 ? '' : 's'} unchanged
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-caption">
          <thead>
            <tr className="text-micro uppercase tracking-wider text-slate">
              <th className="text-left font-medium px-3 py-1.5">Row</th>
              <th className="text-left font-medium px-3 py-1.5">Item</th>
              <th className="text-right font-medium px-3 py-1.5">Was</th>
              <th className="text-right font-medium px-3 py-1.5">Now</th>
              <th className="text-right font-medium px-3 py-1.5">Movement</th>
            </tr>
          </thead>
          <tbody>
            {section.lines.map((line, index) => (
              <tr
                key={`${line.code}-${index}`}
                className="border-t border-border-light"
              >
                <td className="px-3 py-1.5 font-mono text-micro text-slate whitespace-nowrap">
                  {line.code}
                  {line.isTotal && (
                    <span className="ml-1 text-micro uppercase">· total</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-navy/90">{line.description}</td>
                <td className="px-3 py-1.5 text-right font-mono tnum text-slate">
                  {line.change === 'added' ? '—' : fmtSnapshotCell(line.baseValue)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tnum text-navy">
                  {line.change === 'removed'
                    ? '—'
                    : fmtSnapshotCell(line.targetValue)}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tnum text-navy/80 whitespace-nowrap">
                  {fmtDelta(line.delta)}
                  {line.deltaPct !== null && (
                    <span className="ml-1 text-micro text-slate">
                      ({line.deltaPct}%)
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ComparisonPanel({
  comparison,
  currentVersion,
}: {
  comparison: PackageComparisonRead;
  currentVersion: number;
}) {
  if (comparison.identical) {
    return (
      <p className="text-caption text-navy/85 leading-relaxed">
        No figure differs from v{currentVersion}. Every line item in{' '}
        {comparison.unchangedSectionCount} section
        {comparison.unchangedSectionCount === 1 ? '' : 's'} matches — this
        version was superseded without any change to the figures.
      </p>
    );
  }
  return (
    <div className="space-y-2.5">
      <p className="text-caption text-navy/85">
        Against v{currentVersion}:{' '}
        <span className="font-medium">{comparison.changedCount} changed</span>,{' '}
        {comparison.addedCount} added, {comparison.removedCount} removed across{' '}
        {comparison.sections.length} section
        {comparison.sections.length === 1 ? '' : 's'}.
      </p>
      {comparison.sections.map((section) => (
        <ComparisonTable
          key={`${section.origin}-${section.code}`}
          section={section}
        />
      ))}
    </div>
  );
}

function PriorVersionRow({
  bankId,
  entry,
  currentPackageId,
  currentVersion,
}: {
  bankId: string;
  entry: PackageVersionChainEntryRead;
  currentPackageId: string | null;
  currentVersion: number | null;
}) {
  const [open, setOpen] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const comparison = useComparePackageVersions(
    bankId,
    entry.packageId,
    currentPackageId,
    comparing
  );

  const failed = (error: unknown) =>
    setDownloadError(error instanceof Error ? error.message : 'Download failed.');

  return (
    <li className="border-b border-border-light last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 px-5 py-2.5 text-left hover:bg-surface"
      >
        {open ? (
          <ChevronDown size={13} className="text-slate shrink-0" aria-hidden />
        ) : (
          <ChevronRight size={13} className="text-slate shrink-0" aria-hidden />
        )}
        <span className="font-mono text-caption text-navy tnum">
          v{entry.version}
        </span>
        <StatusPill tone="slate">Superseded</StatusPill>
        <AttestationStatePill
          state={entry.attestationState as AttestationState}
        />
        <span className="text-micro text-slate">
          {entry.hasRetrievableFiles
            ? `${entry.artifacts.length + entry.artifactVersions.length} file${
                entry.artifacts.length + entry.artifactVersions.length === 1
                  ? ''
                  : 's'
              }`
            : 'Never exported'}
        </span>
        <span className="ml-auto font-mono text-micro text-slate tnum">
          {fmtTimestamp(entry.generatedAt)}
        </span>
      </button>

      {open && (
        <div className="px-5 pb-4 pt-1 space-y-4 bg-surface/40">
          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Certification
            </p>
            {entry.signatures.length > 0 ? (
              <ul className="mt-1.5 space-y-1">
                {entry.signatures.map((signature) => (
                  <SignatureLine
                    key={signature.signatureId}
                    signature={signature}
                  />
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 text-caption text-slate">
                No signature was ever recorded on this version.
              </p>
            )}
            {entry.voidReason && (
              <p className="mt-1.5 text-caption text-slate leading-relaxed">
                Attestation withdrawn
                {/* Optional datetimes generate as `string | null` while required
                    ones generate as `Date`, so they are wrapped at the call
                    site — the same shape AttestationPanel uses. */}
                {entry.voidedAt ? ` ${fmtTimestamp(new Date(entry.voidedAt))}` : ''}:{' '}
                {entry.voidReason}
              </p>
            )}
          </div>

          <div>
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              Files
            </p>
            {entry.hasRetrievableFiles ? (
              <ul className="mt-1.5 space-y-2">
                {entry.artifactVersions.map((version) => (
                  <VersionFileRow
                    key={version.id}
                    label={version.kind}
                    detail={signedRevisionLabel(version)}
                    checksum={version.checksumSha256}
                    sizeBytes={version.sizeBytes}
                    ariaLabel={`Download v${entry.version} ${version.kind} revision`}
                    onDownload={() => {
                      setDownloadError(null);
                      downloadArtifactVersion(bankId, version).catch(failed);
                    }}
                  />
                ))}
                {entry.artifacts.map((artifact) => (
                  <VersionFileRow
                    key={artifact.id}
                    label={artifact.kind}
                    detail="Engine export"
                    checksum={artifact.checksumSha256}
                    sizeBytes={artifact.sizeBytes}
                    ariaLabel={`Download v${entry.version} ${artifact.kind} export`}
                    onDownload={() => {
                      setDownloadError(null);
                      downloadArtifact(bankId, artifact).catch(failed);
                    }}
                  />
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 inline-flex items-start gap-1.5 text-caption text-slate leading-relaxed">
                <FileX2 size={13} className="mt-0.5 shrink-0" aria-hidden />
                Never exported — no artifact was ever rendered for this version,
                so there is no file to retrieve. Its figures are still comparable
                below.
              </p>
            )}
            {downloadError && (
              <p className="mt-2 text-caption text-critical">{downloadError}</p>
            )}
          </div>

          {currentPackageId && currentVersion !== null && (
            <div>
              <div className="flex items-center gap-2">
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  Figures
                </p>
                <button
                  type="button"
                  onClick={() => setComparing(true)}
                  disabled={comparing}
                  className="ml-auto inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-micro font-medium text-navy hover:bg-surface disabled:opacity-60"
                >
                  {comparison.isFetching ? (
                    <Loader2 size={11} className="animate-spin" aria-hidden />
                  ) : (
                    <GitCompareArrows size={11} aria-hidden />
                  )}
                  Compare with current
                </button>
              </div>
              {comparison.error && (
                <div className="mt-2">
                  <ErrorPanel
                    error={comparison.error}
                    title="Could not compare"
                  />
                </div>
              )}
              {comparison.data && (
                <div className="mt-2">
                  <ComparisonPanel
                    comparison={comparison.data}
                    currentVersion={currentVersion}
                  />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export default function PriorVersionsCard({
  bankId,
  packageId,
}: {
  bankId: string;
  /** Any version of the chain; the endpoint resolves the whole supersession
   * chain from it. */
  packageId: string;
}) {
  const chainQuery = usePackageVersionChain(bankId, packageId);
  const chain = chainQuery.data;
  const priorVersions = (chain?.versions ?? []).filter(
    (entry) => !entry.isCurrent
  );
  const currentEntry = (chain?.versions ?? []).find((entry) => entry.isCurrent);

  if (priorVersions.length === 0) return null;

  return (
    <SectionCard
      title="Prior versions"
      subtitle="Superseded snapshots remain immutable history — open a version for its signers, its files, and what changed"
      noPadding
    >
      <ul>
        {priorVersions.map((entry) => (
          <PriorVersionRow
            key={entry.packageId}
            bankId={bankId}
            entry={entry}
            currentPackageId={chain?.currentPackageId ?? null}
            currentVersion={currentEntry?.version ?? null}
          />
        ))}
      </ul>
    </SectionCard>
  );
}
