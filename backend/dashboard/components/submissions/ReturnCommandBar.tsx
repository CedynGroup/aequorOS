'use client';

/**
 * The command bar of the Returns workspace.
 *
 * One row at the top of the screen carrying, in this order: what return this is,
 * what state it is in, the artifacts an officer takes away, and THE ONE ACT this
 * officer may perform on it right now. Nothing competes with that act.
 *
 * The layout is the argument. The return is the subject of this screen — an
 * officer is about to attest to figures a supervisor will read — so the figures
 * get the page, and everything above them is identity, artifacts and the single
 * decision. The previous screen put four workflow cards in a permanent
 * right-hand column, squeezed the figures into two thirds of the width, and
 * buried the exports below the fold behind acts most roles cannot perform.
 */

import type { ReactNode } from 'react';
import { Download, FileOutput, Loader2 } from 'lucide-react';
import type { ArtifactKind } from '@aequoros/risk-service-api';
import { DisabledWithReason } from '@/components/ui/DisabledWithReason';
import type { PrimaryAction } from '@/lib/submissions/returnsSurface';

/** Compact artifact names. The bar has one row; "XLSX with formulas" does not fit. */
export const ARTIFACT_BUTTON_LABELS: Record<string, string> = {
  pdf: 'PDF',
  xlsx: 'XLSX',
  xlsx_working: 'Formula copy',
  csv: 'CSV',
};

export function ReturnCommandBar({
  identity,
  meta,
  pills,
  artifacts,
  notice,
  action,
}: {
  /** "BSD3 — Liquidity Position Return", the return's own name. */
  identity: ReactNode;
  /** Reporting date · version · basis. One line, no pills. */
  meta: ReactNode;
  /** State pills: lifecycle, practice run, revision, signing clearance. */
  pills: ReactNode;
  artifacts: ReactNode;
  /** Stated before the act is offered — e.g. that filing cannot be recalled. */
  notice?: ReactNode;
  action: ReactNode;
}) {
  return (
    <section
      data-testid="return-command-bar"
      className="card px-5 py-4 flex flex-col xl:flex-row xl:items-start gap-5"
    >
      <div className="min-w-0 xl:flex-1">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-h3 text-navy min-w-0">{identity}</h2>
        </div>
        <p className="mt-1 text-caption text-slate">{meta}</p>
        <div className="mt-2 flex items-center gap-2 flex-wrap">{pills}</div>
      </div>

      <div className="shrink-0 xl:border-l xl:border-border-light xl:pl-5">
        <p className="text-micro font-medium uppercase tracking-wider text-slate">
          Artifacts
        </p>
        <div className="mt-1.5">{artifacts}</div>
      </div>

      <div className="shrink-0 xl:border-l xl:border-border-light xl:pl-5 xl:max-w-sm">
        {notice}
        {action}
      </div>
    </section>
  );
}

/**
 * One button per artifact, and it hands the file over.
 *
 * Exporting and downloading were two separate steps on two separate cards, so
 * taking the signed return away meant minting an export, finding it in a list
 * below, and pressing Download. Here the button is the file: it downloads what
 * exists, and mints it first when it does not and this officer may.
 */
export function ArtifactGroup({
  kinds,
  available,
  busyKind,
  canExport,
  onTake,
  signedLabelFor,
  unavailableReason,
}: {
  kinds: ArtifactKind[];
  /** Kinds already minted for this version. */
  available: ReadonlySet<string>;
  busyKind: ArtifactKind | null;
  canExport: boolean;
  onTake: (kind: ArtifactKind) => void;
  /** e.g. 'pdf' → 'Signed PDF' once officers have certified. */
  signedLabelFor?: (kind: ArtifactKind) => string | null;
  /** Why a kind cannot be produced by this officer. */
  unavailableReason: string;
}) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {kinds.map((kind) => {
        const exists = available.has(kind);
        const signed = signedLabelFor?.(kind) ?? null;
        const label = signed ?? ARTIFACT_BUTTON_LABELS[kind] ?? kind.toUpperCase();
        const busy = busyKind === kind;
        if (!exists && !canExport) {
          return (
            <DisabledWithReason key={kind} reason={unavailableReason}>
              {(descriptionId) => (
                <span
                  role="button"
                  aria-disabled="true"
                  aria-describedby={descriptionId}
                  tabIndex={0}
                  className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-caption font-medium text-slate-light"
                >
                  <FileOutput size={12} aria-hidden />
                  {label}
                </span>
              )}
            </DisabledWithReason>
          );
        }
        return (
          <button
            key={kind}
            type="button"
            disabled={busy}
            onClick={() => onTake(kind)}
            aria-label={`${exists ? 'Download' : 'Produce and download'} ${label}`}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-caption font-medium text-navy hover:bg-surface disabled:opacity-60"
          >
            {busy ? (
              <Loader2 size={12} className="animate-spin" aria-hidden />
            ) : exists ? (
              <Download size={12} aria-hidden />
            ) : (
              <FileOutput size={12} aria-hidden />
            )}
            {label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The single act, with its reason when it is not yet available.
 *
 * Disabled means "yours, but not now" and always carries the reason —
 * on screen, not only on hover, because a greyed control with a tooltip is
 * invisible on a touch device and unreadable to a screen reader that never
 * focuses it.
 */
export function PrimaryActionButton({
  action,
  pending,
  onClick,
  icon,
  testId,
}: {
  action: PrimaryAction;
  pending: boolean;
  onClick: () => void;
  icon?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="min-w-0">
      {action.enabled ? (
        <button
          type="button"
          data-testid={testId}
          disabled={pending}
          onClick={onClick}
          className="w-full inline-flex items-center justify-center gap-1.5 px-3.5 py-2.5 text-body font-medium btn-primary disabled:opacity-60"
        >
          {pending ? (
            <Loader2 size={14} className="animate-spin" aria-hidden />
          ) : (
            icon
          )}
          {action.label}
        </button>
      ) : (
        <DisabledWithReason reason={action.reason ?? action.caption}>
          {(descriptionId) => (
            <span
              role="button"
              data-testid={testId}
              aria-disabled="true"
              aria-describedby={descriptionId}
              tabIndex={0}
              className="w-full inline-flex cursor-not-allowed items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-3.5 py-2.5 text-body font-medium text-slate-light"
            >
              {icon}
              {action.label}
            </span>
          )}
        </DisabledWithReason>
      )}
      <p
        data-testid={testId ? `${testId}-reason` : undefined}
        className="mt-1.5 text-caption text-slate leading-relaxed"
      >
        {action.enabled ? action.caption : action.reason}
      </p>
    </div>
  );
}
