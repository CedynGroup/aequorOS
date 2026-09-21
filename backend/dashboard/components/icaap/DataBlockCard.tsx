"use client";

/**
 * One set of figures bound into the cycle.
 *
 * The card's job is to make the PROVENANCE of a figure visible: what it is,
 * what date it is as at, whether newer figures exist, and — when the preparer
 * deliberately keeps the older ones — why. "Kept at earlier figures" always
 * carries its reason, because a pinned number with no stated reason is
 * indistinguishable from a stale one.
 *
 * A refresh reports what CHANGED rather than swapping the numbers silently,
 * and a withdrawn source can never be pinned: the server refuses, and so does
 * this card.
 */

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Pin,
  PinOff,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type {
  IcaapDataBlockRead,
  IcaapFactChangeRead,
} from "@/lib/api/icaap";
import {
  blockStatusCopy,
  blockTitle,
  fmtDateValue,
  fmtFact,
  NOT_AVAILABLE,
} from "./format";
import { INPUT_CLASS, SecondaryButton } from "./Dialog";

const MIN_REASON = 10;

const TONE_CLASS: Record<string, string> = {
  ok: "text-success",
  warn: "text-warning",
  crit: "text-critical",
  neutral: "text-slate",
};

export default function DataBlockCard({
  block,
  readOnly,
  changedFacts,
  refreshOutcome,
  busy,
  onRefresh,
  onPin,
  onUnpin,
  onRetire,
  onInsertBlock,
  onInsertFact,
}: {
  block: IcaapDataBlockRead;
  readOnly: boolean;
  /** The diff from the last refresh of THIS block, if there was one. */
  changedFacts?: readonly IcaapFactChangeRead[];
  refreshOutcome?: "bound" | "unchanged" | "unavailable" | null;
  busy?: boolean;
  onRefresh: () => void;
  onPin: (reason: string) => void;
  onUnpin: () => void;
  onRetire: (reason: string) => void;
  onInsertBlock?: () => void;
  onInsertFact?: (factKey: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [pinReason, setPinReason] = useState("");
  const [pinOpen, setPinOpen] = useState(false);
  const [retireReason, setRetireReason] = useState("");
  const [retireOpen, setRetireOpen] = useState(false);

  const asOf = block.currentBinding?.sourceAsOf
    ? fmtDateValue(block.currentBinding.sourceAsOf)
    : null;
  const status = blockStatusCopy(block.status, {
    asOf: asOf ?? undefined,
    pinReason: block.pinReason,
  });
  const facts = Object.values(block.currentBinding?.facts ?? {});
  // Optional on the contract: absent arrives as `undefined`, never `null`.
  const pinnable =
    block.status !== "source_withdrawn" && Boolean(block.currentBinding);

  return (
    <div className="border-b border-border-light px-4 py-3 last:border-0">
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown size={14} className="mt-1 shrink-0 text-slate" aria-hidden />
          ) : (
            <ChevronRight size={14} className="mt-1 shrink-0 text-slate" aria-hidden />
          )}
          <span className="min-w-0">
            <span className="block truncate text-body font-medium text-navy">
              {blockTitle(block)}
            </span>
            <span className="block text-caption">
              <span className={TONE_CLASS[status.tone]}>{status.label}</span>
              {asOf ? ` · as at ${asOf}` : ""}
            </span>
          </span>
        </button>
        {onInsertBlock && !readOnly && (
          <button
            type="button"
            onClick={onInsertBlock}
            className="shrink-0 text-caption font-medium text-action hover:underline"
          >
            Insert
          </button>
        )}
      </div>

      {block.statusDetail && (
        <p className="mt-1 pl-6 text-caption text-slate">{block.statusDetail}</p>
      )}

      {refreshOutcome === "unchanged" && (
        <p className="mt-1 pl-6 text-caption text-slate">
          Refreshed &mdash; the figures are unchanged.
        </p>
      )}
      {refreshOutcome === "bound" && (changedFacts?.length ?? 0) > 0 && (
        <div className="mt-2 ml-6 rounded border border-border-light bg-surface/60 p-2">
          <p className="text-caption font-medium text-navy">
            Updated figures
          </p>
          <ul className="mt-1 space-y-0.5">
            {changedFacts?.map((change) => (
              <li key={change.key} className="text-caption text-slate">
                {change.key}: {change.before ?? NOT_AVAILABLE} &rarr;{" "}
                {change.after ?? NOT_AVAILABLE}
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded && (
        <div className="mt-2 pl-6">
          {facts.length === 0 ? (
            <p className="text-caption text-slate">
              Nothing is linked yet. Refresh to bind this block to the
              institution&apos;s computed position.
            </p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
              {facts.map((fact) => (
                <div key={fact.key} className="min-w-0">
                  <dt className="flex items-center gap-1 truncate text-caption text-slate">
                    {fact.label}
                    {onInsertFact && !readOnly && (
                      <button
                        type="button"
                        onClick={() => onInsertFact(fact.key)}
                        className="text-action hover:underline"
                        aria-label={`Cite ${fact.label} in the narrative`}
                      >
                        cite
                      </button>
                    )}
                  </dt>
                  <dd className="text-body tnum font-medium text-navy">
                    {fmtFact(fact)}
                  </dd>
                </div>
              ))}
            </dl>
          )}

          {!readOnly && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <SecondaryButton onClick={onRefresh} disabled={busy}>
                <RefreshCw size={12} aria-hidden />
                Refresh
              </SecondaryButton>
              {block.status === "pinned" ? (
                <SecondaryButton onClick={onUnpin} disabled={busy}>
                  <PinOff size={12} aria-hidden />
                  Use the latest figures
                </SecondaryButton>
              ) : (
                <SecondaryButton
                  onClick={() => setPinOpen((value) => !value)}
                  disabled={busy || !pinnable}
                  title={
                    pinnable
                      ? undefined
                      : "Figures whose source was withdrawn cannot be kept."
                  }
                >
                  <Pin size={12} aria-hidden />
                  Keep these figures
                </SecondaryButton>
              )}
              <SecondaryButton
                onClick={() => setRetireOpen((value) => !value)}
                disabled={busy}
              >
                <Trash2 size={12} aria-hidden />
                Remove
              </SecondaryButton>
            </div>
          )}

          {pinOpen && (
            <div className="mt-2 space-y-2">
              <input
                className={INPUT_CLASS}
                value={pinReason}
                maxLength={2000}
                placeholder="Why keep these figures rather than the latest?"
                onChange={(event) => setPinReason(event.target.value)}
              />
              <SecondaryButton
                disabled={pinReason.trim().length < MIN_REASON}
                onClick={() => {
                  onPin(pinReason.trim());
                  setPinOpen(false);
                  setPinReason("");
                }}
              >
                Keep with this reason
              </SecondaryButton>
            </div>
          )}

          {retireOpen && (
            <div className="mt-2 space-y-2">
              <input
                className={INPUT_CLASS}
                value={retireReason}
                maxLength={2000}
                placeholder="Why remove this block from the cycle?"
                onChange={(event) => setRetireReason(event.target.value)}
              />
              <SecondaryButton
                disabled={retireReason.trim().length < MIN_REASON}
                onClick={() => {
                  onRetire(retireReason.trim());
                  setRetireOpen(false);
                  setRetireReason("");
                }}
              >
                Remove from this cycle
              </SecondaryButton>
              <p className="text-caption text-slate">
                Committed versions keep the figures they cited. A block a
                working document still refers to cannot be removed.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
