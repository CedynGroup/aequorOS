"use client";

/**
 * Two people edited the same section.
 *
 * There is no merge in v1, and no silent overwrite: the save that lost the
 * race is held in the browser, the author is told who saved and when, and they
 * choose. Reloading discards their edit; copying it out keeps it as text they
 * can paste back after reading the other version.
 */

import { useState } from "react";
import Dialog, { PrimaryButton, SecondaryButton } from "./Dialog";
import { fmtTimestampValue } from "./format";

export type SectionConflict = {
  currentRev: number | null;
  updatedBy: string | null;
  updatedAt: Date | null;
  /** The author's unsaved text, so it can be recovered before reloading. */
  plainText: string;
};

export default function ConflictDialog({
  conflict,
  onReload,
  onClose,
}: {
  conflict: SectionConflict;
  onReload: () => void;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copyMine = async () => {
    try {
      await navigator.clipboard.writeText(conflict.plainText);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Dialog
      title="Someone else saved this section"
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={() => void copyMine()}>
            {copied ? "Copied" : "Copy my text"}
          </SecondaryButton>
          <PrimaryButton onClick={onReload}>
            Reload their version
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-3 text-body text-navy/80">
        <p>
          Your changes were not saved, because the section changed while you
          were writing. Nothing has been overwritten.
        </p>
        <p>
          {conflict.updatedBy ? `Saved by ${conflict.updatedBy}` : "Last saved"}
          {conflict.updatedAt ? ` on ${fmtTimestampValue(conflict.updatedAt)}` : ""}
          {conflict.currentRev != null
            ? `. Their version is revision ${conflict.currentRev}.`
            : "."}
        </p>
        <p>
          Copy your text first if you want to keep it, then reload and merge it
          in by hand.
        </p>
      </div>
    </Dialog>
  );
}
