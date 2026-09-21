"use client";

/**
 * The cycle's figures, alongside the section being written.
 *
 * Blocks belong to the CYCLE, not to a section (DV-007 §3), so the same
 * capital position can be cited from the executive summary and from the
 * capital-adequacy section without being bound twice. The panel puts the types
 * the framework expects for the open section first.
 */

import { useState } from "react";
import { Plus } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import {
  useCreateIcaapBlock,
  usePinIcaapBlock,
  useRefreshIcaapBlock,
  useRetireIcaapBlock,
  useUnpinIcaapBlock,
  type IcaapAttachmentRead,
  type IcaapBlockRefreshRead,
  type IcaapBlockTypeRead,
  type IcaapDataBlockRead,
} from "@/lib/api/icaap";
import AddBlockDialog from "./AddBlockDialog";
import DataBlockCard from "./DataBlockCard";
import ManualTableEditor from "./ManualTableEditor";
import { SecondaryButton } from "./Dialog";
import { blockTitle } from "./format";

export default function DataBlockPanel({
  bankId,
  cycleId,
  blocks,
  blockTypes,
  attachments,
  expectedTypes,
  readOnly,
  onInsertBlock,
  onInsertFact,
}: {
  bankId: string;
  cycleId: string;
  blocks: readonly IcaapDataBlockRead[];
  blockTypes: readonly IcaapBlockTypeRead[];
  attachments: readonly IcaapAttachmentRead[];
  expectedTypes: readonly string[];
  readOnly: boolean;
  onInsertBlock?: (blockId: string) => void;
  onInsertFact?: (blockId: string, factKey: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<
    Record<string, IcaapBlockRefreshRead>
  >({});
  const refresh = useRefreshIcaapBlock(bankId, cycleId);
  const pin = usePinIcaapBlock(bankId, cycleId);
  const unpin = useUnpinIcaapBlock(bankId, cycleId);
  const retire = useRetireIcaapBlock(bankId, cycleId);
  const create = useCreateIcaapBlock(bankId, cycleId);

  const ordered = [...blocks]
    // `retiredAt` is OPTIONAL on the contract, so an unset value arrives as
    // `undefined`, not `null`. Comparing with `=== null` hid every live block
    // and left the panel reading "no figures are linked to this cycle yet"
    // while the cycle carried them.
    .filter((block) => !block.retiredAt)
    .sort((a, b) => {
      const aExpected = expectedTypes.includes(a.blockType) ? 0 : 1;
      const bExpected = expectedTypes.includes(b.blockType) ? 0 : 1;
      return (
        aExpected - bExpected || blockTitle(a).localeCompare(blockTitle(b))
      );
    });

  const missingExpected = expectedTypes.filter(
    (type) => !ordered.some((block) => block.blockType === type),
  );

  const busy =
    refresh.isPending ||
    pin.isPending ||
    unpin.isPending ||
    retire.isPending ||
    create.isPending;

  const error = refresh.error ?? pin.error ?? unpin.error ?? retire.error;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-start justify-between gap-2 border-b border-border-light px-4 py-3">
        <div className="min-w-0">
          <h3 className="text-h3 text-navy">Figures</h3>
          <p className="mt-0.5 text-caption text-slate">
            Bound to this cycle&apos;s reporting date.
          </p>
        </div>
        {!readOnly && (
          <SecondaryButton onClick={() => setAdding(true)} disabled={busy}>
            <Plus size={12} aria-hidden />
            Add
          </SecondaryButton>
        )}
      </div>

      {ordered.length === 0 ? (
        <p className="px-4 py-3 text-body text-slate">
          No figures are linked to this cycle yet. Add a set to cite the
          institution&apos;s own computed position in the narrative, rather than
          typing numbers into the prose.
        </p>
      ) : (
        <div className="max-h-[32rem] overflow-y-auto">
          {ordered.map((block) => (
            <div key={block.id}>
              <DataBlockCard
                block={block}
                readOnly={readOnly}
                busy={busy}
                changedFacts={lastRefresh[block.id]?.changedFacts}
                refreshOutcome={lastRefresh[block.id]?.outcome ?? null}
                onRefresh={() =>
                  refresh.mutate(
                    { blockId: block.id },
                    {
                      onSuccess: (result) =>
                        setLastRefresh((current) => ({
                          ...current,
                          [block.id]: result,
                        })),
                    },
                  )
                }
                onPin={(reason) => pin.mutate({ blockId: block.id, reason })}
                onUnpin={() => unpin.mutate({ blockId: block.id })}
                onRetire={(reason) =>
                  retire.mutate({ blockId: block.id, reason })
                }
                onInsertBlock={
                  onInsertBlock ? () => onInsertBlock(block.id) : undefined
                }
                onInsertFact={
                  onInsertFact
                    ? (factKey) => onInsertFact(block.id, factKey)
                    : undefined
                }
              />
              {block.spec.manual && (
                <ManualTableEditor
                  bankId={bankId}
                  cycleId={cycleId}
                  block={block}
                  attachments={attachments}
                  readOnly={readOnly}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {missingExpected.length > 0 && (
        <p className="border-t border-border-light px-4 py-2 text-caption text-warning">
          The framework expects figures this section does not yet carry:{" "}
          {missingExpected
            .map(
              (type) =>
                blockTypes.find((spec) => spec.type === type)?.title ?? type,
            )
            .join(", ")}
          .
        </p>
      )}

      {error != null && (
        <p className="border-t border-border-light px-4 py-2 text-caption text-critical">
          {isApiError(error) ? error.message : "That action did not complete."}
        </p>
      )}

      {adding && (
        <AddBlockDialog
          bankId={bankId}
          cycleId={cycleId}
          blockTypes={blockTypes}
          existingTypes={ordered.map((block) => block.blockType)}
          suggestedTypes={expectedTypes}
          onClose={() => setAdding(false)}
        />
      )}
    </section>
  );
}
