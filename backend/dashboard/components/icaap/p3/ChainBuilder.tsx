"use client";

/**
 * Composing the institution's own ICAAP review chain, stage by stage.
 *
 * WHY THIS MIRRORS NOTHING. A chain is not free-form — it starts with one
 * preparation stage, ends with one signature, carries at least one review or
 * approval between them, and names exactly one approval stage that seals the
 * report, immediately before the signature. Those rules are ONE function,
 * `app/domain/icaap/workflow.validate_stages`, and it validates the regime's
 * default chain and a bank's proposal alike.
 *
 * This composer therefore holds no copy of them. It builds the stages, and the
 * SERVER decides whether they make a chain: both the propose and the update
 * endpoints run that function before anything is written, so an invalid chain
 * is refused with the server's own code and sentence, and no row is created.
 * The screen prints that sentence verbatim rather than a guess of its own.
 *
 * A client-side mirror would be a second opinion that can drift from the first
 * — and a chain the screen accepts and the server refuses is worse than no
 * composer at all.
 *
 * Saving is MAKER work: it produces a draft, which governs nothing until a
 * different officer approves it, and an in-flight cycle keeps the chain it was
 * put forward under.
 */

import { useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import Dialog, {
  FieldLabel,
  INPUT_CLASS,
  PrimaryButton,
  SecondaryButton,
} from "@/components/icaap/Dialog";
import {
  ICON_XS,
  RATIONALE_MAX,
  REASON_MIN,
  ROWS_LONG,
} from "@/components/icaap/p2/display";
import type { IcaapStageInput } from "@/lib/api/icaapFiling";
import {
  BUILDER_HOW_IT_IS_DECIDED,
  BUILDER_SAVE_IS_A_DRAFT,
  STAGE_KIND_OPTIONS,
  STAGE_REFERENCE_HINT,
  STAGE_SEALS_HINT,
  STAGE_TAKEN_BY_HINT,
  stageKindAsk,
  stageKindLabel,
} from "./labels";

/** A stage while it is being composed. `seq` is assigned from the order. */
export type DraftStage = {
  stageKey: string;
  title: string;
  kind: IcaapStageInput["kind"];
  officerTitles: string;
  freezeOnApprove: boolean;
};

/**
 * A title as a machine reference.
 *
 * The reference is shown and editable, so the server's `stage_key_invalid`
 * sentence lands on a field the composer can actually see. Deriving it
 * silently would produce a refusal about something nobody was ever shown.
 */
export function referenceFor(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function toDraft(stages: readonly IcaapStageInput[]): DraftStage[] {
  return stages.map((stage) => ({
    stageKey: stage.stageKey,
    title: stage.title,
    kind: stage.kind,
    officerTitles: stage.officerTitles.join(", "),
    freezeOnApprove: stage.freezeOnApprove,
  }));
}

/** The wire payload. `seq` is the position, so reordering IS the numbering. */
export function toPayload(stages: readonly DraftStage[]) {
  return stages.map((stage, index) => ({
    seq: index + 1,
    stageKey: stage.stageKey.trim(),
    title: stage.title.trim(),
    decisionKind: stage.kind,
    officerTitles: stage.officerTitles
      .split(",")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0),
    freezeOnApprove: stage.freezeOnApprove,
  }));
}

/**
 * Whether Save is held back — and the ONLY two reasons it ever is.
 *
 * Both are about the request, not about the chain: a chain with no stages is
 * not a payload, and a change of governance without a reason is not a record.
 * Nothing here judges the SHAPE of the chain. A single review stage, two
 * sealing stages, a signature in the middle — all of them are offered to the
 * server, which refuses them in its own words. That is the point: the composer
 * has no opinion the server could contradict.
 */
export function saveBlocked(input: {
  stageCount: number;
  reason: string;
  saving: boolean;
}): boolean {
  return (
    input.saving ||
    input.stageCount === 0 ||
    input.reason.trim().length < REASON_MIN
  );
}

function moved<T>(rows: readonly T[], from: number, to: number): T[] {
  if (to < 0 || to >= rows.length) return [...rows];
  const next = [...rows];
  const [row] = next.splice(from, 1);
  next.splice(to, 0, row);
  return next;
}

function StageRow({
  stage,
  index,
  total,
  onChange,
  onMove,
  onRemove,
}: {
  stage: DraftStage;
  index: number;
  total: number;
  onChange: (next: DraftStage) => void;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const position = index + 1;
  return (
    <li className="rounded border border-border-light p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-caption font-medium text-navy">Stage {position}</p>
        <div className="flex items-center gap-1">
          <SecondaryButton
            title="Move earlier"
            disabled={index === 0}
            onClick={() => onMove(index - 1)}
          >
            <ArrowUp size={ICON_XS} aria-hidden />
            <span className="sr-only">Move stage {position} earlier</span>
          </SecondaryButton>
          <SecondaryButton
            title="Move later"
            disabled={position === total}
            onClick={() => onMove(index + 1)}
          >
            <ArrowDown size={ICON_XS} aria-hidden />
            <span className="sr-only">Move stage {position} later</span>
          </SecondaryButton>
          <SecondaryButton title="Remove this stage" onClick={onRemove}>
            <Trash2 size={ICON_XS} aria-hidden />
            <span className="sr-only">Remove stage {position}</span>
          </SecondaryButton>
        </div>
      </div>

      <div className="mt-2 space-y-3">
        <FieldLabel label="What this stage is called">
          <input
            value={stage.title}
            maxLength={RATIONALE_MAX}
            aria-label={`Stage ${position} name`}
            onChange={(event) => {
              const title = event.target.value;
              const key =
                stage.stageKey === "" ||
                stage.stageKey === referenceFor(stage.title)
                  ? referenceFor(title)
                  : stage.stageKey;
              onChange({ ...stage, title, stageKey: key });
            }}
            className={INPUT_CLASS}
          />
        </FieldLabel>

        <FieldLabel label="Reference" hint={STAGE_REFERENCE_HINT}>
          <input
            value={stage.stageKey}
            aria-label={`Stage ${position} reference`}
            onChange={(event) =>
              onChange({ ...stage, stageKey: event.target.value })
            }
            className={INPUT_CLASS}
          />
        </FieldLabel>

        <FieldLabel label="What happens here" hint={stageKindAsk(stage.kind)}>
          <select
            value={stage.kind}
            aria-label={`What happens at stage ${position}`}
            onChange={(event) =>
              onChange({
                ...stage,
                kind: event.target.value as DraftStage["kind"],
              })
            }
            className={INPUT_CLASS}
          >
            {STAGE_KIND_OPTIONS.map((kind) => (
              <option key={kind} value={kind}>
                {stageKindLabel(kind)}
              </option>
            ))}
          </select>
        </FieldLabel>

        <FieldLabel label="Taken by" hint={STAGE_TAKEN_BY_HINT}>
          <input
            value={stage.officerTitles}
            aria-label={`Who takes stage ${position}`}
            onChange={(event) =>
              onChange({ ...stage, officerTitles: event.target.value })
            }
            className={INPUT_CLASS}
          />
        </FieldLabel>

        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={stage.freezeOnApprove}
            aria-label={`Stage ${position} seals the report`}
            onChange={(event) =>
              onChange({ ...stage, freezeOnApprove: event.target.checked })
            }
            className="mt-1"
          />
          <span className="text-caption text-slate">{STAGE_SEALS_HINT}</span>
        </label>
      </div>
    </li>
  );
}

export default function ChainBuilder({
  title,
  seed,
  initialReason = "",
  saving,
  error,
  onSave,
  onClose,
}: {
  title: string;
  seed: readonly IcaapStageInput[];
  initialReason?: string;
  saving: boolean;
  /** The SERVER's refusal, printed as written. Never a message composed here. */
  error: string | null;
  onSave: (payload: {
    stages: ReturnType<typeof toPayload>;
    reason: string;
  }) => void;
  onClose: () => void;
}) {
  const [stages, setStages] = useState<DraftStage[]>(() => toDraft(seed ?? []));
  const [reason, setReason] = useState(initialReason);

  const update = (index: number, next: DraftStage) =>
    setStages((rows) => rows.map((row, at) => (at === index ? next : row)));

  return (
    <Dialog
      title={title}
      description={BUILDER_SAVE_IS_A_DRAFT}
      onClose={onClose}
      wide
      footer={
        <>
          <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          <PrimaryButton
            disabled={saveBlocked({
              stageCount: stages.length,
              reason,
              saving,
            })}
            onClick={() =>
              onSave({ stages: toPayload(stages), reason: reason.trim() })
            }
          >
            {saving ? "Saving…" : "Save this chain"}
          </PrimaryButton>
        </>
      }
    >
      <div className="space-y-4">
        {error !== null && (
          <p
            role="alert"
            className="card border-l-4 border-l-critical bg-critical-light/40 p-3 text-body text-navy/80"
          >
            {error}
          </p>
        )}

        <p className="text-caption leading-relaxed text-slate">
          {BUILDER_HOW_IT_IS_DECIDED}
        </p>

        <ol className="space-y-3">
          {stages.map((stage, index) => (
            <StageRow
              key={index}
              stage={stage}
              index={index}
              total={stages.length}
              onChange={(next) => update(index, next)}
              onMove={(to) => setStages((rows) => moved(rows, index, to))}
              onRemove={() =>
                setStages((rows) => rows.filter((_row, at) => at !== index))
              }
            />
          ))}
        </ol>

        <SecondaryButton
          onClick={() =>
            setStages((rows) => [
              ...rows,
              {
                stageKey: "",
                title: "",
                kind: "review",
                officerTitles: "",
                freezeOnApprove: false,
              },
            ])
          }
        >
          <Plus size={ICON_XS} aria-hidden />
          Add a stage
        </SecondaryButton>

        <FieldLabel label="Why this chain">
          <textarea
            value={reason}
            rows={ROWS_LONG}
            maxLength={RATIONALE_MAX}
            aria-label="Why this chain"
            onChange={(event) => setReason(event.target.value)}
            className={INPUT_CLASS}
          />
        </FieldLabel>
      </div>
    </Dialog>
  );
}
