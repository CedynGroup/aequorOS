"use client";

/**
 * Add a set of figures to the cycle.
 *
 * Only the block types this phase actually resolves are offered. A type the
 * platform declares but cannot yet produce is listed as unavailable rather
 * than hidden, so the catalogue is honest about what is coming without
 * pretending it is here.
 */

import { useState } from "react";
import { isApiError } from "@/lib/api/client";
import { useCreateIcaapBlock, type IcaapBlockTypeRead } from "@/lib/api/icaap";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "./Dialog";

export default function AddBlockDialog({
  bankId,
  cycleId,
  blockTypes,
  existingTypes,
  suggestedTypes,
  onClose,
}: {
  bankId: string;
  cycleId: string;
  blockTypes: readonly IcaapBlockTypeRead[];
  existingTypes: readonly string[];
  suggestedTypes: readonly string[];
  onClose: () => void;
}) {
  const available = blockTypes.filter((type) => type.available);
  const unavailable = blockTypes.filter((type) => !type.available);
  const ordered = [...available].sort((a, b) => {
    const aSuggested = suggestedTypes.includes(a.type) ? 0 : 1;
    const bSuggested = suggestedTypes.includes(b.type) ? 0 : 1;
    return aSuggested - bSuggested || a.title.localeCompare(b.title);
  });
  const [blockType, setBlockType] = useState(ordered[0]?.type ?? "");
  const [title, setTitle] = useState("");
  const create = useCreateIcaapBlock(bankId, cycleId);
  const alreadyAdded = existingTypes.includes(blockType);

  return (
    <Dialog
      title="Add figures"
      description="Figures are bound to the institution's own computed position for the cycle's reporting date."
      onClose={onClose}
      footer={
        <>
          <SecondaryButton onClick={onClose} disabled={create.isPending}>
            Cancel
          </SecondaryButton>
          <PrimaryButton
            disabled={!blockType || create.isPending}
            onClick={() =>
              create.mutate(
                { blockType, title: title.trim() ? title.trim() : null },
                { onSuccess: onClose },
              )
            }
          >
            {create.isPending ? "Adding…" : "Add"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-4">
        <FieldLabel
          label="Figures"
          hint={
            suggestedTypes.length > 0
              ? "The framework expects the first entries for this section."
              : undefined
          }
        >
          <select
            aria-label="Figures"
            className={INPUT_CLASS}
            value={blockType}
            onChange={(event) => setBlockType(event.target.value)}
          >
            {ordered.map((type) => (
              <option key={type.type} value={type.type}>
                {`${type.title}${suggestedTypes.includes(type.type) ? " (expected here)" : ""}`}
              </option>
            ))}
          </select>
        </FieldLabel>
        {alreadyAdded && (
          <p className="-mt-2 text-caption text-warning">
            This cycle already carries these figures. Refresh the existing block
            instead of adding a second copy.
          </p>
        )}

        <FieldLabel
          label="Title (optional)"
          hint="Leave blank to use the platform's own name for these figures."
        >
          <input
            className={INPUT_CLASS}
            value={title}
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
          />
        </FieldLabel>

        {unavailable.length > 0 && (
          <div className="rounded border border-border-light bg-surface/60 p-3">
            <p className="text-caption font-medium text-navy">
              Not available yet
            </p>
            <p className="mt-1 text-caption text-slate">
              {unavailable.map((type) => type.title).join(", ")}
            </p>
          </div>
        )}

        {create.error != null && (
          <p className="text-body text-critical">
            {isApiError(create.error)
              ? create.error.message
              : "Could not add those figures."}
          </p>
        )}
      </div>
    </Dialog>
  );
}
